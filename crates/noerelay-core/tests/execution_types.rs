//! Unit tests for execution state machines and type serialization.
//!
//! These tests validate:
//! - Legal state transitions for all entity types
//! - Illegal transitions are rejected
//! - Serialization/deserialization roundtrips
//! - Terminal state detection

use chrono::Utc;
use noerelay_core::execution::*;
use noerelay_core::iam::{OrganizationId, PrincipalId};
use uuid::Uuid;

// ============================================================================
// RunStatus State Machine Tests
// ============================================================================

#[test]
fn run_pending_to_running_is_legal() {
    assert!(RunStatus::Pending.can_transition(RunStatus::Running));
}

#[test]
fn run_pending_to_cancelled_is_legal() {
    assert!(RunStatus::Pending.can_transition(RunStatus::Cancelled));
}

#[test]
fn run_pending_to_completed_is_illegal() {
    assert!(!RunStatus::Pending.can_transition(RunStatus::Completed));
}

#[test]
fn run_running_to_awaiting_approval_is_legal() {
    assert!(RunStatus::Running.can_transition(RunStatus::AwaitingApproval));
}

#[test]
fn run_running_to_awaiting_verification_is_legal() {
    assert!(RunStatus::Running.can_transition(RunStatus::AwaitingVerification));
}

#[test]
fn run_running_to_timed_out_is_legal() {
    assert!(RunStatus::Running.can_transition(RunStatus::TimedOut));
}

#[test]
fn run_awaiting_approval_to_running_is_legal() {
    assert!(RunStatus::AwaitingApproval.can_transition(RunStatus::Running));
}

#[test]
fn run_awaiting_verification_to_completed_is_legal() {
    assert!(RunStatus::AwaitingVerification.can_transition(RunStatus::Completed));
}

#[test]
fn run_completed_is_terminal() {
    assert!(RunStatus::Completed.is_terminal());
    assert!(RunStatus::Completed.legal_transitions().is_empty());
}

#[test]
fn run_failed_is_terminal() {
    assert!(RunStatus::Failed.is_terminal());
    assert!(RunStatus::Failed.legal_transitions().is_empty());
}

#[test]
fn run_cancelled_is_terminal() {
    assert!(RunStatus::Cancelled.is_terminal());
}

#[test]
fn run_timed_out_is_terminal() {
    assert!(RunStatus::TimedOut.is_terminal());
}

#[test]
fn run_state_machine_accepts_legal_transition() {
    let result = RunStateMachine::transition(RunStatus::Pending, RunStatus::Running);
    assert!(result.is_ok());
    assert_eq!(result.unwrap(), RunStatus::Running);
}

#[test]
fn run_state_machine_rejects_illegal_transition() {
    let result = RunStateMachine::transition(RunStatus::Completed, RunStatus::Running);
    assert!(result.is_err());
    match result {
        Err(ExecutionError::IllegalTransition { entity_type, .. }) => {
            assert_eq!(entity_type, "RunStatus");
        }
        _ => panic!("expected IllegalTransition error"),
    }
}

// ============================================================================
// StepStatus State Machine Tests
// ============================================================================

#[test]
fn step_pending_to_ready_is_legal() {
    assert!(StepStatus::Pending.can_transition(StepStatus::Ready));
}

#[test]
fn step_pending_to_skipped_is_legal() {
    assert!(StepStatus::Pending.can_transition(StepStatus::Skipped));
}

#[test]
fn step_ready_to_running_is_legal() {
    assert!(StepStatus::Ready.can_transition(StepStatus::Running));
}

#[test]
fn step_running_to_completed_is_legal() {
    assert!(StepStatus::Running.can_transition(StepStatus::Completed));
}

#[test]
fn step_running_to_failed_is_legal() {
    assert!(StepStatus::Running.can_transition(StepStatus::Failed));
}

#[test]
fn step_failed_can_retry_to_pending() {
    assert!(StepStatus::Failed.can_transition(StepStatus::Pending));
}

#[test]
fn step_failed_can_retry_to_ready() {
    assert!(StepStatus::Failed.can_transition(StepStatus::Ready));
}

#[test]
fn step_completed_is_terminal() {
    assert!(StepStatus::Completed.is_terminal());
    assert!(StepStatus::Completed.legal_transitions().is_empty());
}

#[test]
fn step_skipped_is_terminal() {
    assert!(StepStatus::Skipped.is_terminal());
}

#[test]
fn step_cancelled_is_terminal() {
    assert!(StepStatus::Cancelled.is_terminal());
}

#[test]
fn step_state_machine_rejects_illegal() {
    let result = StepStateMachine::transition(StepStatus::Completed, StepStatus::Running);
    assert!(result.is_err());
}

// ============================================================================
// AttemptStatus State Machine Tests
// ============================================================================

#[test]
fn attempt_pending_to_running_is_legal() {
    assert!(AttemptStatus::Pending.can_transition(AttemptStatus::Running));
}

#[test]
fn attempt_pending_to_cancelled_is_legal() {
    assert!(AttemptStatus::Pending.can_transition(AttemptStatus::Cancelled));
}

#[test]
fn attempt_running_to_succeeded_is_legal() {
    assert!(AttemptStatus::Running.can_transition(AttemptStatus::Succeeded));
}

#[test]
fn attempt_running_to_failed_is_legal() {
    assert!(AttemptStatus::Running.can_transition(AttemptStatus::Failed));
}

#[test]
fn attempt_running_to_timed_out_is_legal() {
    assert!(AttemptStatus::Running.can_transition(AttemptStatus::TimedOut));
}

#[test]
fn attempt_succeeded_is_terminal() {
    assert!(AttemptStatus::Succeeded.is_terminal());
}

#[test]
fn attempt_failed_is_terminal() {
    assert!(AttemptStatus::Failed.is_terminal());
}

#[test]
fn attempt_state_machine_rejects_illegal() {
    let result = AttemptStateMachine::transition(AttemptStatus::Succeeded, AttemptStatus::Running);
    assert!(result.is_err());
}

// ============================================================================
// WorkItemStatus State Machine Tests
// ============================================================================

#[test]
fn work_item_pending_to_claimed_is_legal() {
    assert!(WorkItemStatus::Pending.can_transition(WorkItemStatus::Claimed));
}

#[test]
fn work_item_pending_to_cancelled_is_legal() {
    assert!(WorkItemStatus::Pending.can_transition(WorkItemStatus::Cancelled));
}

#[test]
fn work_item_claimed_to_running_is_legal() {
    assert!(WorkItemStatus::Claimed.can_transition(WorkItemStatus::Running));
}

#[test]
fn work_item_claimed_to_failed_is_legal() {
    assert!(WorkItemStatus::Claimed.can_transition(WorkItemStatus::Failed));
}

#[test]
fn work_item_running_to_completed_is_legal() {
    assert!(WorkItemStatus::Running.can_transition(WorkItemStatus::Completed));
}

#[test]
fn work_item_running_to_failed_is_legal() {
    assert!(WorkItemStatus::Running.can_transition(WorkItemStatus::Failed));
}

#[test]
fn work_item_failed_to_dead_letter_is_legal() {
    assert!(WorkItemStatus::Failed.can_transition(WorkItemStatus::DeadLetter));
}

#[test]
fn work_item_failed_can_retry_to_pending() {
    assert!(WorkItemStatus::Failed.can_transition(WorkItemStatus::Pending));
}

#[test]
fn work_item_completed_is_terminal() {
    assert!(WorkItemStatus::Completed.is_terminal());
}

#[test]
fn work_item_dead_letter_is_terminal() {
    assert!(WorkItemStatus::DeadLetter.is_terminal());
}

#[test]
fn work_item_cancelled_is_terminal() {
    assert!(WorkItemStatus::Cancelled.is_terminal());
}

#[test]
fn work_item_state_machine_rejects_illegal() {
    let result =
        WorkItemStateMachine::transition(WorkItemStatus::Completed, WorkItemStatus::Running);
    assert!(result.is_err());
}

// ============================================================================
// ReservationStatus State Machine Tests
// ============================================================================

#[test]
fn reservation_active_to_consumed_is_legal() {
    assert!(ReservationStatus::Active.can_transition(ReservationStatus::Consumed));
}

#[test]
fn reservation_active_to_released_is_legal() {
    assert!(ReservationStatus::Active.can_transition(ReservationStatus::Released));
}

#[test]
fn reservation_active_to_expired_is_legal() {
    assert!(ReservationStatus::Active.can_transition(ReservationStatus::Expired));
}

#[test]
fn reservation_released_is_terminal() {
    assert!(ReservationStatus::Released.is_terminal());
}

#[test]
fn reservation_expired_is_terminal() {
    assert!(ReservationStatus::Expired.is_terminal());
}

#[test]
fn reservation_consumed_is_terminal() {
    assert!(ReservationStatus::Consumed.is_terminal());
}

#[test]
fn reservation_state_machine_rejects_illegal() {
    let result =
        ReservationStateMachine::transition(ReservationStatus::Released, ReservationStatus::Active);
    assert!(result.is_err());
}

// ============================================================================
// Serialization Tests
// ============================================================================

#[test]
fn run_status_serialization_roundtrip() {
    let cases = [
        RunStatus::Pending,
        RunStatus::Running,
        RunStatus::AwaitingApproval,
        RunStatus::AwaitingVerification,
        RunStatus::Completed,
        RunStatus::Failed,
        RunStatus::Cancelled,
        RunStatus::TimedOut,
    ];
    for status in cases {
        let json = serde_json::to_string(&status).unwrap();
        let deserialized: RunStatus = serde_json::from_str(&json).unwrap();
        assert_eq!(status, deserialized);
    }
}

#[test]
fn step_status_serialization_roundtrip() {
    let cases = [
        StepStatus::Pending,
        StepStatus::Ready,
        StepStatus::Running,
        StepStatus::Completed,
        StepStatus::Failed,
        StepStatus::Skipped,
        StepStatus::Cancelled,
    ];
    for status in cases {
        let json = serde_json::to_string(&status).unwrap();
        let deserialized: StepStatus = serde_json::from_str(&json).unwrap();
        assert_eq!(status, deserialized);
    }
}

#[test]
fn attempt_status_serialization_roundtrip() {
    let cases = [
        AttemptStatus::Pending,
        AttemptStatus::Running,
        AttemptStatus::Succeeded,
        AttemptStatus::Failed,
        AttemptStatus::Cancelled,
        AttemptStatus::TimedOut,
    ];
    for status in cases {
        let json = serde_json::to_string(&status).unwrap();
        let deserialized: AttemptStatus = serde_json::from_str(&json).unwrap();
        assert_eq!(status, deserialized);
    }
}

#[test]
fn work_item_status_serialization_roundtrip() {
    let cases = [
        WorkItemStatus::Pending,
        WorkItemStatus::Claimed,
        WorkItemStatus::Running,
        WorkItemStatus::Completed,
        WorkItemStatus::Failed,
        WorkItemStatus::Cancelled,
        WorkItemStatus::DeadLetter,
    ];
    for status in cases {
        let json = serde_json::to_string(&status).unwrap();
        let deserialized: WorkItemStatus = serde_json::from_str(&json).unwrap();
        assert_eq!(status, deserialized);
    }
}

#[test]
fn reservation_status_serialization_roundtrip() {
    let cases = [
        ReservationStatus::Active,
        ReservationStatus::Released,
        ReservationStatus::Expired,
        ReservationStatus::Consumed,
    ];
    for status in cases {
        let json = serde_json::to_string(&status).unwrap();
        let deserialized: ReservationStatus = serde_json::from_str(&json).unwrap();
        assert_eq!(status, deserialized);
    }
}

#[test]
fn run_id_serialization_is_transparent() {
    let id = RunId(Uuid::new_v4());
    let json = serde_json::to_string(&id).unwrap();
    // Should be just the UUID string, not an object
    assert!(json.starts_with('"') && json.ends_with('"'));
    let deserialized: RunId = serde_json::from_str(&json).unwrap();
    assert_eq!(id, deserialized);
}

#[test]
fn step_id_serialization_is_transparent() {
    let id = StepId(Uuid::new_v4());
    let json = serde_json::to_string(&id).unwrap();
    assert!(json.starts_with('"') && json.ends_with('"'));
    let deserialized: StepId = serde_json::from_str(&json).unwrap();
    assert_eq!(id, deserialized);
}

#[test]
fn run_struct_serialization_roundtrip() {
    let run = Run {
        id: RunId(Uuid::new_v4()),
        organization_id: OrganizationId(Uuid::new_v4()),
        project_id: None,
        environment_id: None,
        principal_id: PrincipalId(Uuid::new_v4()),
        contract_hash: "abc123def456".into(),
        context_manifest_hash: Some("ctx789".into()),
        policy_revision: "v1.0.0".into(),
        status: RunStatus::Pending,
        parent_run_id: None,
        created_at: Utc::now(),
        updated_at: Utc::now(),
        completed_at: None,
        terminal_receipt_id: None,
    };
    let json = serde_json::to_string(&run).unwrap();
    let deserialized: Run = serde_json::from_str(&json).unwrap();
    assert_eq!(run, deserialized);
}

#[test]
fn step_struct_serialization_roundtrip() {
    let step = Step {
        id: StepId(Uuid::new_v4()),
        run_id: RunId(Uuid::new_v4()),
        parent_step_id: None,
        step_type: StepType::ProviderCall,
        name: "call-gpt4".into(),
        status: StepStatus::Ready,
        sequence: 1,
        input_hash: Some("in-hash".into()),
        output_hash: None,
        created_at: Utc::now(),
        updated_at: Utc::now(),
        completed_at: None,
    };
    let json = serde_json::to_string(&step).unwrap();
    let deserialized: Step = serde_json::from_str(&json).unwrap();
    assert_eq!(step, deserialized);
}

#[test]
fn attempt_struct_serialization_roundtrip() {
    let attempt = Attempt {
        id: AttemptId(Uuid::new_v4()),
        step_id: StepId(Uuid::new_v4()),
        attempt_number: 1,
        status: AttemptStatus::Running,
        provider_call_id: None,
        started_at: Utc::now(),
        completed_at: None,
        error: None,
        cost_micro_usd: Some(150),
    };
    let json = serde_json::to_string(&attempt).unwrap();
    let deserialized: Attempt = serde_json::from_str(&json).unwrap();
    assert_eq!(attempt, deserialized);
}

#[test]
fn work_item_struct_serialization_roundtrip() {
    let item = WorkItem {
        id: WorkItemId(Uuid::new_v4()),
        run_id: RunId(Uuid::new_v4()),
        step_id: None,
        item_type: "provider_call".into(),
        payload: serde_json::json!({"model": "gpt-4o", "messages": []}),
        status: WorkItemStatus::Pending,
        priority: 5,
        lease_id: None,
        lease_expires_at: None,
        fencing_token: None,
        attempts: 0,
        max_attempts: 3,
        available_at: Utc::now(),
        created_at: Utc::now(),
        updated_at: Utc::now(),
        version: 0,
    };
    let json = serde_json::to_string(&item).unwrap();
    let deserialized: WorkItem = serde_json::from_str(&json).unwrap();
    assert_eq!(item, deserialized);
}

#[test]
fn reservation_struct_serialization_roundtrip() {
    let reservation = Reservation {
        id: ReservationId(Uuid::new_v4()),
        run_id: RunId(Uuid::new_v4()),
        resource_type: "llm_call".into(),
        resource_id: "gpt-4o".into(),
        status: ReservationStatus::Active,
        amount_micro_usd: 5000,
        expires_at: Utc::now(),
        created_at: Utc::now(),
        released_at: None,
    };
    let json = serde_json::to_string(&reservation).unwrap();
    let deserialized: Reservation = serde_json::from_str(&json).unwrap();
    assert_eq!(reservation, deserialized);
}

#[test]
fn tool_effect_struct_serialization_roundtrip() {
    let effect = ToolEffect {
        id: ToolEffectId(Uuid::new_v4()),
        attempt_id: AttemptId(Uuid::new_v4()),
        tool_id: "file_write".into(),
        effect_kind: "filesystem".into(),
        effect_id_external: Some("ext-123".into()),
        status: "pending".into(),
        request_hash: "req-hash".into(),
        response_hash: None,
        created_at: Utc::now(),
        reconciled_at: None,
    };
    let json = serde_json::to_string(&effect).unwrap();
    let deserialized: ToolEffect = serde_json::from_str(&json).unwrap();
    assert_eq!(effect, deserialized);
}

#[test]
fn provider_call_struct_serialization_roundtrip() {
    let call = ProviderCall {
        id: ProviderCallId(Uuid::new_v4()),
        attempt_id: AttemptId(Uuid::new_v4()),
        provider: "openai".into(),
        model: "gpt-4o".into(),
        request_hash: "req-hash-123".into(),
        response_hash: Some("resp-hash-456".into()),
        usage_input_tokens: Some(100),
        usage_output_tokens: Some(50),
        status: "completed".into(),
        started_at: Utc::now(),
        completed_at: Some(Utc::now()),
    };
    let json = serde_json::to_string(&call).unwrap();
    let deserialized: ProviderCall = serde_json::from_str(&json).unwrap();
    assert_eq!(call, deserialized);
}

#[test]
fn step_type_serialization_roundtrip() {
    let cases = [
        StepType::Contract,
        StepType::Route,
        StepType::ProviderCall,
        StepType::ToolExecution,
        StepType::Verification,
        StepType::Approval,
        StepType::ContextBuild,
        StepType::ArtifactStore,
        StepType::ReceiptSign,
    ];
    for step_type in cases {
        let json = serde_json::to_string(&step_type).unwrap();
        let deserialized: StepType = serde_json::from_str(&json).unwrap();
        assert_eq!(step_type, deserialized);
    }
}

#[test]
fn execution_error_display_formatting() {
    let err = ExecutionError::IllegalTransition {
        from: "pending".into(),
        to: "completed".into(),
        entity_type: "RunStatus".into(),
    };
    let msg = err.to_string();
    assert!(msg.contains("pending"));
    assert!(msg.contains("completed"));
    assert!(msg.contains("RunStatus"));
}

#[test]
fn execution_error_not_found() {
    let err = ExecutionError::NotFound("run-123".into());
    assert!(err.to_string().contains("run-123"));
}

#[test]
fn execution_error_conflict() {
    let err = ExecutionError::Conflict("lease expired".into());
    assert!(err.to_string().contains("lease expired"));
}
