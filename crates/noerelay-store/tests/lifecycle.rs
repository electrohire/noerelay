use chrono::Utc;
use noerelay_core::iam::*;
use noerelay_store::iam::set_tenant_context;
use noerelay_store::{LifecycleRepository, LifecycleStoreError};
use sqlx::PgPool;
use uuid::Uuid;

async fn repository() -> LifecycleRepository {
    let url = std::env::var("DATABASE_URL").expect("DATABASE_URL required for ignored test");
    LifecycleRepository::new(PgPool::connect(&url).await.unwrap())
}

fn ids() -> (OrganizationId, PrincipalId) {
    (OrganizationId(Uuid::new_v4()), PrincipalId(Uuid::new_v4()))
}

fn policy(org: OrganizationId, category: DataCategory, version: i32) -> LifecyclePolicy {
    let now = Utc::now();
    LifecyclePolicy {
        id: format!("{}-{category:?}-v{version}", Uuid::new_v4()),
        organization_id: org,
        category,
        action: RetentionAction::Delete,
        retain_days: Some(30),
        delete_after: Some(now + chrono::Duration::days(30)),
        description: "PostgreSQL lifecycle integration test".into(),
        created_at: now,
        updated_at: now,
        version,
        active: true,
    }
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn creates_and_gets_active_policy() {
    let repo = repository().await;
    let (org, _) = ids();
    let expected = policy(org, DataCategory::Outputs, 1);
    repo.create_policy(expected.clone()).await.unwrap();
    let actual = repo
        .get_active_policy(org, DataCategory::Outputs)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(actual.id, expected.id);
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn versions_policy_and_deactivates_predecessor() {
    let repo = repository().await;
    let (org, _) = ids();
    let first = policy(org, DataCategory::Prompts, 1);
    repo.create_policy(first.clone()).await.unwrap();
    let second = policy(org, DataCategory::Prompts, 2);
    repo.update_policy(&first.id, second.clone()).await.unwrap();
    let rows = repo.list_policies(org).await.unwrap();
    assert_eq!(rows.len(), 2);
    assert!(rows.iter().any(|row| row.id == first.id && !row.active));
    assert!(rows.iter().any(|row| row.id == second.id && row.active));
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn rejects_invalid_policy_version_increment() {
    let repo = repository().await;
    let (org, _) = ids();
    let first = policy(org, DataCategory::Logs, 1);
    repo.create_policy(first.clone()).await.unwrap();
    let invalid = policy(org, DataCategory::Logs, 3);
    assert!(matches!(
        repo.update_policy(&first.id, invalid).await,
        Err(LifecycleStoreError::InvalidValue(_))
    ));
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn deactivates_policy() {
    let repo = repository().await;
    let (org, _) = ids();
    let created = policy(org, DataCategory::Caches, 1);
    repo.create_policy(created.clone()).await.unwrap();
    repo.deactivate_policy(&created.id).await.unwrap();
    assert!(
        repo.get_active_policy(org, DataCategory::Caches)
            .await
            .unwrap()
            .is_none()
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn creates_and_lists_deletion_job() {
    let repo = repository().await;
    let (org, principal) = ids();
    let job = repo
        .create_deletion_job(org, DataCategory::Logs, principal)
        .await
        .unwrap();
    assert_eq!(job.status, DeletionStatus::Pending);
    assert!(
        repo.list_deletion_jobs(org)
            .await
            .unwrap()
            .iter()
            .any(|row| row.id == job.id)
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn completes_deletion_job_lifecycle() {
    let repo = repository().await;
    let (org, principal) = ids();
    let job = repo
        .create_deletion_job(org, DataCategory::ProviderCopies, principal)
        .await
        .unwrap();
    repo.start_deletion_job(job.id).await.unwrap();
    repo.complete_deletion_job(job.id).await.unwrap();
    assert_eq!(
        repo.get_deletion_job(job.id).await.unwrap().status,
        DeletionStatus::Completed
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn fails_deletion_job_with_reason() {
    let repo = repository().await;
    let (org, principal) = ids();
    let job = repo
        .create_deletion_job(org, DataCategory::Traces, principal)
        .await
        .unwrap();
    repo.fail_deletion_job(job.id, "trace backend unavailable")
        .await
        .unwrap();
    let failed = repo.get_deletion_job(job.id).await.unwrap();
    assert_eq!(failed.status, DeletionStatus::Failed);
    assert_eq!(failed.error.as_deref(), Some("trace backend unavailable"));
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn cancels_pending_deletion_job() {
    let repo = repository().await;
    let (org, principal) = ids();
    let job = repo
        .create_deletion_job(org, DataCategory::Caches, principal)
        .await
        .unwrap();
    repo.cancel_deletion_job(job.id).await.unwrap();
    assert_eq!(
        repo.get_deletion_job(job.id).await.unwrap().status,
        DeletionStatus::Cancelled
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn generates_complete_data_inventory() {
    let repo = repository().await;
    let (org, _) = ids();
    let inventory = repo.generate_data_inventory(org).await.unwrap();
    assert_eq!(inventory.organization_id, org);
    assert_eq!(inventory.entries.len(), 14);
    assert!(
        inventory
            .entries
            .iter()
            .any(|entry| entry.category == DataCategory::ProviderCopies)
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn creates_and_completes_export_request() {
    let repo = repository().await;
    let (org, principal) = ids();
    let request = repo
        .create_export_request(
            org,
            vec![DataCategory::Prompts, DataCategory::Outputs],
            principal,
        )
        .await
        .unwrap();
    repo.update_export_status(request.id, ExportStatus::InProgress, None)
        .await
        .unwrap();
    let artifact_id = Uuid::new_v4();
    repo.update_export_status(request.id, ExportStatus::Completed, Some(artifact_id))
        .await
        .unwrap();
    let completed = repo.get_export_request(request.id).await.unwrap();
    assert_eq!(completed.status, ExportStatus::Completed);
    assert_eq!(completed.artifact_id, Some(artifact_id));
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn lists_export_requests() {
    let repo = repository().await;
    let (org, principal) = ids();
    let request = repo
        .create_export_request(org, vec![DataCategory::AuditEvents], principal)
        .await
        .unwrap();
    assert!(
        repo.list_export_requests(org)
            .await
            .unwrap()
            .iter()
            .any(|row| row.id == request.id)
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn creates_and_filters_tombstones() {
    let repo = repository().await;
    let (org, principal) = ids();
    let tombstone = Tombstone {
        id: Uuid::new_v4(),
        organization_id: org,
        original_table: "artifacts".into(),
        original_id: Uuid::new_v4().to_string(),
        deleted_at: Utc::now(),
        deleted_by: principal,
        deletion_job_id: None,
        reason: "retention expired".into(),
    };
    repo.create_tombstone(tombstone.clone()).await.unwrap();
    let rows = repo
        .list_tombstones(org, Some("artifacts".into()))
        .await
        .unwrap();
    assert!(rows.iter().any(|row| row.id == tombstone.id));
    assert!(
        repo.list_tombstones(org, Some("usage_records".into()))
            .await
            .unwrap()
            .is_empty()
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn reconciles_inventory_with_timestamp() {
    let repo = repository().await;
    let (org, _) = ids();
    let inventory = repo.reconcile_inventory(org).await.unwrap();
    assert!(
        inventory
            .entries
            .iter()
            .all(|entry| entry.last_reconciled_at.is_some())
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn legal_hold_is_counted_and_blocks_full_completion() {
    let url = std::env::var("DATABASE_URL").expect("DATABASE_URL required for ignored test");
    let pool = PgPool::connect(&url).await.unwrap();
    let repo = LifecycleRepository::new(pool.clone());
    let (org, principal) = ids();
    let mut tx = pool.begin().await.unwrap();
    set_tenant_context(&mut tx, &org.0.to_string(), Some(principal))
        .await
        .unwrap();
    sqlx::query(
        "INSERT INTO artifacts \
         (id, organization_id, artifact_type, content_hash, content_type, size_bytes, \
          storage_key, created_by, legal_hold) \
         VALUES ($1,$2,'response',$3,'application/json',2,$4,$5,true)",
    )
    .bind(Uuid::new_v4())
    .bind(org.0)
    .bind(Uuid::new_v4().to_string())
    .bind(format!("test/{}", Uuid::new_v4()))
    .bind(principal.0)
    .execute(&mut *tx)
    .await
    .unwrap();
    tx.commit().await.unwrap();

    let job = repo
        .create_deletion_job(org, DataCategory::Outputs, principal)
        .await
        .unwrap();
    assert_eq!(job.items_skipped_legal_hold, 1);
    repo.start_deletion_job(job.id).await.unwrap();
    repo.complete_deletion_job(job.id).await.unwrap();
    assert_eq!(
        repo.get_deletion_job(job.id).await.unwrap().status,
        DeletionStatus::PartiallyCompleted
    );
}
