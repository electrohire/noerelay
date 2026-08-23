//! Integration tests for IAM middleware.
//!
//! These tests verify the IAM middleware identity resolution and
//! deny-by-default authorization behavior.

use axum::body::Body;
use axum::http::Request;
use noerelay_core::iam::*;
use noerelay_gateway::iam::IamMiddlewareState;
use sha2::Digest;
use uuid::Uuid;

const TEST_API_KEY: &str = "test-api-key-that-is-at-least-32-chars";

fn make_middleware_state() -> IamMiddlewareState {
    let key_hash: [u8; 32] = sha2::Sha256::digest(TEST_API_KEY.as_bytes()).into();
    IamMiddlewareState::new(None, None, key_hash, None)
}

#[allow(dead_code)]
fn make_middleware_state_with_org() -> IamMiddlewareState {
    let key_hash: [u8; 32] = sha2::Sha256::digest(TEST_API_KEY.as_bytes()).into();
    let org_id = OrganizationId(Uuid::new_v4());
    IamMiddlewareState::new(None, None, key_hash, Some(org_id))
}

// ============================================================================
// Deny-by-Default Tests
// ============================================================================

#[test]
fn require_permission_with_no_identity_returns_false() {
    let request = Request::builder().uri("/test").body(Body::empty()).unwrap();

    assert!(!noerelay_gateway::iam::require_permission(
        &request, "project", "read"
    ));
}

#[test]
fn deny_by_default_returns_403_when_no_permission() {
    let request = Request::builder().uri("/test").body(Body::empty()).unwrap();

    let response = noerelay_gateway::iam::deny_by_default(&request, "project", "read");
    assert!(response.is_some());
}

#[test]
fn extract_identity_returns_none_when_not_set() {
    let request = Request::builder().uri("/test").body(Body::empty()).unwrap();

    assert!(noerelay_gateway::iam::extract_identity(&request).is_none());
}

// ============================================================================
// Constant-Time Comparison Tests
// ============================================================================

#[test]
fn constant_time_equal_is_correct() {
    let key_hash: [u8; 32] = sha2::Sha256::digest(TEST_API_KEY.as_bytes()).into();
    let state = IamMiddlewareState::new(None, None, key_hash, None);

    let same_hash: [u8; 32] = sha2::Sha256::digest(TEST_API_KEY.as_bytes()).into();
    assert_eq!(state.bearer_key_sha256, same_hash);

    let diff_hash: [u8; 32] = sha2::Sha256::digest(b"different-key-that-is-at-least-32").into();
    assert_ne!(state.bearer_key_sha256, diff_hash);
}

// ============================================================================
// IamMiddlewareState Tests
// ============================================================================

#[test]
fn middleware_state_has_correct_defaults() {
    let state = make_middleware_state();
    assert!(state.repo.is_none());
    assert!(state.default_org_id.is_none());
}

#[test]
fn middleware_state_with_org_stores_org_id() {
    let org_id = OrganizationId(Uuid::new_v4());
    let key_hash: [u8; 32] = sha2::Sha256::digest(TEST_API_KEY.as_bytes()).into();
    let state = IamMiddlewareState::new(None, None, key_hash, Some(org_id));
    assert_eq!(state.default_org_id, Some(org_id));
}

// ============================================================================
// ResolvedIdentity Permission Tests
// ============================================================================

#[test]
fn resolved_identity_with_no_memberships_has_no_permissions() {
    let org_id = OrganizationId(Uuid::new_v4());
    let now = chrono::Utc::now();

    let identity = ResolvedIdentity {
        principal: Principal {
            principal_id: PrincipalId(Uuid::new_v4()),
            organization_id: org_id,
            principal_type: PrincipalType::Human,
            external_id: "test@example.com".into(),
            display_name: "Test".into(),
            status: EntityStatus::Active,
            created_at: now,
            updated_at: now,
            deleted_at: None,
        },
        memberships: vec![],
        roles: vec![],
        permissions: vec![],
        effective_scope: Scope::Organization(org_id),
    };

    assert!(!identity.has_permission("project", "read", &Scope::Organization(org_id)));
}

#[test]
fn resolved_identity_with_permission_but_no_membership_denies() {
    let org_id = OrganizationId(Uuid::new_v4());
    let now = chrono::Utc::now();

    let identity = ResolvedIdentity {
        principal: Principal {
            principal_id: PrincipalId(Uuid::new_v4()),
            organization_id: org_id,
            principal_type: PrincipalType::Human,
            external_id: "test@example.com".into(),
            display_name: "Test".into(),
            status: EntityStatus::Active,
            created_at: now,
            updated_at: now,
            deleted_at: None,
        },
        memberships: vec![],
        roles: vec![],
        permissions: vec![Permission {
            permission_id: "project:read".into(),
            name: "Read Project".into(),
            description: None,
            resource: "project".into(),
            action: "read".into(),
        }],
        effective_scope: Scope::Organization(org_id),
    };

    // Has the permission but no membership, so denied
    assert!(!identity.has_permission("project", "read", &Scope::Organization(org_id)));
}
