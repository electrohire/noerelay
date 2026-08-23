//! Canonical IAM domain types for NoeRelay tenancy and policy scope.
//!
//! This module defines the normalized identity and access management model:
//! organizations → projects → environments with principals, roles,
//! permissions, memberships, quotas, policy bindings, and service identities.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use thiserror::Error;
use uuid::Uuid;

use crate::artifacts::ArtifactId;
use chrono::{DateTime, Utc};

// ============================================================================
// Identifier Types
// ============================================================================

/// Unique identifier for an organization (UUID v4)
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct OrganizationId(pub Uuid);

/// Unique identifier for a project (UUID v4)
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct ProjectId(pub Uuid);

/// Unique identifier for an environment (UUID v4)
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct EnvironmentId(pub Uuid);

/// Unique identifier for a principal (UUID v4)
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct PrincipalId(pub Uuid);

/// Unique identifier for a role (UUID v4)
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct RoleId(pub Uuid);

/// Unique identifier for a membership (UUID v4)
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct MembershipId(pub Uuid);

/// Unique identifier for a quota (UUID v4)
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct QuotaId(pub Uuid);

/// Unique identifier for a policy binding (UUID v4)
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct PolicyBindingId(pub Uuid);

/// Unique identifier for a service identity (UUID v4)
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct ServiceIdentityId(pub Uuid);

// ============================================================================
// Entity Status
// ============================================================================

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, Default)]
#[serde(rename_all = "snake_case")]
pub enum EntityStatus {
    #[default]
    Active,
    Suspended,
    Archived,
    Revoked,
}

// ============================================================================
// Principal Types
// ============================================================================

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum PrincipalType {
    Human,
    Service,
}

// ============================================================================
// Scope Types
// ============================================================================

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ScopeType {
    Organization,
    Project,
    Environment,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum Scope {
    Organization(OrganizationId),
    Project(OrganizationId, ProjectId),
    Environment(OrganizationId, ProjectId, EnvironmentId),
}

impl Scope {
    pub fn scope_type(&self) -> ScopeType {
        match self {
            Self::Organization(_) => ScopeType::Organization,
            Self::Project(_, _) => ScopeType::Project,
            Self::Environment(_, _, _) => ScopeType::Environment,
        }
    }

    pub fn organization_id(&self) -> OrganizationId {
        match self {
            Self::Organization(org_id) => *org_id,
            Self::Project(org_id, _) => *org_id,
            Self::Environment(org_id, _, _) => *org_id,
        }
    }
}

// ============================================================================
// Core Entities
// ============================================================================

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Organization {
    pub organization_id: OrganizationId,
    pub name: String,
    pub slug: String,
    pub status: EntityStatus,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
    pub deleted_at: Option<chrono::DateTime<chrono::Utc>>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Project {
    pub project_id: ProjectId,
    pub organization_id: OrganizationId,
    pub name: String,
    pub slug: String,
    pub status: EntityStatus,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
    pub deleted_at: Option<chrono::DateTime<chrono::Utc>>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Environment {
    pub environment_id: EnvironmentId,
    pub organization_id: OrganizationId,
    pub project_id: ProjectId,
    pub name: String,
    pub slug: String,
    pub status: EntityStatus,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
    pub deleted_at: Option<chrono::DateTime<chrono::Utc>>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Principal {
    pub principal_id: PrincipalId,
    pub organization_id: OrganizationId,
    pub principal_type: PrincipalType,
    pub external_id: String,
    pub display_name: String,
    pub status: EntityStatus,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
    pub deleted_at: Option<chrono::DateTime<chrono::Utc>>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Role {
    pub role_id: RoleId,
    pub organization_id: OrganizationId,
    pub name: String,
    pub description: Option<String>,
    pub is_system: bool,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Permission {
    pub permission_id: String,
    pub name: String,
    pub description: Option<String>,
    pub resource: String,
    pub action: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Membership {
    pub membership_id: MembershipId,
    pub principal_id: PrincipalId,
    pub organization_id: OrganizationId,
    pub project_id: Option<ProjectId>,
    pub environment_id: Option<EnvironmentId>,
    pub role_id: RoleId,
    pub status: EntityStatus,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

impl Membership {
    /// Returns the scope of this membership
    pub fn scope(&self) -> Scope {
        match (self.project_id, self.environment_id) {
            (None, None) => Scope::Organization(self.organization_id),
            (Some(proj_id), None) => Scope::Project(self.organization_id, proj_id),
            (Some(proj_id), Some(env_id)) => {
                Scope::Environment(self.organization_id, proj_id, env_id)
            }
            (None, Some(_)) => unreachable!("environment requires project per CHECK constraint"),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum QuotaPeriod {
    Daily,
    Weekly,
    Monthly,
    Total,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Quota {
    pub quota_id: QuotaId,
    pub scope_type: ScopeType,
    pub scope_id: String,
    pub resource_type: String,
    pub limit_value: u64,
    pub period: QuotaPeriod,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct PolicyBinding {
    pub binding_id: PolicyBindingId,
    pub scope_type: ScopeType,
    pub scope_id: String,
    pub policy_type: String,
    pub policy_data: serde_json::Value,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ServiceIdentity {
    pub service_identity_id: ServiceIdentityId,
    pub principal_id: PrincipalId,
    pub service_name: String,
    pub credential_hash: String,
    pub status: EntityStatus,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

// ============================================================================
// Resolved Identity (Server-Side)
// ============================================================================

/// Fully resolved identity with authorization context.
/// This is derived server-side from authenticated credentials,
/// never trusted from caller headers.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ResolvedIdentity {
    pub principal: Principal,
    pub memberships: Vec<Membership>,
    pub roles: Vec<Role>,
    pub permissions: Vec<Permission>,
    pub effective_scope: Scope,
}

impl ResolvedIdentity {
    /// Check if this identity has a specific permission at the given scope
    pub fn has_permission(&self, resource: &str, action: &str, scope: &Scope) -> bool {
        // Check if any membership at or above the requested scope grants the permission
        self.memberships
            .iter()
            .any(|m| m.status == EntityStatus::Active && self.scope_covers(&m.scope(), scope))
            && self
                .permissions
                .iter()
                .any(|p| p.resource == resource && p.action == action)
    }

    /// Check if a membership scope covers the requested scope
    fn scope_covers(&self, membership_scope: &Scope, requested_scope: &Scope) -> bool {
        match (membership_scope, requested_scope) {
            (Scope::Organization(org1), Scope::Organization(org2)) => org1 == org2,
            (Scope::Organization(org1), Scope::Project(org2, _)) => org1 == org2,
            (Scope::Organization(org1), Scope::Environment(org2, _, _)) => org1 == org2,
            (Scope::Project(org1, proj1), Scope::Project(org2, proj2)) => {
                org1 == org2 && proj1 == proj2
            }
            (Scope::Project(org1, proj1), Scope::Environment(org2, proj2, _)) => {
                org1 == org2 && proj1 == proj2
            }
            (Scope::Environment(org1, proj1, env1), Scope::Environment(org2, proj2, env2)) => {
                org1 == org2 && proj1 == proj2 && env1 == env2
            }
            _ => false,
        }
    }
}

// ============================================================================
// Audit Log Entry
// ============================================================================

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct AuditLogEntry {
    pub audit_id: Uuid,
    pub organization_id: String,
    pub actor_principal_id: Option<PrincipalId>,
    pub action: String,
    pub resource_type: String,
    pub resource_id: String,
    pub old_value: Option<serde_json::Value>,
    pub new_value: Option<serde_json::Value>,
    pub ip_address: Option<String>,
    pub user_agent: Option<String>,
    pub created_at: chrono::DateTime<chrono::Utc>,
}

// ============================================================================
// Tenant Data Lifecycle
// ============================================================================

/// Classes of tenant data tracked by lifecycle inventory and policy.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum DataCategory {
    Prompts,
    Outputs,
    Artifacts,
    Caches,
    Traces,
    Logs,
    Receipts,
    LedgerEvents,
    Recommendations,
    Exports,
    ProviderCopies,
    AuditEvents,
    ContextNodes,
    UsageRecords,
}

/// Action applied when a lifecycle policy reaches its retention boundary.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum RetentionAction {
    Retain,
    Delete,
    CryptographicDelete,
    Archive,
    Export,
}

/// Versioned organization policy for one data category.
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct LifecyclePolicy {
    pub id: String,
    pub organization_id: OrganizationId,
    pub category: DataCategory,
    pub action: RetentionAction,
    pub retain_days: Option<i32>,
    pub delete_after: Option<DateTime<Utc>>,
    pub description: String,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub version: i32,
    pub active: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum DeletionStatus {
    Pending,
    InProgress,
    Completed,
    Failed,
    PartiallyCompleted,
    Cancelled,
}

impl DeletionStatus {
    /// Returns whether the deletion job state machine permits this transition.
    pub fn can_transition_to(self, next: Self) -> bool {
        matches!(
            (self, next),
            (
                Self::Pending,
                Self::InProgress | Self::Failed | Self::Cancelled
            ) | (
                Self::InProgress,
                Self::Completed | Self::Failed | Self::PartiallyCompleted | Self::Cancelled
            )
        )
    }
}

/// Progress record for an organization-scoped deletion operation.
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct DeletionJob {
    pub id: Uuid,
    pub organization_id: OrganizationId,
    pub category: DataCategory,
    pub status: DeletionStatus,
    pub started_at: Option<DateTime<Utc>>,
    pub completed_at: Option<DateTime<Utc>>,
    pub items_total: i64,
    pub items_deleted: i64,
    pub items_failed: i64,
    pub items_skipped_legal_hold: i64,
    pub error: Option<String>,
    pub created_at: DateTime<Utc>,
    pub created_by: PrincipalId,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct DataInventoryEntry {
    pub category: DataCategory,
    pub location: String,
    pub count: i64,
    pub size_bytes: Option<i64>,
    pub retention_policy_id: Option<String>,
    pub legal_hold_count: i64,
    pub last_reconciled_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct DataInventory {
    pub organization_id: OrganizationId,
    pub entries: Vec<DataInventoryEntry>,
    pub generated_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ExportRequest {
    pub id: Uuid,
    pub organization_id: OrganizationId,
    pub requested_by: PrincipalId,
    pub categories: Vec<DataCategory>,
    pub status: ExportStatus,
    pub artifact_id: Option<ArtifactId>,
    pub created_at: DateTime<Utc>,
    pub completed_at: Option<DateTime<Utc>>,
    pub expires_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ExportStatus {
    Pending,
    InProgress,
    Completed,
    Failed,
    Expired,
}

/// Minimal durable proof that an authoritative item was deleted.
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Tombstone {
    pub id: Uuid,
    pub organization_id: OrganizationId,
    pub original_table: String,
    pub original_id: String,
    pub deleted_at: DateTime<Utc>,
    pub deleted_by: PrincipalId,
    pub deletion_job_id: Option<Uuid>,
    pub reason: String,
}

// ============================================================================
// Errors
// ============================================================================

#[derive(Debug, Error, PartialEq, Eq)]
pub enum IamError {
    #[error("entity not found: {0}")]
    NotFound(String),
    #[error("entity already exists: {0}")]
    AlreadyExists(String),
    #[error("permission denied: {0}")]
    PermissionDenied(String),
    #[error("invalid scope: {0}")]
    InvalidScope(String),
    #[error("quota exceeded: {0}")]
    QuotaExceeded(String),
    #[error("validation failed: {0}")]
    Validation(String),
    #[error("API key not found")]
    ApiKeyNotFound,
    #[error("API key has expired")]
    ApiKeyExpired,
    #[error("API key has been revoked")]
    ApiKeyRevoked,
    #[error("rate limit exceeded")]
    RateLimitExceeded,
    #[error("concurrency limit exceeded")]
    ConcurrencyExceeded,
    #[error("invalid identity token")]
    InvalidToken,
    #[error("identity token has expired")]
    TokenExpired,
    #[error("identity token issuer is invalid")]
    InvalidIssuer,
    #[error("identity token audience is invalid")]
    InvalidAudience,
    #[error("identity token signature is invalid")]
    InvalidSignature,
    #[error("step-up approval is required")]
    StepUpRequired,
    #[error("step-up approval has expired")]
    StepUpExpired,
    #[error("separation-of-duties rule was violated")]
    SeparationOfDutiesViolation,
    #[error("lifecycle policy not found")]
    LifecyclePolicyNotFound,
    #[error("deletion job not found")]
    DeletionJobNotFound,
    #[error("export request not found")]
    ExportNotFound,
    #[error("legal hold blocks deletion")]
    LegalHoldConflict,
    #[error("inventory reconciliation failed: {0}")]
    ReconciliationFailed(String),
}

// ============================================================================
// Identity Provider Port and OIDC Types (v1)
// ============================================================================

/// Version 1 configuration for an organization-scoped OIDC provider.
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct OidcConfig {
    pub issuer: String,
    pub audience: String,
    pub jwks_url: String,
    /// Maps an OIDC claim name to the prefix used for each resulting scope.
    pub claim_to_scope: HashMap<String, String>,
    pub clock_skew_seconds: i64,
    pub require_nonce: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct OidcClaims {
    pub issuer: String,
    pub subject: String,
    pub audience: String,
    pub expires_at: chrono::DateTime<chrono::Utc>,
    pub issued_at: chrono::DateTime<chrono::Utc>,
    pub nonce: Option<String>,
    pub email: Option<String>,
    pub name: Option<String>,
    pub custom_claims: HashMap<String, serde_json::Value>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum IdentityProviderType {
    Oidc,
    ApiKey,
    ServiceIdentity,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct AuthenticatedIdentity {
    pub provider_type: IdentityProviderType,
    pub principal_id: PrincipalId,
    pub organization_id: OrganizationId,
    pub scopes: Vec<String>,
    pub claims: Option<OidcClaims>,
    pub authenticated_at: chrono::DateTime<chrono::Utc>,
}

/// Synchronous, versioned identity-provider boundary. Provider implementations
/// own credential verification and return only canonical v1 identities.
pub trait IdentityProvider: Send + Sync {
    fn authenticate(&self, token: &str) -> Result<AuthenticatedIdentity, IamError>;
    fn provider_type(&self) -> IdentityProviderType;
}

// ============================================================================
// Deny-default RBAC
// ============================================================================

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RoutePermission {
    pub method: String,
    pub path_pattern: String,
    pub required_permission: String,
    pub requires_step_up: bool,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct PermissionRegistry {
    pub routes: Vec<RoutePermission>,
}

impl PermissionRegistry {
    /// Finds an exact or segment-parameterized route. Parameters use Axum's
    /// `{name}` syntax and match exactly one non-empty path segment.
    pub fn find_route(&self, method: &str, path: &str) -> Option<&RoutePermission> {
        let request_path = path.split('?').next().unwrap_or(path);
        self.routes.iter().find(|route| {
            route.method.eq_ignore_ascii_case(method)
                && route_pattern_matches(&route.path_pattern, request_path)
        })
    }

    /// Checks the canonical identity's explicitly mapped scopes. Unknown
    /// routes always deny; there is no implicit administrative access.
    pub fn check(
        &self,
        identity: &AuthenticatedIdentity,
        method: &str,
        path: &str,
    ) -> RbacDecision {
        let Some(route) = self.find_route(method, path) else {
            return RbacDecision::denied(RbacDenyReason::RouteNotFound, None, false);
        };

        if identity.principal_id.0.is_nil() || identity.organization_id.0.is_nil() {
            return RbacDecision::denied(
                RbacDenyReason::InvalidIdentity,
                Some(route.required_permission.clone()),
                route.requires_step_up,
            );
        }

        if identity
            .claims
            .as_ref()
            .is_some_and(|claims| claims.expires_at < chrono::Utc::now())
        {
            return RbacDecision::denied(
                RbacDenyReason::Expired,
                Some(route.required_permission.clone()),
                route.requires_step_up,
            );
        }

        if !identity
            .scopes
            .iter()
            .any(|scope| scope == &route.required_permission)
        {
            return RbacDecision::denied(
                RbacDenyReason::MissingPermission,
                Some(route.required_permission.clone()),
                route.requires_step_up,
            );
        }

        if route.requires_step_up {
            return RbacDecision::denied(
                RbacDenyReason::StepUpRequired,
                Some(route.required_permission.clone()),
                true,
            );
        }

        RbacDecision {
            allowed: true,
            reason: RbacDenyReason::Allowed,
            required_permission: Some(route.required_permission.clone()),
            requires_step_up: false,
        }
    }
}

fn route_pattern_matches(pattern: &str, path: &str) -> bool {
    let pattern_segments: Vec<_> = pattern.trim_end_matches('/').split('/').collect();
    let path_segments: Vec<_> = path.trim_end_matches('/').split('/').collect();
    pattern_segments.len() == path_segments.len()
        && pattern_segments
            .iter()
            .zip(path_segments)
            .all(|(expected, actual)| {
                (expected.starts_with('{') && expected.ends_with('}') && !actual.is_empty())
                    || expected == &actual
            })
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RbacDecision {
    pub allowed: bool,
    pub reason: RbacDenyReason,
    pub required_permission: Option<String>,
    pub requires_step_up: bool,
}

impl RbacDecision {
    fn denied(
        reason: RbacDenyReason,
        required_permission: Option<String>,
        requires_step_up: bool,
    ) -> Self {
        Self {
            allowed: false,
            reason,
            required_permission,
            requires_step_up,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum RbacDenyReason {
    Allowed,
    RouteNotFound,
    MissingPermission,
    StepUpRequired,
    Expired,
    InvalidIdentity,
}

// ============================================================================
// Step-up Approval
// ============================================================================

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct StepUpApproval {
    pub id: Uuid,
    pub approver_id: PrincipalId,
    pub organization_id: OrganizationId,
    pub action_hash: String,
    pub action_description: String,
    pub scope: Scope,
    pub granted_permissions: Vec<String>,
    pub expires_at: chrono::DateTime<chrono::Utc>,
    pub separation_of_duties: bool,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub used_at: Option<chrono::DateTime<chrono::Utc>>,
    pub revoked_at: Option<chrono::DateTime<chrono::Utc>>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct StepUpRequest {
    pub requester_id: PrincipalId,
    pub action_hash: String,
    pub action_description: String,
    pub scope: Scope,
    pub required_permissions: Vec<String>,
    pub expiry_seconds: i64,
    pub separation_of_duties: bool,
}

// ============================================================================
// API Key Types
// ============================================================================

/// Unique identifier for an API key (UUID v4)
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct ApiKeyId(pub Uuid);

/// Versioned API key prefix (e.g., "nr_live_v1_a1b2c3d4")
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct ApiKeyPrefix(pub String);

/// Argon2id hash of the API key secret. Never contains the plaintext.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct ApiKeyHash(pub String);

/// The plaintext API key secret. Only available at creation time.
/// Must never be logged, stored, or returned after initial issuance.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct ApiKeySecret(pub String);

impl std::fmt::Display for ApiKeySecret {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "[REDACTED]")
    }
}

/// A stored API key record. Does NOT contain the plaintext secret.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ApiKey {
    pub id: ApiKeyId,
    pub principal_id: PrincipalId,
    pub organization_id: OrganizationId,
    pub project_id: Option<ProjectId>,
    pub environment_id: Option<EnvironmentId>,
    pub role_id: Option<RoleId>,
    /// Human-readable name for this key
    pub name: String,
    /// Versioned prefix, stored non-secret
    pub prefix: ApiKeyPrefix,
    /// Argon2id hash of the secret — never the plaintext
    pub key_hash: ApiKeyHash,
    pub status: EntityStatus,
    pub expires_at: Option<chrono::DateTime<chrono::Utc>>,
    pub last_used_at: Option<chrono::DateTime<chrono::Utc>>,
    pub last_used_ip: Option<String>,
    pub rate_limit_per_minute: Option<i32>,
    pub concurrency_limit: Option<i32>,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub revoked_at: Option<chrono::DateTime<chrono::Utc>>,
    pub revoked_by: Option<PrincipalId>,
    pub revoked_reason: Option<String>,
}

/// Returned only at key creation time. Contains the one-time plaintext secret.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ApiKeyIssuance {
    /// The key metadata (without secret)
    pub api_key: ApiKey,
    /// The one-time plaintext secret — never shown again
    pub secret: ApiKeySecret,
    /// Security warning for the caller
    pub warning: String,
}

/// Result of verifying an API key against stored credentials.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ApiKeyVerification {
    pub valid: bool,
    pub api_key: Option<ApiKey>,
    /// Reason for failure: "not_found", "expired", "revoked", "hash_mismatch"
    pub failure_reason: Option<String>,
}

/// Rate limit decision for a single request.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RateLimitDecision {
    pub allowed: bool,
    pub remaining: i32,
    pub reset_at: chrono::DateTime<chrono::Utc>,
    pub limit: i32,
}

// ============================================================================
// Relationship to Legacy IdentityScope
// ============================================================================

/// Conversion from resolved identity to legacy IdentityScope for backward compatibility
impl From<&ResolvedIdentity> for crate::types::IdentityScope {
    fn from(identity: &ResolvedIdentity) -> Self {
        let scope = &identity.effective_scope;
        Self {
            organization_id: scope.organization_id().0.to_string(),
            project_id: match scope {
                Scope::Project(_, proj_id) | Scope::Environment(_, proj_id, _) => {
                    proj_id.0.to_string()
                }
                Scope::Organization(_) => String::new(),
            },
            environment_id: match scope {
                Scope::Environment(_, _, env_id) => env_id.0.to_string(),
                _ => String::new(),
            },
            user_id: identity.principal.external_id.clone(),
            session_id: String::new(), // Session managed separately
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_org_id() -> OrganizationId {
        OrganizationId(Uuid::new_v4())
    }

    fn make_proj_id() -> ProjectId {
        ProjectId(Uuid::new_v4())
    }

    fn make_env_id() -> EnvironmentId {
        EnvironmentId(Uuid::new_v4())
    }

    fn make_principal(org_id: OrganizationId) -> Principal {
        Principal {
            principal_id: PrincipalId(Uuid::new_v4()),
            organization_id: org_id,
            principal_type: PrincipalType::Human,
            external_id: "user@example.com".into(),
            display_name: "Test User".into(),
            status: EntityStatus::Active,
            created_at: chrono::Utc::now(),
            updated_at: chrono::Utc::now(),
            deleted_at: None,
        }
    }

    fn make_permission(resource: &str, action: &str) -> Permission {
        Permission {
            permission_id: format!("{resource}:{action}"),
            name: format!("{resource} {action}"),
            description: None,
            resource: resource.into(),
            action: action.into(),
        }
    }

    #[test]
    fn scope_type_discriminates_correctly() {
        let org_id = make_org_id();
        let proj_id = make_proj_id();
        let env_id = make_env_id();

        assert_eq!(
            Scope::Organization(org_id).scope_type(),
            ScopeType::Organization
        );
        assert_eq!(
            Scope::Project(org_id, proj_id).scope_type(),
            ScopeType::Project
        );
        assert_eq!(
            Scope::Environment(org_id, proj_id, env_id).scope_type(),
            ScopeType::Environment
        );
    }

    #[test]
    fn scope_organization_id_extracts_correctly() {
        let org_id = make_org_id();
        let proj_id = make_proj_id();
        let env_id = make_env_id();

        assert_eq!(Scope::Organization(org_id).organization_id(), org_id);
        assert_eq!(Scope::Project(org_id, proj_id).organization_id(), org_id);
        assert_eq!(
            Scope::Environment(org_id, proj_id, env_id).organization_id(),
            org_id
        );
    }

    #[test]
    fn membership_scope_derives_correctly() {
        let org_id = make_org_id();
        let proj_id = make_proj_id();
        let env_id = make_env_id();
        let role_id = RoleId(Uuid::new_v4());
        let now = chrono::Utc::now();

        let org_membership = Membership {
            membership_id: MembershipId(Uuid::new_v4()),
            principal_id: PrincipalId(Uuid::new_v4()),
            organization_id: org_id,
            project_id: None,
            environment_id: None,
            role_id,
            status: EntityStatus::Active,
            created_at: now,
            updated_at: now,
        };
        assert_eq!(org_membership.scope(), Scope::Organization(org_id));

        let proj_membership = Membership {
            membership_id: MembershipId(Uuid::new_v4()),
            principal_id: PrincipalId(Uuid::new_v4()),
            organization_id: org_id,
            project_id: Some(proj_id),
            environment_id: None,
            role_id,
            status: EntityStatus::Active,
            created_at: now,
            updated_at: now,
        };
        assert_eq!(proj_membership.scope(), Scope::Project(org_id, proj_id));

        let env_membership = Membership {
            membership_id: MembershipId(Uuid::new_v4()),
            principal_id: PrincipalId(Uuid::new_v4()),
            organization_id: org_id,
            project_id: Some(proj_id),
            environment_id: Some(env_id),
            role_id,
            status: EntityStatus::Active,
            created_at: now,
            updated_at: now,
        };
        assert_eq!(
            env_membership.scope(),
            Scope::Environment(org_id, proj_id, env_id)
        );
    }

    #[test]
    fn resolved_identity_has_permission_checks_scope_hierarchy() {
        let org_id = make_org_id();
        let proj_id = make_proj_id();
        let env_id = make_env_id();
        let role_id = RoleId(Uuid::new_v4());
        let now = chrono::Utc::now();

        let principal = make_principal(org_id);

        // Org-level membership
        let membership = Membership {
            membership_id: MembershipId(Uuid::new_v4()),
            principal_id: principal.principal_id,
            organization_id: org_id,
            project_id: None,
            environment_id: None,
            role_id,
            status: EntityStatus::Active,
            created_at: now,
            updated_at: now,
        };

        let read_perm = make_permission("project", "read");
        let write_perm = make_permission("project", "write");

        let identity = ResolvedIdentity {
            principal,
            memberships: vec![membership],
            roles: vec![],
            permissions: vec![read_perm.clone(), write_perm.clone()],
            effective_scope: Scope::Organization(org_id),
        };

        // Org admin can read/write at org scope
        assert!(identity.has_permission("project", "read", &Scope::Organization(org_id)));
        assert!(identity.has_permission("project", "write", &Scope::Organization(org_id)));

        // Org admin can read/write at project scope (org covers project)
        assert!(identity.has_permission("project", "read", &Scope::Project(org_id, proj_id)));
        assert!(identity.has_permission("project", "write", &Scope::Project(org_id, proj_id)));

        // Org admin can read/write at environment scope (org covers environment)
        assert!(identity.has_permission(
            "project",
            "read",
            &Scope::Environment(org_id, proj_id, env_id)
        ));

        // Cannot access different org
        let other_org = make_org_id();
        assert!(!identity.has_permission("project", "read", &Scope::Organization(other_org)));
    }

    #[test]
    fn resolved_identity_project_scope_does_not_cover_other_projects() {
        let org_id = make_org_id();
        let proj_a = make_proj_id();
        let proj_b = make_proj_id();
        let role_id = RoleId(Uuid::new_v4());
        let now = chrono::Utc::now();

        let principal = make_principal(org_id);

        let membership = Membership {
            membership_id: MembershipId(Uuid::new_v4()),
            principal_id: principal.principal_id,
            organization_id: org_id,
            project_id: Some(proj_a),
            environment_id: None,
            role_id,
            status: EntityStatus::Active,
            created_at: now,
            updated_at: now,
        };

        let read_perm = make_permission("project", "read");

        let identity = ResolvedIdentity {
            principal,
            memberships: vec![membership],
            roles: vec![],
            permissions: vec![read_perm],
            effective_scope: Scope::Project(org_id, proj_a),
        };

        // Can access own project
        assert!(identity.has_permission("project", "read", &Scope::Project(org_id, proj_a)));

        // Cannot access other project
        assert!(!identity.has_permission("project", "read", &Scope::Project(org_id, proj_b)));
    }

    #[test]
    fn suspended_membership_denies_permission() {
        let org_id = make_org_id();
        let role_id = RoleId(Uuid::new_v4());
        let now = chrono::Utc::now();

        let principal = make_principal(org_id);

        let membership = Membership {
            membership_id: MembershipId(Uuid::new_v4()),
            principal_id: principal.principal_id,
            organization_id: org_id,
            project_id: None,
            environment_id: None,
            role_id,
            status: EntityStatus::Suspended,
            created_at: now,
            updated_at: now,
        };

        let read_perm = make_permission("project", "read");

        let identity = ResolvedIdentity {
            principal,
            memberships: vec![membership],
            roles: vec![],
            permissions: vec![read_perm],
            effective_scope: Scope::Organization(org_id),
        };

        assert!(!identity.has_permission("project", "read", &Scope::Organization(org_id)));
    }

    #[test]
    fn entity_status_default_is_active() {
        assert_eq!(EntityStatus::default(), EntityStatus::Active);
    }

    #[test]
    fn iam_error_display_messages() {
        assert_eq!(
            IamError::NotFound("org-1".into()).to_string(),
            "entity not found: org-1"
        );
        assert_eq!(
            IamError::PermissionDenied("no access".into()).to_string(),
            "permission denied: no access"
        );
    }

    #[test]
    fn resolved_identity_to_legacy_scope_conversion() {
        let org_id = make_org_id();
        let proj_id = make_proj_id();
        let env_id = make_env_id();
        let _role_id = RoleId(Uuid::new_v4());
        let now = chrono::Utc::now();

        let principal = Principal {
            principal_id: PrincipalId(Uuid::new_v4()),
            organization_id: org_id,
            principal_type: PrincipalType::Human,
            external_id: "user@test.com".into(),
            display_name: "Test".into(),
            status: EntityStatus::Active,
            created_at: now,
            updated_at: now,
            deleted_at: None,
        };

        let identity = ResolvedIdentity {
            principal,
            memberships: vec![],
            roles: vec![],
            permissions: vec![],
            effective_scope: Scope::Environment(org_id, proj_id, env_id),
        };

        let legacy: crate::types::IdentityScope = (&identity).into();
        assert_eq!(legacy.organization_id, org_id.0.to_string());
        assert_eq!(legacy.project_id, proj_id.0.to_string());
        assert_eq!(legacy.environment_id, env_id.0.to_string());
        assert_eq!(legacy.user_id, "user@test.com");
    }

    #[test]
    fn id_newtypes_serialize_as_transparent_uuid() {
        let id = OrganizationId(Uuid::parse_str("550e8400-e29b-41d4-a716-446655440000").unwrap());
        let json = serde_json::to_string(&id).unwrap();
        assert_eq!(json, "\"550e8400-e29b-41d4-a716-446655440000\"");
        let parsed: OrganizationId = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed, id);
    }

    #[test]
    fn entity_status_serialization_roundtrips() {
        let statuses = vec![
            EntityStatus::Active,
            EntityStatus::Suspended,
            EntityStatus::Archived,
        ];
        for status in statuses {
            let json = serde_json::to_string(&status).unwrap();
            let parsed: EntityStatus = serde_json::from_str(&json).unwrap();
            assert_eq!(parsed, status);
        }
    }

    #[test]
    fn quota_period_serialization_roundtrips() {
        let periods = vec![
            QuotaPeriod::Daily,
            QuotaPeriod::Weekly,
            QuotaPeriod::Monthly,
            QuotaPeriod::Total,
        ];
        for period in periods {
            let json = serde_json::to_string(&period).unwrap();
            let parsed: QuotaPeriod = serde_json::from_str(&json).unwrap();
            assert_eq!(parsed, period);
        }
    }
}
