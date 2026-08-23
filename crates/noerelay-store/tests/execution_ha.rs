//! PostgreSQL integration coverage for RUN-04. Run with `--include-ignored`.

use noerelay_core::execution::*;
use noerelay_core::iam::{OrganizationId, PrincipalId};
use noerelay_store::{ExecutionRepository, ExecutionStoreError};
use sqlx::{PgPool, Row};
use uuid::Uuid;

async fn fixture() -> (PgPool, ExecutionRepository, OrganizationId, PrincipalId) {
    let pool = PgPool::connect(&std::env::var("DATABASE_URL").expect("DATABASE_URL required"))
        .await
        .unwrap();
    sqlx::migrate!("./migrations").run(&pool).await.unwrap();
    let org = OrganizationId(Uuid::new_v4());
    let principal = PrincipalId(Uuid::new_v4());
    sqlx::query("INSERT INTO organizations (organization_id, name) VALUES ($1, 'RUN-04 test')")
        .bind(org.0.to_string())
        .execute(&pool)
        .await
        .unwrap();
    sqlx::query(
        "INSERT INTO principals (principal_id, organization_id, principal_type, external_id, \
         display_name) VALUES ($1, $2, 'service', $3, 'RUN-04 test')",
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

async fn register(repo: &ExecutionRepository, org: OrganizationId, worker: &str) {
    repo.register_worker(org, worker, "1.0.0", &["execute".into(), "stream".into()])
        .await
        .unwrap();
}

async fn enqueue(
    repo: &ExecutionRepository,
    org: OrganizationId,
    principal: PrincipalId,
) -> WorkItem {
    let run = repo
        .create_run(
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
        .unwrap();
    repo.enqueue_work_item(
        org,
        run.id,
        None,
        "test",
        &serde_json::json!({"value": 1}),
        0,
        3,
        None,
    )
    .await
    .unwrap()
}

async fn tenant_exec(pool: &PgPool, org: OrganizationId, sql: &str) {
    let mut tx = pool.begin().await.unwrap();
    sqlx::query("SELECT set_config('noerelay.organization_id', $1, true)")
        .bind(org.0.to_string())
        .execute(&mut *tx)
        .await
        .unwrap();
    sqlx::query(sql).execute(&mut *tx).await.unwrap();
    tx.commit().await.unwrap();
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn worker_registration_records_capabilities() {
    let (_, repo, org, _) = fixture().await;
    let worker = repo
        .register_worker(org, "worker-register", "1.2.3", &["stream".into()])
        .await
        .unwrap();
    assert_eq!(worker.status, WorkerStatus::Active);
    assert_eq!(worker.capabilities, vec!["stream"]);
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn worker_heartbeat_succeeds() {
    let (_, repo, org, _) = fixture().await;
    register(&repo, org, "worker-heartbeat").await;
    repo.heartbeat(org, "worker-heartbeat").await.unwrap();
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn graceful_drain_prevents_new_claims() {
    let (_, repo, org, principal) = fixture().await;
    register(&repo, org, "worker-drain").await;
    enqueue(&repo, org, principal).await;
    assert_eq!(
        repo.drain_worker(org, "worker-drain").await.unwrap(),
        WorkerStatus::Draining
    );
    assert!(
        repo.claim_work_item_with_retry(org, "worker-drain", 30)
            .await
            .unwrap()
            .is_none()
    );
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn drain_completes_without_in_flight_work() {
    let (_, repo, org, _) = fixture().await;
    register(&repo, org, "worker-drained").await;
    repo.drain_worker(org, "worker-drained").await.unwrap();
    assert_eq!(
        repo.complete_drain(org, "worker-drained").await.unwrap(),
        WorkerStatus::Drained
    );
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn drain_completion_rejects_in_flight_work() {
    let (_, repo, org, principal) = fixture().await;
    register(&repo, org, "worker-busy").await;
    enqueue(&repo, org, principal).await;
    repo.claim_work_item_with_retry(org, "worker-busy", 30)
        .await
        .unwrap()
        .unwrap();
    repo.drain_worker(org, "worker-busy").await.unwrap();
    assert!(matches!(
        repo.complete_drain(org, "worker-busy").await,
        Err(ExecutionStoreError::ConcurrencyConflict(_))
    ));
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn cas_succeeds_when_version_matches() {
    let (_, repo, org, principal) = fixture().await;
    let item = enqueue(&repo, org, principal).await;
    let updated = repo
        .update_work_item_cas(org, item.id, 0, |item| item.priority = 99)
        .await
        .unwrap();
    assert_eq!(updated.priority, 99);
    assert_eq!(updated.version, 1);
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn cas_returns_report_when_version_mismatches() {
    let (_, repo, org, principal) = fixture().await;
    let item = enqueue(&repo, org, principal).await;
    assert!(matches!(
        repo.update_work_item_cas(org, item.id, 7, |_| {}).await,
        Err(ExecutionStoreError::OptimisticConflict(ConflictReport {
            conflict_type: ConflictType::VersionMismatch,
            resolution: ConflictResolution::ReloadAndRetry,
            ..
        }))
    ));
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn expired_lease_is_reclaimed() {
    let (pool, repo, org, principal) = fixture().await;
    register(&repo, org, "worker-old").await;
    register(&repo, org, "worker-new").await;
    enqueue(&repo, org, principal).await;
    repo.claim_work_item_with_retry(org, "worker-old", 30)
        .await
        .unwrap()
        .unwrap();
    tenant_exec(
        &pool,
        org,
        "UPDATE work_items SET lease_expires_at = now() - interval '1 second'",
    )
    .await;
    let claimed = repo
        .claim_orphaned_work(org, "worker-new", 30)
        .await
        .unwrap();
    assert_eq!(claimed.len(), 1);
    assert_eq!(claimed[0].1.worker_id, "worker-new");
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn stream_can_be_acquired_and_renewed() {
    let (_, repo, org, _) = fixture().await;
    register(&repo, org, "stream-owner").await;
    repo.acquire_stream(org, "stream-a", "stream-owner", 1, 30)
        .await
        .unwrap();
    let renewed = repo
        .renew_stream(org, "stream-a", "stream-owner", 1)
        .await
        .unwrap();
    assert!(renewed.last_heartbeat_at.is_some());
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn stream_can_be_released() {
    let (_, repo, org, _) = fixture().await;
    register(&repo, org, "release-owner").await;
    repo.acquire_stream(org, "stream-release", "release-owner", 1, 30)
        .await
        .unwrap();
    repo.release_stream(org, "stream-release", "release-owner", 1)
        .await
        .unwrap();
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn stale_fencing_token_cannot_release_stream() {
    let (_, repo, org, _) = fixture().await;
    register(&repo, org, "fenced-owner").await;
    repo.acquire_stream(org, "stream-fenced", "fenced-owner", 2, 30)
        .await
        .unwrap();
    assert!(matches!(
        repo.release_stream(org, "stream-fenced", "fenced-owner", 1)
            .await,
        Err(ExecutionStoreError::ConcurrencyConflict(_))
    ));
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn expired_stream_can_be_stolen() {
    let (pool, repo, org, _) = fixture().await;
    register(&repo, org, "stream-old").await;
    register(&repo, org, "stream-new").await;
    repo.acquire_stream(org, "stream-steal", "stream-old", 1, 30)
        .await
        .unwrap();
    tenant_exec(
        &pool,
        org,
        "UPDATE stream_ownership SET expires_at = now() - interval '1 second'",
    )
    .await;
    let ownership = repo
        .acquire_stream(org, "stream-steal", "stream-new", 2, 30)
        .await
        .unwrap();
    assert_eq!(ownership.owner_worker_id, "stream-new");
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn unresponsive_worker_is_detected() {
    let (pool, repo, org, _) = fixture().await;
    register(&repo, org, "worker-stale").await;
    tenant_exec(
        &pool,
        org,
        "UPDATE workers SET last_heartbeat_at = now() - interval '2 minutes'",
    )
    .await;
    assert_eq!(
        repo.get_unresponsive_workers(org, 60).await.unwrap().len(),
        1
    );
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn conflict_is_logged() {
    let (pool, repo, org, principal) = fixture().await;
    let item = enqueue(&repo, org, principal).await;
    let report = ConflictReport {
        work_item_id: item.id,
        conflict_type: ConflictType::LeaseExpired,
        resolution: ConflictResolution::TakeOver,
        current_owner: Some("old".into()),
        fencing_token: 2,
        detected_at: chrono::Utc::now(),
    };
    repo.log_conflict(org, &report).await.unwrap();
    let mut tx = pool.begin().await.unwrap();
    sqlx::query("SELECT set_config('noerelay.organization_id', $1, true)")
        .bind(org.0.to_string())
        .execute(&mut *tx)
        .await
        .unwrap();
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM conflict_log")
        .fetch_one(&mut *tx)
        .await
        .unwrap();
    assert_eq!(count, 1);
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn conflict_can_be_resolved() {
    let (pool, repo, org, principal) = fixture().await;
    let item = enqueue(&repo, org, principal).await;
    repo.log_conflict(
        org,
        &ConflictReport {
            work_item_id: item.id,
            conflict_type: ConflictType::WorkerUnresponsive,
            resolution: ConflictResolution::Wait,
            current_owner: None,
            fencing_token: 1,
            detected_at: chrono::Utc::now(),
        },
    )
    .await
    .unwrap();
    let mut tx = pool.begin().await.unwrap();
    sqlx::query("SELECT set_config('noerelay.organization_id', $1, true)")
        .bind(org.0.to_string())
        .execute(&mut *tx)
        .await
        .unwrap();
    let row = sqlx::query("SELECT id FROM conflict_log LIMIT 1")
        .fetch_one(&mut *tx)
        .await
        .unwrap();
    let id: Uuid = row.try_get("id").unwrap();
    tx.commit().await.unwrap();
    repo.resolve_conflict(org, id, ConflictResolution::TakeOver)
        .await
        .unwrap();
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn database_health_reports_latency() {
    let (_, repo, _, _) = fixture().await;
    let health = repo.check_database_health().await.unwrap();
    assert!(health.healthy);
    assert!(health.latency_ms >= 0);
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn failover_fences_active_work() {
    let (_, repo, org, principal) = fixture().await;
    register(&repo, org, "worker-failover").await;
    enqueue(&repo, org, principal).await;
    repo.claim_work_item_with_retry(org, "worker-failover", 30)
        .await
        .unwrap()
        .unwrap();
    let reports = repo.handle_failover(org).await.unwrap();
    assert_eq!(reports.len(), 1);
    assert_eq!(reports[0].conflict_type, ConflictType::DatabaseFailover);
}
