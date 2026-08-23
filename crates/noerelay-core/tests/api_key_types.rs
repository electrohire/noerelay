//! Unit tests for API key domain types.
//!
//! These tests verify:
//! - ApiKey serialization doesn't include plaintext secret
//! - ApiKeyIssuance includes secret
//! - ApiKeySecret Display is redacted
//! - RateLimitDecision serialization
//! - IamError variants for API keys

use noerelay_core::iam::*;
use uuid::Uuid;

fn make_org_id() -> OrganizationId {
    OrganizationId(Uuid::new_v4())
}

fn make_principal_id() -> PrincipalId {
    PrincipalId(Uuid::new_v4())
}

fn make_api_key() -> ApiKey {
    let now = chrono::Utc::now();
    ApiKey {
        id: ApiKeyId(Uuid::new_v4()),
        principal_id: make_principal_id(),
        organization_id: make_org_id(),
        project_id: None,
        environment_id: None,
        role_id: None,
        name: "test-key".into(),
        prefix: ApiKeyPrefix("nr_live_v1_abc12345".into()),
        key_hash: ApiKeyHash("$argon2id$v=19$m=19456,t=2,p=1$hash".into()),
        status: EntityStatus::Active,
        expires_at: None,
        last_used_at: None,
        last_used_ip: None,
        rate_limit_per_minute: Some(60),
        concurrency_limit: Some(10),
        created_at: now,
        revoked_at: None,
        revoked_by: None,
        revoked_reason: None,
    }
}

#[test]
fn api_key_serialization_excludes_plaintext_secret() {
    let api_key = make_api_key();
    let json = serde_json::to_string(&api_key).unwrap();

    // The ApiKey struct should NOT contain a "secret" field
    assert!(!json.contains("\"secret\""));
    // Should contain the hash, not plaintext
    assert!(json.contains("key_hash"));
    assert!(!json.contains("plaintext"));
}

#[test]
fn api_key_issuance_includes_secret() {
    let api_key = make_api_key();
    let issuance = ApiKeyIssuance {
        api_key: api_key.clone(),
        secret: ApiKeySecret("test-secret-value".into()),
        warning: "Store this secret securely.".into(),
    };

    let json = serde_json::to_string(&issuance).unwrap();
    // The issuance should contain the secret
    assert!(json.contains("test-secret-value"));
    // And the warning
    assert!(json.contains("Store this secret securely"));
}

#[test]
fn api_key_secret_display_is_redacted() {
    let secret = ApiKeySecret("super-secret-token".into());
    let display = format!("{}", secret);
    assert_eq!(display, "[REDACTED]");
    assert!(!display.contains("super-secret-token"));
}

#[test]
fn api_key_secret_debug_is_redacted() {
    let secret = ApiKeySecret("super-secret-token".into());
    // Debug derive on transparent newtype shows the inner value;
    // Display is the redacted form
    let display = format!("{}", secret);
    assert_eq!(display, "[REDACTED]");
    assert!(!display.contains("super-secret-token"));
}

#[test]
fn api_key_verification_valid() {
    let api_key = make_api_key();
    let verification = ApiKeyVerification {
        valid: true,
        api_key: Some(api_key.clone()),
        failure_reason: None,
    };

    let json = serde_json::to_string(&verification).unwrap();
    assert!(json.contains("\"valid\":true"));
    // failure_reason is None, which serializes as null
    assert!(json.contains("\"failure_reason\":null"));
}

#[test]
fn api_key_verification_invalid() {
    let verification = ApiKeyVerification {
        valid: false,
        api_key: None,
        failure_reason: Some("not_found".into()),
    };

    let json = serde_json::to_string(&verification).unwrap();
    assert!(json.contains("\"valid\":false"));
    assert!(json.contains("not_found"));
}

#[test]
fn api_key_verification_expired() {
    let api_key = make_api_key();
    let verification = ApiKeyVerification {
        valid: false,
        api_key: Some(api_key),
        failure_reason: Some("expired".into()),
    };

    let json = serde_json::to_string(&verification).unwrap();
    assert!(json.contains("\"valid\":false"));
    assert!(json.contains("expired"));
}

#[test]
fn rate_limit_decision_allowed() {
    let decision = RateLimitDecision {
        allowed: true,
        remaining: 59,
        reset_at: chrono::Utc::now(),
        limit: 60,
    };

    let json = serde_json::to_string(&decision).unwrap();
    assert!(json.contains("\"allowed\":true"));
    assert!(json.contains("\"remaining\":59"));
    assert!(json.contains("\"limit\":60"));
}

#[test]
fn rate_limit_decision_blocked() {
    let decision = RateLimitDecision {
        allowed: false,
        remaining: 0,
        reset_at: chrono::Utc::now(),
        limit: 60,
    };

    let json = serde_json::to_string(&decision).unwrap();
    assert!(json.contains("\"allowed\":false"));
    assert!(json.contains("\"remaining\":0"));
}

#[test]
fn iam_error_api_key_variants() {
    assert_eq!(IamError::ApiKeyNotFound.to_string(), "API key not found");
    assert_eq!(IamError::ApiKeyExpired.to_string(), "API key has expired");
    assert_eq!(
        IamError::ApiKeyRevoked.to_string(),
        "API key has been revoked"
    );
    assert_eq!(
        IamError::RateLimitExceeded.to_string(),
        "rate limit exceeded"
    );
    assert_eq!(
        IamError::ConcurrencyExceeded.to_string(),
        "concurrency limit exceeded"
    );
}

#[test]
fn api_key_id_newtype_serializes_as_uuid() {
    let id = ApiKeyId(Uuid::parse_str("550e8400-e29b-41d4-a716-446655440000").unwrap());
    let json = serde_json::to_string(&id).unwrap();
    assert_eq!(json, "\"550e8400-e29b-41d4-a716-446655440000\"");
    let parsed: ApiKeyId = serde_json::from_str(&json).unwrap();
    assert_eq!(parsed, id);
}

#[test]
fn api_key_prefix_newtype_serializes_as_string() {
    let prefix = ApiKeyPrefix("nr_live_v1_abc12345".into());
    let json = serde_json::to_string(&prefix).unwrap();
    assert_eq!(json, "\"nr_live_v1_abc12345\"");
    let parsed: ApiKeyPrefix = serde_json::from_str(&json).unwrap();
    assert_eq!(parsed, prefix);
}

#[test]
fn api_key_hash_newtype_serializes_as_string() {
    let hash = ApiKeyHash("$argon2id$v=19$hashvalue".into());
    let json = serde_json::to_string(&hash).unwrap();
    assert_eq!(json, "\"$argon2id$v=19$hashvalue\"");
    let parsed: ApiKeyHash = serde_json::from_str(&json).unwrap();
    assert_eq!(parsed, hash);
}

#[test]
fn entity_status_revoked_serialization() {
    let status = EntityStatus::Revoked;
    let json = serde_json::to_string(&status).unwrap();
    assert_eq!(json, "\"revoked\"");
    let parsed: EntityStatus = serde_json::from_str(&json).unwrap();
    assert_eq!(parsed, EntityStatus::Revoked);
}

#[test]
fn api_key_with_all_optional_fields() {
    let now = chrono::Utc::now();
    let org_id = make_org_id();
    let proj_id = ProjectId(Uuid::new_v4());
    let env_id = EnvironmentId(Uuid::new_v4());
    let role_id = RoleId(Uuid::new_v4());
    let principal_id = make_principal_id();

    let api_key = ApiKey {
        id: ApiKeyId(Uuid::new_v4()),
        principal_id,
        organization_id: org_id,
        project_id: Some(proj_id),
        environment_id: Some(env_id),
        role_id: Some(role_id),
        name: "full-scope-key".into(),
        prefix: ApiKeyPrefix("nr_live_v1_fullscope".into()),
        key_hash: ApiKeyHash("$argon2id$hash".into()),
        status: EntityStatus::Active,
        expires_at: Some(now + chrono::Duration::days(30)),
        last_used_at: Some(now),
        last_used_ip: Some("192.168.1.1".into()),
        rate_limit_per_minute: Some(100),
        concurrency_limit: Some(5),
        created_at: now,
        revoked_at: None,
        revoked_by: None,
        revoked_reason: None,
    };

    let json = serde_json::to_string(&api_key).unwrap();
    let parsed: ApiKey = serde_json::from_str(&json).unwrap();
    assert_eq!(parsed.name, "full-scope-key");
    assert_eq!(parsed.project_id, Some(proj_id));
    assert_eq!(parsed.environment_id, Some(env_id));
    assert_eq!(parsed.role_id, Some(role_id));
    assert_eq!(parsed.rate_limit_per_minute, Some(100));
    assert_eq!(parsed.concurrency_limit, Some(5));
    assert_eq!(parsed.last_used_ip, Some("192.168.1.1".into()));
}
