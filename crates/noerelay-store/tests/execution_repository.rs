//! Integration tests for ExecutionRepository CRUD operations.
//!
//! These tests require a PostgreSQL database with the execution migration applied.
//! Set `DATABASE_URL` environment variable and run with:
//! ```text
//! cargo test --package noerelay-store --test execution_repository -- --include-ignored
//! ```
//!
//! Without `DATABASE_URL`, these tests are skipped.

use chrono::{Duration, Utc};
use noerelay_core::execution::*;
use noerelay_core::iam::{OrganizationId, PrincipalId};
use noerelay_store::ExecutionRepository;
use sqlx::PgPool;
use uuid::Uuid;

async fn setup_pool() -> Option<PgPool> {
    let database_url = std::env::var("DATABASE_URL").ok()?;
    let pool = PgPool::connect(&database_url).await.ok()?;
    // Run migrations
    sqlx::migrate!("./migrations").run(&pool).await.ok()?;
    Some(pool)
}

fn make_org_id() -> OrganizationId {
    OrganizationId(Uuid::new_v4())
}

fn make_principal_id() -> PrincipalId {
    PrincipalId(Uuid::new_v4())
}

// ============================================================================
// Run CRUD Tests
// ============================================================================

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn create_and_get_run() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = ExecutionRepository::new(pool);
    let org_id = make_org_id();
    let principal_id = make_principal_id();

    let run = repo
        .create_run(
            org_id,
            None,
            None,
            principal_id,
            "contract-hash-001",
            None,
            "v1",
            None,
        )
        .await
        .expect("create run");

    assert_eq!(run.contract_hash, "contract-hash-001");
    assert_eq!(run.status, RunStatus::Pending);
    assert_eq!(run.principal_id, principal_id);

    let fetched = repo
        .get_run(org_id, run.id)
        .await
        .expect("get run")
        .expect("run exists");

    assert_eq!(fetched.id, run.id);
    assert_eq!(fetched.contract_hash, "contract-hash-001");
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn update_run_status_valid_transition() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = ExecutionRepository::new(pool);
    let org_id = make_org_id();
    let principal_id = make_principal_id();

    let run = repo
        .create_run(org_id, None, None, principal_id, "hash", None, "v1", None)
        .await
        .expect("create run");

    // Pending -> Running is legal
    let updated = repo
        .update_run_status(org_id, run.id, RunStatus::Running)
        .await
        .expect("update to running");

    assert_eq!(updated.status, RunStatus::Running);
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn update_run_status_illegal_transition_rejected() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = ExecutionRepository::new(pool);
    let org_id = make_org_id();
    let principal_id = make_principal_id();

    let run = repo
        .create_run(org_id, None, None, principal_id, "hash", None, "v1", None)
        .await
        .expect("create run");

    // Pending -> Completed is illegal
    let result = repo
        .update_run_status(org_id, run.id, RunStatus::Completed)
        .await;

    assert!(result.is_err());
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn run_completed_sets_completed_at() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = ExecutionRepository::new(pool);
    let org_id = make_org_id();
    let principal_id = make_principal_id();

    let run = repo
        .create_run(org_id, None, None, principal_id, "hash", None, "v1", None)
        .await
        .expect("create run");

    // Pending -> Running -> Completed
    repo.update_run_status(org_id, run.id, RunStatus::Running)
        .await
        .expect("update to running");

    let completed = repo
        .update_run_status(org_id, run.id, RunStatus::Completed)
        .await
        .expect("update to completed");

    assert_eq!(completed.status, RunStatus::Completed);
    assert!(completed.completed_at.is_some());
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn list_runs_returns_created_runs() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = ExecutionRepository::new(pool);
    let org_id = make_org_id();
    let principal_id = make_principal_id();

    let run1 = repo
        .create_run(org_id, None, None, principal_id, "hash1", None, "v1", None)
        .await
        .expect("create run1");
    let run2 = repo
        .create_run(org_id, None, None, principal_id, "hash2", None, "v1", None)
        .await
        .expect("create run2");

    let runs = repo.list_runs(org_id, 10, 0).await.expect("list runs");
    assert!(runs.len() >= 2);
    let ids: Vec<_> = runs.iter().map(|r| r.id).collect();
    assert!(ids.contains(&run1.id));
    assert!(ids.contains(&run2.id));
}

// ============================================================================
// Step CRUD Tests
// ============================================================================

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn create_and_get_step() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = ExecutionRepository::new(pool);
    let org_id = make_org_id();
    let principal_id = make_principal_id();

    let run = repo
        .create_run(org_id, None, None, principal_id, "hash", None, "v1", None)
        .await
        .expect("create run");

    let step = repo
        .create_step(
            org_id,
            run.id,
            None,
            StepType::ProviderCall,
            "call-gpt4",
            1,
            None,
        )
        .await
        .expect("create step");

    assert_eq!(step.name, "call-gpt4");
    assert_eq!(step.step_type, StepType::ProviderCall);
    assert_eq!(step.status, StepStatus::Pending);
    assert_eq!(step.sequence, 1);

    let fetched = repo
        .get_step(org_id, step.id)
        .await
        .expect("get step")
        .expect("step exists");

    assert_eq!(fetched.id, step.id);
    assert_eq!(fetched.name, "call-gpt4");
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn update_step_status_valid_transition() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = ExecutionRepository::new(pool);
    let org_id = make_org_id();
    let principal_id = make_principal_id();

    let run = repo
        .create_run(org_id, None, None, principal_id, "hash", None, "v1", None)
        .await
        .expect("create run");

    let step = repo
        .create_step(org_id, run.id, None, StepType::Contract, "compile", 1, None)
        .await
        .expect("create step");

    // Pending -> Ready is legal
    let updated = repo
        .update_step_status(org_id, step.id, StepStatus::Ready)
        .await
        .expect("update to ready");

    assert_eq!(updated.status, StepStatus::Ready);
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn list_steps_for_run_ordered_by_sequence() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = ExecutionRepository::new(pool);
    let org_id = make_org_id();
    let principal_id = make_principal_id();

    let run = repo
        .create_run(org_id, None, None, principal_id, "hash", None, "v1", None)
        .await
        .expect("create run");

    let step1 = repo
        .create_step(org_id, run.id, None, StepType::Contract, "first", 1, None)
        .await
        .expect("create step1");
    let step2 = repo
        .create_step(org_id, run.id, None, StepType::Route, "second", 2, None)
        .await
        .expect("create step2");
    let step3 = repo
        .create_step(
            org_id,
            run.id,
            None,
            StepType::ProviderCall,
            "third",
            3,
            None,
        )
        .await
        .expect("create step3");

    let steps = repo
        .list_steps_for_run(org_id, run.id)
        .await
        .expect("list steps");

    assert_eq!(steps.len(), 3);
    assert_eq!(steps[0].id, step1.id);
    assert_eq!(steps[1].id, step2.id);
    assert_eq!(steps[2].id, step3.id);
}

// ============================================================================
// Attempt CRUD Tests
// ============================================================================

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn create_and_get_attempt() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = ExecutionRepository::new(pool);
    let org_id = make_org_id();
    let principal_id = make_principal_id();

    let run = repo
        .create_run(org_id, None, None, principal_id, "hash", None, "v1", None)
        .await
        .expect("create run");

    let step = repo
        .create_step(
            org_id,
            run.id,
            None,
            StepType::ProviderCall,
            "call",
            1,
            None,
        )
        .await
        .expect("create step");

    let attempt = repo
        .create_attempt(org_id, step.id, 1, None)
        .await
        .expect("create attempt");

    assert_eq!(attempt.attempt_number, 1);
    assert_eq!(attempt.status, AttemptStatus::Pending);

    let fetched = repo
        .get_attempt(org_id, attempt.id)
        .await
        .expect("get attempt")
        .expect("attempt exists");

    assert_eq!(fetched.id, attempt.id);
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn update_attempt_status_with_cost() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = ExecutionRepository::new(pool);
    let org_id = make_org_id();
    let principal_id = make_principal_id();

    let run = repo
        .create_run(org_id, None, None, principal_id, "hash", None, "v1", None)
        .await
        .expect("create run");

    let step = repo
        .create_step(
            org_id,
            run.id,
            None,
            StepType::ProviderCall,
            "call",
            1,
            None,
        )
        .await
        .expect("create step");

    let attempt = repo
        .create_attempt(org_id, step.id, 1, None)
        .await
        .expect("create attempt");

    // Pending -> Running -> Succeeded
    repo.update_attempt_status(org_id, attempt.id, AttemptStatus::Running, None, None)
        .await
        .expect("update to running");

    let succeeded = repo
        .update_attempt_status(
            org_id,
            attempt.id,
            AttemptStatus::Succeeded,
            None,
            Some(150),
        )
        .await
        .expect("update to succeeded");

    assert_eq!(succeeded.status, AttemptStatus::Succeeded);
    assert_eq!(succeeded.cost_micro_usd, Some(150));
    assert!(succeeded.completed_at.is_some());
}

// ============================================================================
// Work Item Tests
// ============================================================================

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn enqueue_and_claim_work_item() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = ExecutionRepository::new(pool);
    let org_id = make_org_id();
    let principal_id = make_principal_id();

    let run = repo
        .create_run(org_id, None, None, principal_id, "hash", None, "v1", None)
        .await
        .expect("create run");

    let item = repo
        .enqueue_work_item(
            org_id,
            run.id,
            None,
            "provider_call",
            &serde_json::json!({"model": "gpt-4o"}),
            5,
            3,
            None,
        )
        .await
        .expect("enqueue");

    assert_eq!(item.status, WorkItemStatus::Pending);
    assert_eq!(item.priority, 5);

    // Claim the item
    let claimed = repo
        .claim_work_item(org_id, "worker-1", 60)
        .await
        .expect("claim")
        .expect("item claimed");

    assert_eq!(claimed.id, item.id);
    assert_eq!(claimed.status, WorkItemStatus::Claimed);
    assert_eq!(claimed.lease_id.as_deref(), Some("worker-1"));
    assert_eq!(claimed.attempts, 1);
    assert!(claimed.fencing_token.is_some());
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn claim_work_item_skip_locked_prevents_double_claim() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = ExecutionRepository::new(pool);
    let org_id = make_org_id();
    let principal_id = make_principal_id();

    let run = repo
        .create_run(org_id, None, None, principal_id, "hash", None, "v1", None)
        .await
        .expect("create run");

    // Enqueue a single item
    repo.enqueue_work_item(
        org_id,
        run.id,
        None,
        "test",
        &serde_json::json!({}),
        0,
        3,
        None,
    )
    .await
    .expect("enqueue");

    // First claim should succeed
    let claimed = repo
        .claim_work_item(org_id, "worker-1", 60)
        .await
        .expect("claim 1");

    assert!(claimed.is_some());

    // Second claim should return None (item already claimed)
    let second = repo
        .claim_work_item(org_id, "worker-2", 60)
        .await
        .expect("claim 2");

    assert!(second.is_none());
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn complete_work_item_with_fencing_token() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = ExecutionRepository::new(pool);
    let org_id = make_org_id();
    let principal_id = make_principal_id();

    let run = repo
        .create_run(org_id, None, None, principal_id, "hash", None, "v1", None)
        .await
        .expect("create run");

    repo.enqueue_work_item(
        org_id,
        run.id,
        None,
        "test",
        &serde_json::json!({}),
        0,
        3,
        None,
    )
    .await
    .expect("enqueue");

    let claimed = repo
        .claim_work_item(org_id, "worker-1", 60)
        .await
        .expect("claim")
        .expect("item claimed");

    let token = claimed.fencing_token.unwrap();

    let completed = repo
        .complete_work_item(org_id, claimed.id, token)
        .await
        .expect("complete");

    assert_eq!(completed.status, WorkItemStatus::Completed);
    assert!(completed.lease_id.is_none());
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn complete_work_item_wrong_fencing_token_fails() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = ExecutionRepository::new(pool);
    let org_id = make_org_id();
    let principal_id = make_principal_id();

    let run = repo
        .create_run(org_id, None, None, principal_id, "hash", None, "v1", None)
        .await
        .expect("create run");

    repo.enqueue_work_item(
        org_id,
        run.id,
        None,
        "test",
        &serde_json::json!({}),
        0,
        3,
        None,
    )
    .await
    .expect("enqueue");

    let claimed = repo
        .claim_work_item(org_id, "worker-1", 60)
        .await
        .expect("claim")
        .expect("item claimed");

    // Use wrong fencing token
    let result = repo.complete_work_item(org_id, claimed.id, 99999).await;

    assert!(result.is_err());
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn fail_work_item_exceeding_max_attempts_goes_to_dead_letter() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = ExecutionRepository::new(pool);
    let org_id = make_org_id();
    let principal_id = make_principal_id();

    let run = repo
        .create_run(org_id, None, None, principal_id, "hash", None, "v1", None)
        .await
        .expect("create run");

    // max_attempts = 1
    repo.enqueue_work_item(
        org_id,
        run.id,
        None,
        "test",
        &serde_json::json!({}),
        0,
        1,
        None,
    )
    .await
    .expect("enqueue");

    let claimed = repo
        .claim_work_item(org_id, "worker-1", 60)
        .await
        .expect("claim")
        .expect("item claimed");

    let token = claimed.fencing_token.unwrap();

    let failed = repo
        .fail_work_item(org_id, claimed.id, token)
        .await
        .expect("fail");

    // Since max_attempts=1 and attempts is now 1, should be dead_letter
    assert_eq!(failed.status, WorkItemStatus::DeadLetter);
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn requeue_expired_leases_returns_items_to_pending() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = ExecutionRepository::new(pool);
    let org_id = make_org_id();
    let principal_id = make_principal_id();

    let run = repo
        .create_run(org_id, None, None, principal_id, "hash", None, "v1", None)
        .await
        .expect("create run");

    repo.enqueue_work_item(
        org_id,
        run.id,
        None,
        "test",
        &serde_json::json!({}),
        0,
        3,
        None,
    )
    .await
    .expect("enqueue");

    // Claim with a very short lease (1 second)
    let claimed = repo
        .claim_work_item(org_id, "worker-1", 1)
        .await
        .expect("claim")
        .expect("item claimed");

    assert_eq!(claimed.status, WorkItemStatus::Claimed);

    // Wait for lease to expire
    tokio::time::sleep(std::time::Duration::from_secs(2)).await;

    let count = repo.requeue_expired_leases(org_id).await.expect("requeue");

    assert_eq!(count, 1);

    // Now we should be able to claim it again
    let reclaimed = repo
        .claim_work_item(org_id, "worker-2", 60)
        .await
        .expect("reclaim")
        .expect("item reclaimed");

    assert_eq!(reclaimed.id, claimed.id);
    assert_eq!(reclaimed.status, WorkItemStatus::Claimed);
    assert_eq!(reclaimed.attempts, 2); // attempts incremented
}

// ============================================================================
// Reservation Tests
// ============================================================================

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn create_and_release_reservation() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = ExecutionRepository::new(pool);
    let org_id = make_org_id();
    let principal_id = make_principal_id();

    let run = repo
        .create_run(org_id, None, None, principal_id, "hash", None, "v1", None)
        .await
        .expect("create run");

    let reservation = repo
        .create_reservation(
            org_id,
            run.id,
            "llm_call",
            "gpt-4o",
            5000,
            Utc::now() + Duration::hours(1),
        )
        .await
        .expect("create reservation");

    assert_eq!(reservation.status, ReservationStatus::Active);
    assert_eq!(reservation.amount_micro_usd, 5000);

    let released = repo
        .release_reservation(org_id, reservation.id)
        .await
        .expect("release");

    assert_eq!(released.status, ReservationStatus::Released);
    assert!(released.released_at.is_some());
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn expire_reservations_past_expiry() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = ExecutionRepository::new(pool);
    let org_id = make_org_id();
    let principal_id = make_principal_id();

    let run = repo
        .create_run(org_id, None, None, principal_id, "hash", None, "v1", None)
        .await
        .expect("create run");

    // Create a reservation that expires in 1 second
    repo.create_reservation(
        org_id,
        run.id,
        "llm_call",
        "gpt-4o",
        1000,
        Utc::now() + Duration::seconds(1),
    )
    .await
    .expect("create reservation");

    // Wait for expiry
    tokio::time::sleep(std::time::Duration::from_secs(2)).await;

    let count = repo.expire_reservations(org_id).await.expect("expire");

    assert_eq!(count, 1);
}

// ============================================================================
// Tool Effect Tests
// ============================================================================

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn record_and_reconcile_tool_effect() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = ExecutionRepository::new(pool);
    let org_id = make_org_id();
    let principal_id = make_principal_id();

    let run = repo
        .create_run(org_id, None, None, principal_id, "hash", None, "v1", None)
        .await
        .expect("create run");

    let step = repo
        .create_step(
            org_id,
            run.id,
            None,
            StepType::ToolExecution,
            "tool",
            1,
            None,
        )
        .await
        .expect("create step");

    let attempt = repo
        .create_attempt(org_id, step.id, 1, None)
        .await
        .expect("create attempt");

    let effect = repo
        .record_tool_effect(
            org_id,
            attempt.id,
            "file_write",
            "filesystem",
            Some("ext-123"),
            "pending",
            "req-hash-001",
            None,
        )
        .await
        .expect("record effect");

    assert_eq!(effect.tool_id, "file_write");
    assert_eq!(effect.effect_kind, "filesystem");
    assert!(effect.reconciled_at.is_none());

    let reconciled = repo
        .reconcile_tool_effect(org_id, effect.id)
        .await
        .expect("reconcile");

    assert!(reconciled.reconciled_at.is_some());
}

// ============================================================================
// Provider Call Tests
// ============================================================================

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn record_and_get_provider_call() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = ExecutionRepository::new(pool);
    let org_id = make_org_id();
    let principal_id = make_principal_id();

    let run = repo
        .create_run(org_id, None, None, principal_id, "hash", None, "v1", None)
        .await
        .expect("create run");

    let step = repo
        .create_step(
            org_id,
            run.id,
            None,
            StepType::ProviderCall,
            "call",
            1,
            None,
        )
        .await
        .expect("create step");

    let attempt = repo
        .create_attempt(org_id, step.id, 1, None)
        .await
        .expect("create attempt");

    let call = repo
        .record_provider_call(
            org_id,
            attempt.id,
            "openai",
            "gpt-4o",
            "req-hash",
            Some("resp-hash"),
            Some(100),
            Some(50),
            "completed",
        )
        .await
        .expect("record call");

    assert_eq!(call.provider, "openai");
    assert_eq!(call.model, "gpt-4o");
    assert_eq!(call.usage_input_tokens, Some(100));
    assert_eq!(call.usage_output_tokens, Some(50));

    let fetched = repo
        .get_provider_call(org_id, call.id)
        .await
        .expect("get call")
        .expect("call exists");

    assert_eq!(fetched.id, call.id);
    assert_eq!(fetched.request_hash, "req-hash");
}
