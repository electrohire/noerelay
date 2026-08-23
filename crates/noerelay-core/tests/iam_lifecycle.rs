use chrono::{Duration, Utc};
use noerelay_core::artifacts::ArtifactId;
use noerelay_core::iam::*;
use uuid::Uuid;

fn ids() -> (OrganizationId, PrincipalId) {
    (OrganizationId(Uuid::new_v4()), PrincipalId(Uuid::new_v4()))
}

fn round_trip<T>(value: &T) -> T
where
    T: serde::Serialize + serde::de::DeserializeOwned,
{
    serde_json::from_value(serde_json::to_value(value).unwrap()).unwrap()
}

#[test]
fn data_category_variants_use_stable_wire_names() {
    let cases = [
        (DataCategory::Prompts, "prompts"),
        (DataCategory::Outputs, "outputs"),
        (DataCategory::Artifacts, "artifacts"),
        (DataCategory::Caches, "caches"),
        (DataCategory::Traces, "traces"),
        (DataCategory::Logs, "logs"),
        (DataCategory::Receipts, "receipts"),
        (DataCategory::LedgerEvents, "ledger_events"),
        (DataCategory::Recommendations, "recommendations"),
        (DataCategory::Exports, "exports"),
        (DataCategory::ProviderCopies, "provider_copies"),
        (DataCategory::AuditEvents, "audit_events"),
        (DataCategory::ContextNodes, "context_nodes"),
        (DataCategory::UsageRecords, "usage_records"),
    ];
    for (value, name) in cases {
        assert_eq!(serde_json::to_value(value).unwrap(), name);
    }
}

#[test]
fn retention_action_variants_round_trip() {
    for action in [
        RetentionAction::Retain,
        RetentionAction::Delete,
        RetentionAction::CryptographicDelete,
        RetentionAction::Archive,
        RetentionAction::Export,
    ] {
        assert_eq!(round_trip(&action), action);
    }
}

#[test]
fn lifecycle_policy_serialization_preserves_version_and_deadline() {
    let (organization_id, _) = ids();
    let now = Utc::now();
    let policy = LifecyclePolicy {
        id: "outputs-v2".into(),
        organization_id,
        category: DataCategory::Outputs,
        action: RetentionAction::CryptographicDelete,
        retain_days: Some(30),
        delete_after: Some(now + Duration::days(30)),
        description: "local test policy".into(),
        created_at: now,
        updated_at: now,
        version: 2,
        active: true,
    };
    let decoded = round_trip(&policy);
    assert_eq!(decoded.id, policy.id);
    assert_eq!(decoded.version, 2);
    assert_eq!(decoded.action, RetentionAction::CryptographicDelete);
    assert_eq!(decoded.delete_after, policy.delete_after);
}

#[test]
fn lifecycle_policy_rejects_unknown_fields() {
    let (organization_id, _) = ids();
    let value = serde_json::json!({
        "id": "p1", "organization_id": organization_id, "category": "logs",
        "action": "delete", "retain_days": 1, "delete_after": null,
        "description": "", "created_at": Utc::now(), "updated_at": Utc::now(),
        "version": 1, "active": true, "unexpected": true
    });
    assert!(serde_json::from_value::<LifecyclePolicy>(value).is_err());
}

#[test]
fn deletion_job_serialization_preserves_counters() {
    let (organization_id, created_by) = ids();
    let job = DeletionJob {
        id: Uuid::new_v4(),
        organization_id,
        category: DataCategory::Artifacts,
        status: DeletionStatus::PartiallyCompleted,
        started_at: Some(Utc::now()),
        completed_at: Some(Utc::now()),
        items_total: 10,
        items_deleted: 7,
        items_failed: 1,
        items_skipped_legal_hold: 2,
        error: None,
        created_at: Utc::now(),
        created_by,
    };
    let decoded = round_trip(&job);
    assert_eq!(decoded.items_deleted, 7);
    assert_eq!(decoded.items_skipped_legal_hold, 2);
    assert_eq!(decoded.status, DeletionStatus::PartiallyCompleted);
}

#[test]
fn data_inventory_serialization_preserves_locations() {
    let (organization_id, _) = ids();
    let inventory = DataInventory {
        organization_id,
        entries: vec![DataInventoryEntry {
            category: DataCategory::Caches,
            location: "redis:cache".into(),
            count: 4,
            size_bytes: Some(128),
            retention_policy_id: Some("cache-v1".into()),
            legal_hold_count: 0,
            last_reconciled_at: Some(Utc::now()),
        }],
        generated_at: Utc::now(),
    };
    let decoded = round_trip(&inventory);
    assert_eq!(decoded.entries[0].location, "redis:cache");
    assert_eq!(decoded.entries[0].size_bytes, Some(128));
}

#[test]
fn export_request_serialization_preserves_artifact_reference() {
    let (organization_id, requested_by) = ids();
    let artifact_id: ArtifactId = Uuid::new_v4();
    let request = ExportRequest {
        id: Uuid::new_v4(),
        organization_id,
        requested_by,
        categories: vec![DataCategory::Prompts, DataCategory::Outputs],
        status: ExportStatus::Completed,
        artifact_id: Some(artifact_id),
        created_at: Utc::now(),
        completed_at: Some(Utc::now()),
        expires_at: Some(Utc::now() + Duration::days(7)),
    };
    let decoded = round_trip(&request);
    assert_eq!(decoded.artifact_id, Some(artifact_id));
    assert_eq!(decoded.categories.len(), 2);
}

#[test]
fn export_status_variants_round_trip() {
    for status in [
        ExportStatus::Pending,
        ExportStatus::InProgress,
        ExportStatus::Completed,
        ExportStatus::Failed,
        ExportStatus::Expired,
    ] {
        assert_eq!(round_trip(&status), status);
    }
}

#[test]
fn tombstone_serialization_preserves_deletion_proof() {
    let (organization_id, deleted_by) = ids();
    let job_id = Uuid::new_v4();
    let tombstone = Tombstone {
        id: Uuid::new_v4(),
        organization_id,
        original_table: "artifacts".into(),
        original_id: Uuid::new_v4().to_string(),
        deleted_at: Utc::now(),
        deleted_by,
        deletion_job_id: Some(job_id),
        reason: "retention expired".into(),
    };
    let decoded = round_trip(&tombstone);
    assert_eq!(decoded.deletion_job_id, Some(job_id));
    assert_eq!(decoded.reason, "retention expired");
}

#[test]
fn deletion_status_state_machine_allows_documented_transitions() {
    assert!(DeletionStatus::Pending.can_transition_to(DeletionStatus::InProgress));
    assert!(DeletionStatus::Pending.can_transition_to(DeletionStatus::Cancelled));
    assert!(DeletionStatus::InProgress.can_transition_to(DeletionStatus::Completed));
    assert!(DeletionStatus::InProgress.can_transition_to(DeletionStatus::PartiallyCompleted));
    assert!(DeletionStatus::InProgress.can_transition_to(DeletionStatus::Failed));
}

#[test]
fn deletion_status_state_machine_rejects_terminal_transitions() {
    for terminal in [
        DeletionStatus::Completed,
        DeletionStatus::Failed,
        DeletionStatus::PartiallyCompleted,
        DeletionStatus::Cancelled,
    ] {
        assert!(!terminal.can_transition_to(DeletionStatus::InProgress));
        assert!(!terminal.can_transition_to(DeletionStatus::Pending));
    }
    assert!(!DeletionStatus::Pending.can_transition_to(DeletionStatus::Completed));
}

#[test]
fn lifecycle_error_messages_are_stable() {
    assert_eq!(
        IamError::LifecyclePolicyNotFound.to_string(),
        "lifecycle policy not found"
    );
    assert_eq!(
        IamError::LegalHoldConflict.to_string(),
        "legal hold blocks deletion"
    );
}
