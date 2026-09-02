//! IAM middleware for the NoeRelay gateway.
//!
//! Provides identity resolution from API key headers, attaching
//! [`ResolvedIdentity`] to request extensions for downstream handlers.
//! Implements deny-by-default authorization with rate limiting and
//! concurrency tracking via [`ApiKeyRepository`].

use axum::{
    Json,
    extract::{Request, State},
    http::{HeaderMap, StatusCode},
    middleware::Next,
    response::{IntoResponse, Response},
};
use jsonwebtoken::{DecodingKey, Validation, decode, decode_header, jwk::JwkSet};
use noerelay_core::iam::*;
use noerelay_store::{ApiKeyRepository, IamRepository, IamStoreError};
use serde::Deserialize;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{collections::HashMap, sync::Arc};
use uuid::Uuid;

/// Key for extracting [`ResolvedIdentity`] from request extensions.
pub const RESOLVED_IDENTITY_KEY: &str = "noerelay.resolved_identity";

/// IAM middleware state, shared across all requests.
#[derive(Clone)]
pub struct IamMiddlewareState {
    pub repo: Option<IamRepository>,
    pub api_key_repo: Option<ApiKeyRepository>,
    pub bearer_key_sha256: [u8; 32],
    pub default_org_id: Option<OrganizationId>,
    pub oidc_providers: Vec<Arc<dyn IdentityProvider>>,
    pub permission_registry: Arc<PermissionRegistry>,
}

impl IamMiddlewareState {
    pub fn new(
        repo: Option<IamRepository>,
        api_key_repo: Option<ApiKeyRepository>,
        bearer_key_sha256: [u8; 32],
        default_org_id: Option<OrganizationId>,
    ) -> Self {
        Self {
            repo,
            api_key_repo,
            bearer_key_sha256,
            default_org_id,
            oidc_providers: Vec::new(),
            permission_registry: Arc::new(admin_permission_registry()),
        }
    }

    pub fn with_oidc_provider(mut self, provider: Arc<dyn IdentityProvider>) -> Self {
        self.oidc_providers.push(provider);
        self
    }

    pub fn with_permission_registry(mut self, registry: PermissionRegistry) -> Self {
        self.permission_registry = Arc::new(registry);
        self
    }
}

/// OIDC provider with a fetched, immutable JWKS snapshot. Construct a new
/// instance to refresh rotated keys; tokens never select an untrusted JWKS URL.
pub struct OidcIdentityProvider {
    config: OidcConfig,
    organization_id: OrganizationId,
    jwks: JwkSet,
    expected_nonce: Option<String>,
}

impl OidcIdentityProvider {
    pub async fn fetch(
        config: OidcConfig,
        organization_id: OrganizationId,
        expected_nonce: Option<String>,
    ) -> Result<Self, IamError> {
        let client = reqwest::Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .map_err(|_| IamError::InvalidToken)?;
        let jwks = client
            .get(&config.jwks_url)
            .send()
            .await
            .map_err(|_| IamError::InvalidToken)?
            .error_for_status()
            .map_err(|_| IamError::InvalidToken)?
            .json::<JwkSet>()
            .await
            .map_err(|_| IamError::InvalidToken)?;
        Ok(Self::from_jwks(
            config,
            organization_id,
            jwks,
            expected_nonce,
        ))
    }

    pub fn from_jwks(
        config: OidcConfig,
        organization_id: OrganizationId,
        jwks: JwkSet,
        expected_nonce: Option<String>,
    ) -> Self {
        Self {
            config,
            organization_id,
            jwks,
            expected_nonce,
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
#[serde(untagged)]
enum JwtAudience {
    One(String),
    Many(Vec<String>),
}

impl JwtAudience {
    fn contains(&self, expected: &str) -> bool {
        match self {
            Self::One(value) => value == expected,
            Self::Many(values) => values.iter().any(|value| value == expected),
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
struct JwtClaims {
    iss: String,
    sub: String,
    aud: JwtAudience,
    exp: i64,
    iat: i64,
    nonce: Option<String>,
    email: Option<String>,
    name: Option<String>,
    #[serde(flatten)]
    custom: HashMap<String, serde_json::Value>,
}

impl IdentityProvider for OidcIdentityProvider {
    fn authenticate(&self, token: &str) -> Result<AuthenticatedIdentity, IamError> {
        let header = decode_header(token).map_err(|_| IamError::InvalidToken)?;
        let key_id = header.kid.ok_or(IamError::InvalidSignature)?;
        let jwk = self
            .jwks
            .keys
            .iter()
            .find(|key| key.common.key_id.as_deref() == Some(key_id.as_str()))
            .ok_or(IamError::InvalidSignature)?;
        let key = DecodingKey::from_jwk(jwk).map_err(|_| IamError::InvalidSignature)?;
        let mut validation = Validation::new(header.alg);
        validation.set_issuer(&[self.config.issuer.as_str()]);
        validation.set_audience(&[self.config.audience.as_str()]);
        validation.leeway = self.config.clock_skew_seconds.max(0) as u64;
        validation.set_required_spec_claims(&["exp", "iat", "iss", "sub", "aud"]);
        let decoded = decode::<JwtClaims>(token, &key, &validation).map_err(map_jwt_error)?;
        let claims = decoded.claims;

        // Keep explicit checks at the port boundary even though jsonwebtoken's
        // validation already enforces these values.
        if claims.iss != self.config.issuer {
            return Err(IamError::InvalidIssuer);
        }
        if !claims.aud.contains(&self.config.audience) {
            return Err(IamError::InvalidAudience);
        }
        if self.config.require_nonce {
            let (Some(expected), Some(actual)) =
                (self.expected_nonce.as_deref(), claims.nonce.as_deref())
            else {
                return Err(IamError::InvalidToken);
            };
            if expected != actual {
                return Err(IamError::InvalidToken);
            }
        }
        let now = chrono::Utc::now().timestamp();
        let skew = self.config.clock_skew_seconds.max(0);
        if claims.iat > now.saturating_add(skew) {
            return Err(IamError::InvalidToken);
        }

        let principal_id = claims
            .custom
            .get("principal_id")
            .and_then(serde_json::Value::as_str)
            .and_then(|value| Uuid::parse_str(value).ok())
            .map(PrincipalId)
            .ok_or(IamError::InvalidToken)?;
        let provider_type = match claims
            .custom
            .get("identity_type")
            .and_then(serde_json::Value::as_str)
        {
            Some("service") | Some("workload") => IdentityProviderType::ServiceIdentity,
            _ => IdentityProviderType::Oidc,
        };
        let scopes = map_claims_to_scopes(&claims.custom, &self.config.claim_to_scope);
        let expires_at =
            chrono::DateTime::from_timestamp(claims.exp, 0).ok_or(IamError::InvalidToken)?;
        let issued_at =
            chrono::DateTime::from_timestamp(claims.iat, 0).ok_or(IamError::InvalidToken)?;

        Ok(AuthenticatedIdentity {
            provider_type,
            principal_id,
            organization_id: self.organization_id,
            scopes,
            claims: Some(OidcClaims {
                issuer: claims.iss,
                subject: claims.sub,
                audience: self.config.audience.clone(),
                expires_at,
                issued_at,
                nonce: claims.nonce,
                email: claims.email,
                name: claims.name,
                custom_claims: claims.custom,
            }),
            authenticated_at: chrono::Utc::now(),
        })
    }

    fn provider_type(&self) -> IdentityProviderType {
        IdentityProviderType::Oidc
    }
}

/// IAM middleware that resolves identity from the Authorization header.
///
/// Authentication flow:
/// 1. If [`ApiKeyRepository`] is available, parse Bearer token as
///    `prefix.secret` and verify via Argon2id constant-time comparison.
/// 2. Fall back to legacy SHA256 comparison against configured key.
/// 3. Apply rate limiting and concurrency tracking if configured.
/// 4. Resolve the full identity with memberships and permissions.
/// 5. Attach [`ResolvedIdentity`] to request extensions.
///
/// If no repository is available (stateless mode), a default identity
/// is constructed from the configured default scope.
pub async fn iam_middleware(
    State(state): State<Arc<IamMiddlewareState>>,
    mut request: Request,
    next: Next,
) -> Response {
    let headers = request.headers().clone();
    let ip = extract_client_ip(&headers);
    let method = request.method().as_str().to_owned();
    let path = request.uri().path().to_owned();

    // Try API key repository verification first
    let auth_result = if let Some(api_key_repo) = &state.api_key_repo {
        verify_with_api_key_repo(api_key_repo, &headers, ip.as_deref()).await
    } else {
        None
    };

    match auth_result {
        Some(Ok(verification)) if verification.valid => {
            // API key verified successfully
            if let Some(ref api_key) = verification.api_key {
                // Apply rate limiting
                if let Some(api_key_repo) = &state.api_key_repo {
                    match api_key_repo.check_rate_limit(api_key.id).await {
                        Ok(decision) if !decision.allowed => {
                            return error_response(
                                StatusCode::TOO_MANY_REQUESTS,
                                "rate_limit_exceeded",
                                &format!("Rate limit exceeded. Reset at {}", decision.reset_at),
                            );
                        }
                        Err(_) => {
                            return error_response(
                                StatusCode::SERVICE_UNAVAILABLE,
                                "rate_limit_error",
                                "Rate limit check failed",
                            );
                        }
                        _ => {} // allowed
                    }

                    // Acquire concurrency slot
                    match api_key_repo.acquire_concurrency(api_key.id).await {
                        Ok(true) => {
                            // Store key_id for release after request
                            request.extensions_mut().insert(api_key.id);
                        }
                        Ok(false) => {
                            return error_response(
                                StatusCode::TOO_MANY_REQUESTS,
                                "concurrency_exceeded",
                                "Concurrency limit exceeded",
                            );
                        }
                        Err(_) => {
                            return error_response(
                                StatusCode::SERVICE_UNAVAILABLE,
                                "concurrency_error",
                                "Concurrency check failed",
                            );
                        }
                    }
                }

                // Resolve identity from the API key's principal
                let identity = match &state.repo {
                    Some(repo) => match resolve_from_api_key(repo, api_key, &state).await {
                        Ok(Some(identity)) => identity,
                        Ok(None) => {
                            return error_response(
                                StatusCode::UNAUTHORIZED,
                                "identity_not_found",
                                "No principal found for this API key",
                            );
                        }
                        Err(_) => {
                            return error_response(
                                StatusCode::SERVICE_UNAVAILABLE,
                                "iam_error",
                                "Identity resolution failed",
                            );
                        }
                    },
                    None => default_identity(&state),
                };

                let authenticated =
                    authenticated_from_resolved(&identity, IdentityProviderType::ApiKey);
                if !is_authenticated_api_route(&method, &path) {
                    if let Some(response) = rbac_denial(state.permission_registry.check(
                        &authenticated,
                        &method,
                        &path,
                    )) {
                        return response;
                    }
                }
                request.extensions_mut().insert(authenticated);
                request.extensions_mut().insert(identity);
                let response = next.run(request).await;
                return response;
            }
        }
        Some(Ok(_verification)) => {
            // A JWT-shaped token can legitimately miss API-key lookup; allow
            // explicitly configured OIDC providers to validate it next.
            if !bearer_token(&headers).is_some_and(|token| token.split('.').count() == 3)
                || state.oidc_providers.is_empty()
            {
                return error_response(
                    StatusCode::UNAUTHORIZED,
                    "invalid_api_key",
                    "Invalid API key",
                );
            }
        }
        Some(Err(_)) => {
            return error_response(
                StatusCode::SERVICE_UNAVAILABLE,
                "iam_error",
                "API key verification failed",
            );
        }
        None => {
            // Fall through to legacy auth
        }
    }

    // A compact JWT has exactly three dot-separated segments. API keys were
    // attempted first, so JWT-shaped credentials never fall back to API-key
    // hash authentication after OIDC validation fails.
    if bearer_token(&headers).is_some_and(|token| token.split('.').count() == 3) {
        let token = bearer_token(&headers).expect("bearer token checked above");
        let authenticated = state
            .oidc_providers
            .iter()
            .find_map(|provider| provider.authenticate(token).ok());
        let Some(authenticated) = authenticated else {
            return error_response(
                StatusCode::UNAUTHORIZED,
                "invalid_oidc_token",
                "OIDC token validation failed",
            );
        };
        if !is_authenticated_api_route(&method, &path) {
            if let Some(response) = rbac_denial(state.permission_registry.check(
                &authenticated,
                &method,
                &path,
            )) {
                return response;
            }
        }
        request.extensions_mut().insert(authenticated);
        return next.run(request).await;
    }

    // Legacy: validate against configured bearer key SHA256
    if !authorized(&headers, &state.bearer_key_sha256) {
        return error_response(
            StatusCode::UNAUTHORIZED,
            "invalid_api_key",
            "Invalid API key",
        );
    }

    // Resolve identity (legacy path)
    let identity = match &state.repo {
        Some(repo) => match resolve_from_repo(repo, &state).await {
            Ok(Some(identity)) => identity,
            Ok(None) => {
                // No principal in DB — fall back to stateless default identity
                default_identity(&state)
            }
            Err(_) => {
                return error_response(
                    StatusCode::SERVICE_UNAVAILABLE,
                    "iam_error",
                    "Identity resolution failed",
                );
            }
        },
        None => {
            // Stateless mode: construct a default identity
            default_identity(&state)
        }
    };

    let authenticated = authenticated_from_resolved(&identity, IdentityProviderType::ApiKey);
    if !is_authenticated_api_route(&method, &path) {
        if let Some(response) = rbac_denial(state.permission_registry.check(
            &authenticated,
            &method,
            &path,
        )) {
            return response;
        }
    }
    request.extensions_mut().insert(authenticated);
    request.extensions_mut().insert(identity);
    next.run(request).await
}

/// Extract [`ResolvedIdentity`] from request extensions.
///
/// Returns `None` if the IAM middleware was not applied.
pub fn extract_identity(request: &Request) -> Option<&ResolvedIdentity> {
    request.extensions().get::<ResolvedIdentity>()
}

/// Require a specific permission for the current request.
///
/// Returns `true` if the identity has the permission, `false` otherwise.
/// If no identity is present, returns `false`.
pub fn require_permission(request: &Request, resource: &str, action: &str) -> bool {
    extract_identity(request).is_some_and(|identity| {
        identity.has_permission(resource, action, &identity.effective_scope)
    })
}

/// Deny-by-default guard: returns 403 if the identity lacks the permission.
pub fn deny_by_default(request: &Request, resource: &str, action: &str) -> Option<Response> {
    if !require_permission(request, resource, action) {
        Some(error_response(
            StatusCode::FORBIDDEN,
            "permission_denied",
            &format!("Missing permission: {resource}:{action}"),
        ))
    } else {
        None
    }
}

// ============================================================================
// Internal helpers
// ============================================================================

async fn resolve_from_repo(
    repo: &IamRepository,
    state: &IamMiddlewareState,
) -> Result<Option<ResolvedIdentity>, IamStoreError> {
    let Some(org_id) = state.default_org_id else {
        return Ok(None);
    };

    // Use the API key hash as the external ID for the principal lookup
    let external_id = hex::encode(state.bearer_key_sha256);

    repo.resolve_identity_by_external_id(
        org_id,
        PrincipalType::Service,
        &external_id,
        &Scope::Organization(org_id),
    )
    .await
}

/// Verify a Bearer token using the ApiKeyRepository.
///
/// Parses the token as `prefix.secret` (dot-separated) and performs
/// Argon2id constant-time verification against stored hashes.
async fn verify_with_api_key_repo(
    api_key_repo: &ApiKeyRepository,
    headers: &HeaderMap,
    ip: Option<&str>,
) -> Option<Result<ApiKeyVerification, noerelay_store::ApiKeyStoreError>> {
    let token = headers
        .get("authorization")
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.strip_prefix("Bearer "))?;

    // Parse as "prefix.secret" — the dot separates the non-secret prefix
    // from the secret portion
    let dot_pos = token.find('.')?;
    let prefix = &token[..dot_pos];
    let secret = &token[dot_pos + 1..];

    if prefix.is_empty() || secret.is_empty() {
        return None;
    }

    Some(api_key_repo.verify_key(prefix, secret, ip).await)
}

/// Resolve identity from a verified API key's principal.
async fn resolve_from_api_key(
    repo: &IamRepository,
    api_key: &ApiKey,
    _state: &IamMiddlewareState,
) -> Result<Option<ResolvedIdentity>, IamStoreError> {
    let org_id = api_key.organization_id;
    let scope = match (api_key.project_id, api_key.environment_id) {
        (None, None) => Scope::Organization(org_id),
        (Some(proj_id), None) => Scope::Project(org_id, proj_id),
        (Some(proj_id), Some(env_id)) => Scope::Environment(org_id, proj_id, env_id),
        (None, Some(_)) => Scope::Organization(org_id),
    };

    repo.resolve_identity(api_key.principal_id, &scope).await
}

/// Extract the client IP from request headers.
fn extract_client_ip(headers: &HeaderMap) -> Option<String> {
    headers
        .get("x-forwarded-for")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.split(',').next())
        .map(|s| s.trim().to_string())
        .or_else(|| {
            headers
                .get("x-real-ip")
                .and_then(|v| v.to_str().ok())
                .map(|s| s.to_string())
        })
}

fn bearer_token(headers: &HeaderMap) -> Option<&str> {
    headers
        .get("authorization")
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.strip_prefix("Bearer "))
}

fn authenticated_from_resolved(
    identity: &ResolvedIdentity,
    provider_type: IdentityProviderType,
) -> AuthenticatedIdentity {
    AuthenticatedIdentity {
        provider_type,
        principal_id: identity.principal.principal_id,
        organization_id: identity.principal.organization_id,
        scopes: identity
            .permissions
            .iter()
            .map(|permission| format!("{}:{}", permission.resource, permission.action))
            .collect(),
        claims: None,
        authenticated_at: chrono::Utc::now(),
    }
}

fn rbac_denial(decision: RbacDecision) -> Option<Response> {
    if decision.allowed {
        return None;
    }
    if decision.reason == RbacDenyReason::StepUpRequired {
        return Some(
            (
                StatusCode::FORBIDDEN,
                Json(json!({
                    "error": {
                        "message": "Step-up approval is required for this action",
                        "type": "noerelay_error",
                        "code": "step_up_required",
                    },
                    "step_up": {
                        "required_permission": decision.required_permission,
                        "action_hash_required": true,
                    }
                })),
            )
                .into_response(),
        );
    }
    Some(error_response(
        StatusCode::FORBIDDEN,
        match decision.reason {
            RbacDenyReason::RouteNotFound => "route_not_mapped",
            RbacDenyReason::MissingPermission => "permission_denied",
            RbacDenyReason::Expired => "identity_expired",
            RbacDenyReason::InvalidIdentity => "invalid_identity",
            RbacDenyReason::Allowed | RbacDenyReason::StepUpRequired => "permission_denied",
        },
        "Request denied by the permission registry",
    ))
}

fn is_authenticated_api_route(method: &str, path: &str) -> bool {
    matches!(
        (method, path),
        ("GET", "/v1/models")
            | ("POST", "/v1/chat/completions")
            | ("POST", "/v1/responses")
            | ("GET", "/v1/noerelay/reports/costs")
            | ("POST", "/v1/noerelay/governance/release-gate")
    ) || (method == "GET" && path.starts_with("/v1/noerelay/runs/") && path.ends_with("/receipt"))
}

fn map_claims_to_scopes(
    claims: &HashMap<String, serde_json::Value>,
    mapping: &HashMap<String, String>,
) -> Vec<String> {
    let mut scopes = Vec::new();
    for (claim_name, prefix) in mapping {
        let Some(value) = claims.get(claim_name) else {
            continue;
        };
        let values: Vec<&str> = match value {
            serde_json::Value::String(value) => vec![value],
            serde_json::Value::Array(values) => values
                .iter()
                .filter_map(serde_json::Value::as_str)
                .collect(),
            _ => Vec::new(),
        };
        scopes.extend(values.into_iter().map(|value| {
            if prefix.is_empty() {
                value.to_owned()
            } else {
                format!("{prefix}:{value}")
            }
        }));
    }
    scopes.sort();
    scopes.dedup();
    scopes
}

fn map_jwt_error(error: jsonwebtoken::errors::Error) -> IamError {
    use jsonwebtoken::errors::ErrorKind;
    match error.kind() {
        ErrorKind::ExpiredSignature => IamError::TokenExpired,
        ErrorKind::InvalidIssuer => IamError::InvalidIssuer,
        ErrorKind::InvalidAudience => IamError::InvalidAudience,
        ErrorKind::InvalidSignature | ErrorKind::InvalidAlgorithm => IamError::InvalidSignature,
        _ => IamError::InvalidToken,
    }
}

/// Complete deny-default mapping for every route declared by `admin_routes`.
pub fn admin_permission_registry() -> PermissionRegistry {
    fn route(method: &str, path: &str, permission: &str, step_up: bool) -> RoutePermission {
        RoutePermission {
            method: method.to_owned(),
            path_pattern: path.to_owned(),
            required_permission: permission.to_owned(),
            requires_step_up: step_up,
        }
    }

    PermissionRegistry {
        routes: vec![
            route("GET", "/v1/admin/organizations", "organization:read", false),
            route(
                "POST",
                "/v1/admin/organizations",
                "organization:create",
                true,
            ),
            route(
                "GET",
                "/v1/admin/organizations/{organization_id}",
                "organization:read",
                false,
            ),
            route(
                "GET",
                "/v1/admin/organizations/{organization_id}/projects",
                "project:read",
                false,
            ),
            route(
                "POST",
                "/v1/admin/organizations/{organization_id}/projects",
                "project:create",
                true,
            ),
            route(
                "GET",
                "/v1/admin/organizations/{organization_id}/projects/{project_id}",
                "project:read",
                false,
            ),
            route(
                "GET",
                "/v1/admin/organizations/{organization_id}/projects/{project_id}/environments",
                "environment:read",
                false,
            ),
            route(
                "POST",
                "/v1/admin/organizations/{organization_id}/projects/{project_id}/environments",
                "environment:create",
                true,
            ),
            route(
                "GET",
                "/v1/admin/organizations/{organization_id}/projects/{project_id}/environments/{environment_id}",
                "environment:read",
                false,
            ),
            route(
                "GET",
                "/v1/admin/organizations/{organization_id}/principals",
                "principal:read",
                false,
            ),
            route(
                "POST",
                "/v1/admin/organizations/{organization_id}/principals",
                "principal:create",
                true,
            ),
            route(
                "GET",
                "/v1/admin/organizations/{organization_id}/principals/{principal_id}",
                "principal:read",
                false,
            ),
            route(
                "GET",
                "/v1/admin/organizations/{organization_id}/roles",
                "role:read",
                false,
            ),
            route(
                "POST",
                "/v1/admin/organizations/{organization_id}/roles",
                "role:create",
                true,
            ),
            route(
                "GET",
                "/v1/admin/organizations/{organization_id}/roles/{role_id}",
                "role:read",
                false,
            ),
            route(
                "GET",
                "/v1/admin/organizations/{organization_id}/memberships",
                "membership:read",
                false,
            ),
            route(
                "POST",
                "/v1/admin/organizations/{organization_id}/memberships",
                "membership:create",
                true,
            ),
            route("GET", "/v1/admin/quotas", "quota:read", false),
            route("POST", "/v1/admin/quotas", "quota:create", true),
            route(
                "GET",
                "/v1/admin/policy-bindings",
                "policy_binding:read",
                false,
            ),
            route(
                "POST",
                "/v1/admin/policy-bindings",
                "policy_binding:create",
                true,
            ),
            route("GET", "/v1/admin/permissions", "permission:read", false),
            route(
                "GET",
                "/v1/admin/organizations/{organization_id}/audit-log",
                "audit_log:read",
                false,
            ),
            route("GET", "/v1/admin/api-keys", "api_key:read", false),
            route("POST", "/v1/admin/api-keys", "api_key:create", true),
            route(
                "DELETE",
                "/v1/admin/api-keys/{api_key_id}",
                "api_key:revoke",
                true,
            ),
            route(
                "POST",
                "/v1/admin/api-keys/{api_key_id}/rotate",
                "api_key:rotate",
                true,
            ),
        ],
    }
}

fn default_identity(state: &IamMiddlewareState) -> ResolvedIdentity {
    let org_id = state.default_org_id.unwrap_or(OrganizationId(Uuid::nil()));
    let now = chrono::Utc::now();

    ResolvedIdentity {
        principal: Principal {
            principal_id: PrincipalId(Uuid::nil()),
            organization_id: org_id,
            principal_type: PrincipalType::Service,
            external_id: "api-key-principal".into(),
            display_name: "API Key Principal".into(),
            status: EntityStatus::Active,
            created_at: now,
            updated_at: now,
            deleted_at: None,
        },
        memberships: vec![],
        roles: vec![],
        permissions: vec![],
        effective_scope: Scope::Organization(org_id),
    }
}

fn authorized(headers: &HeaderMap, expected: &[u8; 32]) -> bool {
    let Some(value) = headers
        .get("authorization")
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.strip_prefix("Bearer "))
    else {
        return false;
    };
    let actual: [u8; 32] = Sha256::digest(value.as_bytes()).into();
    constant_time_equal(&actual, expected)
}

fn constant_time_equal(left: &[u8], right: &[u8]) -> bool {
    if left.len() != right.len() {
        return false;
    }
    left.iter()
        .zip(right)
        .fold(0_u8, |difference, (a, b)| difference | (a ^ b))
        == 0
}

fn error_response(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({
            "error": {
                "message": message,
                "type": "noerelay_error",
                "code": code,
            }
        })),
    )
        .into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn constant_time_equality_is_correct() {
        assert!(constant_time_equal(b"same", b"same"));
        assert!(!constant_time_equal(b"same", b"diff"));
        assert!(!constant_time_equal(b"short", b"longer"));
    }

    #[test]
    fn default_identity_has_nil_uuids() {
        let state = IamMiddlewareState::new(None, None, [0u8; 32], None);
        let identity = default_identity(&state);
        assert_eq!(identity.principal.principal_id.0, Uuid::nil());
        assert!(identity.permissions.is_empty());
        assert!(identity.memberships.is_empty());
    }

    #[test]
    fn default_identity_with_org_id() {
        let org_id = OrganizationId(Uuid::new_v4());
        let state = IamMiddlewareState::new(None, None, [0u8; 32], Some(org_id));
        let identity = default_identity(&state);
        assert_eq!(identity.principal.organization_id, org_id);
        assert_eq!(identity.effective_scope, Scope::Organization(org_id));
    }
}
