use chrono::{Duration, Utc};
use noerelay_core::iam::*;
use std::collections::HashMap;
use uuid::Uuid;

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

fn registry(step_up: bool) -> PermissionRegistry {
    PermissionRegistry {
        routes: vec![RoutePermission {
            method: "GET".into(),
            path_pattern: "/v1/admin/organizations/{organization_id}".into(),
            required_permission: "organization:read".into(),
            requires_step_up: step_up,
        }],
    }
}

#[test]
fn exact_route_matches() {
    let registry = PermissionRegistry {
        routes: vec![RoutePermission {
            method: "GET".into(),
            path_pattern: "/v1/admin/organizations".into(),
            required_permission: "organization:read".into(),
            requires_step_up: false,
        }],
    };
    assert!(
        registry
            .find_route("GET", "/v1/admin/organizations")
            .is_some()
    );
}

#[test]
fn parameterized_route_matches_one_segment() {
    assert!(
        registry(false)
            .find_route("GET", "/v1/admin/organizations/acme")
            .is_some()
    );
}

#[test]
fn query_string_does_not_change_route_match() {
    assert!(
        registry(false)
            .find_route("GET", "/v1/admin/organizations/acme?page=1")
            .is_some()
    );
}

#[test]
fn wrong_method_does_not_match() {
    assert!(
        registry(false)
            .find_route("POST", "/v1/admin/organizations/acme")
            .is_none()
    );
}

#[test]
fn pattern_does_not_match_multiple_segments() {
    assert!(
        registry(false)
            .find_route("GET", "/v1/admin/organizations/acme/extra")
            .is_none()
    );
}

#[test]
fn unmapped_route_denies_by_default() {
    let decision = registry(false).check(&identity(&["organization:read"]), "GET", "/unmapped");
    assert!(!decision.allowed);
    assert_eq!(decision.reason, RbacDenyReason::RouteNotFound);
}

#[test]
fn mapped_route_with_permission_allows() {
    let decision = registry(false).check(
        &identity(&["organization:read"]),
        "GET",
        "/v1/admin/organizations/acme",
    );
    assert!(decision.allowed);
    assert_eq!(decision.reason, RbacDenyReason::Allowed);
}

#[test]
fn mapped_route_without_permission_denies() {
    let decision = registry(false).check(
        &identity(&["project:read"]),
        "GET",
        "/v1/admin/organizations/acme",
    );
    assert_eq!(decision.reason, RbacDenyReason::MissingPermission);
}

#[test]
fn mapped_step_up_route_challenges() {
    let decision = registry(true).check(
        &identity(&["organization:read"]),
        "GET",
        "/v1/admin/organizations/acme",
    );
    assert_eq!(decision.reason, RbacDenyReason::StepUpRequired);
    assert!(decision.requires_step_up);
}

#[test]
fn nil_principal_is_invalid() {
    let mut identity = identity(&["organization:read"]);
    identity.principal_id = PrincipalId(Uuid::nil());
    let decision = registry(false).check(&identity, "GET", "/v1/admin/organizations/acme");
    assert_eq!(decision.reason, RbacDenyReason::InvalidIdentity);
}

#[test]
fn expired_oidc_identity_denies() {
    let mut identity = identity(&["organization:read"]);
    identity.claims = Some(OidcClaims {
        issuer: "https://issuer.example".into(),
        subject: "subject".into(),
        audience: "noerelay".into(),
        expires_at: Utc::now() - Duration::seconds(1),
        issued_at: Utc::now() - Duration::minutes(5),
        nonce: None,
        email: None,
        name: None,
        custom_claims: HashMap::new(),
    });
    let decision = registry(false).check(&identity, "GET", "/v1/admin/organizations/acme");
    assert_eq!(decision.reason, RbacDenyReason::Expired);
}

#[test]
fn oidc_config_serialization_round_trips() {
    let config = OidcConfig {
        issuer: "https://issuer.example".into(),
        audience: "noerelay".into(),
        jwks_url: "https://issuer.example/jwks".into(),
        claim_to_scope: HashMap::from([("roles".into(), "role".into())]),
        clock_skew_seconds: 30,
        require_nonce: true,
    };
    let value = serde_json::to_value(&config).unwrap();
    let decoded: OidcConfig = serde_json::from_value(value).unwrap();
    assert_eq!(decoded.issuer, config.issuer);
    assert!(decoded.require_nonce);
}

#[test]
fn authenticated_identity_serialization_round_trips() {
    let original = identity(&["organization:read"]);
    let decoded: AuthenticatedIdentity =
        serde_json::from_value(serde_json::to_value(&original).unwrap()).unwrap();
    assert_eq!(decoded.principal_id, original.principal_id);
    assert_eq!(decoded.scopes, original.scopes);
}

#[test]
fn step_up_approval_serialization_round_trips() {
    let org = OrganizationId(Uuid::new_v4());
    let approval = StepUpApproval {
        id: Uuid::new_v4(),
        approver_id: PrincipalId(Uuid::new_v4()),
        organization_id: org,
        action_hash: "sha256:abc".into(),
        action_description: "Rotate API key".into(),
        scope: Scope::Organization(org),
        granted_permissions: vec!["api_key:rotate".into()],
        expires_at: Utc::now() + Duration::minutes(5),
        separation_of_duties: true,
        created_at: Utc::now(),
        used_at: None,
        revoked_at: None,
    };
    let decoded: StepUpApproval =
        serde_json::from_value(serde_json::to_value(&approval).unwrap()).unwrap();
    assert_eq!(decoded.id, approval.id);
    assert_eq!(decoded.action_hash, approval.action_hash);
}
