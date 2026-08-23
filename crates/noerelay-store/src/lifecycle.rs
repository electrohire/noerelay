//! Tenant-scoped lifecycle policy, inventory, deletion, export, and tombstone persistence.

use chrono::Utc;
use noerelay_core::artifacts::ArtifactId;
use noerelay_core::iam::{
    DataCategory, DataInventory, DataInventoryEntry, DeletionJob, DeletionStatus, ExportRequest,
    ExportStatus, LifecyclePolicy, OrganizationId, PrincipalId, RetentionAction, Tombstone,
};
use sqlx::{PgPool, Postgres, Row, Transaction};
use thiserror::Error;
use uuid::Uuid;

use crate::iam::set_tenant_context;

const POLICY_COLUMNS: &str = "id, organization_id, category, action, retain_days, delete_after, \
    description, created_at, updated_at, version, active";
const JOB_COLUMNS: &str = "id, organization_id, category, status, started_at, completed_at, \
    items_total, items_deleted, items_failed, items_skipped_legal_hold, error, created_at, created_by";
const EXPORT_COLUMNS: &str = "id, organization_id, requested_by, categories, status, artifact_id, \
    created_at, completed_at, expires_at";
const TOMBSTONE_COLUMNS: &str = "id, organization_id, original_table, original_id, deleted_at, \
    deleted_by, deletion_job_id, reason";

#[derive(Debug, Error)]
pub enum LifecycleStoreError {
    #[error("database operation failed: {0}")]
    Database(#[from] sqlx::Error),
    #[error("lifecycle serialization failed: {0}")]
    Serialization(#[from] serde_json::Error),
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
    #[error("invalid lifecycle value in database: {0}")]
    InvalidValue(String),
    #[error("invalid lifecycle state transition from {from} to {to}")]
    InvalidTransition { from: String, to: String },
}

#[derive(Clone)]
pub struct LifecycleRepository {
    pool: PgPool,
}

impl LifecycleRepository {
    pub fn new(pool: PgPool) -> Self {
        Self { pool }
    }

    // Policy management -------------------------------------------------------

    pub async fn create_policy(&self, policy: LifecyclePolicy) -> Result<(), LifecycleStoreError> {
        validate_policy(&policy)?;
        let mut tx = self
            .tenant_transaction(policy.organization_id, None)
            .await?;
        sqlx::query(
            "INSERT INTO lifecycle_policies \
             (id, organization_id, category, action, retain_days, delete_after, description, \
              created_at, updated_at, version, active) \
             VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
        )
        .bind(&policy.id)
        .bind(policy.organization_id.0)
        .bind(data_category_to_str(policy.category))
        .bind(retention_action_to_str(policy.action))
        .bind(policy.retain_days)
        .bind(policy.delete_after)
        .bind(&policy.description)
        .bind(policy.created_at)
        .bind(policy.updated_at)
        .bind(policy.version)
        .bind(policy.active)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        Ok(())
    }

    pub async fn update_policy(
        &self,
        policy_id: &str,
        new_version: LifecyclePolicy,
    ) -> Result<(), LifecycleStoreError> {
        validate_policy(&new_version)?;
        let mut tx = self
            .tenant_transaction(new_version.organization_id, None)
            .await?;
        let old = sqlx::query(
            "SELECT organization_id, category, version FROM lifecycle_policies \
             WHERE id = $1 AND active = true FOR UPDATE",
        )
        .bind(policy_id)
        .fetch_optional(&mut *tx)
        .await?
        .ok_or(LifecycleStoreError::LifecyclePolicyNotFound)?;
        let old_org: Uuid = old.try_get("organization_id")?;
        let old_category: String = old.try_get("category")?;
        let old_version: i32 = old.try_get("version")?;
        if old_org != new_version.organization_id.0
            || old_category != data_category_to_str(new_version.category)
            || new_version.version != old_version + 1
            || new_version.id == policy_id
            || !new_version.active
        {
            return Err(LifecycleStoreError::InvalidValue(
                "new policy must preserve tenant/category, use a new id, increment version, and be active"
                    .into(),
            ));
        }
        sqlx::query(
            "UPDATE lifecycle_policies SET active = false, updated_at = now() WHERE id = $1",
        )
        .bind(policy_id)
        .execute(&mut *tx)
        .await?;
        insert_policy(&mut tx, &new_version).await?;
        tx.commit().await?;
        Ok(())
    }

    pub async fn get_active_policy(
        &self,
        org_id: OrganizationId,
        category: DataCategory,
    ) -> Result<Option<LifecyclePolicy>, LifecycleStoreError> {
        let mut tx = self.tenant_transaction(org_id, None).await?;
        let query = format!(
            "SELECT {POLICY_COLUMNS} FROM lifecycle_policies \
             WHERE organization_id = $1 AND category = $2 AND active = true \
             ORDER BY version DESC LIMIT 1"
        );
        let row = sqlx::query(&query)
            .bind(org_id.0)
            .bind(data_category_to_str(category))
            .fetch_optional(&mut *tx)
            .await?;
        tx.commit().await?;
        row.map(|row| row_to_policy(&row)).transpose()
    }

    pub async fn list_policies(
        &self,
        org_id: OrganizationId,
    ) -> Result<Vec<LifecyclePolicy>, LifecycleStoreError> {
        let mut tx = self.tenant_transaction(org_id, None).await?;
        let query = format!(
            "SELECT {POLICY_COLUMNS} FROM lifecycle_policies \
             WHERE organization_id = $1 ORDER BY category, version DESC"
        );
        let rows = sqlx::query(&query)
            .bind(org_id.0)
            .fetch_all(&mut *tx)
            .await?;
        tx.commit().await?;
        rows.iter().map(row_to_policy).collect()
    }

    pub async fn deactivate_policy(&self, policy_id: &str) -> Result<(), LifecycleStoreError> {
        let result = sqlx::query(
            "UPDATE lifecycle_policies SET active = false, updated_at = now() \
             WHERE id = $1 AND active = true",
        )
        .bind(policy_id)
        .execute(&self.pool)
        .await?;
        if result.rows_affected() == 0 {
            return Err(LifecycleStoreError::LifecyclePolicyNotFound);
        }
        Ok(())
    }

    // Deletion jobs -----------------------------------------------------------

    pub async fn create_deletion_job(
        &self,
        org_id: OrganizationId,
        category: DataCategory,
        created_by: PrincipalId,
    ) -> Result<DeletionJob, LifecycleStoreError> {
        let mut tx = self.tenant_transaction(org_id, Some(created_by)).await?;
        let (total, _, legal_holds) = category_stats(&mut tx, org_id, category).await?;
        let query = format!(
            "INSERT INTO deletion_jobs \
             (organization_id, category, created_by, items_total, items_skipped_legal_hold) \
             VALUES ($1,$2,$3,$4,$5) RETURNING {JOB_COLUMNS}"
        );
        let row = sqlx::query(&query)
            .bind(org_id.0)
            .bind(data_category_to_str(category))
            .bind(created_by.0)
            .bind(total)
            .bind(legal_holds)
            .fetch_one(&mut *tx)
            .await?;
        let job = row_to_deletion_job(&row)?;
        tx.commit().await?;
        Ok(job)
    }

    pub async fn start_deletion_job(&self, job_id: Uuid) -> Result<(), LifecycleStoreError> {
        self.transition_job(
            job_id,
            &[DeletionStatus::Pending],
            DeletionStatus::InProgress,
            "started_at = now(), error = NULL",
        )
        .await
    }

    pub async fn update_deletion_progress(
        &self,
        job_id: Uuid,
        deleted: i64,
        failed: i64,
        skipped: i64,
    ) -> Result<(), LifecycleStoreError> {
        if deleted < 0 || failed < 0 || skipped < 0 {
            return Err(LifecycleStoreError::InvalidValue(
                "deletion progress increments cannot be negative".into(),
            ));
        }
        let result = sqlx::query(
            "UPDATE deletion_jobs SET \
             items_deleted = items_deleted + $2, items_failed = items_failed + $3, \
             items_skipped_legal_hold = items_skipped_legal_hold + $4 \
             WHERE id = $1 AND status = 'in_progress' \
             AND items_deleted + items_failed + $2 + $3 <= items_total",
        )
        .bind(job_id)
        .bind(deleted)
        .bind(failed)
        .bind(skipped)
        .execute(&self.pool)
        .await?;
        if result.rows_affected() == 0 {
            return Err(LifecycleStoreError::InvalidTransition {
                from: "non-in-progress or over-counted job".into(),
                to: "progress update".into(),
            });
        }
        Ok(())
    }

    pub async fn complete_deletion_job(&self, job_id: Uuid) -> Result<(), LifecycleStoreError> {
        let result = sqlx::query(
            "UPDATE deletion_jobs SET status = CASE \
                 WHEN items_failed > 0 OR items_skipped_legal_hold > 0 \
                 THEN 'partially_completed' ELSE 'completed' END, completed_at = now() \
             WHERE id = $1 AND status = 'in_progress'",
        )
        .bind(job_id)
        .execute(&self.pool)
        .await?;
        ensure_job_updated(result.rows_affected())
    }

    pub async fn fail_deletion_job(
        &self,
        job_id: Uuid,
        error: &str,
    ) -> Result<(), LifecycleStoreError> {
        if error.trim().is_empty() {
            return Err(LifecycleStoreError::InvalidValue(
                "deletion failure requires an error".into(),
            ));
        }
        let result = sqlx::query(
            "UPDATE deletion_jobs SET status = 'failed', completed_at = now(), error = $2 \
             WHERE id = $1 AND status IN ('pending','in_progress')",
        )
        .bind(job_id)
        .bind(error)
        .execute(&self.pool)
        .await?;
        ensure_job_updated(result.rows_affected())
    }

    pub async fn cancel_deletion_job(&self, job_id: Uuid) -> Result<(), LifecycleStoreError> {
        self.transition_job(
            job_id,
            &[DeletionStatus::Pending, DeletionStatus::InProgress],
            DeletionStatus::Cancelled,
            "completed_at = now()",
        )
        .await
    }

    pub async fn get_deletion_job(&self, job_id: Uuid) -> Result<DeletionJob, LifecycleStoreError> {
        let query = format!("SELECT {JOB_COLUMNS} FROM deletion_jobs WHERE id = $1");
        let row = sqlx::query(&query)
            .bind(job_id)
            .fetch_optional(&self.pool)
            .await?
            .ok_or(LifecycleStoreError::DeletionJobNotFound)?;
        row_to_deletion_job(&row)
    }

    pub async fn list_deletion_jobs(
        &self,
        org_id: OrganizationId,
    ) -> Result<Vec<DeletionJob>, LifecycleStoreError> {
        let mut tx = self.tenant_transaction(org_id, None).await?;
        let query = format!(
            "SELECT {JOB_COLUMNS} FROM deletion_jobs WHERE organization_id = $1 \
             ORDER BY created_at DESC, id"
        );
        let rows = sqlx::query(&query)
            .bind(org_id.0)
            .fetch_all(&mut *tx)
            .await?;
        tx.commit().await?;
        rows.iter().map(row_to_deletion_job).collect()
    }

    // Inventory and reconciliation ------------------------------------------

    pub async fn generate_data_inventory(
        &self,
        org_id: OrganizationId,
    ) -> Result<DataInventory, LifecycleStoreError> {
        self.inventory(org_id, None).await
    }

    pub async fn reconcile_inventory(
        &self,
        org_id: OrganizationId,
    ) -> Result<DataInventory, LifecycleStoreError> {
        let reconciled_at = Utc::now();
        self.inventory(org_id, Some(reconciled_at))
            .await
            .map_err(|error| LifecycleStoreError::ReconciliationFailed(error.to_string()))
    }

    // Export requests ---------------------------------------------------------

    pub async fn create_export_request(
        &self,
        org_id: OrganizationId,
        categories: Vec<DataCategory>,
        requested_by: PrincipalId,
    ) -> Result<ExportRequest, LifecycleStoreError> {
        if categories.is_empty() {
            return Err(LifecycleStoreError::InvalidValue(
                "export must include at least one category".into(),
            ));
        }
        let mut tx = self.tenant_transaction(org_id, Some(requested_by)).await?;
        let query = format!(
            "INSERT INTO export_requests (organization_id, requested_by, categories) \
             VALUES ($1,$2,$3) RETURNING {EXPORT_COLUMNS}"
        );
        let row = sqlx::query(&query)
            .bind(org_id.0)
            .bind(requested_by.0)
            .bind(serde_json::to_value(&categories)?)
            .fetch_one(&mut *tx)
            .await?;
        let request = row_to_export_request(&row)?;
        tx.commit().await?;
        Ok(request)
    }

    pub async fn update_export_status(
        &self,
        export_id: Uuid,
        status: ExportStatus,
        artifact_id: Option<ArtifactId>,
    ) -> Result<(), LifecycleStoreError> {
        if status == ExportStatus::Completed && artifact_id.is_none() {
            return Err(LifecycleStoreError::InvalidValue(
                "completed export requires an artifact".into(),
            ));
        }
        let result = sqlx::query(
            "UPDATE export_requests SET status = $2, artifact_id = COALESCE($3, artifact_id), \
             completed_at = CASE WHEN $2 IN ('completed','failed','expired') THEN now() \
                                 ELSE completed_at END \
             WHERE id = $1 AND status NOT IN ('completed','failed','expired')",
        )
        .bind(export_id)
        .bind(export_status_to_str(status))
        .bind(artifact_id)
        .execute(&self.pool)
        .await?;
        if result.rows_affected() == 0 {
            return Err(LifecycleStoreError::ExportNotFound);
        }
        Ok(())
    }

    pub async fn get_export_request(
        &self,
        export_id: Uuid,
    ) -> Result<ExportRequest, LifecycleStoreError> {
        let query = format!("SELECT {EXPORT_COLUMNS} FROM export_requests WHERE id = $1");
        let row = sqlx::query(&query)
            .bind(export_id)
            .fetch_optional(&self.pool)
            .await?
            .ok_or(LifecycleStoreError::ExportNotFound)?;
        row_to_export_request(&row)
    }

    pub async fn list_export_requests(
        &self,
        org_id: OrganizationId,
    ) -> Result<Vec<ExportRequest>, LifecycleStoreError> {
        let mut tx = self.tenant_transaction(org_id, None).await?;
        let query = format!(
            "SELECT {EXPORT_COLUMNS} FROM export_requests WHERE organization_id = $1 \
             ORDER BY created_at DESC, id"
        );
        let rows = sqlx::query(&query)
            .bind(org_id.0)
            .fetch_all(&mut *tx)
            .await?;
        tx.commit().await?;
        rows.iter().map(row_to_export_request).collect()
    }

    pub async fn cleanup_expired_exports(&self) -> Result<i32, LifecycleStoreError> {
        let result = sqlx::query(
            "UPDATE export_requests SET status = 'expired', completed_at = COALESCE(completed_at, now()) \
             WHERE expires_at <= now() AND status NOT IN ('expired','failed')",
        )
        .execute(&self.pool)
        .await?;
        i32::try_from(result.rows_affected())
            .map_err(|_| LifecycleStoreError::InvalidValue("expired export count overflow".into()))
    }

    // Tombstones --------------------------------------------------------------

    pub async fn create_tombstone(&self, tombstone: Tombstone) -> Result<(), LifecycleStoreError> {
        if tombstone.original_table.trim().is_empty()
            || tombstone.original_id.trim().is_empty()
            || tombstone.reason.trim().is_empty()
        {
            return Err(LifecycleStoreError::InvalidValue(
                "tombstone table, id, and reason are required".into(),
            ));
        }
        let mut tx = self
            .tenant_transaction(tombstone.organization_id, Some(tombstone.deleted_by))
            .await?;
        sqlx::query(
            "INSERT INTO tombstones \
             (id, organization_id, original_table, original_id, deleted_at, deleted_by, \
              deletion_job_id, reason) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
        )
        .bind(tombstone.id)
        .bind(tombstone.organization_id.0)
        .bind(&tombstone.original_table)
        .bind(&tombstone.original_id)
        .bind(tombstone.deleted_at)
        .bind(tombstone.deleted_by.0)
        .bind(tombstone.deletion_job_id)
        .bind(&tombstone.reason)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        Ok(())
    }

    pub async fn list_tombstones(
        &self,
        org_id: OrganizationId,
        table_name: Option<String>,
    ) -> Result<Vec<Tombstone>, LifecycleStoreError> {
        let mut tx = self.tenant_transaction(org_id, None).await?;
        let query = format!(
            "SELECT {TOMBSTONE_COLUMNS} FROM tombstones \
             WHERE organization_id = $1 AND ($2::text IS NULL OR original_table = $2) \
             ORDER BY deleted_at DESC, id"
        );
        let rows = sqlx::query(&query)
            .bind(org_id.0)
            .bind(table_name)
            .fetch_all(&mut *tx)
            .await?;
        tx.commit().await?;
        rows.iter().map(row_to_tombstone).collect()
    }

    async fn inventory(
        &self,
        org_id: OrganizationId,
        reconciled_at: Option<chrono::DateTime<Utc>>,
    ) -> Result<DataInventory, LifecycleStoreError> {
        let mut tx = self.tenant_transaction(org_id, None).await?;
        let mut entries = Vec::with_capacity(14);
        for category in ALL_CATEGORIES {
            let (count, size_bytes, legal_hold_count) =
                category_stats(&mut tx, org_id, category).await?;
            let policy_id: Option<String> = sqlx::query_scalar(
                "SELECT id FROM lifecycle_policies \
                 WHERE organization_id = $1 AND category = $2 AND active = true \
                 ORDER BY version DESC LIMIT 1",
            )
            .bind(org_id.0)
            .bind(data_category_to_str(category))
            .fetch_optional(&mut *tx)
            .await?;
            entries.push(DataInventoryEntry {
                category,
                location: category_location(category).into(),
                count,
                size_bytes,
                retention_policy_id: policy_id,
                legal_hold_count,
                last_reconciled_at: reconciled_at,
            });
        }
        tx.commit().await?;
        Ok(DataInventory {
            organization_id: org_id,
            entries,
            generated_at: Utc::now(),
        })
    }

    async fn tenant_transaction(
        &self,
        org_id: OrganizationId,
        principal_id: Option<PrincipalId>,
    ) -> Result<Transaction<'_, Postgres>, LifecycleStoreError> {
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id.0.to_string(), principal_id).await?;
        Ok(tx)
    }

    async fn transition_job(
        &self,
        job_id: Uuid,
        from: &[DeletionStatus],
        to: DeletionStatus,
        assignments: &str,
    ) -> Result<(), LifecycleStoreError> {
        let allowed: Vec<&str> = from.iter().copied().map(deletion_status_to_str).collect();
        let query = format!(
            "UPDATE deletion_jobs SET status = $2, {assignments} WHERE id = $1 AND status = ANY($3)"
        );
        let result = sqlx::query(&query)
            .bind(job_id)
            .bind(deletion_status_to_str(to))
            .bind(allowed)
            .execute(&self.pool)
            .await?;
        ensure_job_updated(result.rows_affected())
    }
}

const ALL_CATEGORIES: [DataCategory; 14] = [
    DataCategory::Prompts,
    DataCategory::Outputs,
    DataCategory::Artifacts,
    DataCategory::Caches,
    DataCategory::Traces,
    DataCategory::Logs,
    DataCategory::Receipts,
    DataCategory::LedgerEvents,
    DataCategory::Recommendations,
    DataCategory::Exports,
    DataCategory::ProviderCopies,
    DataCategory::AuditEvents,
    DataCategory::ContextNodes,
    DataCategory::UsageRecords,
];

async fn insert_policy(
    tx: &mut Transaction<'_, Postgres>,
    policy: &LifecyclePolicy,
) -> Result<(), LifecycleStoreError> {
    sqlx::query(
        "INSERT INTO lifecycle_policies \
         (id, organization_id, category, action, retain_days, delete_after, description, \
          created_at, updated_at, version, active) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
    )
    .bind(&policy.id)
    .bind(policy.organization_id.0)
    .bind(data_category_to_str(policy.category))
    .bind(retention_action_to_str(policy.action))
    .bind(policy.retain_days)
    .bind(policy.delete_after)
    .bind(&policy.description)
    .bind(policy.created_at)
    .bind(policy.updated_at)
    .bind(policy.version)
    .bind(policy.active)
    .execute(&mut **tx)
    .await?;
    Ok(())
}

fn validate_policy(policy: &LifecyclePolicy) -> Result<(), LifecycleStoreError> {
    if policy.id.trim().is_empty()
        || policy.version < 1
        || policy.retain_days.is_some_and(|d| d < 0)
    {
        return Err(LifecycleStoreError::InvalidValue(
            "policy id, positive version, and non-negative retention are required".into(),
        ));
    }
    Ok(())
}

async fn category_stats(
    tx: &mut Transaction<'_, Postgres>,
    org_id: OrganizationId,
    category: DataCategory,
) -> Result<(i64, Option<i64>, i64), LifecycleStoreError> {
    let (count, size_bytes, legal_holds) = match category {
        DataCategory::Prompts => artifact_stats(tx, org_id, Some("request")).await?,
        DataCategory::Outputs => artifact_stats(tx, org_id, Some("response")).await?,
        DataCategory::Artifacts => artifact_stats(tx, org_id, None).await?,
        DataCategory::Receipts => text_table_count(tx, "run_receipts", org_id).await?,
        DataCategory::LedgerEvents => text_table_count(tx, "ledger_events", org_id).await?,
        DataCategory::Recommendations => text_table_count(tx, "model_observations", org_id).await?,
        DataCategory::Exports => uuid_table_count(tx, "export_requests", org_id).await?,
        DataCategory::AuditEvents => text_table_count(tx, "iam_audit_log", org_id).await?,
        DataCategory::ContextNodes => artifact_stats(tx, org_id, Some("context")).await?,
        DataCategory::UsageRecords => text_table_count(tx, "usage_records", org_id).await?,
        DataCategory::Caches
        | DataCategory::Traces
        | DataCategory::Logs
        | DataCategory::ProviderCopies => (0, None, 0),
    };
    Ok((count, size_bytes, legal_holds))
}

async fn artifact_stats(
    tx: &mut Transaction<'_, Postgres>,
    org_id: OrganizationId,
    artifact_type: Option<&str>,
) -> Result<(i64, Option<i64>, i64), LifecycleStoreError> {
    let row = sqlx::query(
        "SELECT COUNT(*)::bigint AS count, COALESCE(SUM(size_bytes), 0)::bigint AS size_bytes, \
                COUNT(*) FILTER (WHERE legal_hold)::bigint AS legal_holds \
         FROM artifacts WHERE organization_id = $1 AND deleted_at IS NULL \
         AND ($2::text IS NULL OR artifact_type = $2)",
    )
    .bind(org_id.0)
    .bind(artifact_type)
    .fetch_one(&mut **tx)
    .await?;
    Ok((
        row.try_get("count")?,
        Some(row.try_get("size_bytes")?),
        row.try_get("legal_holds")?,
    ))
}

async fn text_table_count(
    tx: &mut Transaction<'_, Postgres>,
    table: &str,
    org_id: OrganizationId,
) -> Result<(i64, Option<i64>, i64), LifecycleStoreError> {
    let query = format!("SELECT COUNT(*)::bigint FROM {table} WHERE organization_id = $1");
    let count = sqlx::query_scalar(&query)
        .bind(org_id.0.to_string())
        .fetch_one(&mut **tx)
        .await?;
    Ok((count, None, 0))
}

async fn uuid_table_count(
    tx: &mut Transaction<'_, Postgres>,
    table: &str,
    org_id: OrganizationId,
) -> Result<(i64, Option<i64>, i64), LifecycleStoreError> {
    let query = format!("SELECT COUNT(*)::bigint FROM {table} WHERE organization_id = $1");
    let count = sqlx::query_scalar(&query)
        .bind(org_id.0)
        .fetch_one(&mut **tx)
        .await?;
    Ok((count, None, 0))
}

fn row_to_policy(row: &sqlx::postgres::PgRow) -> Result<LifecyclePolicy, LifecycleStoreError> {
    Ok(LifecyclePolicy {
        id: row.try_get("id")?,
        organization_id: OrganizationId(row.try_get("organization_id")?),
        category: str_to_data_category(&row.try_get::<String, _>("category")?)?,
        action: str_to_retention_action(&row.try_get::<String, _>("action")?)?,
        retain_days: row.try_get("retain_days")?,
        delete_after: row.try_get("delete_after")?,
        description: row.try_get("description")?,
        created_at: row.try_get("created_at")?,
        updated_at: row.try_get("updated_at")?,
        version: row.try_get("version")?,
        active: row.try_get("active")?,
    })
}

fn row_to_deletion_job(row: &sqlx::postgres::PgRow) -> Result<DeletionJob, LifecycleStoreError> {
    Ok(DeletionJob {
        id: row.try_get("id")?,
        organization_id: OrganizationId(row.try_get("organization_id")?),
        category: str_to_data_category(&row.try_get::<String, _>("category")?)?,
        status: str_to_deletion_status(&row.try_get::<String, _>("status")?)?,
        started_at: row.try_get("started_at")?,
        completed_at: row.try_get("completed_at")?,
        items_total: row.try_get("items_total")?,
        items_deleted: row.try_get("items_deleted")?,
        items_failed: row.try_get("items_failed")?,
        items_skipped_legal_hold: row.try_get("items_skipped_legal_hold")?,
        error: row.try_get("error")?,
        created_at: row.try_get("created_at")?,
        created_by: PrincipalId(row.try_get("created_by")?),
    })
}

fn row_to_export_request(
    row: &sqlx::postgres::PgRow,
) -> Result<ExportRequest, LifecycleStoreError> {
    let categories: serde_json::Value = row.try_get("categories")?;
    Ok(ExportRequest {
        id: row.try_get("id")?,
        organization_id: OrganizationId(row.try_get("organization_id")?),
        requested_by: PrincipalId(row.try_get("requested_by")?),
        categories: serde_json::from_value(categories)?,
        status: str_to_export_status(&row.try_get::<String, _>("status")?)?,
        artifact_id: row.try_get("artifact_id")?,
        created_at: row.try_get("created_at")?,
        completed_at: row.try_get("completed_at")?,
        expires_at: row.try_get("expires_at")?,
    })
}

fn row_to_tombstone(row: &sqlx::postgres::PgRow) -> Result<Tombstone, LifecycleStoreError> {
    Ok(Tombstone {
        id: row.try_get("id")?,
        organization_id: OrganizationId(row.try_get("organization_id")?),
        original_table: row.try_get("original_table")?,
        original_id: row.try_get("original_id")?,
        deleted_at: row.try_get("deleted_at")?,
        deleted_by: PrincipalId(row.try_get("deleted_by")?),
        deletion_job_id: row.try_get("deletion_job_id")?,
        reason: row.try_get("reason")?,
    })
}

fn ensure_job_updated(rows: u64) -> Result<(), LifecycleStoreError> {
    if rows == 0 {
        Err(LifecycleStoreError::DeletionJobNotFound)
    } else {
        Ok(())
    }
}

fn category_location(category: DataCategory) -> &'static str {
    match category {
        DataCategory::Prompts | DataCategory::Outputs | DataCategory::Artifacts => {
            "postgresql:artifacts+s3:artifact-bytes"
        }
        DataCategory::Caches => "redis:cache",
        DataCategory::Traces => "telemetry:traces",
        DataCategory::Logs => "telemetry:logs",
        DataCategory::Receipts => "postgresql:run_receipts",
        DataCategory::LedgerEvents => "postgresql:ledger_events",
        DataCategory::Recommendations => "postgresql:model_observations",
        DataCategory::Exports => "postgresql:export_requests+s3:artifact-bytes",
        DataCategory::ProviderCopies => "third-party:provider-copies",
        DataCategory::AuditEvents => "postgresql:iam_audit_log",
        DataCategory::ContextNodes => "postgresql:artifacts(context)",
        DataCategory::UsageRecords => "postgresql:usage_records",
    }
}

fn data_category_to_str(value: DataCategory) -> &'static str {
    match value {
        DataCategory::Prompts => "prompts",
        DataCategory::Outputs => "outputs",
        DataCategory::Artifacts => "artifacts",
        DataCategory::Caches => "caches",
        DataCategory::Traces => "traces",
        DataCategory::Logs => "logs",
        DataCategory::Receipts => "receipts",
        DataCategory::LedgerEvents => "ledger_events",
        DataCategory::Recommendations => "recommendations",
        DataCategory::Exports => "exports",
        DataCategory::ProviderCopies => "provider_copies",
        DataCategory::AuditEvents => "audit_events",
        DataCategory::ContextNodes => "context_nodes",
        DataCategory::UsageRecords => "usage_records",
    }
}

fn str_to_data_category(value: &str) -> Result<DataCategory, LifecycleStoreError> {
    match value {
        "prompts" => Ok(DataCategory::Prompts),
        "outputs" => Ok(DataCategory::Outputs),
        "artifacts" => Ok(DataCategory::Artifacts),
        "caches" => Ok(DataCategory::Caches),
        "traces" => Ok(DataCategory::Traces),
        "logs" => Ok(DataCategory::Logs),
        "receipts" => Ok(DataCategory::Receipts),
        "ledger_events" => Ok(DataCategory::LedgerEvents),
        "recommendations" => Ok(DataCategory::Recommendations),
        "exports" => Ok(DataCategory::Exports),
        "provider_copies" => Ok(DataCategory::ProviderCopies),
        "audit_events" => Ok(DataCategory::AuditEvents),
        "context_nodes" => Ok(DataCategory::ContextNodes),
        "usage_records" => Ok(DataCategory::UsageRecords),
        invalid => Err(LifecycleStoreError::InvalidValue(invalid.into())),
    }
}

fn retention_action_to_str(value: RetentionAction) -> &'static str {
    match value {
        RetentionAction::Retain => "retain",
        RetentionAction::Delete => "delete",
        RetentionAction::CryptographicDelete => "cryptographic_delete",
        RetentionAction::Archive => "archive",
        RetentionAction::Export => "export",
    }
}

fn str_to_retention_action(value: &str) -> Result<RetentionAction, LifecycleStoreError> {
    match value {
        "retain" => Ok(RetentionAction::Retain),
        "delete" => Ok(RetentionAction::Delete),
        "cryptographic_delete" => Ok(RetentionAction::CryptographicDelete),
        "archive" => Ok(RetentionAction::Archive),
        "export" => Ok(RetentionAction::Export),
        invalid => Err(LifecycleStoreError::InvalidValue(invalid.into())),
    }
}

fn deletion_status_to_str(value: DeletionStatus) -> &'static str {
    match value {
        DeletionStatus::Pending => "pending",
        DeletionStatus::InProgress => "in_progress",
        DeletionStatus::Completed => "completed",
        DeletionStatus::Failed => "failed",
        DeletionStatus::PartiallyCompleted => "partially_completed",
        DeletionStatus::Cancelled => "cancelled",
    }
}

fn str_to_deletion_status(value: &str) -> Result<DeletionStatus, LifecycleStoreError> {
    match value {
        "pending" => Ok(DeletionStatus::Pending),
        "in_progress" => Ok(DeletionStatus::InProgress),
        "completed" => Ok(DeletionStatus::Completed),
        "failed" => Ok(DeletionStatus::Failed),
        "partially_completed" => Ok(DeletionStatus::PartiallyCompleted),
        "cancelled" => Ok(DeletionStatus::Cancelled),
        invalid => Err(LifecycleStoreError::InvalidValue(invalid.into())),
    }
}

fn export_status_to_str(value: ExportStatus) -> &'static str {
    match value {
        ExportStatus::Pending => "pending",
        ExportStatus::InProgress => "in_progress",
        ExportStatus::Completed => "completed",
        ExportStatus::Failed => "failed",
        ExportStatus::Expired => "expired",
    }
}

fn str_to_export_status(value: &str) -> Result<ExportStatus, LifecycleStoreError> {
    match value {
        "pending" => Ok(ExportStatus::Pending),
        "in_progress" => Ok(ExportStatus::InProgress),
        "completed" => Ok(ExportStatus::Completed),
        "failed" => Ok(ExportStatus::Failed),
        "expired" => Ok(ExportStatus::Expired),
        invalid => Err(LifecycleStoreError::InvalidValue(invalid.into())),
    }
}
