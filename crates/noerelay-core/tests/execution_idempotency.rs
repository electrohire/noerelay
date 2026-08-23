use chrono::Utc;
use noerelay_core::execution::{
    CancellationReason, EffectIntent, EffectRequest, EffectResult, EffectResultStatus,
    IdempotencyKey, IdempotencyStatus,
};
use noerelay_core::iam::PrincipalId;
use uuid::Uuid;

fn key() -> IdempotencyKey {
    IdempotencyKey {
        key: "client-key-1".into(),
        principal_id: PrincipalId(Uuid::nil()),
        endpoint_profile: "chat-completions-v1".into(),
        request_hash: "sha256:request".into(),
        policy_revision: "policy-v1".into(),
    }
}

#[test]
fn idempotency_key_serialization_round_trips() {
    let value = key();
    let json = serde_json::to_string(&value).unwrap();
    assert_eq!(
        serde_json::from_str::<IdempotencyKey>(&json).unwrap(),
        value
    );
}

#[test]
fn idempotency_key_contains_scope_fields() {
    let json = serde_json::to_value(key()).unwrap();
    assert_eq!(json["endpoint_profile"], "chat-completions-v1");
    assert_eq!(json["policy_revision"], "policy-v1");
}

#[test]
fn in_progress_can_complete() {
    assert!(IdempotencyStatus::InProgress.can_transition(IdempotencyStatus::Completed));
}

#[test]
fn in_progress_can_fail_or_expire() {
    assert!(IdempotencyStatus::InProgress.can_transition(IdempotencyStatus::Failed));
    assert!(IdempotencyStatus::InProgress.can_transition(IdempotencyStatus::Expired));
}

#[test]
fn failed_and_expired_can_be_reclaimed() {
    assert!(IdempotencyStatus::Failed.can_transition(IdempotencyStatus::InProgress));
    assert!(IdempotencyStatus::Expired.can_transition(IdempotencyStatus::InProgress));
}

#[test]
fn completed_cannot_transition() {
    assert!(!IdempotencyStatus::Completed.can_transition(IdempotencyStatus::InProgress));
}

#[test]
fn idempotency_status_uses_wire_names() {
    assert_eq!(
        serde_json::to_string(&IdempotencyStatus::InProgress).unwrap(),
        "\"in_progress\""
    );
}

#[test]
fn cancellation_reason_variants_use_wire_names() {
    let variants = [
        (CancellationReason::UserRequested, "user_requested"),
        (CancellationReason::Timeout, "timeout"),
        (CancellationReason::BudgetExceeded, "budget_exceeded"),
        (CancellationReason::PolicyViolation, "policy_violation"),
        (CancellationReason::ParentCancelled, "parent_cancelled"),
        (CancellationReason::DependencyFailed, "dependency_failed"),
    ];
    for (variant, expected) in variants {
        assert_eq!(serde_json::to_value(variant).unwrap(), expected);
    }
}

#[test]
fn effect_intent_variants_use_wire_names() {
    assert_eq!(serde_json::to_value(EffectIntent::Read).unwrap(), "read");
    assert_eq!(serde_json::to_value(EffectIntent::Write).unwrap(), "write");
    assert_eq!(
        serde_json::to_value(EffectIntent::Delete).unwrap(),
        "delete"
    );
}

#[test]
fn effect_result_status_variants_use_wire_names() {
    let variants = [
        EffectResultStatus::Applied,
        EffectResultStatus::Rejected,
        EffectResultStatus::Unknown,
        EffectResultStatus::Reconciled,
        EffectResultStatus::Compensated,
    ];
    let names = [
        "applied",
        "rejected",
        "unknown",
        "reconciled",
        "compensated",
    ];
    for (variant, expected) in variants.into_iter().zip(names) {
        assert_eq!(serde_json::to_value(variant).unwrap(), expected);
    }
}

#[test]
fn effect_request_serialization_round_trips() {
    let request = EffectRequest {
        effect_id: "run/step/effect-1".into(),
        tool_id: "ticket.create".into(),
        intent: EffectIntent::Write,
        request_hash: "sha256:intent".into(),
        idempotency_key: Some("downstream-key".into()),
        created_at: Utc::now(),
    };
    let json = serde_json::to_string(&request).unwrap();
    assert_eq!(
        serde_json::from_str::<EffectRequest>(&json).unwrap(),
        request
    );
}

#[test]
fn effect_request_supports_no_downstream_idempotency() {
    let request = EffectRequest {
        effect_id: "effect-2".into(),
        tool_id: "legacy.write".into(),
        intent: EffectIntent::Write,
        request_hash: "sha256:intent".into(),
        idempotency_key: None,
        created_at: Utc::now(),
    };
    assert!(serde_json::to_value(request).unwrap()["idempotency_key"].is_null());
}

#[test]
fn unknown_effect_result_serialization_round_trips() {
    let result = EffectResult {
        effect_id: "effect-2".into(),
        status: EffectResultStatus::Unknown,
        external_effect_id: None,
        response_hash: None,
        reconciled_at: None,
        error: Some("connection lost after dispatch".into()),
        created_at: Utc::now(),
    };
    let json = serde_json::to_string(&result).unwrap();
    assert_eq!(serde_json::from_str::<EffectResult>(&json).unwrap(), result);
}

#[test]
fn reconciled_effect_result_retains_external_reference() {
    let result = EffectResult {
        effect_id: "effect-3".into(),
        status: EffectResultStatus::Reconciled,
        external_effect_id: Some("external-42".into()),
        response_hash: Some("sha256:response".into()),
        reconciled_at: Some(Utc::now()),
        error: None,
        created_at: Utc::now(),
    };
    let json = serde_json::to_value(result).unwrap();
    assert_eq!(json["external_effect_id"], "external-42");
}
