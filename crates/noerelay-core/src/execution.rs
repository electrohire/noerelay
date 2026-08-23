//! Durable execution state machines for NoeRelay runs.
//!
//! This module defines the normalized append-friendly execution entities:
//! runs, steps, attempts, work items, reservations, tool effects, and
//! provider calls. Each entity has a formal state machine with explicitly
//! defined legal transitions, enforced both in Rust and at the database level.
//!
//! # Design
//!
//! - **Runs** are the top-level execution unit, scoped to an organization
//!   and optionally a project/environment.
//! - **Steps** decompose a run into discrete phases (contract compilation,
//!   provider calls, verification, etc.).
//! - **Attempts** represent individual retry attempts for a step.
//! - **Work items** form a durable queue for asynchronous work distribution.
//! - **Reservations** track budget reservations for cost control.
//! - **Tool effects** record side effects from tool executions.
//! - **Provider calls** capture LLM provider API call details.

use chrono::{DateTime, Utc};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use thiserror::Error;
use uuid::Uuid;

use crate::iam::{EnvironmentId, OrganizationId, PrincipalId, ProjectId};

// ============================================================================
// Identifier Types
// ============================================================================

/// Unique identifier for a run (UUID v4).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct RunId(pub Uuid);

/// Unique identifier for a step (UUID v4).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct StepId(pub Uuid);

/// Unique identifier for an attempt (UUID v4).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct AttemptId(pub Uuid);

/// Unique identifier for a work item (UUID v4).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct WorkItemId(pub Uuid);

/// Unique identifier for a reservation (UUID v4).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct ReservationId(pub Uuid);

/// Unique identifier for a tool effect (UUID v4).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct ToolEffectId(pub Uuid);

/// Unique identifier for a provider call (UUID v4).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct ProviderCallId(pub Uuid);

// ============================================================================
// Idempotency, Cancellation, and Effect Protocol Types
// ============================================================================

/// A client idempotency key bound to the full compatibility and authority scope.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct IdempotencyKey {
    pub key: String,
    pub principal_id: PrincipalId,
    pub endpoint_profile: String,
    pub request_hash: String,
    pub policy_revision: String,
}

/// Durable state associated with a scoped idempotency key.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct IdempotencyRecord {
    pub id: Uuid,
    pub idempotency_key: IdempotencyKey,
    pub organization_id: OrganizationId,
    pub run_id: Option<RunId>,
    pub status: IdempotencyStatus,
    pub response_ref: Option<String>,
    pub terminal_receipt_id: Option<String>,
    pub created_at: DateTime<Utc>,
    pub completed_at: Option<DateTime<Utc>>,
    pub expires_at: Option<DateTime<Utc>>,
}

/// Lifecycle of a scoped idempotency claim.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum IdempotencyStatus {
    InProgress,
    Completed,
    Failed,
    Expired,
}

impl IdempotencyStatus {
    /// Whether this status may atomically transition to `target`.
    pub fn can_transition(self, target: Self) -> bool {
        matches!(
            (self, target),
            (
                Self::InProgress,
                Self::Completed | Self::Failed | Self::Expired
            ) | (Self::Failed | Self::Expired, Self::InProgress)
        )
    }
}

/// Why cancellation was requested.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum CancellationReason {
    UserRequested,
    Timeout,
    BudgetExceeded,
    PolicyViolation,
    ParentCancelled,
    DependencyFailed,
}

/// Request to cancel a run and, optionally, all descendants.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CancellationRequest {
    pub run_id: RunId,
    pub reason: CancellationReason,
    pub requested_by: PrincipalId,
    pub requested_at: DateTime<Utc>,
    pub propagate: bool,
}

/// Resources affected by an atomic cancellation operation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CancellationResult {
    pub run_id: RunId,
    pub cancelled: bool,
    pub cancelled_descendants: Vec<RunId>,
    pub released_reservations: Vec<ReservationId>,
    pub cancelled_provider_calls: Vec<ProviderCallId>,
    pub cancelled_tool_effects: Vec<ToolEffectId>,
}

/// A durable cancellation audit entry.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CancellationLogEntry {
    pub id: Uuid,
    pub run_id: RunId,
    pub reason: CancellationReason,
    pub requested_by: PrincipalId,
    pub requested_at: DateTime<Utc>,
    pub processed_at: Option<DateTime<Utc>>,
    pub cancelled_descendants: Vec<RunId>,
    pub released_reservations: Vec<ReservationId>,
    pub cancelled_provider_calls: Vec<ProviderCallId>,
    pub cancelled_tool_effects: Vec<ToolEffectId>,
}

/// Declared side-effect class for an effect request.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum EffectIntent {
    Read,
    Write,
    Delete,
}

/// Intent durably recorded before dispatching a tool effect.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EffectRequest {
    pub effect_id: String,
    pub tool_id: String,
    pub intent: EffectIntent,
    pub request_hash: String,
    pub idempotency_key: Option<String>,
    pub created_at: DateTime<Utc>,
}

/// Durable outcome or reconciliation state of an effect.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum EffectResultStatus {
    Applied,
    Rejected,
    Unknown,
    Reconciled,
    Compensated,
}

/// Result paired with a previously recorded effect intent.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EffectResult {
    pub effect_id: String,
    pub status: EffectResultStatus,
    pub external_effect_id: Option<String>,
    pub response_hash: Option<String>,
    pub reconciled_at: Option<DateTime<Utc>>,
    pub error: Option<String>,
    pub created_at: DateTime<Utc>,
}

// ============================================================================
// State Machine Enums
// ============================================================================

/// Top-level run lifecycle.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum RunStatus {
    /// Run has been created but not yet started.
    Pending,
    /// Run is actively executing.
    Running,
    /// Run is blocked waiting for human approval.
    AwaitingApproval,
    /// Run is blocked waiting for verification checks.
    AwaitingVerification,
    /// Run completed successfully.
    Completed,
    /// Run failed with an error.
    Failed,
    /// Run was cancelled by a principal.
    Cancelled,
    /// Run exceeded its time budget.
    TimedOut,
}

/// Step lifecycle within a run.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum StepStatus {
    /// Step has been created but is not yet ready to execute.
    Pending,
    /// Step dependencies are satisfied and it is ready to run.
    Ready,
    /// Step is actively executing.
    Running,
    /// Step completed successfully.
    Completed,
    /// Step failed with an error.
    Failed,
    /// Step was skipped (e.g., conditional branch not taken).
    Skipped,
    /// Step was cancelled.
    Cancelled,
}

/// Attempt lifecycle for retryable step execution.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum AttemptStatus {
    /// Attempt has been created but not yet started.
    Pending,
    /// Attempt is actively executing.
    Running,
    /// Attempt succeeded.
    Succeeded,
    /// Attempt failed (may be retried).
    Failed,
    /// Attempt was cancelled.
    Cancelled,
    /// Attempt timed out.
    TimedOut,
}

/// Work item lifecycle in the durable queue.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum WorkItemStatus {
    /// Item is waiting to be claimed.
    Pending,
    /// Item has been claimed by a worker.
    Claimed,
    /// Item is actively being processed.
    Running,
    /// Item completed successfully.
    Completed,
    /// Item failed (may be retried if attempts < max_attempts).
    Failed,
    /// Item was cancelled.
    Cancelled,
    /// Item exceeded max attempts and was moved to dead letter queue.
    DeadLetter,
}

/// Reservation lifecycle for budget tracking.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ReservationStatus {
    /// Reservation is active and holding budget.
    Active,
    /// Reservation has been explicitly released.
    Released,
    /// Reservation expired without being consumed.
    Expired,
    /// Reservation was consumed (budget spent).
    Consumed,
}

// ============================================================================
// Step Type Enum
// ============================================================================

/// Classification of step purpose within a run.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum StepType {
    /// Compile and validate a task contract.
    Contract,
    /// Route a request to a provider/model.
    Route,
    /// Execute a provider API call.
    ProviderCall,
    /// Execute a tool (function calling).
    ToolExecution,
    /// Run verification checks.
    Verification,
    /// Obtain human approval.
    Approval,
    /// Build context manifest.
    ContextBuild,
    /// Store an artifact.
    ArtifactStore,
    /// Sign a run receipt.
    ReceiptSign,
}

// ============================================================================
// Entity Structs
// ============================================================================

/// A durable execution run — the top-level unit of work.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Run {
    /// Unique run identifier.
    pub id: RunId,
    /// Owning organization.
    pub organization_id: OrganizationId,
    /// Optional project scope.
    pub project_id: Option<ProjectId>,
    /// Optional environment scope.
    pub environment_id: Option<EnvironmentId>,
    /// Principal that initiated the run.
    pub principal_id: PrincipalId,
    /// Hash of the task contract governing this run.
    pub contract_hash: String,
    /// Optional hash of the context manifest.
    pub context_manifest_hash: Option<String>,
    /// Policy revision identifier.
    pub policy_revision: String,
    /// Current run status.
    pub status: RunStatus,
    /// Optional parent run for nested/sub-run relationships.
    pub parent_run_id: Option<RunId>,
    /// When the run was created.
    pub created_at: DateTime<Utc>,
    /// When the run was last updated.
    pub updated_at: DateTime<Utc>,
    /// When the run reached a terminal state.
    pub completed_at: Option<DateTime<Utc>>,
    /// Optional reference to the terminal signed receipt.
    pub terminal_receipt_id: Option<String>,
}

/// A discrete step within a run.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Step {
    /// Unique step identifier.
    pub id: StepId,
    /// Owning run.
    pub run_id: RunId,
    /// Optional parent step for nested step hierarchies.
    pub parent_step_id: Option<StepId>,
    /// Classification of this step.
    pub step_type: StepType,
    /// Human-readable step name.
    pub name: String,
    /// Current step status.
    pub status: StepStatus,
    /// Ordering within the run (lower = earlier).
    pub sequence: i32,
    /// Optional hash of step input data.
    pub input_hash: Option<String>,
    /// Optional hash of step output data.
    pub output_hash: Option<String>,
    /// When the step was created.
    pub created_at: DateTime<Utc>,
    /// When the step was last updated.
    pub updated_at: DateTime<Utc>,
    /// When the step reached a terminal state.
    pub completed_at: Option<DateTime<Utc>>,
}

/// A single execution attempt for a step (supports retries).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Attempt {
    /// Unique attempt identifier.
    pub id: AttemptId,
    /// Owning step.
    pub step_id: StepId,
    /// Monotonically increasing attempt number (1-based).
    pub attempt_number: i32,
    /// Current attempt status.
    pub status: AttemptStatus,
    /// Optional associated provider call.
    pub provider_call_id: Option<ProviderCallId>,
    /// When the attempt started executing.
    pub started_at: DateTime<Utc>,
    /// When the attempt reached a terminal state.
    pub completed_at: Option<DateTime<Utc>>,
    /// Error message if the attempt failed.
    pub error: Option<String>,
    /// Cost in micro USD (1/1,000,000 of a cent).
    pub cost_micro_usd: Option<i64>,
}

/// A durable work item for asynchronous processing.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct WorkItem {
    /// Unique work item identifier.
    pub id: WorkItemId,
    /// Owning run.
    pub run_id: RunId,
    /// Optional associated step.
    pub step_id: Option<StepId>,
    /// Work item type for routing/dispatch.
    pub item_type: String,
    /// Arbitrary JSON payload.
    pub payload: serde_json::Value,
    /// Current work item status.
    pub status: WorkItemStatus,
    /// Priority (higher = more urgent).
    pub priority: i32,
    /// Lease identifier for the claiming worker.
    pub lease_id: Option<String>,
    /// When the current lease expires.
    pub lease_expires_at: Option<DateTime<Utc>>,
    /// Monotonically increasing fencing token for lease ownership.
    pub fencing_token: Option<i64>,
    /// Number of processing attempts so far.
    pub attempts: i32,
    /// Maximum number of attempts before dead letter.
    pub max_attempts: i32,
    /// Earliest time this item can be claimed.
    pub available_at: DateTime<Utc>,
    /// When the work item was created.
    pub created_at: DateTime<Utc>,
    /// When the work item was last updated.
    pub updated_at: DateTime<Utc>,
    /// Monotonically increasing optimistic-concurrency version.
    pub version: i64,
}

/// A budget reservation for cost control.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Reservation {
    /// Unique reservation identifier.
    pub id: ReservationId,
    /// Owning run.
    pub run_id: RunId,
    /// Type of resource being reserved.
    pub resource_type: String,
    /// Identifier of the specific resource.
    pub resource_id: String,
    /// Current reservation status.
    pub status: ReservationStatus,
    /// Reserved amount in micro USD.
    pub amount_micro_usd: i64,
    /// When this reservation expires.
    pub expires_at: DateTime<Utc>,
    /// When the reservation was created.
    pub created_at: DateTime<Utc>,
    /// When the reservation was released (if applicable).
    pub released_at: Option<DateTime<Utc>>,
}

/// A recorded side effect from a tool execution.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ToolEffect {
    /// Unique tool effect identifier.
    pub id: ToolEffectId,
    /// Owning attempt.
    pub attempt_id: AttemptId,
    /// Identifier of the tool that produced this effect.
    pub tool_id: String,
    /// Kind of effect (e.g., "file_write", "shell_exec", "api_call").
    pub effect_kind: String,
    /// Optional external identifier for the effect.
    pub effect_id_external: Option<String>,
    /// Current reconciliation status.
    pub status: String,
    /// Hash of the effect request.
    pub request_hash: String,
    /// Optional hash of the effect response.
    pub response_hash: Option<String>,
    /// When the effect was recorded.
    pub created_at: DateTime<Utc>,
    /// When the effect was reconciled.
    pub reconciled_at: Option<DateTime<Utc>>,
}

/// A recorded LLM provider API call.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ProviderCall {
    /// Unique provider call identifier.
    pub id: ProviderCallId,
    /// Owning attempt.
    pub attempt_id: AttemptId,
    /// Provider name (e.g., "openai", "anthropic").
    pub provider: String,
    /// Model identifier (e.g., "gpt-4o", "claude-sonnet-4-20250514").
    pub model: String,
    /// Hash of the request payload.
    pub request_hash: String,
    /// Optional hash of the response payload.
    pub response_hash: Option<String>,
    /// Number of input/prompt tokens consumed.
    pub usage_input_tokens: Option<i32>,
    /// Number of output/completion tokens consumed.
    pub usage_output_tokens: Option<i32>,
    /// Current call status.
    pub status: String,
    /// When the call was initiated.
    pub started_at: DateTime<Utc>,
    /// When the call completed.
    pub completed_at: Option<DateTime<Utc>>,
}

// ============================================================================
// Failure Classification
// ============================================================================

/// Classification of execution failures for retry and circuit-breaking decisions.
///
/// Each failure class determines whether an operation should be retried and
/// how circuit breakers should respond.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum FailureClass {
    /// Network, DNS, TLS, or timeout errors — typically transient.
    Transport,
    /// Rate limited or quota exceeded — backoff and retry.
    RateQuota,
    /// Model or provider doesn't support the requested feature.
    Capability,
    /// Response doesn't meet semantic requirements (e.g., validation failure).
    Semantic,
    /// Insufficient evidence or confidence in the result.
    Epistemic,
    /// Blocked by policy (e.g., content filter, governance rule).
    Policy,
    /// Protocol or schema violation in request/response.
    Specification,
    /// Explicitly cancelled by a principal or system.
    Cancellation,
    /// Unrecoverable error — should not be retried.
    Permanent,
}

impl FailureClass {
    /// Returns true if this failure class is generally retryable.
    pub fn is_retryable(self) -> bool {
        matches!(self, FailureClass::Transport | FailureClass::RateQuota)
    }

    /// Returns true if this failure class should trip a circuit breaker.
    pub fn trips_circuit_breaker(self) -> bool {
        matches!(
            self,
            FailureClass::Transport
                | FailureClass::RateQuota
                | FailureClass::Capability
                | FailureClass::Specification
                | FailureClass::Permanent
        )
    }
}

// ============================================================================
// Retry Policy
// ============================================================================

/// Operation-specific retry policy with budget awareness.
///
/// Controls how many times an operation can be retried, the backoff strategy,
/// which failure classes are retryable, and an optional cost budget limit.
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct RetryPolicy {
    /// Maximum number of attempts (including the initial attempt).
    pub max_attempts: i32,
    /// Base delay in milliseconds before the first retry.
    pub base_delay_ms: i64,
    /// Maximum delay in milliseconds between retries.
    pub max_delay_ms: i64,
    /// Multiplier for exponential backoff (e.g., 2.0 doubles each attempt).
    pub backoff_multiplier: f64,
    /// Failure classes that are eligible for retry.
    pub retryable_failures: Vec<FailureClass>,
    /// Optional budget limit in micro USD (1/1,000,000 of a cent).
    /// If set, retries stop when cumulative cost exceeds this limit.
    pub budget_limit_micro_usd: Option<i64>,
}

impl RetryPolicy {
    /// Compute the delay in milliseconds for a given attempt number (1-based).
    ///
    /// Uses exponential backoff: `base_delay_ms * backoff_multiplier^(attempt-1)`,
    /// capped at `max_delay_ms`.
    pub fn delay_for_attempt(&self, attempt: i32) -> i64 {
        if attempt <= 1 {
            return 0;
        }
        let exponent = (attempt - 1) as f64;
        let delay = self.base_delay_ms as f64 * self.backoff_multiplier.powf(exponent);
        (delay as i64).min(self.max_delay_ms)
    }

    /// Returns true if the given failure class is retryable under this policy.
    pub fn is_retryable(&self, failure: FailureClass) -> bool {
        self.retryable_failures.contains(&failure)
    }

    /// Returns true if the operation should be retried given the current state.
    ///
    /// Considers the attempt number, failure class, and cumulative cost spent.
    pub fn should_retry(&self, attempt: i32, failure: FailureClass, spent_micro_usd: i64) -> bool {
        if attempt >= self.max_attempts {
            return false;
        }
        if !self.is_retryable(failure) {
            return false;
        }
        if let Some(budget) = self.budget_limit_micro_usd {
            if spent_micro_usd >= budget {
                return false;
            }
        }
        true
    }
}

impl Default for RetryPolicy {
    fn default() -> Self {
        Self {
            max_attempts: 3,
            base_delay_ms: 1000,
            max_delay_ms: 60000,
            backoff_multiplier: 2.0,
            retryable_failures: vec![FailureClass::Transport, FailureClass::RateQuota],
            budget_limit_micro_usd: None,
        }
    }
}

// ============================================================================
// Circuit Breaker
// ============================================================================

/// Circuit breaker state for failing fast when a downstream dependency is unhealthy.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum CircuitState {
    /// Normal operation — requests are allowed.
    Closed,
    /// Testing recovery — a limited number of requests are allowed.
    HalfOpen,
    /// Failing fast — all requests are rejected.
    Open,
}

/// A circuit breaker scoped by provider, model, agent, or tool.
///
/// Tracks failure/success counts and transitions between [`CircuitState`] variants
/// based on configurable thresholds and cooldown periods.
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct CircuitBreaker {
    /// Scope identifier (e.g., "provider:openrouter:model:gpt-4o").
    pub scope: String,
    /// Current circuit state.
    pub state: CircuitState,
    /// Consecutive failure count in the current window.
    pub failure_count: i32,
    /// Consecutive success count in the current window.
    pub success_count: i32,
    /// Number of consecutive failures to transition Closed → Open.
    pub failure_threshold: i32,
    /// Number of consecutive successes to transition HalfOpen → Closed.
    pub success_threshold: i32,
    /// If in Open state, the time after which the circuit transitions to HalfOpen.
    pub open_until: Option<DateTime<Utc>>,
    /// Cooldown period in seconds before transitioning Open → HalfOpen.
    pub cooldown_seconds: i64,
    /// Timestamp of the last recorded failure.
    pub last_failure_at: Option<DateTime<Utc>>,
    /// Timestamp of the last recorded success.
    pub last_success_at: Option<DateTime<Utc>>,
    /// When this circuit breaker was created.
    pub created_at: DateTime<Utc>,
    /// When this circuit breaker was last updated.
    pub updated_at: DateTime<Utc>,
}

impl CircuitBreaker {
    /// Record a successful request and transition state if appropriate.
    pub fn record_success(&mut self) {
        let now = Utc::now();
        self.last_success_at = Some(now);
        self.updated_at = now;

        match self.state {
            CircuitState::Closed => {
                // Reset failure count on success in closed state
                self.failure_count = 0;
            }
            CircuitState::HalfOpen => {
                self.success_count += 1;
                if self.success_count >= self.success_threshold {
                    self.state = CircuitState::Closed;
                    self.failure_count = 0;
                    self.success_count = 0;
                }
            }
            CircuitState::Open => {
                // Success in open state shouldn't normally happen,
                // but if it does, check if cooldown has elapsed
                if let Some(open_until) = self.open_until {
                    if now >= open_until {
                        self.state = CircuitState::HalfOpen;
                        self.success_count = 1;
                        self.failure_count = 0;
                    }
                }
            }
        }
    }

    /// Record a failed request and transition state if appropriate.
    pub fn record_failure(&mut self) {
        let now = Utc::now();
        self.last_failure_at = Some(now);
        self.updated_at = now;

        match self.state {
            CircuitState::Closed => {
                self.failure_count += 1;
                if self.failure_count >= self.failure_threshold {
                    self.state = CircuitState::Open;
                    self.open_until = Some(now + chrono::Duration::seconds(self.cooldown_seconds));
                    self.success_count = 0;
                }
            }
            CircuitState::HalfOpen => {
                // Any failure in half-open immediately re-opens the circuit
                self.state = CircuitState::Open;
                self.failure_count = 1;
                self.success_count = 0;
                self.open_until = Some(now + chrono::Duration::seconds(self.cooldown_seconds));
            }
            CircuitState::Open => {
                self.failure_count += 1;
                // Extend the cooldown on repeated failures
                self.open_until = Some(now + chrono::Duration::seconds(self.cooldown_seconds));
            }
        }
    }

    /// Returns true if a request should be allowed through the circuit breaker.
    pub fn allow_request(&self) -> bool {
        match self.state {
            CircuitState::Closed => true,
            CircuitState::HalfOpen => true,
            CircuitState::Open => {
                // Check if cooldown has elapsed
                if let Some(open_until) = self.open_until {
                    Utc::now() >= open_until
                } else {
                    false
                }
            }
        }
    }
}

// ============================================================================
// Outbox Event
// ============================================================================

/// Status of an outbox event in the transactional outbox pattern.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum OutboxEventStatus {
    /// Event has been inserted but not yet published.
    Pending,
    /// Event has been successfully published to the message broker.
    Published,
    /// Event publication failed but may be retried.
    Failed,
    /// Event has been dead-lettered after exceeding max delivery attempts.
    DeadLettered,
}

/// An event stored in the transactional outbox for reliable publishing.
///
/// The outbox pattern ensures that state changes and event publications are
/// atomic: the event is written in the same database transaction as the
/// state change, and a separate process publishes pending events.
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct OutboxEvent {
    /// Unique event identifier.
    pub id: Uuid,
    /// Identifier of the aggregate that produced this event.
    pub aggregate_id: String,
    /// Type of the aggregate (e.g., "run", "step", "work_item").
    pub aggregate_type: String,
    /// Event type name (e.g., "run.completed", "work_item.claimed").
    pub event_type: String,
    /// Event payload as arbitrary JSON.
    pub payload: serde_json::Value,
    /// When the event was created.
    pub created_at: DateTime<Utc>,
    /// When the event was successfully published.
    pub published_at: Option<DateTime<Utc>>,
    /// Number of delivery attempts so far.
    pub delivery_attempts: i32,
    /// Current event status.
    pub status: OutboxEventStatus,
}

// ============================================================================
// Lease Info
// ============================================================================

/// Information about a lease held by a worker on a work item.
///
/// Leases provide bounded ownership with fencing tokens to prevent
/// split-brain scenarios in distributed worker pools.
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct LeaseInfo {
    /// Unique lease identifier.
    pub lease_id: String,
    /// Identifier of the worker holding this lease.
    pub worker_id: String,
    /// When the lease was acquired.
    pub acquired_at: DateTime<Utc>,
    /// When the lease expires.
    pub expires_at: DateTime<Utc>,
    /// When the worker last sent a heartbeat.
    pub heartbeat_at: Option<DateTime<Utc>>,
    /// Monotonically increasing fencing token for lease ownership validation.
    pub fencing_token: i64,
}

// ============================================================================
// Multi-Replica Coordination
// ============================================================================

/// Durable registration and liveness state for a worker replica.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct WorkerRegistration {
    pub worker_id: String,
    pub worker_version: String,
    pub capabilities: Vec<String>,
    pub registered_at: DateTime<Utc>,
    pub last_heartbeat_at: DateTime<Utc>,
    pub status: WorkerStatus,
}

/// Lifecycle state for a registered worker.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum WorkerStatus {
    Active,
    Draining,
    Drained,
    Failed,
    Decommissioned,
}

impl WorkerStatus {
    /// Returns the set of legal next states from this status.
    pub fn legal_transitions(&self) -> &[WorkerStatus] {
        match self {
            Self::Active => &[Self::Draining, Self::Failed],
            Self::Draining => &[Self::Drained, Self::Failed],
            Self::Drained => &[Self::Decommissioned],
            Self::Failed => &[Self::Decommissioned],
            Self::Decommissioned => &[],
        }
    }

    /// Returns whether this worker may transition to `to`.
    pub fn can_transition(&self, to: WorkerStatus) -> bool {
        self.legal_transitions().contains(&to)
    }
}

/// Exclusive, fenced ownership of a replica-served stream.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct StreamOwnership {
    pub stream_id: String,
    pub owner_worker_id: String,
    pub acquired_at: DateTime<Utc>,
    pub fencing_token: i64,
    pub expires_at: DateTime<Utc>,
    pub last_heartbeat_at: Option<DateTime<Utc>>,
}

/// Action selected after a multi-replica conflict is detected.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ConflictResolution {
    ReloadAndRetry,
    TakeOver,
    Wait,
    Abort,
}

/// Auditable description of a multi-replica conflict.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ConflictReport {
    pub work_item_id: WorkItemId,
    pub conflict_type: ConflictType,
    pub resolution: ConflictResolution,
    pub current_owner: Option<String>,
    pub fencing_token: i64,
    pub detected_at: DateTime<Utc>,
}

/// Classifies conflicts relevant to ownership and failover recovery.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ConflictType {
    VersionMismatch,
    LeaseExpired,
    WorkerUnresponsive,
    DatabaseFailover,
    StreamOwnershipLost,
}

/// Result of a lightweight authoritative-database health probe.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct DatabaseHealth {
    pub healthy: bool,
    pub latency_ms: i64,
    pub checked_at: DateTime<Utc>,
}

// ============================================================================
// State Machine Logic — RunStatus
// ============================================================================

impl RunStatus {
    /// Returns the set of legal next states from this status.
    pub fn legal_transitions(&self) -> &[RunStatus] {
        match self {
            RunStatus::Pending => &[RunStatus::Running, RunStatus::Cancelled],
            RunStatus::Running => &[
                RunStatus::AwaitingApproval,
                RunStatus::AwaitingVerification,
                RunStatus::Completed,
                RunStatus::Failed,
                RunStatus::Cancelled,
                RunStatus::TimedOut,
            ],
            RunStatus::AwaitingApproval => &[
                RunStatus::Running,
                RunStatus::Failed,
                RunStatus::Cancelled,
                RunStatus::TimedOut,
            ],
            RunStatus::AwaitingVerification => &[
                RunStatus::Running,
                RunStatus::Completed,
                RunStatus::Failed,
                RunStatus::Cancelled,
                RunStatus::TimedOut,
            ],
            RunStatus::Completed => &[],
            RunStatus::Failed => &[],
            RunStatus::Cancelled => &[],
            RunStatus::TimedOut => &[],
        }
    }

    /// Returns true if transitioning from `self` to `to` is legal.
    pub fn can_transition(&self, to: RunStatus) -> bool {
        self.legal_transitions().contains(&to)
    }

    /// Returns true if this is a terminal (non-progressable) state.
    pub fn is_terminal(&self) -> bool {
        matches!(
            self,
            RunStatus::Completed | RunStatus::Failed | RunStatus::Cancelled | RunStatus::TimedOut
        )
    }
}

/// State machine for [`Run`] transitions.
pub struct RunStateMachine;

impl RunStateMachine {
    /// Attempt to transition a run from `current` to `target`.
    ///
    /// Returns the new status on success, or an [`ExecutionError::IllegalTransition`]
    /// if the transition is not allowed.
    pub fn transition(current: RunStatus, target: RunStatus) -> Result<RunStatus, ExecutionError> {
        if current.can_transition(target) {
            Ok(target)
        } else {
            Err(ExecutionError::IllegalTransition {
                from: format!("{:?}", current),
                to: format!("{:?}", target),
                entity_type: "RunStatus".into(),
            })
        }
    }
}

// ============================================================================
// State Machine Logic — StepStatus
// ============================================================================

impl StepStatus {
    /// Returns the set of legal next states from this status.
    pub fn legal_transitions(&self) -> &[StepStatus] {
        match self {
            StepStatus::Pending => &[
                StepStatus::Ready,
                StepStatus::Cancelled,
                StepStatus::Skipped,
            ],
            StepStatus::Ready => &[
                StepStatus::Running,
                StepStatus::Cancelled,
                StepStatus::Skipped,
            ],
            StepStatus::Running => &[
                StepStatus::Completed,
                StepStatus::Failed,
                StepStatus::Cancelled,
            ],
            StepStatus::Completed => &[],
            StepStatus::Failed => &[StepStatus::Pending, StepStatus::Ready],
            StepStatus::Skipped => &[],
            StepStatus::Cancelled => &[],
        }
    }

    /// Returns true if transitioning from `self` to `to` is legal.
    pub fn can_transition(&self, to: StepStatus) -> bool {
        self.legal_transitions().contains(&to)
    }

    /// Returns true if this is a terminal (non-progressable) state.
    pub fn is_terminal(&self) -> bool {
        matches!(
            self,
            StepStatus::Completed
                | StepStatus::Failed
                | StepStatus::Skipped
                | StepStatus::Cancelled
        )
    }
}

/// State machine for [`Step`] transitions.
pub struct StepStateMachine;

impl StepStateMachine {
    /// Attempt to transition a step from `current` to `target`.
    pub fn transition(
        current: StepStatus,
        target: StepStatus,
    ) -> Result<StepStatus, ExecutionError> {
        if current.can_transition(target) {
            Ok(target)
        } else {
            Err(ExecutionError::IllegalTransition {
                from: format!("{:?}", current),
                to: format!("{:?}", target),
                entity_type: "StepStatus".into(),
            })
        }
    }
}

// ============================================================================
// State Machine Logic — AttemptStatus
// ============================================================================

impl AttemptStatus {
    /// Returns the set of legal next states from this status.
    pub fn legal_transitions(&self) -> &[AttemptStatus] {
        match self {
            AttemptStatus::Pending => &[AttemptStatus::Running, AttemptStatus::Cancelled],
            AttemptStatus::Running => &[
                AttemptStatus::Succeeded,
                AttemptStatus::Failed,
                AttemptStatus::Cancelled,
                AttemptStatus::TimedOut,
            ],
            AttemptStatus::Succeeded => &[],
            AttemptStatus::Failed => &[],
            AttemptStatus::Cancelled => &[],
            AttemptStatus::TimedOut => &[],
        }
    }

    /// Returns true if transitioning from `self` to `to` is legal.
    pub fn can_transition(&self, to: AttemptStatus) -> bool {
        self.legal_transitions().contains(&to)
    }

    /// Returns true if this is a terminal (non-progressable) state.
    pub fn is_terminal(&self) -> bool {
        matches!(
            self,
            AttemptStatus::Succeeded
                | AttemptStatus::Failed
                | AttemptStatus::Cancelled
                | AttemptStatus::TimedOut
        )
    }
}

/// State machine for [`Attempt`] transitions.
pub struct AttemptStateMachine;

impl AttemptStateMachine {
    /// Attempt to transition an attempt from `current` to `target`.
    pub fn transition(
        current: AttemptStatus,
        target: AttemptStatus,
    ) -> Result<AttemptStatus, ExecutionError> {
        if current.can_transition(target) {
            Ok(target)
        } else {
            Err(ExecutionError::IllegalTransition {
                from: format!("{:?}", current),
                to: format!("{:?}", target),
                entity_type: "AttemptStatus".into(),
            })
        }
    }
}

// ============================================================================
// State Machine Logic — WorkItemStatus
// ============================================================================

impl WorkItemStatus {
    /// Returns the set of legal next states from this status.
    pub fn legal_transitions(&self) -> &[WorkItemStatus] {
        match self {
            WorkItemStatus::Pending => &[WorkItemStatus::Claimed, WorkItemStatus::Cancelled],
            WorkItemStatus::Claimed => &[
                WorkItemStatus::Running,
                WorkItemStatus::Failed,
                WorkItemStatus::Cancelled,
            ],
            WorkItemStatus::Running => &[
                WorkItemStatus::Completed,
                WorkItemStatus::Failed,
                WorkItemStatus::Cancelled,
            ],
            WorkItemStatus::Completed => &[],
            WorkItemStatus::Failed => &[
                WorkItemStatus::Pending,
                WorkItemStatus::DeadLetter,
                WorkItemStatus::Cancelled,
            ],
            WorkItemStatus::Cancelled => &[],
            WorkItemStatus::DeadLetter => &[],
        }
    }

    /// Returns true if transitioning from `self` to `to` is legal.
    pub fn can_transition(&self, to: WorkItemStatus) -> bool {
        self.legal_transitions().contains(&to)
    }

    /// Returns true if this is a terminal (non-progressable) state.
    pub fn is_terminal(&self) -> bool {
        matches!(
            self,
            WorkItemStatus::Completed | WorkItemStatus::Cancelled | WorkItemStatus::DeadLetter
        )
    }
}

/// State machine for [`WorkItem`] transitions.
pub struct WorkItemStateMachine;

impl WorkItemStateMachine {
    /// Attempt to transition a work item from `current` to `target`.
    pub fn transition(
        current: WorkItemStatus,
        target: WorkItemStatus,
    ) -> Result<WorkItemStatus, ExecutionError> {
        if current.can_transition(target) {
            Ok(target)
        } else {
            Err(ExecutionError::IllegalTransition {
                from: format!("{:?}", current),
                to: format!("{:?}", target),
                entity_type: "WorkItemStatus".into(),
            })
        }
    }
}

// ============================================================================
// State Machine Logic — ReservationStatus
// ============================================================================

impl ReservationStatus {
    /// Returns the set of legal next states from this status.
    pub fn legal_transitions(&self) -> &[ReservationStatus] {
        match self {
            ReservationStatus::Active => &[
                ReservationStatus::Released,
                ReservationStatus::Expired,
                ReservationStatus::Consumed,
            ],
            ReservationStatus::Released => &[],
            ReservationStatus::Expired => &[],
            ReservationStatus::Consumed => &[],
        }
    }

    /// Returns true if transitioning from `self` to `to` is legal.
    pub fn can_transition(&self, to: ReservationStatus) -> bool {
        self.legal_transitions().contains(&to)
    }

    /// Returns true if this is a terminal (non-progressable) state.
    pub fn is_terminal(&self) -> bool {
        matches!(
            self,
            ReservationStatus::Released | ReservationStatus::Expired | ReservationStatus::Consumed
        )
    }
}

/// State machine for [`Reservation`] transitions.
pub struct ReservationStateMachine;

impl ReservationStateMachine {
    /// Attempt to transition a reservation from `current` to `target`.
    pub fn transition(
        current: ReservationStatus,
        target: ReservationStatus,
    ) -> Result<ReservationStatus, ExecutionError> {
        if current.can_transition(target) {
            Ok(target)
        } else {
            Err(ExecutionError::IllegalTransition {
                from: format!("{:?}", current),
                to: format!("{:?}", target),
                entity_type: "ReservationStatus".into(),
            })
        }
    }
}

// ============================================================================
// Error Type
// ============================================================================

/// Errors that can occur during execution state machine operations.
#[derive(Debug, Error)]
pub enum ExecutionError {
    /// An illegal state transition was attempted.
    #[error("illegal state transition: {from} -> {to} for entity type {entity_type}")]
    IllegalTransition {
        /// Source state.
        from: String,
        /// Target state.
        to: String,
        /// Entity type (e.g., "RunStatus", "StepStatus").
        entity_type: String,
    },

    /// The requested entity was not found.
    #[error("entity not found: {0}")]
    NotFound(String),

    /// A concurrency conflict occurred.
    #[error("concurrency conflict: {0}")]
    Conflict(String),

    /// Invalid input was provided.
    #[error("invalid input: {0}")]
    InvalidInput(String),

    /// A database operation failed.
    #[error("database error: {0}")]
    Database(String),

    /// Serialization or deserialization failed.
    #[error("serialization error: {0}")]
    Serialization(String),
}

// ============================================================================
// Tests
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    // --- RunStatus ---

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
    fn run_completed_is_terminal() {
        assert!(RunStatus::Completed.is_terminal());
        assert!(RunStatus::Completed.legal_transitions().is_empty());
    }

    #[test]
    fn run_running_to_awaiting_approval_is_legal() {
        assert!(RunStatus::Running.can_transition(RunStatus::AwaitingApproval));
    }

    #[test]
    fn run_state_machine_rejects_illegal() {
        let result = RunStateMachine::transition(RunStatus::Completed, RunStatus::Running);
        assert!(result.is_err());
        match result {
            Err(ExecutionError::IllegalTransition { entity_type, .. }) => {
                assert_eq!(entity_type, "RunStatus");
            }
            _ => panic!("expected IllegalTransition error"),
        }
    }

    // --- StepStatus ---

    #[test]
    fn step_pending_to_ready_is_legal() {
        assert!(StepStatus::Pending.can_transition(StepStatus::Ready));
    }

    #[test]
    fn step_running_to_completed_is_legal() {
        assert!(StepStatus::Running.can_transition(StepStatus::Completed));
    }

    #[test]
    fn step_failed_can_retry_to_pending() {
        assert!(StepStatus::Failed.can_transition(StepStatus::Pending));
    }

    #[test]
    fn step_completed_is_terminal() {
        assert!(StepStatus::Completed.is_terminal());
        assert!(StepStatus::Completed.legal_transitions().is_empty());
    }

    #[test]
    fn step_state_machine_rejects_illegal() {
        let result = StepStateMachine::transition(StepStatus::Completed, StepStatus::Running);
        assert!(result.is_err());
    }

    // --- AttemptStatus ---

    #[test]
    fn attempt_pending_to_running_is_legal() {
        assert!(AttemptStatus::Pending.can_transition(AttemptStatus::Running));
    }

    #[test]
    fn attempt_running_to_succeeded_is_legal() {
        assert!(AttemptStatus::Running.can_transition(AttemptStatus::Succeeded));
    }

    #[test]
    fn attempt_succeeded_is_terminal() {
        assert!(AttemptStatus::Succeeded.is_terminal());
    }

    #[test]
    fn attempt_state_machine_rejects_illegal() {
        let result =
            AttemptStateMachine::transition(AttemptStatus::Succeeded, AttemptStatus::Running);
        assert!(result.is_err());
    }

    // --- WorkItemStatus ---

    #[test]
    fn work_item_pending_to_claimed_is_legal() {
        assert!(WorkItemStatus::Pending.can_transition(WorkItemStatus::Claimed));
    }

    #[test]
    fn work_item_claimed_to_running_is_legal() {
        assert!(WorkItemStatus::Claimed.can_transition(WorkItemStatus::Running));
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
    fn work_item_state_machine_rejects_illegal() {
        let result =
            WorkItemStateMachine::transition(WorkItemStatus::Completed, WorkItemStatus::Running);
        assert!(result.is_err());
    }

    // --- ReservationStatus ---

    #[test]
    fn reservation_active_to_consumed_is_legal() {
        assert!(ReservationStatus::Active.can_transition(ReservationStatus::Consumed));
    }

    #[test]
    fn reservation_active_to_released_is_legal() {
        assert!(ReservationStatus::Active.can_transition(ReservationStatus::Released));
    }

    #[test]
    fn reservation_released_is_terminal() {
        assert!(ReservationStatus::Released.is_terminal());
    }

    #[test]
    fn reservation_state_machine_rejects_illegal() {
        let result = ReservationStateMachine::transition(
            ReservationStatus::Released,
            ReservationStatus::Active,
        );
        assert!(result.is_err());
    }

    // --- Serialization ---

    #[test]
    fn run_status_serialization_roundtrip() {
        let status = RunStatus::AwaitingApproval;
        let json = serde_json::to_string(&status).unwrap();
        let deserialized: RunStatus = serde_json::from_str(&json).unwrap();
        assert_eq!(status, deserialized);
    }

    #[test]
    fn step_status_serialization_roundtrip() {
        let status = StepStatus::Ready;
        let json = serde_json::to_string(&status).unwrap();
        let deserialized: StepStatus = serde_json::from_str(&json).unwrap();
        assert_eq!(status, deserialized);
    }

    #[test]
    fn work_item_status_serialization_roundtrip() {
        let status = WorkItemStatus::DeadLetter;
        let json = serde_json::to_string(&status).unwrap();
        let deserialized: WorkItemStatus = serde_json::from_str(&json).unwrap();
        assert_eq!(status, deserialized);
    }

    #[test]
    fn run_id_serialization_is_transparent() {
        let id = RunId(Uuid::new_v4());
        let json = serde_json::to_string(&id).unwrap();
        // Should be just the UUID string, not an object
        let _uuid_str = Uuid::new_v4().to_string();
        assert!(json.starts_with('"') && json.ends_with('"'));
        let deserialized: RunId = serde_json::from_str(&json).unwrap();
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
            contract_hash: "abc123".into(),
            context_manifest_hash: None,
            policy_revision: "v1".into(),
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
}
