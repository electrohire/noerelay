//! PostgreSQL persistence beneath the Rust authority boundary.

pub mod api_keys;
pub mod artifacts;
pub mod execution;
pub mod governance;
pub mod iam;
pub mod lifecycle;
pub mod registry;

use noerelay_core::{GovernanceSnapshot, LedgerEvent, SignedRunReceipt};
use serde::{Deserialize, Serialize};
use sqlx::{PgPool, Row, postgres::PgPoolOptions};
use thiserror::Error;

pub use api_keys::{ApiKeyRepository, ApiKeyStoreError};
pub use artifacts::{ArtifactRepository, ArtifactStorage, LocalArtifactStorage};
pub use execution::{ExecutionRepository, ExecutionStoreError};
pub use governance::{GovernanceRepository, GovernanceStoreError};
pub use iam::{IamRepository, IamStoreError};
pub use lifecycle::{LifecycleRepository, LifecycleStoreError};
pub use registry::{RegistryRepository, RegistryStoreError};

static MIGRATOR: sqlx::migrate::Migrator = sqlx::migrate!("./migrations");

#[derive(Debug, Error)]
pub enum StoreError {
    #[error("database operation failed: {0}")]
    Database(#[from] sqlx::Error),
    #[error("database migration failed: {0}")]
    Migration(#[from] sqlx::migrate::MigrateError),
    #[error("authority serialization failed: {0}")]
    Serialization(#[from] serde_json::Error),
    #[error("authority storage version conflict")]
    VersionConflict,
    #[error("stored authority snapshot is invalid: {0}")]
    InvalidSnapshot(String),
}

#[derive(Debug, Clone)]
pub struct StoredAuthority {
    pub storage_version: i64,
    pub snapshot: GovernanceSnapshot,
}

#[derive(Clone)]
pub struct PostgresAuthorityStore {
    pool: PgPool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CostRollupRow {
    pub organization_id: String,
    pub project_id: String,
    pub user_id: String,
    pub requests: u64,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub cost_microusd: u64,
}

impl PostgresAuthorityStore {
    pub async fn connect(database_url: &str, maximum_connections: u32) -> Result<Self, StoreError> {
        let pool = PgPoolOptions::new()
            .max_connections(maximum_connections)
            .connect(database_url)
            .await?;
        MIGRATOR.run(&pool).await?;
        Ok(Self { pool })
    }

    pub async fn health(&self) -> Result<(), StoreError> {
        sqlx::query("SELECT 1").execute(&self.pool).await?;
        Ok(())
    }

    pub async fn load(
        &self,
        organization_id: &str,
        project_id: &str,
    ) -> Result<Option<StoredAuthority>, StoreError> {
        let mut transaction = self.pool.begin().await?;
        set_scope(&mut transaction, organization_id).await?;
        let row = sqlx::query(
            "SELECT storage_version, snapshot FROM authority_snapshots \
             WHERE organization_id = $1 AND project_id = $2",
        )
        .bind(organization_id)
        .bind(project_id)
        .fetch_optional(&mut *transaction)
        .await?;
        transaction.commit().await?;
        row.map(|row| {
            let storage_version: i64 = row.try_get("storage_version")?;
            let snapshot_value: serde_json::Value = row.try_get("snapshot")?;
            let snapshot: GovernanceSnapshot = serde_json::from_value(snapshot_value)?;
            noerelay_core::GovernanceRuntime::from_snapshot(snapshot.clone())
                .map_err(|error| StoreError::InvalidSnapshot(error.to_string()))?;
            Ok(StoredAuthority {
                storage_version,
                snapshot,
            })
        })
        .transpose()
    }

    pub async fn save(
        &self,
        organization_id: &str,
        project_id: &str,
        expected_version: i64,
        snapshot: &GovernanceSnapshot,
        receipt: Option<&SignedRunReceipt>,
    ) -> Result<i64, StoreError> {
        let mut transaction = self.pool.begin().await?;
        set_scope(&mut transaction, organization_id).await?;
        sqlx::query(
            "INSERT INTO organizations (organization_id) VALUES ($1) ON CONFLICT DO NOTHING",
        )
        .bind(organization_id)
        .execute(&mut *transaction)
        .await?;
        sqlx::query(
            "INSERT INTO projects (organization_id, project_id) VALUES ($1, $2) \
             ON CONFLICT DO NOTHING",
        )
        .bind(organization_id)
        .bind(project_id)
        .execute(&mut *transaction)
        .await?;
        let current = sqlx::query(
            "SELECT storage_version FROM authority_snapshots \
             WHERE organization_id = $1 AND project_id = $2 FOR UPDATE",
        )
        .bind(organization_id)
        .bind(project_id)
        .fetch_optional(&mut *transaction)
        .await?
        .map(|row| row.get::<i64, _>("storage_version"))
        .unwrap_or(0);
        if current != expected_version {
            return Err(StoreError::VersionConflict);
        }
        let next = current.checked_add(1).ok_or(StoreError::VersionConflict)?;
        let snapshot_value = serde_json::to_value(snapshot)?;
        sqlx::query(
            "INSERT INTO authority_snapshots \
             (organization_id, project_id, storage_version, snapshot) VALUES ($1, $2, $3, $4) \
             ON CONFLICT (organization_id, project_id) DO UPDATE SET \
             storage_version = EXCLUDED.storage_version, snapshot = EXCLUDED.snapshot, \
             updated_at = clock_timestamp()",
        )
        .bind(organization_id)
        .bind(project_id)
        .bind(next)
        .bind(snapshot_value)
        .execute(&mut *transaction)
        .await?;
        for event in snapshot.ledger.events() {
            insert_ledger_event(&mut transaction, event).await?;
        }
        if let Some(receipt) = receipt {
            let value = serde_json::to_value(receipt)?;
            let result = sqlx::query(
                "INSERT INTO run_receipts \
                 (organization_id, project_id, user_id, run_id, receipt_hash, receipt) \
                 VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT DO NOTHING",
            )
            .bind(&receipt.receipt.organization_id)
            .bind(&receipt.receipt.project_id)
            .bind(&receipt.receipt.user_id)
            .bind(&receipt.receipt.run_id)
            .bind(&receipt.receipt.receipt_hash)
            .bind(value)
            .execute(&mut *transaction)
            .await?;
            if result.rows_affected() == 0 {
                let stored: String = sqlx::query_scalar(
                    "SELECT receipt_hash FROM run_receipts \
                     WHERE organization_id = $1 AND run_id = $2",
                )
                .bind(&receipt.receipt.organization_id)
                .bind(&receipt.receipt.run_id)
                .fetch_one(&mut *transaction)
                .await?;
                if stored != receipt.receipt.receipt_hash {
                    return Err(StoreError::VersionConflict);
                }
            }
            sqlx::query(
                "INSERT INTO usage_records \
                 (organization_id, project_id, user_id, run_id, cost_microusd, source, \
                  input_tokens, output_tokens) \
                 VALUES ($1, $2, $3, $4, $5, $6, $7, $8) ON CONFLICT DO NOTHING",
            )
            .bind(&receipt.receipt.organization_id)
            .bind(&receipt.receipt.project_id)
            .bind(&receipt.receipt.user_id)
            .bind(&receipt.receipt.run_id)
            .bind(
                i64::try_from(receipt.receipt.actual_cost_microusd)
                    .map_err(|_| StoreError::VersionConflict)?,
            )
            .bind(&receipt.receipt.cost_source)
            .bind(
                i64::try_from(receipt.receipt.input_tokens)
                    .map_err(|_| StoreError::VersionConflict)?,
            )
            .bind(
                i64::try_from(receipt.receipt.output_tokens)
                    .map_err(|_| StoreError::VersionConflict)?,
            )
            .execute(&mut *transaction)
            .await?;
        }
        transaction.commit().await?;
        Ok(next)
    }

    pub async fn receipt(
        &self,
        organization_id: &str,
        run_id: &str,
    ) -> Result<Option<SignedRunReceipt>, StoreError> {
        let mut transaction = self.pool.begin().await?;
        set_scope(&mut transaction, organization_id).await?;
        let value = sqlx::query(
            "SELECT receipt FROM run_receipts WHERE organization_id = $1 AND run_id = $2",
        )
        .bind(organization_id)
        .bind(run_id)
        .fetch_optional(&mut *transaction)
        .await?
        .map(|row| row.try_get::<serde_json::Value, _>("receipt"))
        .transpose()?;
        transaction.commit().await?;
        value
            .map(serde_json::from_value)
            .transpose()
            .map_err(StoreError::from)
    }

    pub async fn cost_rollups(
        &self,
        organization_id: &str,
        project_id: Option<&str>,
    ) -> Result<Vec<CostRollupRow>, StoreError> {
        let mut transaction = self.pool.begin().await?;
        set_scope(&mut transaction, organization_id).await?;
        let rows = sqlx::query(
            "SELECT organization_id, project_id, user_id, COUNT(*) AS requests, \
                    SUM(input_tokens)::bigint AS input_tokens, \
                    SUM(output_tokens)::bigint AS output_tokens, \
                    SUM(cost_microusd)::bigint AS cost_microusd \
             FROM usage_records WHERE organization_id = $1 \
               AND ($2::text IS NULL OR project_id = $2) \
             GROUP BY organization_id, project_id, user_id \
             ORDER BY project_id, user_id",
        )
        .bind(organization_id)
        .bind(project_id)
        .fetch_all(&mut *transaction)
        .await?;
        transaction.commit().await?;
        rows.into_iter()
            .map(|row| {
                Ok(CostRollupRow {
                    organization_id: row.try_get("organization_id")?,
                    project_id: row.try_get("project_id")?,
                    user_id: row.try_get("user_id")?,
                    requests: u64::try_from(row.try_get::<i64, _>("requests")?)
                        .map_err(|_| StoreError::VersionConflict)?,
                    input_tokens: u64::try_from(row.try_get::<i64, _>("input_tokens")?)
                        .map_err(|_| StoreError::VersionConflict)?,
                    output_tokens: u64::try_from(row.try_get::<i64, _>("output_tokens")?)
                        .map_err(|_| StoreError::VersionConflict)?,
                    cost_microusd: u64::try_from(row.try_get::<i64, _>("cost_microusd")?)
                        .map_err(|_| StoreError::VersionConflict)?,
                })
            })
            .collect()
    }
}

async fn set_scope(
    transaction: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    organization_id: &str,
) -> Result<(), sqlx::Error> {
    sqlx::query("SELECT set_config('noerelay.organization_id', $1, true)")
        .bind(organization_id)
        .execute(&mut **transaction)
        .await?;
    Ok(())
}

async fn insert_ledger_event(
    transaction: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    event: &LedgerEvent,
) -> Result<(), StoreError> {
    let kind = serde_json::to_value(event.kind)?
        .as_str()
        .expect("ledger event kind serializes as a string")
        .to_owned();
    let result = sqlx::query(
        "INSERT INTO ledger_events \
         (organization_id, project_id, sequence, occurred_at_unix_ms, run_id, event_kind, \
          payload, previous_hash, event_hash) \
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) ON CONFLICT DO NOTHING",
    )
    .bind(&event.organization_id)
    .bind(&event.project_id)
    .bind(i64::try_from(event.sequence).map_err(|_| StoreError::VersionConflict)?)
    .bind(i64::try_from(event.occurred_at_unix_ms).map_err(|_| StoreError::VersionConflict)?)
    .bind(&event.run_id)
    .bind(kind)
    .bind(&event.payload)
    .bind(&event.previous_hash)
    .bind(&event.event_hash)
    .execute(&mut **transaction)
    .await?;
    if result.rows_affected() == 0 {
        let stored: String = sqlx::query_scalar(
            "SELECT event_hash FROM ledger_events WHERE organization_id = $1 AND sequence = $2",
        )
        .bind(&event.organization_id)
        .bind(i64::try_from(event.sequence).map_err(|_| StoreError::VersionConflict)?)
        .fetch_one(&mut **transaction)
        .await?;
        if stored != event.event_hash {
            return Err(StoreError::VersionConflict);
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    #[test]
    fn migration_enforces_tenant_and_append_only_invariants() {
        let migration = include_str!("../migrations/0001_authority.sql");
        assert!(migration.contains("FORCE ROW LEVEL SECURITY"));
        assert!(migration.contains("reject_ledger_mutation"));
        assert!(migration.contains("usage_records"));
        assert!(migration.contains("model_observations"));
        assert!(!migration.contains("double precision"));
    }
}
