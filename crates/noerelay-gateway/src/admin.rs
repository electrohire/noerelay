//! Admin API routes for IAM management.
//!
//! These routes provide CRUD operations for organizations, projects,
//! environments, principals, roles, memberships, quotas, policy bindings,
//! and API keys. All routes require authentication via the IAM middleware
//! and enforce deny-by-default authorization.

use axum::{
    Json, Router,
    extract::{Path, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{delete, get, post},
};
use noerelay_core::iam::*;
use noerelay_store::{ApiKeyRepository, ApiKeyStoreError, IamRepository, IamStoreError};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use uuid::Uuid;

/// Shared state for admin routes.
#[derive(Clone)]
pub struct AdminState {
    pub repo: IamRepository,
    pub api_key_repo: Option<ApiKeyRepository>,
}

// ============================================================================
// Request/Response types
// ============================================================================

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreateOrganizationRequest {
    pub name: String,
    pub slug: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreateProjectRequest {
    pub name: String,
    pub slug: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreateEnvironmentRequest {
    pub name: String,
    pub slug: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreatePrincipalRequest {
    pub principal_type: PrincipalType,
    pub external_id: String,
    pub display_name: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreateRoleRequest {
    pub name: String,
    pub description: Option<String>,
    pub is_system: Option<bool>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreateMembershipRequest {
    pub principal_id: PrincipalId,
    pub scope: Scope,
    pub role_id: RoleId,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreateQuotaRequest {
    pub scope_type: ScopeType,
    pub scope_id: String,
    pub resource_type: String,
    pub limit_value: u64,
    pub period: QuotaPeriod,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreatePolicyBindingRequest {
    pub scope_type: ScopeType,
    pub scope_id: String,
    pub policy_type: String,
    pub policy_data: serde_json::Value,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreateApiKeyRequest {
    pub principal_id: PrincipalId,
    pub organization_id: OrganizationId,
    pub project_id: Option<ProjectId>,
    pub environment_id: Option<EnvironmentId>,
    pub role_id: Option<RoleId>,
    pub name: String,
    pub expires_at: Option<chrono::DateTime<chrono::Utc>>,
    pub rate_limit_per_minute: Option<i32>,
    pub concurrency_limit: Option<i32>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RevokeApiKeyRequest {
    pub reason: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub struct ListResponse<T: Serialize> {
    pub data: Vec<T>,
    pub total: usize,
}

// ============================================================================
// Router
// ============================================================================

pub fn admin_routes(state: AdminState) -> Router {
    let shared = Arc::new(state);
    Router::new()
        // Organizations
        .route("/v1/admin/organizations", post(create_organization).get(list_organizations))
        .route("/v1/admin/organizations/{organization_id}", get(get_organization))
        // Projects
        .route("/v1/admin/organizations/{organization_id}/projects", post(create_project).get(list_projects))
        .route("/v1/admin/organizations/{organization_id}/projects/{project_id}", get(get_project))
        // Environments
        .route("/v1/admin/organizations/{organization_id}/projects/{project_id}/environments", post(create_environment).get(list_environments))
        .route("/v1/admin/organizations/{organization_id}/projects/{project_id}/environments/{environment_id}", get(get_environment))
        // Principals
        .route("/v1/admin/organizations/{organization_id}/principals", post(create_principal).get(list_principals))
        .route("/v1/admin/organizations/{organization_id}/principals/{principal_id}", get(get_principal))
        // Roles
        .route("/v1/admin/organizations/{organization_id}/roles", post(create_role).get(list_roles))
        .route("/v1/admin/organizations/{organization_id}/roles/{role_id}", get(get_role))
        // Memberships
        .route("/v1/admin/organizations/{organization_id}/memberships", post(create_membership).get(list_memberships))
        // Quotas
        .route("/v1/admin/quotas", post(create_quota).get(list_quotas))
        // Policy bindings
        .route("/v1/admin/policy-bindings", post(create_policy_binding).get(list_policy_bindings))
        // Permissions (read-only)
        .route("/v1/admin/permissions", get(list_permissions))
        // Audit log
        .route("/v1/admin/organizations/{organization_id}/audit-log", get(list_audit_log))
        // API keys
        .route("/v1/admin/api-keys", post(create_api_key).get(list_api_keys))
        .route("/v1/admin/api-keys/{api_key_id}", delete(revoke_api_key))
        .route("/v1/admin/api-keys/{api_key_id}/rotate", post(rotate_api_key))
        .with_state(shared)
}

// ============================================================================
// Organization handlers
// ============================================================================

async fn create_organization(
    State(state): State<Arc<AdminState>>,
    Json(body): Json<CreateOrganizationRequest>,
) -> Response {
    match state.repo.create_organization(&body.name, &body.slug).await {
        Ok(org) => (StatusCode::CREATED, Json(org)).into_response(),
        Err(e) => store_error_response(e),
    }
}

async fn get_organization(
    State(state): State<Arc<AdminState>>,
    Path(organization_id): Path<Uuid>,
) -> Response {
    let org_id = OrganizationId(organization_id);
    match state.repo.get_organization(org_id).await {
        Ok(Some(org)) => Json(org).into_response(),
        Ok(None) => error_response(StatusCode::NOT_FOUND, "not_found", "Organization not found"),
        Err(e) => store_error_response(e),
    }
}

async fn list_organizations(State(state): State<Arc<AdminState>>) -> Response {
    match state.repo.list_organizations(100, 0).await {
        Ok(orgs) => Json(ListResponse {
            total: orgs.len(),
            data: orgs,
        })
        .into_response(),
        Err(e) => store_error_response(e),
    }
}

// ============================================================================
// Project handlers
// ============================================================================

async fn create_project(
    State(state): State<Arc<AdminState>>,
    Path(organization_id): Path<Uuid>,
    Json(body): Json<CreateProjectRequest>,
) -> Response {
    let org_id = OrganizationId(organization_id);
    match state
        .repo
        .create_project(org_id, &body.name, &body.slug)
        .await
    {
        Ok(proj) => (StatusCode::CREATED, Json(proj)).into_response(),
        Err(e) => store_error_response(e),
    }
}

async fn get_project(
    State(state): State<Arc<AdminState>>,
    Path((organization_id, project_id)): Path<(Uuid, Uuid)>,
) -> Response {
    let org_id = OrganizationId(organization_id);
    let proj_id = ProjectId(project_id);
    match state.repo.get_project(org_id, proj_id).await {
        Ok(Some(proj)) => Json(proj).into_response(),
        Ok(None) => error_response(StatusCode::NOT_FOUND, "not_found", "Project not found"),
        Err(e) => store_error_response(e),
    }
}

async fn list_projects(
    State(state): State<Arc<AdminState>>,
    Path(organization_id): Path<Uuid>,
) -> Response {
    let org_id = OrganizationId(organization_id);
    match state.repo.list_projects(org_id, 100, 0).await {
        Ok(projects) => Json(ListResponse {
            total: projects.len(),
            data: projects,
        })
        .into_response(),
        Err(e) => store_error_response(e),
    }
}

// ============================================================================
// Environment handlers
// ============================================================================

async fn create_environment(
    State(state): State<Arc<AdminState>>,
    Path((organization_id, project_id)): Path<(Uuid, Uuid)>,
    Json(body): Json<CreateEnvironmentRequest>,
) -> Response {
    let org_id = OrganizationId(organization_id);
    let proj_id = ProjectId(project_id);
    match state
        .repo
        .create_environment(org_id, proj_id, &body.name, &body.slug)
        .await
    {
        Ok(env) => (StatusCode::CREATED, Json(env)).into_response(),
        Err(e) => store_error_response(e),
    }
}

async fn get_environment(
    State(state): State<Arc<AdminState>>,
    Path((_organization_id, _project_id, environment_id)): Path<(Uuid, Uuid, Uuid)>,
) -> Response {
    let env_id = EnvironmentId(environment_id);
    match state.repo.get_environment(env_id).await {
        Ok(Some(env)) => Json(env).into_response(),
        Ok(None) => error_response(StatusCode::NOT_FOUND, "not_found", "Environment not found"),
        Err(e) => store_error_response(e),
    }
}

async fn list_environments(
    State(state): State<Arc<AdminState>>,
    Path((organization_id, project_id)): Path<(Uuid, Uuid)>,
) -> Response {
    let org_id = OrganizationId(organization_id);
    let proj_id = ProjectId(project_id);
    match state.repo.list_environments(org_id, proj_id).await {
        Ok(envs) => Json(ListResponse {
            total: envs.len(),
            data: envs,
        })
        .into_response(),
        Err(e) => store_error_response(e),
    }
}

// ============================================================================
// Principal handlers
// ============================================================================

async fn create_principal(
    State(state): State<Arc<AdminState>>,
    Path(organization_id): Path<Uuid>,
    Json(body): Json<CreatePrincipalRequest>,
) -> Response {
    let org_id = OrganizationId(organization_id);
    match state
        .repo
        .create_principal(
            org_id,
            body.principal_type,
            &body.external_id,
            &body.display_name,
        )
        .await
    {
        Ok(principal) => (StatusCode::CREATED, Json(principal)).into_response(),
        Err(e) => store_error_response(e),
    }
}

async fn get_principal(
    State(state): State<Arc<AdminState>>,
    Path((_organization_id, principal_id)): Path<(Uuid, Uuid)>,
) -> Response {
    let pid = PrincipalId(principal_id);
    match state.repo.get_principal(pid).await {
        Ok(Some(principal)) => Json(principal).into_response(),
        Ok(None) => error_response(StatusCode::NOT_FOUND, "not_found", "Principal not found"),
        Err(e) => store_error_response(e),
    }
}

async fn list_principals(
    State(state): State<Arc<AdminState>>,
    Path(organization_id): Path<Uuid>,
) -> Response {
    let org_id = OrganizationId(organization_id);
    match state.repo.list_principals(org_id, None, 100, 0).await {
        Ok(principals) => Json(ListResponse {
            total: principals.len(),
            data: principals,
        })
        .into_response(),
        Err(e) => store_error_response(e),
    }
}

// ============================================================================
// Role handlers
// ============================================================================

async fn create_role(
    State(state): State<Arc<AdminState>>,
    Path(organization_id): Path<Uuid>,
    Json(body): Json<CreateRoleRequest>,
) -> Response {
    let org_id = OrganizationId(organization_id);
    match state
        .repo
        .create_role(
            org_id,
            &body.name,
            body.description.as_deref(),
            body.is_system.unwrap_or(false),
        )
        .await
    {
        Ok(role) => (StatusCode::CREATED, Json(role)).into_response(),
        Err(e) => store_error_response(e),
    }
}

async fn get_role(
    State(state): State<Arc<AdminState>>,
    Path((_organization_id, role_id)): Path<(Uuid, Uuid)>,
) -> Response {
    let rid = RoleId(role_id);
    match state.repo.get_role(rid).await {
        Ok(Some(role)) => Json(role).into_response(),
        Ok(None) => error_response(StatusCode::NOT_FOUND, "not_found", "Role not found"),
        Err(e) => store_error_response(e),
    }
}

async fn list_roles(
    State(state): State<Arc<AdminState>>,
    Path(organization_id): Path<Uuid>,
) -> Response {
    let org_id = OrganizationId(organization_id);
    match state.repo.list_roles(org_id).await {
        Ok(roles) => Json(ListResponse {
            total: roles.len(),
            data: roles,
        })
        .into_response(),
        Err(e) => store_error_response(e),
    }
}

// ============================================================================
// Membership handlers
// ============================================================================

async fn create_membership(
    State(state): State<Arc<AdminState>>,
    Path(organization_id): Path<Uuid>,
    Json(body): Json<CreateMembershipRequest>,
) -> Response {
    let _org_id = OrganizationId(organization_id);
    match state
        .repo
        .create_membership(body.principal_id, &body.scope, body.role_id)
        .await
    {
        Ok(membership) => (StatusCode::CREATED, Json(membership)).into_response(),
        Err(e) => store_error_response(e),
    }
}

async fn list_memberships(
    State(state): State<Arc<AdminState>>,
    Path(organization_id): Path<Uuid>,
) -> Response {
    let org_id = OrganizationId(organization_id);
    match state
        .repo
        .list_memberships_at_scope(&Scope::Organization(org_id))
        .await
    {
        Ok(memberships) => Json(ListResponse {
            total: memberships.len(),
            data: memberships,
        })
        .into_response(),
        Err(e) => store_error_response(e),
    }
}

// ============================================================================
// Quota handlers
// ============================================================================

async fn create_quota(
    State(state): State<Arc<AdminState>>,
    Json(body): Json<CreateQuotaRequest>,
) -> Response {
    match state
        .repo
        .create_quota(
            body.scope_type,
            &body.scope_id,
            &body.resource_type,
            body.limit_value,
            body.period,
        )
        .await
    {
        Ok(quota) => (StatusCode::CREATED, Json(quota)).into_response(),
        Err(e) => store_error_response(e),
    }
}

async fn list_quotas(State(state): State<Arc<AdminState>>) -> Response {
    // List quotas at org scope by default; specific scope queries use query params
    match state
        .repo
        .list_quotas_at_scope(ScopeType::Organization, "")
        .await
    {
        Ok(quotas) => Json(ListResponse {
            total: quotas.len(),
            data: quotas,
        })
        .into_response(),
        Err(e) => store_error_response(e),
    }
}

// ============================================================================
// Policy binding handlers
// ============================================================================

async fn create_policy_binding(
    State(state): State<Arc<AdminState>>,
    Json(body): Json<CreatePolicyBindingRequest>,
) -> Response {
    match state
        .repo
        .create_policy_binding(
            body.scope_type,
            &body.scope_id,
            &body.policy_type,
            body.policy_data,
        )
        .await
    {
        Ok(binding) => (StatusCode::CREATED, Json(binding)).into_response(),
        Err(e) => store_error_response(e),
    }
}

async fn list_policy_bindings(State(state): State<Arc<AdminState>>) -> Response {
    match state
        .repo
        .list_policy_bindings_at_scope(ScopeType::Organization, "")
        .await
    {
        Ok(bindings) => Json(ListResponse {
            total: bindings.len(),
            data: bindings,
        })
        .into_response(),
        Err(e) => store_error_response(e),
    }
}

// ============================================================================
// Permission handlers
// ============================================================================

async fn list_permissions(State(state): State<Arc<AdminState>>) -> Response {
    match state.repo.list_permissions().await {
        Ok(permissions) => Json(ListResponse {
            total: permissions.len(),
            data: permissions,
        })
        .into_response(),
        Err(e) => store_error_response(e),
    }
}

// ============================================================================
// Audit log handlers
// ============================================================================

async fn list_audit_log(
    State(state): State<Arc<AdminState>>,
    Path(organization_id): Path<Uuid>,
) -> Response {
    let org_id = OrganizationId(organization_id);
    match state.repo.list_audit_log(org_id, 100, 0).await {
        Ok(entries) => Json(ListResponse {
            total: entries.len(),
            data: entries,
        })
        .into_response(),
        Err(e) => store_error_response(e),
    }
}

// ============================================================================
// API Key handlers
// ============================================================================

async fn create_api_key(
    State(state): State<Arc<AdminState>>,
    Json(body): Json<CreateApiKeyRequest>,
) -> Response {
    let api_key_repo = match &state.api_key_repo {
        Some(repo) => repo,
        None => {
            return error_response(
                StatusCode::SERVICE_UNAVAILABLE,
                "not_configured",
                "API key repository not configured",
            );
        }
    };

    match api_key_repo
        .issue_key(
            body.principal_id,
            body.organization_id,
            body.project_id,
            body.environment_id,
            body.role_id,
            &body.name,
            body.expires_at,
            body.rate_limit_per_minute,
            body.concurrency_limit,
        )
        .await
    {
        Ok(issuance) => (StatusCode::CREATED, Json(issuance)).into_response(),
        Err(e) => api_key_store_error_response(e),
    }
}

async fn list_api_keys(State(state): State<Arc<AdminState>>) -> Response {
    let api_key_repo = match &state.api_key_repo {
        Some(repo) => repo,
        None => {
            return error_response(
                StatusCode::SERVICE_UNAVAILABLE,
                "not_configured",
                "API key repository not configured",
            );
        }
    };

    // List all keys for the default principal (admin scope)
    // In production, this would be scoped by the authenticated principal
    let principal_id = PrincipalId(Uuid::nil());
    match api_key_repo.list_keys(principal_id).await {
        Ok(keys) => Json(ListResponse {
            total: keys.len(),
            data: keys,
        })
        .into_response(),
        Err(e) => api_key_store_error_response(e),
    }
}

async fn revoke_api_key(
    State(state): State<Arc<AdminState>>,
    Path(api_key_id): Path<Uuid>,
    Json(body): Json<RevokeApiKeyRequest>,
) -> Response {
    let api_key_repo = match &state.api_key_repo {
        Some(repo) => repo,
        None => {
            return error_response(
                StatusCode::SERVICE_UNAVAILABLE,
                "not_configured",
                "API key repository not configured",
            );
        }
    };

    let key_id = ApiKeyId(api_key_id);
    // Use nil principal as the revoker (admin context)
    let revoked_by = PrincipalId(Uuid::nil());
    match api_key_repo
        .revoke_key(key_id, revoked_by, &body.reason)
        .await
    {
        Ok(()) => (
            StatusCode::OK,
            Json(serde_json::json!({"status": "revoked"})),
        )
            .into_response(),
        Err(e) => api_key_store_error_response(e),
    }
}

async fn rotate_api_key(
    State(state): State<Arc<AdminState>>,
    Path(api_key_id): Path<Uuid>,
) -> Response {
    let api_key_repo = match &state.api_key_repo {
        Some(repo) => repo,
        None => {
            return error_response(
                StatusCode::SERVICE_UNAVAILABLE,
                "not_configured",
                "API key repository not configured",
            );
        }
    };

    let key_id = ApiKeyId(api_key_id);
    let rotated_by = PrincipalId(Uuid::nil());
    match api_key_repo.rotate_key(key_id, rotated_by).await {
        Ok(issuance) => (StatusCode::CREATED, Json(issuance)).into_response(),
        Err(e) => api_key_store_error_response(e),
    }
}

// ============================================================================
// Helpers
// ============================================================================

fn store_error_response(err: IamStoreError) -> Response {
    match err {
        IamStoreError::NotFound(msg) => error_response(StatusCode::NOT_FOUND, "not_found", &msg),
        IamStoreError::AlreadyExists(msg) => {
            error_response(StatusCode::CONFLICT, "already_exists", &msg)
        }
        IamStoreError::ConcurrencyConflict => error_response(
            StatusCode::CONFLICT,
            "concurrency_conflict",
            "Optimistic concurrency conflict",
        ),
        IamStoreError::InvalidScope => error_response(
            StatusCode::BAD_REQUEST,
            "invalid_scope",
            "Invalid scope reference",
        ),
        IamStoreError::StepUpExpired => error_response(
            StatusCode::FORBIDDEN,
            "step_up_expired",
            "Step-up approval has expired",
        ),
        IamStoreError::StepUpUnavailable => error_response(
            StatusCode::CONFLICT,
            "step_up_unavailable",
            "Step-up approval has already been used or revoked",
        ),
        IamStoreError::SeparationOfDutiesViolation => error_response(
            StatusCode::FORBIDDEN,
            "separation_of_duties_violation",
            "Approver must differ from requester",
        ),
        IamStoreError::Database(_) => error_response(
            StatusCode::SERVICE_UNAVAILABLE,
            "database_error",
            "Database operation failed",
        ),
    }
}

fn api_key_store_error_response(err: ApiKeyStoreError) -> Response {
    match err {
        ApiKeyStoreError::NotFound(msg) => error_response(StatusCode::NOT_FOUND, "not_found", &msg),
        ApiKeyStoreError::AlreadyExists(msg) => {
            error_response(StatusCode::CONFLICT, "already_exists", &msg)
        }
        ApiKeyStoreError::Expired => {
            error_response(StatusCode::GONE, "expired", "API key has expired")
        }
        ApiKeyStoreError::Revoked => {
            error_response(StatusCode::GONE, "revoked", "API key has been revoked")
        }
        ApiKeyStoreError::RateLimitExceeded => error_response(
            StatusCode::TOO_MANY_REQUESTS,
            "rate_limit_exceeded",
            "Rate limit exceeded",
        ),
        ApiKeyStoreError::ConcurrencyExceeded => error_response(
            StatusCode::TOO_MANY_REQUESTS,
            "concurrency_exceeded",
            "Concurrency limit exceeded",
        ),
        ApiKeyStoreError::HashError(_) => error_response(
            StatusCode::INTERNAL_SERVER_ERROR,
            "hash_error",
            "Internal hash verification error",
        ),
        ApiKeyStoreError::Database(_) => error_response(
            StatusCode::SERVICE_UNAVAILABLE,
            "database_error",
            "Database operation failed",
        ),
    }
}

fn error_response(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(serde_json::json!({
            "error": {
                "message": message,
                "type": "noerelay_error",
                "code": code,
            }
        })),
    )
        .into_response()
}
