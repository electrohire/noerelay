//! Local storage coverage and opt-in PostgreSQL integration coverage for ART-01.

use std::path::PathBuf;
use std::sync::Arc;

use chrono::Utc;
use noerelay_core::{ArtifactError, ArtifactType, OrganizationId, PrincipalId, RetentionPolicy};
use noerelay_store::artifacts::MAX_ARTIFACT_SIZE_BYTES;
use noerelay_store::{ArtifactRepository, ArtifactStorage, LocalArtifactStorage};
use sqlx::PgPool;
use uuid::Uuid;

struct TempDirectory(PathBuf);

impl TempDirectory {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!("noerelay-artifact-test-{}", Uuid::new_v4()));
        std::fs::create_dir_all(&path).unwrap();
        Self(path)
    }
}

impl Drop for TempDirectory {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn local_fixture() -> (TempDirectory, LocalArtifactStorage) {
    let directory = TempDirectory::new();
    let storage = LocalArtifactStorage::new(directory.0.clone());
    (directory, storage)
}

#[tokio::test]
async fn local_storage_reports_missing_key() {
    let (_directory, storage) = local_fixture();
    assert!(!storage.exists("org/a/hash").await.unwrap());
}

#[tokio::test]
async fn local_storage_store_makes_key_exist() {
    let (_directory, storage) = local_fixture();
    storage
        .store("org/a/hash", b"payload", "text/plain")
        .await
        .unwrap();
    assert!(storage.exists("org/a/hash").await.unwrap());
}

#[tokio::test]
async fn local_storage_round_trips_bytes() {
    let (_directory, storage) = local_fixture();
    let expected = b"\0binary\xffcontent";
    storage
        .store("org/a/hash", expected, "application/octet-stream")
        .await
        .unwrap();
    assert_eq!(storage.retrieve("org/a/hash").await.unwrap(), expected);
}

#[tokio::test]
async fn local_storage_creates_nested_directories() {
    let (directory, storage) = local_fixture();
    storage
        .store("org/a/deep/hash", b"nested", "text/plain")
        .await
        .unwrap();
    assert!(directory.0.join("org/a/deep/hash").is_file());
}

#[tokio::test]
async fn local_storage_overwrites_same_content_address() {
    let (_directory, storage) = local_fixture();
    storage
        .store("org/a/hash", b"first", "text/plain")
        .await
        .unwrap();
    storage
        .store("org/a/hash", b"second", "text/plain")
        .await
        .unwrap();
    assert_eq!(storage.retrieve("org/a/hash").await.unwrap(), b"second");
}

#[tokio::test]
async fn local_storage_delete_removes_bytes() {
    let (_directory, storage) = local_fixture();
    storage
        .store("org/a/hash", b"payload", "text/plain")
        .await
        .unwrap();
    storage.delete("org/a/hash").await.unwrap();
    assert!(!storage.exists("org/a/hash").await.unwrap());
}

#[tokio::test]
async fn local_storage_delete_is_idempotent() {
    let (_directory, storage) = local_fixture();
    storage.delete("org/a/missing").await.unwrap();
    storage.delete("org/a/missing").await.unwrap();
}

#[tokio::test]
async fn local_storage_retrieve_missing_returns_not_found() {
    let (_directory, storage) = local_fixture();
    assert_eq!(
        storage.retrieve("org/a/missing").await,
        Err(ArtifactError::NotFound)
    );
}

#[tokio::test]
async fn local_storage_rejects_parent_directory_traversal() {
    let (_directory, storage) = local_fixture();
    assert!(matches!(
        storage.store("../outside", b"bad", "text/plain").await,
        Err(ArtifactError::StorageError(_))
    ));
}

#[tokio::test]
async fn local_storage_rejects_absolute_paths() {
    let (_directory, storage) = local_fixture();
    let absolute = std::env::temp_dir().join("outside");
    assert!(matches!(
        storage
            .store(absolute.to_string_lossy().as_ref(), b"bad", "text/plain")
            .await,
        Err(ArtifactError::StorageError(_))
    ));
}

async fn repository_fixture() -> (
    TempDirectory,
    PgPool,
    ArtifactRepository,
    Arc<LocalArtifactStorage>,
    OrganizationId,
    PrincipalId,
) {
    let pool = PgPool::connect(&std::env::var("DATABASE_URL").expect("DATABASE_URL required"))
        .await
        .unwrap();
    sqlx::migrate!("./migrations").run(&pool).await.unwrap();
    let directory = TempDirectory::new();
    let storage = Arc::new(LocalArtifactStorage::new(directory.0.clone()));
    let repository = ArtifactRepository::new(pool.clone(), storage.clone());
    (
        directory,
        pool,
        repository,
        storage,
        OrganizationId(Uuid::new_v4()),
        PrincipalId(Uuid::new_v4()),
    )
}

fn immediate_retention() -> RetentionPolicy {
    RetentionPolicy {
        policy_id: "delete-immediately".into(),
        retain_days: Some(0),
        delete_after: Some(Utc::now()),
    }
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn repository_stores_and_retrieves_with_verified_hash() {
    let (_directory, _pool, repository, _storage, org, principal) = repository_fixture().await;
    let metadata = repository
        .store_artifact(
            org,
            ArtifactType::Request,
            "application/json",
            br#"{"prompt":"hello"}"#,
            principal,
            None,
            None,
        )
        .await
        .unwrap();
    let (loaded, bytes) = repository
        .retrieve_artifact(metadata.id, org)
        .await
        .unwrap();
    assert_eq!(loaded, metadata);
    assert_eq!(bytes, br#"{"prompt":"hello"}"#);
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn repository_deduplicates_content_hash_within_organization() {
    let (_directory, _pool, repository, _storage, org, principal) = repository_fixture().await;
    let first = repository
        .store_artifact(
            org,
            ArtifactType::Response,
            "text/plain",
            b"same",
            principal,
            None,
            None,
        )
        .await
        .unwrap();
    let second = repository
        .store_artifact(
            org,
            ArtifactType::Response,
            "text/plain",
            b"same",
            principal,
            None,
            None,
        )
        .await
        .unwrap();
    assert_eq!(first.id, second.id);
    assert_eq!(first.content_hash, second.content_hash);
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn repository_lists_artifacts_with_type_filter() {
    let (_directory, _pool, repository, _storage, org, principal) = repository_fixture().await;
    repository
        .store_artifact(
            org,
            ArtifactType::TestLog,
            "text/plain",
            b"test log",
            principal,
            None,
            None,
        )
        .await
        .unwrap();
    repository
        .store_artifact(
            org,
            ArtifactType::Media,
            "image/png",
            b"png",
            principal,
            None,
            None,
        )
        .await
        .unwrap();
    let artifacts = repository
        .list_artifacts(org, Some(ArtifactType::TestLog), None, 10, 0)
        .await
        .unwrap();
    assert_eq!(artifacts.len(), 1);
    assert_eq!(artifacts[0].artifact_type, ArtifactType::TestLog);
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn repository_soft_deletes_expired_artifact_and_bytes() {
    let (_directory, _pool, repository, storage, org, principal) = repository_fixture().await;
    let metadata = repository
        .store_artifact(
            org,
            ArtifactType::ToolOutput,
            "text/plain",
            b"delete me",
            principal,
            None,
            Some(immediate_retention()),
        )
        .await
        .unwrap();
    repository.delete_artifact(metadata.id, org).await.unwrap();
    assert!(!storage.exists(&metadata.storage_key).await.unwrap());
    assert_eq!(
        repository.get_metadata(metadata.id, org).await,
        Err(ArtifactError::NotFound)
    );
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn legal_hold_prevents_artifact_deletion() {
    let (_directory, _pool, repository, storage, org, principal) = repository_fixture().await;
    let metadata = repository
        .store_artifact(
            org,
            ArtifactType::Evidence,
            "application/json",
            b"evidence",
            principal,
            None,
            Some(immediate_retention()),
        )
        .await
        .unwrap();
    repository.apply_legal_hold(metadata.id, org).await.unwrap();
    assert_eq!(
        repository.delete_artifact(metadata.id, org).await,
        Err(ArtifactError::LegalHoldBlocks)
    );
    assert!(storage.exists(&metadata.storage_key).await.unwrap());
    repository
        .release_legal_hold(metadata.id, org)
        .await
        .unwrap();
    repository.delete_artifact(metadata.id, org).await.unwrap();
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn cleanup_expired_removes_unheld_artifacts() {
    let (_directory, _pool, repository, _storage, org, principal) = repository_fixture().await;
    repository
        .store_artifact(
            org,
            ArtifactType::ProviderLog,
            "text/plain",
            Uuid::new_v4().as_bytes(),
            principal,
            None,
            Some(immediate_retention()),
        )
        .await
        .unwrap();
    assert!(repository.cleanup_expired().await.unwrap() >= 1);
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn integrity_verification_detects_tampered_data() {
    let (_directory, _pool, repository, storage, org, principal) = repository_fixture().await;
    let metadata = repository
        .store_artifact(
            org,
            ArtifactType::VerificationLog,
            "text/plain",
            b"authentic",
            principal,
            None,
            None,
        )
        .await
        .unwrap();
    storage
        .store(&metadata.storage_key, b"tampered", "text/plain")
        .await
        .unwrap();
    assert!(!repository.verify_integrity(metadata.id, org).await.unwrap());
    assert!(matches!(
        repository.retrieve_artifact(metadata.id, org).await,
        Err(ArtifactError::IntegrityError { .. })
    ));
}

#[tokio::test]
#[ignore = "allocates the configured 100 MiB local/test limit and requires DATABASE_URL"]
async fn repository_enforces_artifact_size_limit() {
    let (_directory, _pool, repository, _storage, org, principal) = repository_fixture().await;
    let bytes = vec![0_u8; usize::try_from(MAX_ARTIFACT_SIZE_BYTES + 1).unwrap()];
    assert!(matches!(
        repository
            .store_artifact(
                org,
                ArtifactType::Media,
                "application/octet-stream",
                &bytes,
                principal,
                None,
                None,
            )
            .await,
        Err(ArtifactError::SizeLimitExceeded { .. })
    ));
}
