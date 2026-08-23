//! Content-addressed artifact bytes and database-authoritative metadata.

use std::path::{Component, Path, PathBuf};
use std::sync::Arc;

use async_trait::async_trait;
use chrono::{DateTime, Duration, Utc};
use noerelay_core::artifacts::{
    ArtifactError, ArtifactId, ArtifactMetadata, ArtifactType, ContentHash, EncryptionInfo,
    RetentionPolicy, StorageBackend,
};
use noerelay_core::execution::RunId;
use noerelay_core::iam::{OrganizationId, PrincipalId};
use sha2::{Digest, Sha256};
use sqlx::{PgPool, Postgres, Row, Transaction};
use uuid::Uuid;

use crate::iam::set_tenant_context;

/// Local/test-only maximum artifact size (100 MiB).
pub const MAX_ARTIFACT_SIZE_BYTES: i64 = 100 * 1024 * 1024;

const METADATA_COLUMNS: &str = "id, organization_id, artifact_type, content_hash, \
    content_type, size_bytes, storage_key, storage_backend, encrypted, \
    encryption_key_id, encryption_algorithm, retention_policy_id, retain_days, \
    delete_after, created_at, created_by, run_id, deleted_at, legal_hold";

/// Byte storage behind database-authoritative artifact identity and access control.
#[async_trait]
pub trait ArtifactStorage: Send + Sync {
    async fn store(&self, key: &str, data: &[u8], content_type: &str) -> Result<(), ArtifactError>;
    async fn retrieve(&self, key: &str) -> Result<Vec<u8>, ArtifactError>;
    async fn delete(&self, key: &str) -> Result<(), ArtifactError>;
    async fn exists(&self, key: &str) -> Result<bool, ArtifactError>;
}

/// Local filesystem storage for development and tests.
pub struct LocalArtifactStorage {
    base_path: PathBuf,
}

impl LocalArtifactStorage {
    pub fn new(base_path: PathBuf) -> Self {
        Self { base_path }
    }

    fn path_for(&self, key: &str) -> Result<PathBuf, ArtifactError> {
        let key_path = Path::new(key);
        if key_path.is_absolute()
            || key_path.components().any(|component| {
                matches!(
                    component,
                    Component::ParentDir | Component::RootDir | Component::Prefix(_)
                )
            })
        {
            return Err(ArtifactError::StorageError(
                "artifact storage key escapes the configured base path".into(),
            ));
        }
        Ok(self.base_path.join(key_path))
    }
}

#[async_trait]
impl ArtifactStorage for LocalArtifactStorage {
    async fn store(
        &self,
        key: &str,
        data: &[u8],
        _content_type: &str,
    ) -> Result<(), ArtifactError> {
        let path = self.path_for(key)?;
        let parent = path.parent().ok_or_else(|| {
            ArtifactError::StorageError("artifact storage key has no parent".into())
        })?;
        tokio::fs::create_dir_all(parent)
            .await
            .map_err(storage_error)?;
        tokio::fs::write(path, data).await.map_err(storage_error)
    }

    async fn retrieve(&self, key: &str) -> Result<Vec<u8>, ArtifactError> {
        let path = self.path_for(key)?;
        match tokio::fs::read(path).await {
            Ok(data) => Ok(data),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                Err(ArtifactError::NotFound)
            }
            Err(error) => Err(storage_error(error)),
        }
    }

    async fn delete(&self, key: &str) -> Result<(), ArtifactError> {
        let path = self.path_for(key)?;
        match tokio::fs::remove_file(path).await {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(storage_error(error)),
        }
    }

    async fn exists(&self, key: &str) -> Result<bool, ArtifactError> {
        let path = self.path_for(key)?;
        tokio::fs::try_exists(path).await.map_err(storage_error)
    }
}

/// Coordinates tenant-scoped metadata with an object storage implementation.
pub struct ArtifactRepository {
    pool: PgPool,
    storage: Arc<dyn ArtifactStorage>,
}

impl ArtifactRepository {
    pub fn new(pool: PgPool, storage: Arc<dyn ArtifactStorage>) -> Self {
        Self { pool, storage }
    }

    #[allow(clippy::too_many_arguments)]
    pub async fn store_artifact(
        &self,
        org_id: OrganizationId,
        artifact_type: ArtifactType,
        content_type: &str,
        data: &[u8],
        created_by: PrincipalId,
        run_id: Option<RunId>,
        retention: Option<RetentionPolicy>,
    ) -> Result<ArtifactMetadata, ArtifactError> {
        let size_bytes = i64::try_from(data.len()).unwrap_or(i64::MAX);
        if size_bytes > MAX_ARTIFACT_SIZE_BYTES {
            return Err(ArtifactError::SizeLimitExceeded {
                limit: MAX_ARTIFACT_SIZE_BYTES,
                actual: size_bytes,
            });
        }
        if content_type.trim().is_empty() {
            return Err(ArtifactError::ContentTypeNotAllowed(
                content_type.to_owned(),
            ));
        }

        let content_hash = sha256(data);
        let mut tx = self.tenant_transaction(org_id, Some(created_by)).await?;
        let existing_query = format!(
            "SELECT {METADATA_COLUMNS} FROM artifacts \
             WHERE content_hash = $1 AND organization_id = $2 AND deleted_at IS NULL"
        );
        if let Some(row) = sqlx::query(&existing_query)
            .bind(&content_hash)
            .bind(org_id.0)
            .fetch_optional(&mut *tx)
            .await
            .map_err(database_error)?
        {
            let metadata = row_to_metadata(&row)?;
            tx.commit().await.map_err(database_error)?;
            return Ok(metadata);
        }

        let now = Utc::now();
        let retention = normalize_retention(retention, now)?;
        let storage_key = format!("org/{}/{content_hash}", org_id.0);
        self.storage.store(&storage_key, data, content_type).await?;

        let id = Uuid::new_v4();
        let query = format!(
            "INSERT INTO artifacts (id, organization_id, artifact_type, content_hash, \
             content_type, size_bytes, storage_key, storage_backend, encrypted, \
             retention_policy_id, retain_days, delete_after, created_at, created_by, run_id) \
             VALUES ($1, $2, $3, $4, $5, $6, $7, 'local', false, $8, $9, $10, $11, $12, $13) \
             ON CONFLICT (content_hash, organization_id) DO UPDATE SET content_hash = EXCLUDED.content_hash \
             RETURNING {METADATA_COLUMNS}"
        );
        let row = sqlx::query(&query)
            .bind(id)
            .bind(org_id.0)
            .bind(artifact_type_to_str(artifact_type))
            .bind(&content_hash)
            .bind(content_type)
            .bind(size_bytes)
            .bind(&storage_key)
            .bind(&retention.policy_id)
            .bind(retention.retain_days)
            .bind(retention.delete_after)
            .bind(now)
            .bind(created_by.0)
            .bind(run_id.map(|id| id.0))
            .fetch_one(&mut *tx)
            .await
            .map_err(database_error)?;
        tx.commit().await.map_err(database_error)?;
        row_to_metadata(&row)
    }

    pub async fn retrieve_artifact(
        &self,
        artifact_id: ArtifactId,
        org_id: OrganizationId,
    ) -> Result<(ArtifactMetadata, Vec<u8>), ArtifactError> {
        let metadata = self.get_metadata(artifact_id, org_id).await?;
        let data = self.storage.retrieve(&metadata.storage_key).await?;
        let actual = sha256(&data);
        if actual != metadata.content_hash {
            return Err(ArtifactError::IntegrityError {
                expected: metadata.content_hash,
                actual,
            });
        }
        Ok((metadata, data))
    }

    pub async fn get_metadata(
        &self,
        artifact_id: ArtifactId,
        org_id: OrganizationId,
    ) -> Result<ArtifactMetadata, ArtifactError> {
        let mut tx = self.tenant_transaction(org_id, None).await?;
        let query = format!(
            "SELECT {METADATA_COLUMNS} FROM artifacts WHERE id = $1 AND deleted_at IS NULL"
        );
        let row = sqlx::query(&query)
            .bind(artifact_id)
            .fetch_optional(&mut *tx)
            .await
            .map_err(database_error)?;
        tx.commit().await.map_err(database_error)?;
        row.map(|row| row_to_metadata(&row))
            .transpose()?
            .ok_or(ArtifactError::NotFound)
    }

    pub async fn list_artifacts(
        &self,
        org_id: OrganizationId,
        artifact_type: Option<ArtifactType>,
        run_id: Option<RunId>,
        limit: i32,
        offset: i32,
    ) -> Result<Vec<ArtifactMetadata>, ArtifactError> {
        let mut tx = self.tenant_transaction(org_id, None).await?;
        let query = format!(
            "SELECT {METADATA_COLUMNS} FROM artifacts \
             WHERE deleted_at IS NULL AND ($1::text IS NULL OR artifact_type = $1) \
             AND ($2::uuid IS NULL OR run_id = $2) \
             ORDER BY created_at DESC, id DESC LIMIT $3 OFFSET $4"
        );
        let rows = sqlx::query(&query)
            .bind(artifact_type.map(artifact_type_to_str))
            .bind(run_id.map(|id| id.0))
            .bind(limit.clamp(1, 1000))
            .bind(offset.max(0))
            .fetch_all(&mut *tx)
            .await
            .map_err(database_error)?;
        tx.commit().await.map_err(database_error)?;
        rows.iter().map(row_to_metadata).collect()
    }

    pub async fn delete_artifact(
        &self,
        artifact_id: ArtifactId,
        org_id: OrganizationId,
    ) -> Result<(), ArtifactError> {
        let mut tx = self.tenant_transaction(org_id, None).await?;
        let query = format!(
            "SELECT {METADATA_COLUMNS} FROM artifacts \
             WHERE id = $1 AND deleted_at IS NULL FOR UPDATE"
        );
        let row = sqlx::query(&query)
            .bind(artifact_id)
            .fetch_optional(&mut *tx)
            .await
            .map_err(database_error)?
            .ok_or(ArtifactError::NotFound)?;
        let metadata = row_to_metadata(&row)?;
        if metadata.legal_hold {
            return Err(ArtifactError::LegalHoldBlocks);
        }
        match metadata.delete_after {
            Some(delete_after) if delete_after <= Utc::now() => {}
            Some(delete_after) => {
                return Err(ArtifactError::RetentionViolation(format!(
                    "artifact is retained until {delete_after}"
                )));
            }
            None => {
                return Err(ArtifactError::RetentionViolation(
                    "artifact retention policy retains it forever".into(),
                ));
            }
        }
        sqlx::query("UPDATE artifacts SET deleted_at = now() WHERE id = $1")
            .bind(artifact_id)
            .execute(&mut *tx)
            .await
            .map_err(database_error)?;
        tx.commit().await.map_err(database_error)?;
        self.storage.delete(&metadata.storage_key).await
    }

    pub async fn apply_legal_hold(
        &self,
        artifact_id: ArtifactId,
        org_id: OrganizationId,
    ) -> Result<(), ArtifactError> {
        self.set_legal_hold(artifact_id, org_id, true).await
    }

    pub async fn release_legal_hold(
        &self,
        artifact_id: ArtifactId,
        org_id: OrganizationId,
    ) -> Result<(), ArtifactError> {
        self.set_legal_hold(artifact_id, org_id, false).await
    }

    pub async fn cleanup_expired(&self) -> Result<i32, ArtifactError> {
        let query = format!(
            "SELECT {METADATA_COLUMNS} FROM artifacts WHERE deleted_at IS NULL \
             AND legal_hold = false AND delete_after <= now() ORDER BY delete_after LIMIT 1000"
        );
        let rows = sqlx::query(&query)
            .fetch_all(&self.pool)
            .await
            .map_err(database_error)?;
        let mut count = 0_i32;
        for row in rows {
            let metadata = row_to_metadata(&row)?;
            self.storage.delete(&metadata.storage_key).await?;
            let result = sqlx::query(
                "UPDATE artifacts SET deleted_at = now() \
                 WHERE id = $1 AND deleted_at IS NULL AND legal_hold = false",
            )
            .bind(metadata.id)
            .execute(&self.pool)
            .await
            .map_err(database_error)?;
            count += i32::try_from(result.rows_affected()).unwrap_or(i32::MAX);
        }
        Ok(count)
    }

    pub async fn verify_integrity(
        &self,
        artifact_id: ArtifactId,
        org_id: OrganizationId,
    ) -> Result<bool, ArtifactError> {
        let metadata = self.get_metadata(artifact_id, org_id).await?;
        let data = self.storage.retrieve(&metadata.storage_key).await?;
        Ok(sha256(&data) == metadata.content_hash)
    }

    async fn set_legal_hold(
        &self,
        artifact_id: ArtifactId,
        org_id: OrganizationId,
        legal_hold: bool,
    ) -> Result<(), ArtifactError> {
        let mut tx = self.tenant_transaction(org_id, None).await?;
        let result = sqlx::query(
            "UPDATE artifacts SET legal_hold = $1 WHERE id = $2 AND deleted_at IS NULL",
        )
        .bind(legal_hold)
        .bind(artifact_id)
        .execute(&mut *tx)
        .await
        .map_err(database_error)?;
        tx.commit().await.map_err(database_error)?;
        if result.rows_affected() == 0 {
            return Err(ArtifactError::NotFound);
        }
        Ok(())
    }

    async fn tenant_transaction(
        &self,
        org_id: OrganizationId,
        principal_id: Option<PrincipalId>,
    ) -> Result<Transaction<'_, Postgres>, ArtifactError> {
        let mut tx = self.pool.begin().await.map_err(database_error)?;
        set_tenant_context(&mut tx, &org_id.0.to_string(), principal_id)
            .await
            .map_err(database_error)?;
        Ok(tx)
    }
}

fn normalize_retention(
    retention: Option<RetentionPolicy>,
    created_at: DateTime<Utc>,
) -> Result<RetentionPolicy, ArtifactError> {
    let mut retention = retention.unwrap_or_else(|| RetentionPolicy {
        policy_id: "retain-forever".into(),
        retain_days: None,
        delete_after: None,
    });
    if retention.policy_id.trim().is_empty() {
        return Err(ArtifactError::RetentionViolation(
            "retention policy ID cannot be empty".into(),
        ));
    }
    if let Some(days) = retention.retain_days {
        if days < 0 {
            return Err(ArtifactError::RetentionViolation(
                "retain_days cannot be negative".into(),
            ));
        }
        let minimum = created_at + Duration::days(i64::from(days));
        if retention.delete_after.is_some_and(|date| date < minimum) {
            return Err(ArtifactError::RetentionViolation(
                "delete_after precedes the minimum retention period".into(),
            ));
        }
        retention.delete_after.get_or_insert(minimum);
    }
    Ok(retention)
}

fn row_to_metadata(row: &sqlx::postgres::PgRow) -> Result<ArtifactMetadata, ArtifactError> {
    let artifact_type: String = row.try_get("artifact_type").map_err(database_error)?;
    let storage_backend: String = row.try_get("storage_backend").map_err(database_error)?;
    let delete_after = row.try_get("delete_after").map_err(database_error)?;
    Ok(ArtifactMetadata {
        id: row.try_get("id").map_err(database_error)?,
        organization_id: OrganizationId(row.try_get("organization_id").map_err(database_error)?),
        artifact_type: str_to_artifact_type(&artifact_type)?,
        content_hash: row.try_get("content_hash").map_err(database_error)?,
        content_type: row.try_get("content_type").map_err(database_error)?,
        size_bytes: row.try_get("size_bytes").map_err(database_error)?,
        storage_key: row.try_get("storage_key").map_err(database_error)?,
        storage_backend: str_to_storage_backend(&storage_backend)?,
        encryption: EncryptionInfo {
            encrypted: row.try_get("encrypted").map_err(database_error)?,
            key_id: row.try_get("encryption_key_id").map_err(database_error)?,
            algorithm: row
                .try_get("encryption_algorithm")
                .map_err(database_error)?,
        },
        retention_policy: RetentionPolicy {
            policy_id: row
                .try_get::<Option<String>, _>("retention_policy_id")
                .map_err(database_error)?
                .unwrap_or_else(|| "retain-forever".into()),
            retain_days: row.try_get("retain_days").map_err(database_error)?,
            delete_after,
        },
        created_at: row.try_get("created_at").map_err(database_error)?,
        created_by: PrincipalId(row.try_get("created_by").map_err(database_error)?),
        run_id: row
            .try_get::<Option<Uuid>, _>("run_id")
            .map_err(database_error)?
            .map(RunId),
        deleted_at: row.try_get("deleted_at").map_err(database_error)?,
        delete_after,
        legal_hold: row.try_get("legal_hold").map_err(database_error)?,
    })
}

fn artifact_type_to_str(value: ArtifactType) -> &'static str {
    match value {
        ArtifactType::Request => "request",
        ArtifactType::Response => "response",
        ArtifactType::ProviderLog => "provider_log",
        ArtifactType::ToolOutput => "tool_output",
        ArtifactType::VerificationLog => "verification_log",
        ArtifactType::Media => "media",
        ArtifactType::TestLog => "test_log",
        ArtifactType::Evidence => "evidence",
        ArtifactType::Receipt => "receipt",
        ArtifactType::Context => "context",
    }
}

fn str_to_artifact_type(value: &str) -> Result<ArtifactType, ArtifactError> {
    match value {
        "request" => Ok(ArtifactType::Request),
        "response" => Ok(ArtifactType::Response),
        "provider_log" => Ok(ArtifactType::ProviderLog),
        "tool_output" => Ok(ArtifactType::ToolOutput),
        "verification_log" => Ok(ArtifactType::VerificationLog),
        "media" => Ok(ArtifactType::Media),
        "test_log" => Ok(ArtifactType::TestLog),
        "evidence" => Ok(ArtifactType::Evidence),
        "receipt" => Ok(ArtifactType::Receipt),
        "context" => Ok(ArtifactType::Context),
        invalid => Err(ArtifactError::StorageError(format!(
            "database contains invalid artifact type: {invalid}"
        ))),
    }
}

fn str_to_storage_backend(value: &str) -> Result<StorageBackend, ArtifactError> {
    match value {
        "local" => Ok(StorageBackend::Local),
        "s3" => Ok(StorageBackend::S3),
        "minio" => Ok(StorageBackend::Minio),
        invalid => Err(ArtifactError::StorageError(format!(
            "database contains invalid storage backend: {invalid}"
        ))),
    }
}

fn sha256(data: &[u8]) -> ContentHash {
    format!("{:x}", Sha256::digest(data))
}

fn storage_error(error: std::io::Error) -> ArtifactError {
    ArtifactError::StorageError(error.to_string())
}

fn database_error(error: sqlx::Error) -> ArtifactError {
    ArtifactError::StorageError(format!("artifact metadata operation failed: {error}"))
}
