//! PostgreSQL integration coverage for RUN-03. Run with `--include-ignored`.

use chrono::{Duration, Utc};
use noerelay_core::execution::*;
use noerelay_core::iam::{OrganizationId, PrincipalId};
use noerelay_store::{ExecutionRepository, ExecutionStoreError};
use sqlx::PgPool;
use uuid::Uuid;

async fn fixture() -> (PgPool, ExecutionRepository, OrganizationId, PrincipalId) {
    let pool = PgPool::connect(&std::env::var("DATABASE_URL").expect("DATABASE_URL required"))
        .await
        .unwrap();
    sqlx::migrate!("./migrations").run(&pool).await.unwrap();
    let org = OrganizationId(Uuid::new_v4());
    let principal = PrincipalId(Uuid::new_v4());
    sqlx::query("INSERT INTO organizations (organization_id, name) VALUES ($1, $2)")
        .bind(org.0.to_string())
        .bind("RUN-03 test")
        .execute(&pool)
        .await
        .unwrap();
    sqlx::query(
        "INSERT INTO principals (principal_id, organization_id, principal_type, external_id, display_name) \
         VALUES ($1, $2, 'service', $3, 'RUN-03 test')",
    )
    .bind(principal.0)
    .bind(org.0.to_string())
    .bind(Uuid::new_v4().to_string())
    .execute(&pool)
    .await
    .unwrap();
    let repo = ExecutionRepository::new(pool.clone());
    (pool, repo, org, principal)
}

fn key(principal: PrincipalId, suffix: &str) -> IdempotencyKey {
    IdempotencyKey {
        key: format!("key-{suffix}"),
        principal_id: principal,
        endpoint_profile: "chat-completions-v1".into(),
        request_hash: "sha256:request-a".into(),
        policy_revision: "policy-v1".into(),
    }
}

async fn run(repo: &ExecutionRepository, org: OrganizationId, principal: PrincipalId) -> Run {
    repo.create_run(
        org,
        None,
        None,
        principal,
        "contract",
        None,
        "policy-v1",
        None,
    )
    .await
    .unwrap()
}

async fn attempt(
    repo: &ExecutionRepository,
    org: OrganizationId,
    principal: PrincipalId,
) -> (Run, Attempt) {
    let run = run(repo, org, principal).await;
    let step = repo
        .create_step(org, run.id, None, StepType::ToolExecution, "tool", 1, None)
        .await
        .unwrap();
    let attempt = repo.create_attempt(org, step.id, 1, None).await.unwrap();
    (run, attempt)
}

fn cancellation(run_id: RunId, principal: PrincipalId, propagate: bool) -> CancellationRequest {
    CancellationRequest {
        run_id,
        reason: CancellationReason::UserRequested,
        requested_by: principal,
        requested_at: Utc::now(),
        propagate,
    }
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn same_key_replay_returns_cached_response() {
    let (_, repo, org, principal) = fixture().await;
    let record = repo
        .claim_idempotency_key(key(principal, "replay"), org)
        .await
        .unwrap();
    repo.complete_idempotency(org, record.id, "response://one", Some("receipt-1"))
        .await
        .unwrap();
    let replay = repo
        .claim_idempotency_key(key(principal, "replay"), org)
        .await
        .unwrap();
    assert_eq!(replay.status, IdempotencyStatus::Completed);
    assert_eq!(replay.response_ref.as_deref(), Some("response://one"));
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn changed_input_with_same_key_fails() {
    let (_, repo, org, principal) = fixture().await;
    repo.claim_idempotency_key(key(principal, "changed"), org)
        .await
        .unwrap();
    let mut changed = key(principal, "changed");
    changed.request_hash = "sha256:request-b".into();
    assert!(matches!(
        repo.claim_idempotency_key(changed, org).await,
        Err(ExecutionStoreError::InvalidInput(_))
    ));
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn in_progress_key_blocks_concurrent_requests() {
    let (_, repo, org, principal) = fixture().await;
    repo.claim_idempotency_key(key(principal, "active"), org)
        .await
        .unwrap();
    assert!(matches!(
        repo.claim_idempotency_key(key(principal, "active"), org)
            .await,
        Err(ExecutionStoreError::ConcurrencyConflict(_))
    ));
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn failed_key_allows_retry() {
    let (_, repo, org, principal) = fixture().await;
    let record = repo
        .claim_idempotency_key(key(principal, "failed"), org)
        .await
        .unwrap();
    repo.fail_idempotency(org, record.id).await.unwrap();
    assert_eq!(
        repo.claim_idempotency_key(key(principal, "failed"), org)
            .await
            .unwrap()
            .status,
        IdempotencyStatus::InProgress
    );
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn expired_key_allows_new_request() {
    let (pool, repo, org, principal) = fixture().await;
    let record = repo
        .claim_idempotency_key(key(principal, "expired"), org)
        .await
        .unwrap();
    let mut tx = pool.begin().await.unwrap();
    sqlx::query("SELECT set_config('noerelay.organization_id', $1, true)")
        .bind(org.0.to_string())
        .execute(&mut *tx)
        .await
        .unwrap();
    sqlx::query(
        "UPDATE idempotency_records SET expires_at = now() - interval '1 second' WHERE id = $1",
    )
    .bind(record.id)
    .execute(&mut *tx)
    .await
    .unwrap();
    tx.commit().await.unwrap();
    assert_eq!(repo.expire_idempotency_records(org).await.unwrap(), 1);
    assert_eq!(
        repo.claim_idempotency_key(key(principal, "expired"), org)
            .await
            .unwrap()
            .status,
        IdempotencyStatus::InProgress
    );
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn cancellation_propagates_to_child_runs() {
    let (_, repo, org, principal) = fixture().await;
    let parent = run(&repo, org, principal).await;
    let child = repo
        .create_run(
            org,
            None,
            None,
            principal,
            "child",
            None,
            "policy-v1",
            Some(parent.id),
        )
        .await
        .unwrap();
    let result = repo
        .request_cancellation(org, cancellation(parent.id, principal, true))
        .await
        .unwrap();
    assert!(result.cancelled_descendants.contains(&child.id));
    assert_eq!(
        repo.get_run(org, child.id).await.unwrap().unwrap().status,
        RunStatus::Cancelled
    );
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn cancellation_releases_reservations() {
    let (_, repo, org, principal) = fixture().await;
    let run = run(&repo, org, principal).await;
    let reservation = repo
        .create_reservation(
            org,
            run.id,
            "budget",
            "main",
            100,
            Utc::now() + Duration::hours(1),
        )
        .await
        .unwrap();
    let result = repo
        .request_cancellation(org, cancellation(run.id, principal, false))
        .await
        .unwrap();
    assert!(result.released_reservations.contains(&reservation.id));
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn cancellation_marks_provider_calls_cancelled() {
    let (_, repo, org, principal) = fixture().await;
    let (run, attempt) = attempt(&repo, org, principal).await;
    let call = repo
        .record_provider_call(
            org,
            attempt.id,
            "provider",
            "model",
            "request",
            None,
            None,
            None,
            "streaming",
        )
        .await
        .unwrap();
    let result = repo
        .request_cancellation(org, cancellation(run.id, principal, false))
        .await
        .unwrap();
    assert!(result.cancelled_provider_calls.contains(&call.id));
    assert_eq!(
        repo.get_provider_call(org, call.id)
            .await
            .unwrap()
            .unwrap()
            .status,
        "cancelled"
    );
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn cancellation_marks_tool_effects_cancelled() {
    let (_, repo, org, principal) = fixture().await;
    let (run, attempt) = attempt(&repo, org, principal).await;
    let effect = repo
        .record_tool_effect(
            org, attempt.id, "tool", "write", None, "pending", "request", None,
        )
        .await
        .unwrap();
    let result = repo
        .request_cancellation(org, cancellation(run.id, principal, false))
        .await
        .unwrap();
    assert!(result.cancelled_tool_effects.contains(&effect.id));
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn effect_journal_records_applied_effects() {
    let (_, repo, org, principal) = fixture().await;
    let (_, attempt) = attempt(&repo, org, principal).await;
    let request = EffectRequest {
        effect_id: "applied-1".into(),
        tool_id: "tool".into(),
        intent: EffectIntent::Write,
        request_hash: "request".into(),
        idempotency_key: Some("downstream".into()),
        created_at: Utc::now(),
    };
    repo.record_effect_request(org, &request, attempt.id)
        .await
        .unwrap();
    let result = EffectResult {
        effect_id: request.effect_id.clone(),
        status: EffectResultStatus::Applied,
        external_effect_id: Some("external".into()),
        response_hash: Some("response".into()),
        reconciled_at: None,
        error: None,
        created_at: Utc::now(),
    };
    repo.record_effect_result(org, &request.effect_id, &result)
        .await
        .unwrap();
    assert!(
        repo.get_pending_unknown_effects(org)
            .await
            .unwrap()
            .is_empty()
    );
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn unknown_effects_can_be_reconciled() {
    let (_, repo, org, principal) = fixture().await;
    let (_, attempt) = attempt(&repo, org, principal).await;
    let request = EffectRequest {
        effect_id: "unknown-1".into(),
        tool_id: "tool".into(),
        intent: EffectIntent::Write,
        request_hash: "request".into(),
        idempotency_key: None,
        created_at: Utc::now(),
    };
    repo.record_effect_request(org, &request, attempt.id)
        .await
        .unwrap();
    assert_eq!(
        repo.get_pending_unknown_effects(org).await.unwrap().len(),
        1
    );
    repo.reconcile_effect(
        org,
        &request.effect_id,
        EffectResultStatus::Reconciled,
        Some("external"),
        Some("response"),
    )
    .await
    .unwrap();
    assert!(
        repo.get_pending_unknown_effects(org)
            .await
            .unwrap()
            .is_empty()
    );
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn no_duplicate_visible_effect_on_retry() {
    let (_, repo, org, principal) = fixture().await;
    let (_, attempt) = attempt(&repo, org, principal).await;
    let request = EffectRequest {
        effect_id: "stable-effect".into(),
        tool_id: "tool".into(),
        intent: EffectIntent::Write,
        request_hash: "request".into(),
        idempotency_key: Some("stable-effect".into()),
        created_at: Utc::now(),
    };
    repo.record_effect_request(org, &request, attempt.id)
        .await
        .unwrap();
    repo.record_effect_request(org, &request, attempt.id)
        .await
        .unwrap();
    assert_eq!(
        repo.get_pending_unknown_effects(org).await.unwrap().len(),
        1
    );
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn budget_release_after_cancellation_is_idempotent() {
    let (_, repo, org, principal) = fixture().await;
    let run = run(&repo, org, principal).await;
    repo.create_reservation(
        org,
        run.id,
        "budget",
        "main",
        100,
        Utc::now() + Duration::hours(1),
    )
    .await
    .unwrap();
    assert_eq!(
        repo.request_cancellation(org, cancellation(run.id, principal, false))
            .await
            .unwrap()
            .released_reservations
            .len(),
        1
    );
    assert!(
        repo.request_cancellation(org, cancellation(run.id, principal, false))
            .await
            .unwrap()
            .released_reservations
            .is_empty()
    );
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn cancellation_is_durably_logged() {
    let (_, repo, org, principal) = fixture().await;
    let run = run(&repo, org, principal).await;
    repo.request_cancellation(org, cancellation(run.id, principal, false))
        .await
        .unwrap();
    let entries = repo.get_cancellation_log(org, run.id).await.unwrap();
    assert_eq!(entries.len(), 1);
    assert_eq!(entries[0].reason, CancellationReason::UserRequested);
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn mismatched_effect_id_is_rejected() {
    let (_, repo, org, _) = fixture().await;
    let result = EffectResult {
        effect_id: "result-id".into(),
        status: EffectResultStatus::Applied,
        external_effect_id: None,
        response_hash: None,
        reconciled_at: None,
        error: None,
        created_at: Utc::now(),
    };
    assert!(matches!(
        repo.record_effect_result(org, "different-id", &result)
            .await,
        Err(ExecutionStoreError::InvalidInput(_))
    ));
}
