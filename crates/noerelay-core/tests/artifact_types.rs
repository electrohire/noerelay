use chrono::{TimeZone, Utc};
use noerelay_core::{
    ArtifactError, ArtifactMetadata, ArtifactRef, ArtifactType, EncryptionInfo, OrganizationId,
    PrincipalId, RetentionPolicy, RunId, StorageBackend,
};
use schemars::schema_for;
use uuid::Uuid;

fn metadata() -> ArtifactMetadata {
    ArtifactMetadata {
        id: Uuid::from_u128(1),
        organization_id: OrganizationId(Uuid::from_u128(2)),
        artifact_type: ArtifactType::Response,
        content_hash: "ab".repeat(32),
        content_type: "application/json".into(),
        size_bytes: 42,
        storage_key: format!("org/{}/{}", Uuid::from_u128(2), "ab".repeat(32)),
        storage_backend: StorageBackend::Local,
        encryption: EncryptionInfo {
            encrypted: false,
            key_id: None,
            algorithm: None,
        },
        retention_policy: RetentionPolicy {
            policy_id: "test-policy".into(),
            retain_days: Some(7),
            delete_after: Some(Utc.with_ymd_and_hms(2026, 1, 8, 0, 0, 0).unwrap()),
        },
        created_at: Utc.with_ymd_and_hms(2026, 1, 1, 0, 0, 0).unwrap(),
        created_by: PrincipalId(Uuid::from_u128(3)),
        run_id: Some(RunId(Uuid::from_u128(4))),
        deleted_at: None,
        delete_after: Some(Utc.with_ymd_and_hms(2026, 1, 8, 0, 0, 0).unwrap()),
        legal_hold: false,
    }
}

#[test]
fn artifact_type_serializes_all_variants_as_snake_case() {
    let cases = [
        (ArtifactType::Request, "request"),
        (ArtifactType::Response, "response"),
        (ArtifactType::ProviderLog, "provider_log"),
        (ArtifactType::ToolOutput, "tool_output"),
        (ArtifactType::VerificationLog, "verification_log"),
        (ArtifactType::Media, "media"),
        (ArtifactType::TestLog, "test_log"),
        (ArtifactType::Evidence, "evidence"),
        (ArtifactType::Receipt, "receipt"),
        (ArtifactType::Context, "context"),
    ];
    for (artifact_type, expected) in cases {
        assert_eq!(serde_json::to_value(artifact_type).unwrap(), expected);
    }
}

#[test]
fn artifact_type_deserializes_snake_case() {
    assert_eq!(
        serde_json::from_str::<ArtifactType>("\"verification_log\"").unwrap(),
        ArtifactType::VerificationLog
    );
}

#[test]
fn metadata_serialization_preserves_authority_fields() {
    let value = serde_json::to_value(metadata()).unwrap();
    assert_eq!(value["organization_id"], Uuid::from_u128(2).to_string());
    assert_eq!(value["content_hash"], "ab".repeat(32));
    assert_eq!(value["run_id"], Uuid::from_u128(4).to_string());
    assert_eq!(value["legal_hold"], false);
}

#[test]
fn metadata_round_trips_without_loss() {
    let expected = metadata();
    let encoded = serde_json::to_string(&expected).unwrap();
    assert_eq!(
        serde_json::from_str::<ArtifactMetadata>(&encoded).unwrap(),
        expected
    );
}

#[test]
fn artifact_ref_serialization_binds_id_hash_type_and_size() {
    let reference = ArtifactRef {
        artifact_id: Uuid::from_u128(11),
        content_hash: "cd".repeat(32),
        artifact_type: ArtifactType::Receipt,
        size_bytes: 512,
    };
    let value = serde_json::to_value(reference).unwrap();
    assert_eq!(value["artifact_id"], Uuid::from_u128(11).to_string());
    assert_eq!(value["content_hash"], "cd".repeat(32));
    assert_eq!(value["artifact_type"], "receipt");
    assert_eq!(value["size_bytes"], 512);
}

#[test]
fn retention_policy_supports_retain_forever() {
    let policy = RetentionPolicy {
        policy_id: "forever".into(),
        retain_days: None,
        delete_after: None,
    };
    let value = serde_json::to_value(policy).unwrap();
    assert!(value["retain_days"].is_null());
    assert!(value["delete_after"].is_null());
}

#[test]
fn storage_backend_serializes_named_backends() {
    assert_eq!(
        serde_json::to_value(StorageBackend::Local).unwrap(),
        "local"
    );
    assert_eq!(serde_json::to_value(StorageBackend::S3).unwrap(), "s3");
    assert_eq!(
        serde_json::to_value(StorageBackend::Minio).unwrap(),
        "minio"
    );
}

#[test]
fn integrity_error_serialization_preserves_hashes() {
    let error = ArtifactError::IntegrityError {
        expected: "aa".repeat(32),
        actual: "bb".repeat(32),
    };
    let value = serde_json::to_value(error).unwrap();
    assert_eq!(value["integrity_error"]["expected"], "aa".repeat(32));
    assert_eq!(value["integrity_error"]["actual"], "bb".repeat(32));
}

#[test]
fn artifact_metadata_has_a_json_schema() {
    let schema = serde_json::to_value(schema_for!(ArtifactMetadata)).unwrap();
    assert_eq!(schema["title"], "ArtifactMetadata");
    assert!(schema["properties"]["content_hash"].is_object());
}
