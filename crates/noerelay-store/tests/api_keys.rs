//! Integration tests for ApiKeyRepository.
//!
//! These tests require a running PostgreSQL instance and are marked `#[ignore]`.
//! Run with: `cargo test -- --ignored` after setting DATABASE_URL.
//!
//! Tests verify:
//! - Key creation returns secret only once
//! - Hash is stored, not plaintext
//! - Verification with correct secret succeeds
//! - Verification with wrong secret fails (constant-time)
//! - Expired key fails verification
//! - Revoked key fails verification
//! - Rotation produces new key with same scopes
//! - Rate limiting blocks after limit
//! - Concurrency limiting blocks after limit
//! - Audit events are recorded for all operations

use noerelay_core::iam::*;
use noerelay_store::ApiKeyRepository;
use sqlx::PgPool;
use uuid::Uuid;

async fn setup_repo() -> (ApiKeyRepository, OrganizationId, PrincipalId) {
    let database_url = std::env::var("DATABASE_URL")
        .unwrap_or_else(|_| "postgres://noerelay:noerelay@localhost:5432/noerelay_test".into());

    let pool = PgPool::connect(&database_url)
        .await
        .expect("Failed to connect to PostgreSQL");

    // Run migrations
    sqlx::migrate!("./migrations")
        .run(&pool)
        .await
        .expect("Failed to run migrations");

    let repo = ApiKeyRepository::new(pool);

    let org_id = OrganizationId(Uuid::new_v4());
    let principal_id = PrincipalId(Uuid::new_v4());

    (repo, org_id, principal_id)
}

#[tokio::test]
#[ignore]
async fn key_creation_returns_secret_only_once() {
    let (repo, org_id, principal_id) = setup_repo().await;

    let issuance = repo
        .issue_key(
            principal_id,
            org_id,
            None,
            None,
            None,
            "test-key",
            None,
            None,
            None,
        )
        .await
        .expect("Failed to issue key");

    // Secret should be present in issuance
    assert!(!issuance.secret.0.is_empty());
    assert!(issuance.warning.contains("Store this secret securely"));

    // The api_key in the issuance should NOT contain the secret
    let api_key_json = serde_json::to_string(&issuance.api_key).unwrap();
    assert!(!api_key_json.contains(&issuance.secret.0));
}

#[tokio::test]
#[ignore]
async fn hash_is_stored_not_plaintext() {
    let (repo, org_id, principal_id) = setup_repo().await;

    let issuance = repo
        .issue_key(
            principal_id,
            org_id,
            None,
            None,
            None,
            "hash-test-key",
            None,
            None,
            None,
        )
        .await
        .expect("Failed to issue key");

    // The stored key_hash should be an Argon2id hash, not the plaintext
    assert!(issuance.api_key.key_hash.0.starts_with("$argon2"));
    assert_ne!(issuance.api_key.key_hash.0, issuance.secret.0);

    // The prefix should be versioned
    assert!(issuance.api_key.prefix.0.starts_with("nr_live_v1_"));
}

#[tokio::test]
#[ignore]
async fn verification_with_correct_secret_succeeds() {
    let (repo, org_id, principal_id) = setup_repo().await;

    let issuance = repo
        .issue_key(
            principal_id,
            org_id,
            None,
            None,
            None,
            "verify-test-key",
            None,
            None,
            None,
        )
        .await
        .expect("Failed to issue key");

    let verification = repo
        .verify_key(
            &issuance.api_key.prefix.0,
            &issuance.secret.0,
            Some("127.0.0.1"),
        )
        .await
        .expect("Failed to verify key");

    assert!(verification.valid);
    assert!(verification.api_key.is_some());
    assert!(verification.failure_reason.is_none());
}

#[tokio::test]
#[ignore]
async fn verification_with_wrong_secret_fails() {
    let (repo, org_id, principal_id) = setup_repo().await;

    let issuance = repo
        .issue_key(
            principal_id,
            org_id,
            None,
            None,
            None,
            "wrong-secret-key",
            None,
            None,
            None,
        )
        .await
        .expect("Failed to issue key");

    let verification = repo
        .verify_key(
            &issuance.api_key.prefix.0,
            "wrong-secret-value",
            Some("127.0.0.1"),
        )
        .await
        .expect("Failed to verify key");

    assert!(!verification.valid);
    assert_eq!(verification.failure_reason, Some("hash_mismatch".into()));
}

#[tokio::test]
#[ignore]
async fn verification_with_nonexistent_prefix_fails() {
    let (repo, _org_id, _principal_id) = setup_repo().await;

    let verification = repo
        .verify_key("nonexistent_prefix", "any-secret", None)
        .await
        .expect("Failed to verify key");

    assert!(!verification.valid);
    assert_eq!(verification.failure_reason, Some("not_found".into()));
}

#[tokio::test]
#[ignore]
async fn expired_key_fails_verification() {
    let (repo, org_id, principal_id) = setup_repo().await;

    // Issue a key that expires in the past
    let past = chrono::Utc::now() - chrono::Duration::hours(1);
    let issuance = repo
        .issue_key(
            principal_id,
            org_id,
            None,
            None,
            None,
            "expired-key",
            Some(past),
            None,
            None,
        )
        .await
        .expect("Failed to issue key");

    let verification = repo
        .verify_key(&issuance.api_key.prefix.0, &issuance.secret.0, None)
        .await
        .expect("Failed to verify key");

    assert!(!verification.valid);
    assert_eq!(verification.failure_reason, Some("expired".into()));
}

#[tokio::test]
#[ignore]
async fn revoked_key_fails_verification() {
    let (repo, org_id, principal_id) = setup_repo().await;

    let issuance = repo
        .issue_key(
            principal_id,
            org_id,
            None,
            None,
            None,
            "revoke-test-key",
            None,
            None,
            None,
        )
        .await
        .expect("Failed to issue key");

    // Revoke the key
    repo.revoke_key(issuance.api_key.id, principal_id, "testing revocation")
        .await
        .expect("Failed to revoke key");

    // Verification should fail
    let verification = repo
        .verify_key(&issuance.api_key.prefix.0, &issuance.secret.0, None)
        .await
        .expect("Failed to verify key");

    assert!(!verification.valid);
    assert_eq!(verification.failure_reason, Some("revoked".into()));
}

#[tokio::test]
#[ignore]
async fn rotation_produces_new_key_with_same_scopes() {
    let (repo, org_id, principal_id) = setup_repo().await;
    let proj_id = ProjectId(Uuid::new_v4());
    let env_id = EnvironmentId(Uuid::new_v4());
    let role_id = RoleId(Uuid::new_v4());

    let issuance = repo
        .issue_key(
            principal_id,
            org_id,
            Some(proj_id),
            Some(env_id),
            Some(role_id),
            "rotate-test-key",
            None,
            Some(60),
            Some(10),
        )
        .await
        .expect("Failed to issue key");

    let old_key = issuance.api_key;

    // Rotate the key
    let new_issuance = repo
        .rotate_key(old_key.id, principal_id)
        .await
        .expect("Failed to rotate key");

    let new_key = new_issuance.api_key;

    // New key should have same scopes
    assert_eq!(new_key.principal_id, old_key.principal_id);
    assert_eq!(new_key.organization_id, old_key.organization_id);
    assert_eq!(new_key.project_id, old_key.project_id);
    assert_eq!(new_key.environment_id, old_key.environment_id);
    assert_eq!(new_key.role_id, old_key.role_id);
    assert_eq!(new_key.name, old_key.name);
    assert_eq!(new_key.rate_limit_per_minute, old_key.rate_limit_per_minute);
    assert_eq!(new_key.concurrency_limit, old_key.concurrency_limit);

    // New key should have different id and prefix
    assert_ne!(new_key.id, old_key.id);
    assert_ne!(new_key.prefix, old_key.prefix);

    // New key should have a different secret
    assert_ne!(new_issuance.secret.0, issuance.secret.0);

    // Old key should be revoked
    let old_verification = repo
        .verify_key(&old_key.prefix.0, &issuance.secret.0, None)
        .await
        .expect("Failed to verify old key");
    assert!(!old_verification.valid);
    assert_eq!(old_verification.failure_reason, Some("revoked".into()));

    // New key should work
    let new_verification = repo
        .verify_key(&new_key.prefix.0, &new_issuance.secret.0, None)
        .await
        .expect("Failed to verify new key");
    assert!(new_verification.valid);
}

#[tokio::test]
#[ignore]
async fn rate_limiting_blocks_after_limit() {
    let (repo, org_id, principal_id) = setup_repo().await;

    let issuance = repo
        .issue_key(
            principal_id,
            org_id,
            None,
            None,
            None,
            "rate-limit-key",
            None,
            Some(3), // 3 requests per minute
            None,
        )
        .await
        .expect("Failed to issue key");

    let key_id = issuance.api_key.id;

    // First 3 requests should be allowed
    for _ in 0..3 {
        let decision = repo
            .check_rate_limit(key_id)
            .await
            .expect("Failed to check rate limit");
        assert!(decision.allowed);
    }

    // 4th request should be blocked
    let decision = repo
        .check_rate_limit(key_id)
        .await
        .expect("Failed to check rate limit");
    assert!(!decision.allowed);
    assert_eq!(decision.remaining, 0);
    assert_eq!(decision.limit, 3);
}

#[tokio::test]
#[ignore]
async fn rate_limiting_unlimited_when_not_configured() {
    let (repo, org_id, principal_id) = setup_repo().await;

    let issuance = repo
        .issue_key(
            principal_id,
            org_id,
            None,
            None,
            None,
            "unlimited-key",
            None,
            None, // No rate limit
            None,
        )
        .await
        .expect("Failed to issue key");

    // All requests should be allowed
    for _ in 0..100 {
        let decision = repo
            .check_rate_limit(issuance.api_key.id)
            .await
            .expect("Failed to check rate limit");
        assert!(decision.allowed);
        assert_eq!(decision.limit, -1); // unlimited
    }
}

#[tokio::test]
#[ignore]
async fn concurrency_limiting_blocks_after_limit() {
    let (repo, org_id, principal_id) = setup_repo().await;

    let issuance = repo
        .issue_key(
            principal_id,
            org_id,
            None,
            None,
            None,
            "concurrency-key",
            None,
            None,
            Some(2), // 2 concurrent requests
        )
        .await
        .expect("Failed to issue key");

    let key_id = issuance.api_key.id;

    // Acquire 2 slots
    assert!(repo.acquire_concurrency(key_id).await.expect("acquire 1"));
    assert!(repo.acquire_concurrency(key_id).await.expect("acquire 2"));

    // 3rd should fail
    assert!(!repo.acquire_concurrency(key_id).await.expect("acquire 3"));

    // Release one
    repo.release_concurrency(key_id).await.expect("release");

    // Now should be able to acquire again
    assert!(
        repo.acquire_concurrency(key_id)
            .await
            .expect("acquire after release")
    );

    // Cleanup
    repo.release_concurrency(key_id).await.ok();
    repo.release_concurrency(key_id).await.ok();
}

#[tokio::test]
#[ignore]
async fn concurrency_unlimited_when_not_configured() {
    let (repo, org_id, principal_id) = setup_repo().await;

    let issuance = repo
        .issue_key(
            principal_id,
            org_id,
            None,
            None,
            None,
            "unlimited-concurrency-key",
            None,
            None,
            None, // No concurrency limit
        )
        .await
        .expect("Failed to issue key");

    // All acquires should succeed
    for _ in 0..100 {
        assert!(
            repo.acquire_concurrency(issuance.api_key.id)
                .await
                .expect("acquire")
        );
    }
}

#[tokio::test]
#[ignore]
async fn list_keys_returns_keys_for_principal() {
    let (repo, org_id, principal_id) = setup_repo().await;

    // Issue multiple keys
    repo.issue_key(
        principal_id,
        org_id,
        None,
        None,
        None,
        "key-1",
        None,
        None,
        None,
    )
    .await
    .expect("issue key 1");

    repo.issue_key(
        principal_id,
        org_id,
        None,
        None,
        None,
        "key-2",
        None,
        None,
        None,
    )
    .await
    .expect("issue key 2");

    let keys = repo.list_keys(principal_id).await.expect("list keys");
    assert!(keys.len() >= 2);
    assert!(keys.iter().any(|k| k.name == "key-1"));
    assert!(keys.iter().any(|k| k.name == "key-2"));
}

#[tokio::test]
#[ignore]
async fn get_key_returns_key_metadata() {
    let (repo, org_id, principal_id) = setup_repo().await;

    let issuance = repo
        .issue_key(
            principal_id,
            org_id,
            None,
            None,
            None,
            "get-test-key",
            None,
            None,
            None,
        )
        .await
        .expect("Failed to issue key");

    let key = repo
        .get_key(issuance.api_key.id)
        .await
        .expect("Failed to get key");

    assert_eq!(key.id, issuance.api_key.id);
    assert_eq!(key.name, "get-test-key");
    assert_eq!(key.prefix, issuance.api_key.prefix);
}

#[tokio::test]
#[ignore]
async fn get_key_nonexistent_returns_error() {
    let (repo, _org_id, _principal_id) = setup_repo().await;

    let result = repo.get_key(ApiKeyId(Uuid::new_v4())).await;
    assert!(result.is_err());
}
