//! Canonical artifact identity, integrity, storage, and lifecycle metadata.

use chrono::{DateTime, Utc};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::execution::RunId;
use crate::iam::{OrganizationId, PrincipalId};

/// Database-authoritative identity for an artifact.
pub type ArtifactId = Uuid;

/// Lowercase hexadecimal SHA-256 digest of artifact content.
pub type ContentHash = String;

/// Semantic role of an artifact in a governed run.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ArtifactType {
    Request,
    Response,
    ProviderLog,
    ToolOutput,
    VerificationLog,
    Media,
    TestLog,
    Evidence,
    Receipt,
    Context,
}

/// Object storage implementation holding artifact bytes.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum StorageBackend {
    Local,
    S3,
    Minio,
}

/// Encryption state recorded with an artifact.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EncryptionInfo {
    pub encrypted: bool,
    pub key_id: Option<String>,
    pub algorithm: Option<String>,
}

/// Retention controls copied onto an artifact at creation time.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RetentionPolicy {
    pub policy_id: String,
    pub retain_days: Option<i32>,
    pub delete_after: Option<DateTime<Utc>>,
}

/// Database-authoritative artifact metadata and authorization scope.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ArtifactMetadata {
    pub id: ArtifactId,
    pub organization_id: OrganizationId,
    pub artifact_type: ArtifactType,
    pub content_hash: ContentHash,
    pub content_type: String,
    pub size_bytes: i64,
    pub storage_key: String,
    pub storage_backend: StorageBackend,
    pub encryption: EncryptionInfo,
    pub retention_policy: RetentionPolicy,
    pub created_at: DateTime<Utc>,
    pub created_by: PrincipalId,
    pub run_id: Option<RunId>,
    pub deleted_at: Option<DateTime<Utc>>,
    pub delete_after: Option<DateTime<Utc>>,
    pub legal_hold: bool,
}

/// Immutable identity and digest binding embedded in ledgers and receipts.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ArtifactRef {
    pub artifact_id: ArtifactId,
    pub content_hash: ContentHash,
    pub artifact_type: ArtifactType,
    pub size_bytes: i64,
}

/// Fail-closed artifact operation errors.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ArtifactError {
    NotFound,
    Unauthorized,
    StorageError(String),
    IntegrityError {
        expected: ContentHash,
        actual: ContentHash,
    },
    SizeLimitExceeded {
        limit: i64,
        actual: i64,
    },
    ContentTypeNotAllowed(String),
    RetentionViolation(String),
    LegalHoldBlocks,
}
