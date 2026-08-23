use chrono::{Duration, Utc};
use noerelay_core::execution::*;
use uuid::Uuid;

#[test]
fn active_can_begin_drain() {
    assert!(WorkerStatus::Active.can_transition(WorkerStatus::Draining));
}

#[test]
fn active_can_fail() {
    assert!(WorkerStatus::Active.can_transition(WorkerStatus::Failed));
}

#[test]
fn draining_can_complete() {
    assert!(WorkerStatus::Draining.can_transition(WorkerStatus::Drained));
}

#[test]
fn draining_can_fail() {
    assert!(WorkerStatus::Draining.can_transition(WorkerStatus::Failed));
}

#[test]
fn drained_can_only_be_decommissioned() {
    assert_eq!(
        WorkerStatus::Drained.legal_transitions(),
        &[WorkerStatus::Decommissioned]
    );
}

#[test]
fn failed_can_be_decommissioned() {
    assert!(WorkerStatus::Failed.can_transition(WorkerStatus::Decommissioned));
}

#[test]
fn decommissioned_is_terminal() {
    assert!(WorkerStatus::Decommissioned.legal_transitions().is_empty());
}

#[test]
fn drain_cannot_return_to_active() {
    assert!(!WorkerStatus::Draining.can_transition(WorkerStatus::Active));
}

#[test]
fn conflict_resolution_variants_use_snake_case() {
    let variants = [
        (ConflictResolution::ReloadAndRetry, "\"reload_and_retry\""),
        (ConflictResolution::TakeOver, "\"take_over\""),
        (ConflictResolution::Wait, "\"wait\""),
        (ConflictResolution::Abort, "\"abort\""),
    ];
    for (variant, expected) in variants {
        assert_eq!(serde_json::to_string(&variant).unwrap(), expected);
    }
}

#[test]
fn all_conflict_types_roundtrip() {
    let variants = [
        ConflictType::VersionMismatch,
        ConflictType::LeaseExpired,
        ConflictType::WorkerUnresponsive,
        ConflictType::DatabaseFailover,
        ConflictType::StreamOwnershipLost,
    ];
    for variant in variants {
        let json = serde_json::to_string(&variant).unwrap();
        assert_eq!(
            serde_json::from_str::<ConflictType>(&json).unwrap(),
            variant
        );
    }
}

#[test]
fn stream_ownership_serialization_roundtrip() {
    let ownership = StreamOwnership {
        stream_id: "stream-1".into(),
        owner_worker_id: "worker-1".into(),
        acquired_at: Utc::now(),
        fencing_token: 42,
        expires_at: Utc::now() + Duration::seconds(30),
        last_heartbeat_at: Some(Utc::now()),
    };
    let json = serde_json::to_string(&ownership).unwrap();
    assert_eq!(
        serde_json::from_str::<StreamOwnership>(&json).unwrap(),
        ownership
    );
}

#[test]
fn conflict_report_serialization_roundtrip() {
    let report = ConflictReport {
        work_item_id: WorkItemId(Uuid::new_v4()),
        conflict_type: ConflictType::VersionMismatch,
        resolution: ConflictResolution::ReloadAndRetry,
        current_owner: Some("worker-a".into()),
        fencing_token: 7,
        detected_at: Utc::now(),
    };
    let json = serde_json::to_string(&report).unwrap();
    assert_eq!(
        serde_json::from_str::<ConflictReport>(&json).unwrap(),
        report
    );
}

#[test]
fn worker_registration_serialization_roundtrip() {
    let now = Utc::now();
    let worker = WorkerRegistration {
        worker_id: "worker-a".into(),
        worker_version: "1.0.0".into(),
        capabilities: vec!["execute".into(), "stream".into()],
        registered_at: now,
        last_heartbeat_at: now,
        status: WorkerStatus::Active,
    };
    let json = serde_json::to_string(&worker).unwrap();
    assert_eq!(
        serde_json::from_str::<WorkerRegistration>(&json).unwrap(),
        worker
    );
}
