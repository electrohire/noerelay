use axum::{
    Router,
    body::Body,
    http::{Request, StatusCode},
    middleware,
    routing::get,
};
use chrono::{Duration, Utc};
use noerelay_core::iam::*;
use noerelay_gateway::iam::{IamMiddlewareState, admin_permission_registry, iam_middleware};
use sha2::Digest;
use std::sync::Arc;
use tower::ServiceExt;
use uuid::Uuid;

#[derive(Clone)]
struct StubProvider(AuthenticatedIdentity);

impl IdentityProvider for StubProvider {
    fn authenticate(&self, _token: &str) -> Result<AuthenticatedIdentity, IamError> {
        Ok(self.0.clone())
    }

    fn provider_type(&self) -> IdentityProviderType {
        IdentityProviderType::Oidc
    }
}

fn identity(scopes: &[&str]) -> AuthenticatedIdentity {
    AuthenticatedIdentity {
        provider_type: IdentityProviderType::Oidc,
        principal_id: PrincipalId(Uuid::new_v4()),
        organization_id: OrganizationId(Uuid::new_v4()),
        scopes: scopes.iter().map(|scope| (*scope).to_owned()).collect(),
        claims: None,
        authenticated_at: Utc::now(),
    }
}

async fn request(identity: AuthenticatedIdentity, method: &str, path: &str) -> StatusCode {
    let key_hash: [u8; 32] = sha2::Sha256::digest(b"legacy-key").into();
    let state = Arc::new(
        IamMiddlewareState::new(None, None, key_hash, None)
            .with_oidc_provider(Arc::new(StubProvider(identity))),
    );
    let app = Router::new()
        .fallback(get(|| async { StatusCode::OK }))
        .layer(middleware::from_fn_with_state(state, iam_middleware));
    app.oneshot(
        Request::builder()
            .method(method)
            .uri(path)
            .header("authorization", "Bearer a.b.c")
            .body(Body::empty())
            .unwrap(),
    )
    .await
    .unwrap()
    .status()
}

#[tokio::test]
async fn unmapped_route_returns_forbidden() {
    assert_eq!(
        request(identity(&["organization:read"]), "GET", "/unmapped").await,
        StatusCode::FORBIDDEN
    );
}

#[tokio::test]
async fn correct_permission_passes() {
    assert_eq!(
        request(
            identity(&["organization:read"]),
            "GET",
            "/v1/admin/organizations"
        )
        .await,
        StatusCode::OK
    );
}

#[tokio::test]
async fn wrong_permission_returns_forbidden() {
    assert_eq!(
        request(
            identity(&["project:read"]),
            "GET",
            "/v1/admin/organizations"
        )
        .await,
        StatusCode::FORBIDDEN
    );
}

#[tokio::test]
async fn method_is_part_of_permission_mapping() {
    assert_eq!(
        request(
            identity(&["organization:read"]),
            "PATCH",
            "/v1/admin/organizations"
        )
        .await,
        StatusCode::FORBIDDEN
    );
}

#[tokio::test]
async fn query_string_preserves_mapping() {
    assert_eq!(
        request(
            identity(&["organization:read"]),
            "GET",
            "/v1/admin/organizations?page=2"
        )
        .await,
        StatusCode::OK
    );
}

#[tokio::test]
async fn mutation_with_permission_requires_step_up() {
    assert_eq!(
        request(
            identity(&["organization:create"]),
            "POST",
            "/v1/admin/organizations"
        )
        .await,
        StatusCode::FORBIDDEN
    );
}

#[tokio::test]
async fn nil_identity_returns_forbidden() {
    let mut value = identity(&["organization:read"]);
    value.principal_id = PrincipalId(Uuid::nil());
    assert_eq!(
        request(value, "GET", "/v1/admin/organizations").await,
        StatusCode::FORBIDDEN
    );
}

#[tokio::test]
async fn expired_identity_returns_forbidden() {
    let mut value = identity(&["organization:read"]);
    value.claims = Some(OidcClaims {
        issuer: "https://issuer.example".into(),
        subject: "subject".into(),
        audience: "noerelay".into(),
        expires_at: Utc::now() - Duration::seconds(1),
        issued_at: Utc::now() - Duration::minutes(1),
        nonce: None,
        email: None,
        name: None,
        custom_claims: Default::default(),
    });
    assert_eq!(
        request(value, "GET", "/v1/admin/organizations").await,
        StatusCode::FORBIDDEN
    );
}

#[test]
fn registry_maps_every_declared_admin_method_path_pair() {
    let registry = admin_permission_registry();
    assert_eq!(registry.routes.len(), 27);
    assert!(
        registry
            .routes
            .iter()
            .all(|route| !route.required_permission.is_empty())
    );
}
