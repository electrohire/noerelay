//! Unit tests for retry policy, circuit breaker, and failure classification.
//!
//! These tests validate:
//! - RetryPolicy delay calculation (exponential backoff)
//! - RetryPolicy respects max_attempts
//! - RetryPolicy respects budget limit
//! - RetryPolicy filters retryable failures
//! - CircuitBreaker state transitions
//! - CircuitBreaker allow_request behavior
//! - FailureClass classification

use chrono::{Duration, Utc};
use noerelay_core::execution::*;

// ============================================================================
// FailureClass Tests
// ============================================================================

#[test]
fn failure_class_transport_is_retryable() {
    assert!(FailureClass::Transport.is_retryable());
}

#[test]
fn failure_class_rate_quota_is_retryable() {
    assert!(FailureClass::RateQuota.is_retryable());
}

#[test]
fn failure_class_permanent_is_not_retryable() {
    assert!(!FailureClass::Permanent.is_retryable());
}

#[test]
fn failure_class_cancellation_is_not_retryable() {
    assert!(!FailureClass::Cancellation.is_retryable());
}

#[test]
fn failure_class_semantic_is_not_retryable() {
    assert!(!FailureClass::Semantic.is_retryable());
}

#[test]
fn failure_class_transport_trips_circuit_breaker() {
    assert!(FailureClass::Transport.trips_circuit_breaker());
}

#[test]
fn failure_class_permanent_trips_circuit_breaker() {
    assert!(FailureClass::Permanent.trips_circuit_breaker());
}

#[test]
fn failure_class_semantic_does_not_trip_circuit_breaker() {
    assert!(!FailureClass::Semantic.trips_circuit_breaker());
}

#[test]
fn failure_class_cancellation_does_not_trip_circuit_breaker() {
    assert!(!FailureClass::Cancellation.trips_circuit_breaker());
}

// ============================================================================
// RetryPolicy Tests
// ============================================================================

#[test]
fn retry_policy_delay_exponential_backoff() {
    let policy = RetryPolicy {
        max_attempts: 5,
        base_delay_ms: 1000,
        max_delay_ms: 60000,
        backoff_multiplier: 2.0,
        retryable_failures: vec![FailureClass::Transport],
        budget_limit_micro_usd: None,
    };

    // Attempt 1 (initial) = 0 delay
    assert_eq!(policy.delay_for_attempt(1), 0);
    // Attempt 2 = 1000 * 2^1 = 2000
    assert_eq!(policy.delay_for_attempt(2), 2000);
    // Attempt 3 = 1000 * 2^2 = 4000
    assert_eq!(policy.delay_for_attempt(3), 4000);
    // Attempt 4 = 1000 * 2^3 = 8000
    assert_eq!(policy.delay_for_attempt(4), 8000);
}

#[test]
fn retry_policy_delay_respects_max_delay() {
    let policy = RetryPolicy {
        max_attempts: 10,
        base_delay_ms: 1000,
        max_delay_ms: 5000,
        backoff_multiplier: 2.0,
        retryable_failures: vec![FailureClass::Transport],
        budget_limit_micro_usd: None,
    };

    // Attempt 4 = 1000 * 2^3 = 8000, capped at 5000
    assert_eq!(policy.delay_for_attempt(4), 5000);
    // Attempt 5 = 1000 * 2^4 = 16000, capped at 5000
    assert_eq!(policy.delay_for_attempt(5), 5000);
}

#[test]
fn retry_policy_delay_with_different_multiplier() {
    let policy = RetryPolicy {
        max_attempts: 5,
        base_delay_ms: 500,
        max_delay_ms: 30000,
        backoff_multiplier: 3.0,
        retryable_failures: vec![FailureClass::Transport],
        budget_limit_micro_usd: None,
    };

    assert_eq!(policy.delay_for_attempt(1), 0);
    assert_eq!(policy.delay_for_attempt(2), 1500); // 500 * 3^1
    assert_eq!(policy.delay_for_attempt(3), 4500); // 500 * 3^2
}

#[test]
fn retry_policy_respects_max_attempts() {
    let policy = RetryPolicy {
        max_attempts: 3,
        base_delay_ms: 1000,
        max_delay_ms: 60000,
        backoff_multiplier: 2.0,
        retryable_failures: vec![FailureClass::Transport],
        budget_limit_micro_usd: None,
    };

    // Attempt 1 (initial) with transport failure — should retry
    assert!(policy.should_retry(1, FailureClass::Transport, 0));
    // Attempt 2 — should retry
    assert!(policy.should_retry(2, FailureClass::Transport, 0));
    // Attempt 3 (max_attempts) — should NOT retry
    assert!(!policy.should_retry(3, FailureClass::Transport, 0));
}

#[test]
fn retry_policy_respects_budget_limit() {
    let policy = RetryPolicy {
        max_attempts: 10,
        base_delay_ms: 1000,
        max_delay_ms: 60000,
        backoff_multiplier: 2.0,
        retryable_failures: vec![FailureClass::Transport],
        budget_limit_micro_usd: Some(5000),
    };

    // Under budget — should retry
    assert!(policy.should_retry(1, FailureClass::Transport, 4000));
    // At budget limit — should NOT retry
    assert!(!policy.should_retry(1, FailureClass::Transport, 5000));
    // Over budget — should NOT retry
    assert!(!policy.should_retry(1, FailureClass::Transport, 6000));
}

#[test]
fn retry_policy_filters_non_retryable_failures() {
    let policy = RetryPolicy {
        max_attempts: 5,
        base_delay_ms: 1000,
        max_delay_ms: 60000,
        backoff_multiplier: 2.0,
        retryable_failures: vec![FailureClass::Transport, FailureClass::RateQuota],
        budget_limit_micro_usd: None,
    };

    // Transport is retryable
    assert!(policy.is_retryable(FailureClass::Transport));
    // RateQuota is retryable
    assert!(policy.is_retryable(FailureClass::RateQuota));
    // Permanent is NOT retryable
    assert!(!policy.is_retryable(FailureClass::Permanent));
    // Semantic is NOT retryable
    assert!(!policy.is_retryable(FailureClass::Semantic));

    // should_retry respects retryable_failures
    assert!(policy.should_retry(1, FailureClass::Transport, 0));
    assert!(!policy.should_retry(1, FailureClass::Permanent, 0));
}

#[test]
fn retry_policy_default_values() {
    let policy = RetryPolicy::default();
    assert_eq!(policy.max_attempts, 3);
    assert_eq!(policy.base_delay_ms, 1000);
    assert_eq!(policy.max_delay_ms, 60000);
    assert_eq!(policy.backoff_multiplier, 2.0);
    assert!(policy.budget_limit_micro_usd.is_none());
    assert!(policy.is_retryable(FailureClass::Transport));
    assert!(policy.is_retryable(FailureClass::RateQuota));
    assert!(!policy.is_retryable(FailureClass::Permanent));
}

#[test]
fn retry_policy_serialization_roundtrip() {
    let policy = RetryPolicy {
        max_attempts: 5,
        base_delay_ms: 2000,
        max_delay_ms: 30000,
        backoff_multiplier: 1.5,
        retryable_failures: vec![FailureClass::Transport, FailureClass::RateQuota],
        budget_limit_micro_usd: Some(10000),
    };

    let json = serde_json::to_string(&policy).unwrap();
    let deserialized: RetryPolicy = serde_json::from_str(&json).unwrap();

    assert_eq!(deserialized.max_attempts, 5);
    assert_eq!(deserialized.base_delay_ms, 2000);
    assert_eq!(deserialized.max_delay_ms, 30000);
    assert_eq!(deserialized.backoff_multiplier, 1.5);
    assert_eq!(deserialized.budget_limit_micro_usd, Some(10000));
    assert_eq!(deserialized.retryable_failures.len(), 2);
}

// ============================================================================
// CircuitBreaker Tests — State Transitions
// ============================================================================

fn make_circuit_breaker() -> CircuitBreaker {
    let now = Utc::now();
    CircuitBreaker {
        scope: "provider:test:model:test-model".into(),
        state: CircuitState::Closed,
        failure_count: 0,
        success_count: 0,
        failure_threshold: 3,
        success_threshold: 2,
        open_until: None,
        cooldown_seconds: 30,
        last_failure_at: None,
        last_success_at: None,
        created_at: now,
        updated_at: now,
    }
}

#[test]
fn circuit_breaker_closed_allows_requests() {
    let cb = make_circuit_breaker();
    assert!(cb.allow_request());
}

#[test]
fn circuit_breaker_closed_to_open_on_threshold() {
    let mut cb = make_circuit_breaker();
    assert_eq!(cb.state, CircuitState::Closed);

    // Record failures up to threshold
    cb.record_failure();
    assert_eq!(cb.state, CircuitState::Closed);
    assert_eq!(cb.failure_count, 1);

    cb.record_failure();
    assert_eq!(cb.state, CircuitState::Closed);
    assert_eq!(cb.failure_count, 2);

    cb.record_failure(); // This should trip the breaker
    assert_eq!(cb.state, CircuitState::Open);
    assert_eq!(cb.failure_count, 3);
    assert!(cb.open_until.is_some());
}

#[test]
fn circuit_breaker_open_denies_requests() {
    let mut cb = make_circuit_breaker();
    // Trip the breaker
    cb.record_failure();
    cb.record_failure();
    cb.record_failure();
    assert_eq!(cb.state, CircuitState::Open);
    assert!(!cb.allow_request());
}

#[test]
fn circuit_breaker_open_to_half_open_after_cooldown() {
    let mut cb = make_circuit_breaker();
    cb.cooldown_seconds = 0; // Immediate cooldown for testing

    // Trip the breaker
    cb.record_failure();
    cb.record_failure();
    cb.record_failure();
    assert_eq!(cb.state, CircuitState::Open);

    // With cooldown_seconds=0, allow_request should return true
    // (cooldown has elapsed)
    assert!(cb.allow_request());
}

#[test]
fn circuit_breaker_half_open_to_closed_on_success_threshold() {
    let mut cb = make_circuit_breaker();
    cb.cooldown_seconds = 0; // Immediate cooldown

    // Trip the breaker
    cb.record_failure();
    cb.record_failure();
    cb.record_failure();
    assert_eq!(cb.state, CircuitState::Open);

    // Allow request (cooldown elapsed) and record success
    // The allow_request doesn't transition state; record_success does
    // First, we need to manually transition or use record_success
    // record_success in Open state with elapsed cooldown transitions to HalfOpen
    cb.record_success();
    assert_eq!(cb.state, CircuitState::HalfOpen);
    assert_eq!(cb.success_count, 1);

    // Second success should transition to Closed
    cb.record_success();
    assert_eq!(cb.state, CircuitState::Closed);
    assert_eq!(cb.success_count, 0);
    assert_eq!(cb.failure_count, 0);
}

#[test]
fn circuit_breaker_half_open_to_open_on_failure() {
    let mut cb = make_circuit_breaker();
    cb.cooldown_seconds = 0;

    // Trip the breaker
    cb.record_failure();
    cb.record_failure();
    cb.record_failure();
    assert_eq!(cb.state, CircuitState::Open);

    // Transition to half-open via success
    cb.record_success();
    assert_eq!(cb.state, CircuitState::HalfOpen);

    // A failure in half-open should re-open the circuit
    cb.record_failure();
    assert_eq!(cb.state, CircuitState::Open);
    assert_eq!(cb.failure_count, 1);
}

#[test]
fn circuit_breaker_success_in_closed_resets_failure_count() {
    let mut cb = make_circuit_breaker();

    cb.record_failure();
    cb.record_failure();
    assert_eq!(cb.failure_count, 2);

    // Success resets failure count
    cb.record_success();
    assert_eq!(cb.failure_count, 0);
    assert_eq!(cb.state, CircuitState::Closed);
}

#[test]
fn circuit_breaker_open_extends_cooldown_on_repeated_failures() {
    let mut cb = make_circuit_breaker();

    // Trip the breaker
    cb.record_failure();
    cb.record_failure();
    cb.record_failure();
    assert_eq!(cb.state, CircuitState::Open);

    let first_open_until = cb.open_until;

    // Another failure in open state extends cooldown
    cb.record_failure();
    assert_eq!(cb.state, CircuitState::Open);
    assert!(cb.open_until > first_open_until);
}

#[test]
fn circuit_breaker_allow_request_open_with_elapsed_cooldown() {
    let mut cb = make_circuit_breaker();

    // Trip the breaker with cooldown in the past
    cb.record_failure();
    cb.record_failure();
    cb.record_failure();
    assert_eq!(cb.state, CircuitState::Open);

    // Manually set open_until to the past
    cb.open_until = Some(Utc::now() - Duration::seconds(60));
    assert!(cb.allow_request());
}

#[test]
fn circuit_breaker_serialization_roundtrip() {
    let cb = make_circuit_breaker();
    let json = serde_json::to_string(&cb).unwrap();
    let deserialized: CircuitBreaker = serde_json::from_str(&json).unwrap();

    assert_eq!(deserialized.scope, cb.scope);
    assert_eq!(deserialized.state, cb.state);
    assert_eq!(deserialized.failure_threshold, cb.failure_threshold);
    assert_eq!(deserialized.success_threshold, cb.success_threshold);
    assert_eq!(deserialized.cooldown_seconds, cb.cooldown_seconds);
}

// ============================================================================
// OutboxEvent Tests
// ============================================================================

#[test]
fn outbox_event_serialization_roundtrip() {
    let event = OutboxEvent {
        id: uuid::Uuid::new_v4(),
        aggregate_id: "run-123".into(),
        aggregate_type: "run".into(),
        event_type: "run.completed".into(),
        payload: serde_json::json!({"status": "completed"}),
        created_at: Utc::now(),
        published_at: None,
        delivery_attempts: 0,
        status: OutboxEventStatus::Pending,
    };

    let json = serde_json::to_string(&event).unwrap();
    let deserialized: OutboxEvent = serde_json::from_str(&json).unwrap();

    assert_eq!(deserialized.id, event.id);
    assert_eq!(deserialized.aggregate_id, "run-123");
    assert_eq!(deserialized.event_type, "run.completed");
    assert_eq!(deserialized.status, OutboxEventStatus::Pending);
}

// ============================================================================
// LeaseInfo Tests
// ============================================================================

#[test]
fn lease_info_serialization_roundtrip() {
    let lease = LeaseInfo {
        lease_id: "lease-abc".into(),
        worker_id: "worker-1".into(),
        acquired_at: Utc::now(),
        expires_at: Utc::now() + Duration::seconds(30),
        heartbeat_at: None,
        fencing_token: 5,
    };

    let json = serde_json::to_string(&lease).unwrap();
    let deserialized: LeaseInfo = serde_json::from_str(&json).unwrap();

    assert_eq!(deserialized.lease_id, "lease-abc");
    assert_eq!(deserialized.worker_id, "worker-1");
    assert_eq!(deserialized.fencing_token, 5);
}
