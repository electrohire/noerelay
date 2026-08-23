//! PostgreSQL repository for durable execution entities.
//!
//! Provides [`ExecutionRepository`] with full CRUD operations for runs, steps,
//! attempts, work items, reservations, tool effects, and provider calls.
//! All state transitions are validated against the formal state machines
//! defined in [`noerelay_core::execution`].
//!
//! # Work Item Claiming
//!
//! The [`claim_work_item`] method uses `FOR UPDATE SKIP LOCKED` to atomically
//! claim the next available work item. This ensures that concurrent workers
//! never claim the same item. A lease ID, lease expiry, and fencing token are
//! set on the claimed item.
//!
//! # Tenant Isolation
//!
//! All methods that interact with tenant-bearing tables begin a transaction,
//! set the RLS context via [`set_tenant_context`], execute queries, and
//! commit. This ensures pooled connections never leak tenant context.

use chrono::{DateTime, Utc};
use noerelay_core::execution::*;
use noerelay_core::iam::{EnvironmentId, OrganizationId, PrincipalId, ProjectId};
use sqlx::{PgPool, Row};
use thiserror::Error;
use uuid::Uuid;

use crate::iam::set_tenant_context;

// ============================================================================
// Error Type
// ============================================================================

#[derive(Debug, Error)]
pub enum ExecutionStoreError {
    #[error("database operation failed: {0}")]
    Database(#[from] sqlx::Error),

    #[error("entity not found: {0}")]
    NotFound(String),

    #[error("entity already exists: {0}")]
    AlreadyExists(String),

    #[error("illegal state transition: {0}")]
    IllegalTransition(String),

    #[error("concurrency conflict: {0}")]
    ConcurrencyConflict(String),

    #[error("optimistic concurrency conflict: {0:?}")]
    OptimisticConflict(ConflictReport),

    #[error("invalid input: {0}")]
    InvalidInput(String),

    #[error("serialization error: {0}")]
    Serialization(#[from] serde_json::Error),
}

// ============================================================================
// Repository
// ============================================================================

/// Repository for durable execution entity operations.
///
/// All methods that interact with tenant-bearing tables begin a transaction,
/// set the RLS context via [`set_tenant_context`], execute queries, and
/// commit. This ensures pooled connections never leak tenant context.
#[derive(Clone)]
pub struct ExecutionRepository {
    pool: PgPool,
}

impl ExecutionRepository {
    pub fn new(pool: PgPool) -> Self {
        Self { pool }
    }

    // ========================================================================
    // Run CRUD
    // ========================================================================

    /// Create a new run.
    #[allow(clippy::too_many_arguments)]
    pub async fn create_run(
        &self,
        organization_id: OrganizationId,
        project_id: Option<ProjectId>,
        environment_id: Option<EnvironmentId>,
        principal_id: PrincipalId,
        contract_hash: &str,
        context_manifest_hash: Option<&str>,
        policy_revision: &str,
        parent_run_id: Option<RunId>,
    ) -> Result<Run, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let run_id = RunId(Uuid::new_v4());
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, Some(principal_id)).await?;

        let row = sqlx::query(
            "INSERT INTO runs (id, organization_id, project_id, environment_id, \
             principal_id, contract_hash, context_manifest_hash, policy_revision, \
             status, parent_run_id) \
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'pending', $9) \
             RETURNING id, organization_id, project_id, environment_id, \
                       principal_id, contract_hash, context_manifest_hash, \
                       policy_revision, status, parent_run_id, \
                       created_at, updated_at, completed_at, terminal_receipt_id",
        )
        .bind(run_id.0)
        .bind(&org_id_str)
        .bind(project_id.map(|p| p.0.to_string()))
        .bind(environment_id.map(|e| e.0))
        .bind(principal_id.0)
        .bind(contract_hash)
        .bind(context_manifest_hash)
        .bind(policy_revision)
        .bind(parent_run_id.map(|p| p.0))
        .fetch_one(&mut *tx)
        .await?;

        tx.commit().await?;
        row_to_run(&row)
    }

    /// Retrieve a run by ID.
    pub async fn get_run(
        &self,
        organization_id: OrganizationId,
        run_id: RunId,
    ) -> Result<Option<Run>, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        let row = sqlx::query(
            "SELECT id, organization_id, project_id, environment_id, \
                    principal_id, contract_hash, context_manifest_hash, \
                    policy_revision, status, parent_run_id, \
                    created_at, updated_at, completed_at, terminal_receipt_id \
             FROM runs WHERE id = $1",
        )
        .bind(run_id.0)
        .fetch_optional(&mut *tx)
        .await?;

        tx.commit().await?;
        row.map(|r| row_to_run(&r)).transpose()
    }

    /// Update a run's status, validating the transition against the state machine.
    pub async fn update_run_status(
        &self,
        organization_id: OrganizationId,
        run_id: RunId,
        target_status: RunStatus,
    ) -> Result<Run, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        // Read current status with row lock
        let current_row = sqlx::query("SELECT status FROM runs WHERE id = $1 FOR UPDATE")
            .bind(run_id.0)
            .fetch_optional(&mut *tx)
            .await?;

        let current_row =
            current_row.ok_or_else(|| ExecutionStoreError::NotFound(run_id.0.to_string()))?;

        let current_status_str: String = current_row.try_get("status")?;
        let current_status = str_to_run_status(&current_status_str);

        // Validate transition
        RunStateMachine::transition(current_status, target_status)
            .map_err(|e| ExecutionStoreError::IllegalTransition(e.to_string()))?;

        let target_str = run_status_to_str(target_status);
        let now: Option<DateTime<Utc>> = if target_status.is_terminal() {
            Some(Utc::now())
        } else {
            None
        };

        let row = sqlx::query(
            "UPDATE runs SET status = $1, completed_at = COALESCE($2, completed_at) \
             WHERE id = $3 \
             RETURNING id, organization_id, project_id, environment_id, \
                       principal_id, contract_hash, context_manifest_hash, \
                       policy_revision, status, parent_run_id, \
                       created_at, updated_at, completed_at, terminal_receipt_id",
        )
        .bind(target_str)
        .bind(now)
        .bind(run_id.0)
        .fetch_one(&mut *tx)
        .await?;

        tx.commit().await?;
        row_to_run(&row)
    }

    /// List runs for an organization, ordered by creation time descending.
    pub async fn list_runs(
        &self,
        organization_id: OrganizationId,
        limit: u32,
        offset: u32,
    ) -> Result<Vec<Run>, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        let rows = sqlx::query(
            "SELECT id, organization_id, project_id, environment_id, \
                    principal_id, contract_hash, context_manifest_hash, \
                    policy_revision, status, parent_run_id, \
                    created_at, updated_at, completed_at, terminal_receipt_id \
             FROM runs \
             ORDER BY created_at DESC LIMIT $1 OFFSET $2",
        )
        .bind(limit as i64)
        .bind(offset as i64)
        .fetch_all(&mut *tx)
        .await?;

        tx.commit().await?;
        rows.iter().map(row_to_run).collect()
    }

    // ========================================================================
    // Step CRUD
    // ========================================================================

    /// Create a new step within a run.
    #[allow(clippy::too_many_arguments)]
    pub async fn create_step(
        &self,
        organization_id: OrganizationId,
        run_id: RunId,
        parent_step_id: Option<StepId>,
        step_type: StepType,
        name: &str,
        sequence: i32,
        input_hash: Option<&str>,
    ) -> Result<Step, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let step_id = StepId(Uuid::new_v4());
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        let row = sqlx::query(
            "INSERT INTO steps (id, run_id, parent_step_id, step_type, name, \
             status, sequence, input_hash) \
             VALUES ($1, $2, $3, $4, $5, 'pending', $6, $7) \
             RETURNING id, run_id, parent_step_id, step_type, name, \
                       status, sequence, input_hash, output_hash, \
                       created_at, updated_at, completed_at",
        )
        .bind(step_id.0)
        .bind(run_id.0)
        .bind(parent_step_id.map(|p| p.0))
        .bind(step_type_to_str(step_type))
        .bind(name)
        .bind(sequence)
        .bind(input_hash)
        .fetch_one(&mut *tx)
        .await?;

        tx.commit().await?;
        row_to_step(&row)
    }

    /// Retrieve a step by ID.
    pub async fn get_step(
        &self,
        organization_id: OrganizationId,
        step_id: StepId,
    ) -> Result<Option<Step>, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        let row = sqlx::query(
            "SELECT id, run_id, parent_step_id, step_type, name, \
                    status, sequence, input_hash, output_hash, \
                    created_at, updated_at, completed_at \
             FROM steps WHERE id = $1",
        )
        .bind(step_id.0)
        .fetch_optional(&mut *tx)
        .await?;

        tx.commit().await?;
        row.map(|r| row_to_step(&r)).transpose()
    }

    /// Update a step's status, validating the transition against the state machine.
    pub async fn update_step_status(
        &self,
        organization_id: OrganizationId,
        step_id: StepId,
        target_status: StepStatus,
    ) -> Result<Step, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        let current_row = sqlx::query("SELECT status FROM steps WHERE id = $1 FOR UPDATE")
            .bind(step_id.0)
            .fetch_optional(&mut *tx)
            .await?;

        let current_row =
            current_row.ok_or_else(|| ExecutionStoreError::NotFound(step_id.0.to_string()))?;

        let current_status_str: String = current_row.try_get("status")?;
        let current_status = str_to_step_status(&current_status_str);

        StepStateMachine::transition(current_status, target_status)
            .map_err(|e| ExecutionStoreError::IllegalTransition(e.to_string()))?;

        let target_str = step_status_to_str(target_status);
        let now: Option<DateTime<Utc>> = if target_status.is_terminal() {
            Some(Utc::now())
        } else {
            None
        };

        let row = sqlx::query(
            "UPDATE steps SET status = $1, completed_at = COALESCE($2, completed_at) \
             WHERE id = $3 \
             RETURNING id, run_id, parent_step_id, step_type, name, \
                       status, sequence, input_hash, output_hash, \
                       created_at, updated_at, completed_at",
        )
        .bind(target_str)
        .bind(now)
        .bind(step_id.0)
        .fetch_one(&mut *tx)
        .await?;

        tx.commit().await?;
        row_to_step(&row)
    }

    /// List all steps for a run, ordered by sequence.
    pub async fn list_steps_for_run(
        &self,
        organization_id: OrganizationId,
        run_id: RunId,
    ) -> Result<Vec<Step>, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        let rows = sqlx::query(
            "SELECT id, run_id, parent_step_id, step_type, name, \
                    status, sequence, input_hash, output_hash, \
                    created_at, updated_at, completed_at \
             FROM steps WHERE run_id = $1 \
             ORDER BY sequence ASC",
        )
        .bind(run_id.0)
        .fetch_all(&mut *tx)
        .await?;

        tx.commit().await?;
        rows.iter().map(row_to_step).collect()
    }

    // ========================================================================
    // Attempt CRUD
    // ========================================================================

    /// Create a new attempt for a step.
    pub async fn create_attempt(
        &self,
        organization_id: OrganizationId,
        step_id: StepId,
        attempt_number: i32,
        provider_call_id: Option<ProviderCallId>,
    ) -> Result<Attempt, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let attempt_id = AttemptId(Uuid::new_v4());
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        let row = sqlx::query(
            "INSERT INTO attempts (id, step_id, attempt_number, status, provider_call_id) \
             VALUES ($1, $2, $3, 'pending', $4) \
             RETURNING id, step_id, attempt_number, status, provider_call_id, \
                       started_at, completed_at, error, cost_micro_usd",
        )
        .bind(attempt_id.0)
        .bind(step_id.0)
        .bind(attempt_number)
        .bind(provider_call_id.map(|p| p.0))
        .fetch_one(&mut *tx)
        .await?;

        tx.commit().await?;
        row_to_attempt(&row)
    }

    /// Retrieve an attempt by ID.
    pub async fn get_attempt(
        &self,
        organization_id: OrganizationId,
        attempt_id: AttemptId,
    ) -> Result<Option<Attempt>, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        let row = sqlx::query(
            "SELECT id, step_id, attempt_number, status, provider_call_id, \
                    started_at, completed_at, error, cost_micro_usd \
             FROM attempts WHERE id = $1",
        )
        .bind(attempt_id.0)
        .fetch_optional(&mut *tx)
        .await?;

        tx.commit().await?;
        row.map(|r| row_to_attempt(&r)).transpose()
    }

    /// Update an attempt's status, validating the transition.
    pub async fn update_attempt_status(
        &self,
        organization_id: OrganizationId,
        attempt_id: AttemptId,
        target_status: AttemptStatus,
        error: Option<&str>,
        cost_micro_usd: Option<i64>,
    ) -> Result<Attempt, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        let current_row = sqlx::query("SELECT status FROM attempts WHERE id = $1 FOR UPDATE")
            .bind(attempt_id.0)
            .fetch_optional(&mut *tx)
            .await?;

        let current_row =
            current_row.ok_or_else(|| ExecutionStoreError::NotFound(attempt_id.0.to_string()))?;

        let current_status_str: String = current_row.try_get("status")?;
        let current_status = str_to_attempt_status(&current_status_str);

        AttemptStateMachine::transition(current_status, target_status)
            .map_err(|e| ExecutionStoreError::IllegalTransition(e.to_string()))?;

        let target_str = attempt_status_to_str(target_status);
        let now: Option<DateTime<Utc>> = if target_status.is_terminal() {
            Some(Utc::now())
        } else {
            None
        };

        let row = sqlx::query(
            "UPDATE attempts SET status = $1, completed_at = COALESCE($2, completed_at), \
             error = COALESCE($3, error), cost_micro_usd = COALESCE($4, cost_micro_usd) \
             WHERE id = $5 \
             RETURNING id, step_id, attempt_number, status, provider_call_id, \
                       started_at, completed_at, error, cost_micro_usd",
        )
        .bind(target_str)
        .bind(now)
        .bind(error)
        .bind(cost_micro_usd)
        .bind(attempt_id.0)
        .fetch_one(&mut *tx)
        .await?;

        tx.commit().await?;
        row_to_attempt(&row)
    }

    // ========================================================================
    // Work Item Operations
    // ========================================================================

    /// Enqueue a new work item.
    #[allow(clippy::too_many_arguments)]
    pub async fn enqueue_work_item(
        &self,
        organization_id: OrganizationId,
        run_id: RunId,
        step_id: Option<StepId>,
        item_type: &str,
        payload: &serde_json::Value,
        priority: i32,
        max_attempts: i32,
        available_at: Option<DateTime<Utc>>,
    ) -> Result<WorkItem, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let work_item_id = WorkItemId(Uuid::new_v4());
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        let row = sqlx::query(
            "INSERT INTO work_items (id, run_id, step_id, item_type, payload, \
             status, priority, max_attempts, available_at) \
             VALUES ($1, $2, $3, $4, $5, 'pending', $6, $7, \
                     COALESCE($8, now())) \
             RETURNING id, run_id, step_id, item_type, payload, status, \
                       priority, lease_id, lease_expires_at, fencing_token, \
                       attempts, max_attempts, available_at, \
                       created_at, updated_at, version",
        )
        .bind(work_item_id.0)
        .bind(run_id.0)
        .bind(step_id.map(|s| s.0))
        .bind(item_type)
        .bind(payload)
        .bind(priority)
        .bind(max_attempts)
        .bind(available_at)
        .fetch_one(&mut *tx)
        .await?;

        tx.commit().await?;
        row_to_work_item(&row)
    }

    /// Atomically claim the next available work item.
    ///
    /// Uses `FOR UPDATE SKIP LOCKED` to ensure concurrent workers never claim
    /// the same item. Sets a lease ID, lease expiry, and fencing token on the
    /// claimed item. The lease expires after `lease_duration_secs` seconds.
    ///
    /// Returns `None` if no work items are available.
    pub async fn claim_work_item(
        &self,
        organization_id: OrganizationId,
        lease_id: &str,
        lease_duration_secs: i64,
    ) -> Result<Option<WorkItem>, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        // Use FOR UPDATE SKIP LOCKED to atomically claim the next item
        let row = sqlx::query(
            "SELECT id FROM work_items \
             WHERE status = 'pending' \
               AND available_at <= now() \
             ORDER BY priority DESC, created_at ASC \
             LIMIT 1 \
             FOR UPDATE SKIP LOCKED",
        )
        .fetch_optional(&mut *tx)
        .await?;

        let row = match row {
            Some(r) => r,
            None => {
                tx.commit().await?;
                return Ok(None);
            }
        };

        let item_id: Uuid = row.try_get("id")?;

        // Compute new fencing token
        let current_fencing: Option<i64> =
            sqlx::query_scalar("SELECT fencing_token FROM work_items WHERE id = $1")
                .bind(item_id)
                .fetch_one(&mut *tx)
                .await?;

        let new_fencing = current_fencing.unwrap_or(0) + 1;

        let row = sqlx::query(
            "UPDATE work_items SET \
             status = 'claimed', \
             lease_id = $1, \
             lease_expires_at = now() + ($2 || ' seconds')::interval, \
             fencing_token = $3, \
             attempts = attempts + 1 \
             WHERE id = $4 \
             RETURNING id, run_id, step_id, item_type, payload, status, \
                       priority, lease_id, lease_expires_at, fencing_token, \
                       attempts, max_attempts, available_at, \
                       created_at, updated_at, version",
        )
        .bind(lease_id)
        .bind(lease_duration_secs)
        .bind(new_fencing)
        .bind(item_id)
        .fetch_one(&mut *tx)
        .await?;

        tx.commit().await?;
        Ok(Some(row_to_work_item(&row)?))
    }

    /// Mark a work item as completed.
    pub async fn complete_work_item(
        &self,
        organization_id: OrganizationId,
        work_item_id: WorkItemId,
        fencing_token: i64,
    ) -> Result<WorkItem, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        let row = sqlx::query(
            "UPDATE work_items SET status = 'completed', lease_id = NULL, \
             lease_expires_at = NULL \
             WHERE id = $1 AND fencing_token = $2 \
               AND status IN ('claimed', 'running') \
             RETURNING id, run_id, step_id, item_type, payload, status, \
                       priority, lease_id, lease_expires_at, fencing_token, \
                       attempts, max_attempts, available_at, \
                       created_at, updated_at, version",
        )
        .bind(work_item_id.0)
        .bind(fencing_token)
        .fetch_optional(&mut *tx)
        .await?;

        tx.commit().await?;

        match row {
            Some(r) => row_to_work_item(&r),
            None => Err(ExecutionStoreError::ConcurrencyConflict(
                "fencing token mismatch or item not in claimable state".into(),
            )),
        }
    }

    /// Mark a work item as failed. If attempts >= max_attempts, moves to dead_letter.
    pub async fn fail_work_item(
        &self,
        organization_id: OrganizationId,
        work_item_id: WorkItemId,
        fencing_token: i64,
    ) -> Result<WorkItem, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        // First check if we should dead-letter
        let current: (String, i32, i32) =
            sqlx::query_as("SELECT status, attempts, max_attempts FROM work_items WHERE id = $1")
                .bind(work_item_id.0)
                .fetch_optional(&mut *tx)
                .await?
                .map(|(s, a, m): (String, i32, i32)| (s, a, m))
                .ok_or_else(|| ExecutionStoreError::NotFound(work_item_id.0.to_string()))?;

        let new_status = if current.1 >= current.2 {
            "dead_letter"
        } else {
            "failed"
        };

        let row = sqlx::query(
            "UPDATE work_items SET status = $1, lease_id = NULL, \
             lease_expires_at = NULL \
             WHERE id = $2 AND fencing_token = $3 \
               AND status IN ('claimed', 'running') \
             RETURNING id, run_id, step_id, item_type, payload, status, \
                       priority, lease_id, lease_expires_at, fencing_token, \
                       attempts, max_attempts, available_at, \
                       created_at, updated_at, version",
        )
        .bind(new_status)
        .bind(work_item_id.0)
        .bind(fencing_token)
        .fetch_optional(&mut *tx)
        .await?;

        tx.commit().await?;

        match row {
            Some(r) => row_to_work_item(&r),
            None => Err(ExecutionStoreError::ConcurrencyConflict(
                "fencing token mismatch or item not in claimable state".into(),
            )),
        }
    }

    /// Move a work item to dead letter queue.
    pub async fn dead_letter_work_item(
        &self,
        organization_id: OrganizationId,
        work_item_id: WorkItemId,
    ) -> Result<WorkItem, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        let row = sqlx::query(
            "UPDATE work_items SET status = 'dead_letter', lease_id = NULL, \
             lease_expires_at = NULL \
             WHERE id = $1 \
             RETURNING id, run_id, step_id, item_type, payload, status, \
                       priority, lease_id, lease_expires_at, fencing_token, \
                       attempts, max_attempts, available_at, \
                       created_at, updated_at, version",
        )
        .bind(work_item_id.0)
        .fetch_optional(&mut *tx)
        .await?;

        tx.commit().await?;

        match row {
            Some(r) => row_to_work_item(&r),
            None => Err(ExecutionStoreError::NotFound(work_item_id.0.to_string())),
        }
    }

    /// Requeue work items whose leases have expired back to pending.
    ///
    /// Returns the number of items requeued.
    pub async fn requeue_expired_leases(
        &self,
        organization_id: OrganizationId,
    ) -> Result<u64, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        let result = sqlx::query(
            "UPDATE work_items SET status = 'pending', lease_id = NULL, \
             lease_expires_at = NULL, fencing_token = NULL \
             WHERE status IN ('claimed', 'running') \
               AND lease_expires_at < now()",
        )
        .execute(&mut *tx)
        .await?;

        tx.commit().await?;
        Ok(result.rows_affected())
    }

    // ========================================================================
    // Reservation Operations
    // ========================================================================

    /// Create a new budget reservation.
    pub async fn create_reservation(
        &self,
        organization_id: OrganizationId,
        run_id: RunId,
        resource_type: &str,
        resource_id: &str,
        amount_micro_usd: i64,
        expires_at: DateTime<Utc>,
    ) -> Result<Reservation, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let reservation_id = ReservationId(Uuid::new_v4());
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        let row = sqlx::query(
            "INSERT INTO reservations (id, run_id, resource_type, resource_id, \
             status, amount_micro_usd, expires_at) \
             VALUES ($1, $2, $3, $4, 'active', $5, $6) \
             RETURNING id, run_id, resource_type, resource_id, status, \
                       amount_micro_usd, expires_at, created_at, released_at",
        )
        .bind(reservation_id.0)
        .bind(run_id.0)
        .bind(resource_type)
        .bind(resource_id)
        .bind(amount_micro_usd)
        .bind(expires_at)
        .fetch_one(&mut *tx)
        .await?;

        tx.commit().await?;
        row_to_reservation(&row)
    }

    /// Release a reservation (explicitly free the budget).
    pub async fn release_reservation(
        &self,
        organization_id: OrganizationId,
        reservation_id: ReservationId,
    ) -> Result<Reservation, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        let row = sqlx::query(
            "UPDATE reservations SET status = 'released', released_at = now() \
             WHERE id = $1 AND status = 'active' \
             RETURNING id, run_id, resource_type, resource_id, status, \
                       amount_micro_usd, expires_at, created_at, released_at",
        )
        .bind(reservation_id.0)
        .fetch_optional(&mut *tx)
        .await?;

        tx.commit().await?;

        match row {
            Some(r) => row_to_reservation(&r),
            None => Err(ExecutionStoreError::NotFound(reservation_id.0.to_string())),
        }
    }

    /// Expire all reservations past their expiry time.
    ///
    /// Returns the number of reservations expired.
    pub async fn expire_reservations(
        &self,
        organization_id: OrganizationId,
    ) -> Result<u64, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        let result = sqlx::query(
            "UPDATE reservations SET status = 'expired', released_at = now() \
             WHERE status = 'active' AND expires_at < now()",
        )
        .execute(&mut *tx)
        .await?;

        tx.commit().await?;
        Ok(result.rows_affected())
    }

    // ========================================================================
    // Tool Effect Operations
    // ========================================================================

    /// Record a new tool effect.
    #[allow(clippy::too_many_arguments)]
    pub async fn record_tool_effect(
        &self,
        organization_id: OrganizationId,
        attempt_id: AttemptId,
        tool_id: &str,
        effect_kind: &str,
        effect_id_external: Option<&str>,
        status: &str,
        request_hash: &str,
        response_hash: Option<&str>,
    ) -> Result<ToolEffect, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let effect_id = ToolEffectId(Uuid::new_v4());
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        let row = sqlx::query(
            "INSERT INTO tool_effects (id, attempt_id, tool_id, effect_kind, \
             effect_id_external, status, request_hash, response_hash) \
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8) \
             RETURNING id, attempt_id, tool_id, effect_kind, \
                       effect_id_external, status, request_hash, response_hash, \
                       created_at, reconciled_at",
        )
        .bind(effect_id.0)
        .bind(attempt_id.0)
        .bind(tool_id)
        .bind(effect_kind)
        .bind(effect_id_external)
        .bind(status)
        .bind(request_hash)
        .bind(response_hash)
        .fetch_one(&mut *tx)
        .await?;

        tx.commit().await?;
        row_to_tool_effect(&row)
    }

    /// Reconcile a tool effect (mark as reconciled).
    pub async fn reconcile_tool_effect(
        &self,
        organization_id: OrganizationId,
        effect_id: ToolEffectId,
    ) -> Result<ToolEffect, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        let row = sqlx::query(
            "UPDATE tool_effects SET reconciled_at = now() \
             WHERE id = $1 \
             RETURNING id, attempt_id, tool_id, effect_kind, \
                       effect_id_external, status, request_hash, response_hash, \
                       created_at, reconciled_at",
        )
        .bind(effect_id.0)
        .fetch_optional(&mut *tx)
        .await?;

        tx.commit().await?;

        match row {
            Some(r) => row_to_tool_effect(&r),
            None => Err(ExecutionStoreError::NotFound(effect_id.0.to_string())),
        }
    }

    // ========================================================================
    // Provider Call Operations
    // ========================================================================

    /// Record a new provider call.
    #[allow(clippy::too_many_arguments)]
    pub async fn record_provider_call(
        &self,
        organization_id: OrganizationId,
        attempt_id: AttemptId,
        provider: &str,
        model: &str,
        request_hash: &str,
        response_hash: Option<&str>,
        usage_input_tokens: Option<i32>,
        usage_output_tokens: Option<i32>,
        status: &str,
    ) -> Result<ProviderCall, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let call_id = ProviderCallId(Uuid::new_v4());
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        let row = sqlx::query(
            "INSERT INTO provider_calls (id, attempt_id, provider, model, \
             request_hash, response_hash, usage_input_tokens, usage_output_tokens, \
             status) \
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) \
             RETURNING id, attempt_id, provider, model, request_hash, \
                       response_hash, usage_input_tokens, usage_output_tokens, \
                       status, started_at, completed_at",
        )
        .bind(call_id.0)
        .bind(attempt_id.0)
        .bind(provider)
        .bind(model)
        .bind(request_hash)
        .bind(response_hash)
        .bind(usage_input_tokens)
        .bind(usage_output_tokens)
        .bind(status)
        .fetch_one(&mut *tx)
        .await?;

        tx.commit().await?;
        row_to_provider_call(&row)
    }

    /// Retrieve a provider call by ID.
    pub async fn get_provider_call(
        &self,
        organization_id: OrganizationId,
        call_id: ProviderCallId,
    ) -> Result<Option<ProviderCall>, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        let row = sqlx::query(
            "SELECT id, attempt_id, provider, model, request_hash, \
                    response_hash, usage_input_tokens, usage_output_tokens, \
                    status, started_at, completed_at \
             FROM provider_calls WHERE id = $1",
        )
        .bind(call_id.0)
        .fetch_optional(&mut *tx)
        .await?;

        tx.commit().await?;
        row.map(|r| row_to_provider_call(&r)).transpose()
    }

    // ========================================================================
    // Outbox Operations
    // ========================================================================

    /// Enqueue an outbox event for reliable publishing.
    ///
    /// The event is inserted in its own transaction. For atomicity with
    /// business state changes, use a higher-level service that coordinates
    /// both operations within a single database transaction.
    pub async fn enqueue_outbox_event(
        &self,
        organization_id: OrganizationId,
        event: &OutboxEvent,
    ) -> Result<(), ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        sqlx::query(
            "INSERT INTO outbox (id, aggregate_id, aggregate_type, event_type, \
             payload, status, organization_id) \
             VALUES ($1, $2, $3, $4, $5, $6, $7)",
        )
        .bind(event.id)
        .bind(&event.aggregate_id)
        .bind(&event.aggregate_type)
        .bind(&event.event_type)
        .bind(&event.payload)
        .bind(outbox_event_status_to_str(event.status))
        .bind(&org_id_str)
        .execute(&mut *tx)
        .await?;

        tx.commit().await?;
        Ok(())
    }

    /// Claim and publish a batch of pending outbox events.
    ///
    /// Returns the events that were published. Events are claimed atomically
    /// using `FOR UPDATE SKIP LOCKED` to allow concurrent publishers.
    pub async fn publish_pending_events(
        &self,
        organization_id: OrganizationId,
        batch_size: i32,
    ) -> Result<Vec<OutboxEvent>, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        // Claim pending events
        let rows = sqlx::query(
            "UPDATE outbox SET \
             status = 'published', \
             published_at = now(), \
             delivery_attempts = delivery_attempts + 1 \
             WHERE id IN ( \
                 SELECT id FROM outbox \
                 WHERE status = 'pending' \
                   AND organization_id = $1 \
                 ORDER BY created_at ASC \
                 LIMIT $2 \
                 FOR UPDATE SKIP LOCKED \
             ) \
             RETURNING id, aggregate_id, aggregate_type, event_type, \
                       payload, created_at, published_at, delivery_attempts, \
                       status",
        )
        .bind(&org_id_str)
        .bind(batch_size)
        .fetch_all(&mut *tx)
        .await?;

        tx.commit().await?;

        rows.iter().map(row_to_outbox_event).collect()
    }

    /// Mark an outbox event as failed, incrementing delivery attempts.
    pub async fn mark_event_failed(
        &self,
        organization_id: OrganizationId,
        event_id: Uuid,
        error: &str,
    ) -> Result<(), ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        let result = sqlx::query(
            "UPDATE outbox SET \
             status = 'failed', \
             delivery_attempts = delivery_attempts + 1, \
             last_error = $1 \
             WHERE id = $2 AND organization_id = $3",
        )
        .bind(error)
        .bind(event_id)
        .bind(&org_id_str)
        .execute(&mut *tx)
        .await?;

        tx.commit().await?;

        if result.rows_affected() == 0 {
            return Err(ExecutionStoreError::NotFound(event_id.to_string()));
        }
        Ok(())
    }

    /// Move an outbox event to the dead letter queue.
    pub async fn dead_letter_event(
        &self,
        organization_id: OrganizationId,
        event_id: Uuid,
        error: &str,
    ) -> Result<(), ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        let result = sqlx::query(
            "UPDATE outbox SET \
             status = 'dead_lettered', \
             last_error = $1 \
             WHERE id = $2 AND organization_id = $3",
        )
        .bind(error)
        .bind(event_id)
        .bind(&org_id_str)
        .execute(&mut *tx)
        .await?;

        tx.commit().await?;

        if result.rows_affected() == 0 {
            return Err(ExecutionStoreError::NotFound(event_id.to_string()));
        }
        Ok(())
    }

    // ========================================================================
    // Lease Operations
    // ========================================================================

    /// Acquire a lease on a work item for a worker.
    ///
    /// Atomically claims the work item and creates a lease record with a
    /// monotonically increasing fencing token. Returns the lease info.
    ///
    /// Returns an error if the work item is not in a claimable state or
    /// if another worker already holds the lease.
    pub async fn acquire_lease(
        &self,
        organization_id: OrganizationId,
        work_item_id: WorkItemId,
        worker_id: &str,
        lease_duration_secs: i64,
    ) -> Result<LeaseInfo, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let lease_id = Uuid::new_v4().to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        // Lock the work item row
        let item_row = sqlx::query(
            "SELECT status, fencing_token FROM work_items \
             WHERE id = $1 FOR UPDATE",
        )
        .bind(work_item_id.0)
        .fetch_optional(&mut *tx)
        .await?;

        let item_row =
            item_row.ok_or_else(|| ExecutionStoreError::NotFound(work_item_id.0.to_string()))?;

        let status: String = item_row.try_get("status")?;
        if status != "pending" && status != "failed" {
            return Err(ExecutionStoreError::ConcurrencyConflict(format!(
                "work item {} is not claimable (status: {})",
                work_item_id.0, status
            )));
        }

        let current_fencing: Option<i64> = item_row.try_get("fencing_token")?;
        let new_fencing = current_fencing.unwrap_or(0) + 1;
        let now = Utc::now();
        let expires_at = now + chrono::Duration::seconds(lease_duration_secs);

        // Update work item
        sqlx::query(
            "UPDATE work_items SET \
             status = 'claimed', \
             lease_id = $1, \
             lease_expires_at = $2, \
             fencing_token = $3, \
             attempts = attempts + 1 \
             WHERE id = $4",
        )
        .bind(&lease_id)
        .bind(expires_at)
        .bind(new_fencing)
        .bind(work_item_id.0)
        .execute(&mut *tx)
        .await?;

        // Create lease record
        sqlx::query(
            "INSERT INTO leases (work_item_id, worker_id, lease_id, \
             fencing_token, acquired_at, expires_at, status) \
             VALUES ($1, $2, $3, $4, $5, $6, 'active')",
        )
        .bind(work_item_id.0)
        .bind(worker_id)
        .bind(&lease_id)
        .bind(new_fencing)
        .bind(now)
        .bind(expires_at)
        .execute(&mut *tx)
        .await?;

        tx.commit().await?;

        Ok(LeaseInfo {
            lease_id,
            worker_id: worker_id.to_string(),
            acquired_at: now,
            expires_at,
            heartbeat_at: None,
            fencing_token: new_fencing,
        })
    }

    /// Renew a lease (heartbeat), extending its expiry.
    ///
    /// Validates the fencing token to prevent split-brain scenarios.
    /// Returns the updated lease info.
    pub async fn renew_lease(
        &self,
        organization_id: OrganizationId,
        lease_id: &str,
        fencing_token: i64,
    ) -> Result<LeaseInfo, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        let now = Utc::now();
        // Extend by the original lease duration (infer from existing expiry)
        let row = sqlx::query(
            "UPDATE leases SET \
             heartbeat_at = $1, \
             expires_at = $1 + (expires_at - acquired_at) \
             WHERE lease_id = $2 AND fencing_token = $3 AND status = 'active' \
             RETURNING lease_id, worker_id, acquired_at, expires_at, \
                       heartbeat_at, fencing_token",
        )
        .bind(now)
        .bind(lease_id)
        .bind(fencing_token)
        .fetch_optional(&mut *tx)
        .await?;

        tx.commit().await?;

        match row {
            Some(r) => row_to_lease_info(&r),
            None => Err(ExecutionStoreError::ConcurrencyConflict(
                "fencing token mismatch or lease not active".into(),
            )),
        }
    }

    /// Release a lease, freeing the work item for other workers.
    ///
    /// Validates the fencing token to prevent split-brain scenarios.
    pub async fn release_lease(
        &self,
        organization_id: OrganizationId,
        lease_id: &str,
        fencing_token: i64,
    ) -> Result<(), ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        let result = sqlx::query(
            "UPDATE leases SET \
             status = 'released', \
             released_at = now() \
             WHERE lease_id = $1 AND fencing_token = $2 AND status = 'active'",
        )
        .bind(lease_id)
        .bind(fencing_token)
        .execute(&mut *tx)
        .await?;

        tx.commit().await?;

        if result.rows_affected() == 0 {
            return Err(ExecutionStoreError::ConcurrencyConflict(
                "fencing token mismatch or lease not active".into(),
            ));
        }
        Ok(())
    }

    /// Expire all leases past their expiry time.
    ///
    /// Returns the number of leases expired.
    pub async fn expire_leases(
        &self,
        organization_id: OrganizationId,
    ) -> Result<i32, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        let result = sqlx::query(
            "UPDATE leases SET status = 'expired', released_at = now() \
             WHERE status = 'active' AND expires_at < now()",
        )
        .execute(&mut *tx)
        .await?;

        tx.commit().await?;
        Ok(result.rows_affected() as i32)
    }

    // ========================================================================
    // Circuit Breaker Operations
    // ========================================================================

    /// Get the current circuit breaker state for a scope.
    ///
    /// If no circuit breaker exists for the scope, returns a default
    /// closed circuit breaker (without persisting it).
    pub async fn get_circuit_breaker(
        &self,
        scope: &str,
    ) -> Result<CircuitBreaker, ExecutionStoreError> {
        let mut tx = self.pool.begin().await?;

        let row = sqlx::query(
            "SELECT scope, state, failure_count, success_count, \
                    failure_threshold, success_threshold, open_until, \
                    cooldown_seconds, last_failure_at, last_success_at, \
                    created_at, updated_at \
             FROM circuit_breakers WHERE scope = $1",
        )
        .bind(scope)
        .fetch_optional(&mut *tx)
        .await?;

        tx.commit().await?;

        match row {
            Some(r) => row_to_circuit_breaker(&r),
            None => {
                let now = Utc::now();
                Ok(CircuitBreaker {
                    scope: scope.to_string(),
                    state: CircuitState::Closed,
                    failure_count: 0,
                    success_count: 0,
                    failure_threshold: 5,
                    success_threshold: 2,
                    open_until: None,
                    cooldown_seconds: 30,
                    last_failure_at: None,
                    last_success_at: None,
                    created_at: now,
                    updated_at: now,
                })
            }
        }
    }

    /// Record a successful request through a circuit breaker.
    ///
    /// Updates the circuit breaker state in the database and returns
    /// the new circuit state.
    pub async fn record_circuit_success(
        &self,
        scope: &str,
    ) -> Result<CircuitState, ExecutionStoreError> {
        let mut tx = self.pool.begin().await?;

        // Upsert the circuit breaker and record success
        sqlx::query(
            "INSERT INTO circuit_breakers (scope, state, failure_count, success_count, \
             failure_threshold, success_threshold, cooldown_seconds) \
             VALUES ($1, 'closed', 0, 0, 5, 2, 30) \
             ON CONFLICT (scope) DO NOTHING",
        )
        .bind(scope)
        .execute(&mut *tx)
        .await?;

        // Read current state
        let current = sqlx::query(
            "SELECT state, success_count, success_threshold, \
                    open_until FROM circuit_breakers WHERE scope = $1 FOR UPDATE",
        )
        .bind(scope)
        .fetch_one(&mut *tx)
        .await?;

        let current_state: String = current.try_get("state")?;
        let success_count: i32 = current.try_get("success_count")?;
        let success_threshold: i32 = current.try_get("success_threshold")?;
        let open_until: Option<DateTime<Utc>> = current.try_get("open_until")?;

        let now = Utc::now();
        let new_state = match current_state.as_str() {
            "closed" => {
                // Reset failure count on success
                sqlx::query(
                    "UPDATE circuit_breakers SET \
                     failure_count = 0, success_count = 0, \
                     last_success_at = $1, updated_at = $1 \
                     WHERE scope = $2",
                )
                .bind(now)
                .bind(scope)
                .execute(&mut *tx)
                .await?;
                "closed"
            }
            "half_open" => {
                let new_success = success_count + 1;
                if new_success >= success_threshold {
                    sqlx::query(
                        "UPDATE circuit_breakers SET \
                         state = 'closed', failure_count = 0, success_count = 0, \
                         last_success_at = $1, updated_at = $1 \
                         WHERE scope = $2",
                    )
                    .bind(now)
                    .bind(scope)
                    .execute(&mut *tx)
                    .await?;
                    "closed"
                } else {
                    sqlx::query(
                        "UPDATE circuit_breakers SET \
                         success_count = $1, last_success_at = $2, updated_at = $2 \
                         WHERE scope = $3",
                    )
                    .bind(new_success)
                    .bind(now)
                    .bind(scope)
                    .execute(&mut *tx)
                    .await?;
                    "half_open"
                }
            }
            "open" => {
                // Check if cooldown has elapsed
                if let Some(until) = open_until {
                    if now >= until {
                        sqlx::query(
                            "UPDATE circuit_breakers SET \
                             state = 'half_open', success_count = 1, failure_count = 0, \
                             last_success_at = $1, updated_at = $1 \
                             WHERE scope = $2",
                        )
                        .bind(now)
                        .bind(scope)
                        .execute(&mut *tx)
                        .await?;
                        "half_open"
                    } else {
                        // Still open, but record the success anyway
                        sqlx::query(
                            "UPDATE circuit_breakers SET \
                             last_success_at = $1, updated_at = $1 \
                             WHERE scope = $2",
                        )
                        .bind(now)
                        .bind(scope)
                        .execute(&mut *tx)
                        .await?;
                        "open"
                    }
                } else {
                    "open"
                }
            }
            _ => "closed",
        };

        tx.commit().await?;
        Ok(str_to_circuit_state(new_state))
    }

    /// Record a failed request through a circuit breaker.
    ///
    /// Updates the circuit breaker state in the database and returns
    /// the new circuit state.
    pub async fn record_circuit_failure(
        &self,
        scope: &str,
    ) -> Result<CircuitState, ExecutionStoreError> {
        let mut tx = self.pool.begin().await?;

        // Ensure the row exists
        sqlx::query(
            "INSERT INTO circuit_breakers (scope, state, failure_count, success_count, \
             failure_threshold, success_threshold, cooldown_seconds) \
             VALUES ($1, 'closed', 0, 0, 5, 2, 30) \
             ON CONFLICT (scope) DO NOTHING",
        )
        .bind(scope)
        .execute(&mut *tx)
        .await?;

        // Read current state with lock
        let current = sqlx::query(
            "SELECT state, failure_count, failure_threshold, cooldown_seconds \
             FROM circuit_breakers WHERE scope = $1 FOR UPDATE",
        )
        .bind(scope)
        .fetch_one(&mut *tx)
        .await?;

        let current_state: String = current.try_get("state")?;
        let failure_count: i32 = current.try_get("failure_count")?;
        let failure_threshold: i32 = current.try_get("failure_threshold")?;
        let cooldown_seconds: i32 = current.try_get("cooldown_seconds")?;

        let now = Utc::now();
        let new_state = match current_state.as_str() {
            "closed" => {
                let new_failures = failure_count + 1;
                if new_failures >= failure_threshold {
                    let open_until = now + chrono::Duration::seconds(cooldown_seconds as i64);
                    sqlx::query(
                        "UPDATE circuit_breakers SET \
                         state = 'open', failure_count = $1, success_count = 0, \
                         open_until = $2, last_failure_at = $3, updated_at = $3 \
                         WHERE scope = $4",
                    )
                    .bind(new_failures)
                    .bind(open_until)
                    .bind(now)
                    .bind(scope)
                    .execute(&mut *tx)
                    .await?;
                    "open"
                } else {
                    sqlx::query(
                        "UPDATE circuit_breakers SET \
                         failure_count = $1, last_failure_at = $2, updated_at = $2 \
                         WHERE scope = $3",
                    )
                    .bind(new_failures)
                    .bind(now)
                    .bind(scope)
                    .execute(&mut *tx)
                    .await?;
                    "closed"
                }
            }
            "half_open" => {
                // Any failure in half-open re-opens the circuit
                let open_until = now + chrono::Duration::seconds(cooldown_seconds as i64);
                sqlx::query(
                    "UPDATE circuit_breakers SET \
                     state = 'open', failure_count = 1, success_count = 0, \
                     open_until = $1, last_failure_at = $2, updated_at = $2 \
                     WHERE scope = $3",
                )
                .bind(open_until)
                .bind(now)
                .bind(scope)
                .execute(&mut *tx)
                .await?;
                "open"
            }
            "open" => {
                // Extend the cooldown
                let open_until = now + chrono::Duration::seconds(cooldown_seconds as i64);
                sqlx::query(
                    "UPDATE circuit_breakers SET \
                     failure_count = failure_count + 1, \
                     open_until = $1, last_failure_at = $2, updated_at = $2 \
                     WHERE scope = $3",
                )
                .bind(open_until)
                .bind(now)
                .bind(scope)
                .execute(&mut *tx)
                .await?;
                "open"
            }
            _ => "closed",
        };

        tx.commit().await?;
        Ok(str_to_circuit_state(new_state))
    }

    /// Check if a request is allowed through the circuit breaker for a scope.
    ///
    /// Returns `true` if the circuit is closed, half-open, or the cooldown
    /// period has elapsed for an open circuit.
    pub async fn check_circuit_allowed(&self, scope: &str) -> Result<bool, ExecutionStoreError> {
        let mut tx = self.pool.begin().await?;

        let row = sqlx::query("SELECT state, open_until FROM circuit_breakers WHERE scope = $1")
            .bind(scope)
            .fetch_optional(&mut *tx)
            .await?;

        tx.commit().await?;

        match row {
            Some(r) => {
                let state: String = r.try_get("state")?;
                match state.as_str() {
                    "closed" | "half_open" => Ok(true),
                    "open" => {
                        let open_until: Option<DateTime<Utc>> = r.try_get("open_until")?;
                        if let Some(until) = open_until {
                            Ok(Utc::now() >= until)
                        } else {
                            Ok(false)
                        }
                    }
                    _ => Ok(true),
                }
            }
            None => Ok(true), // No circuit breaker = allowed
        }
    }

    // ========================================================================
    // Retry-Aware Work Item Claiming
    // ========================================================================

    /// Claim a work item with retry policy awareness.
    ///
    /// Claims the next available work item, checks the retry policy to
    /// determine if it should be retried, creates a lease, and returns
    /// both the work item and lease info.
    ///
    /// Returns `None` if no work items are available or if the retry
    /// policy prevents claiming.
    pub async fn claim_work_item_with_retry(
        &self,
        organization_id: OrganizationId,
        worker_id: &str,
        lease_duration_secs: i64,
    ) -> Result<Option<(WorkItem, LeaseInfo)>, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let lease_id = Uuid::new_v4().to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;

        // Find the next available work item
        let item_row = sqlx::query(
            "SELECT id, attempts, max_attempts FROM work_items \
              WHERE status = 'pending' \
                AND available_at <= now() \
                AND EXISTS (SELECT 1 FROM workers \
                            WHERE worker_id = $1 AND status = 'active') \
             ORDER BY priority DESC, created_at ASC \
             LIMIT 1 \
             FOR UPDATE SKIP LOCKED",
        )
        .bind(worker_id)
        .fetch_optional(&mut *tx)
        .await?;

        let item_row = match item_row {
            Some(r) => r,
            None => {
                tx.commit().await?;
                return Ok(None);
            }
        };

        let item_id: Uuid = item_row.try_get("id")?;
        let attempts: i32 = item_row.try_get("attempts")?;
        let max_attempts: i32 = item_row.try_get("max_attempts")?;

        // Check if the item has exceeded max attempts
        if attempts >= max_attempts {
            // Dead-letter the item
            sqlx::query("UPDATE work_items SET status = 'dead_letter' WHERE id = $1")
                .bind(item_id)
                .execute(&mut *tx)
                .await?;
            tx.commit().await?;
            return Ok(None);
        }

        // Compute fencing token
        let current_fencing: Option<i64> =
            sqlx::query_scalar("SELECT fencing_token FROM work_items WHERE id = $1")
                .bind(item_id)
                .fetch_one(&mut *tx)
                .await?;

        let new_fencing = current_fencing.unwrap_or(0) + 1;
        let now = Utc::now();
        let expires_at = now + chrono::Duration::seconds(lease_duration_secs);

        // Update work item
        let wi_row = sqlx::query(
            "UPDATE work_items SET \
             status = 'claimed', \
             lease_id = $1, \
             lease_expires_at = $2, \
             fencing_token = $3, \
             attempts = attempts + 1 \
             WHERE id = $4 \
             RETURNING id, run_id, step_id, item_type, payload, status, \
                       priority, lease_id, lease_expires_at, fencing_token, \
                       attempts, max_attempts, available_at, \
                       created_at, updated_at, version",
        )
        .bind(&lease_id)
        .bind(expires_at)
        .bind(new_fencing)
        .bind(item_id)
        .fetch_one(&mut *tx)
        .await?;

        // Create lease record
        sqlx::query(
            "INSERT INTO leases (work_item_id, worker_id, lease_id, \
             fencing_token, acquired_at, expires_at, status) \
             VALUES ($1, $2, $3, $4, $5, $6, 'active')",
        )
        .bind(item_id)
        .bind(worker_id)
        .bind(&lease_id)
        .bind(new_fencing)
        .bind(now)
        .bind(expires_at)
        .execute(&mut *tx)
        .await?;

        tx.commit().await?;

        let work_item = row_to_work_item(&wi_row)?;
        let lease_info = LeaseInfo {
            lease_id,
            worker_id: worker_id.to_string(),
            acquired_at: now,
            expires_at,
            heartbeat_at: None,
            fencing_token: new_fencing,
        };

        Ok(Some((work_item, lease_info)))
    }

    // ========================================================================
    // Scoped Idempotency Operations
    // ========================================================================

    /// Atomically claim a scoped idempotency key or return its cached result.
    pub async fn claim_idempotency_key(
        &self,
        key: IdempotencyKey,
        organization_id: OrganizationId,
    ) -> Result<IdempotencyRecord, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, Some(key.principal_id)).await?;

        let inserted = sqlx::query(
            "INSERT INTO idempotency_records (idempotency_key, principal_id, \
             endpoint_profile, request_hash, policy_revision, organization_id, \
             status, expires_at) \
             VALUES ($1, $2, $3, $4, $5, $6, 'in_progress', \
                     now() + interval '24 hours') \
             ON CONFLICT (idempotency_key, principal_id, endpoint_profile) DO NOTHING \
             RETURNING id, idempotency_key, principal_id, endpoint_profile, \
                       request_hash, policy_revision, organization_id, run_id, status, \
                       response_ref, terminal_receipt_id, created_at, completed_at, expires_at",
        )
        .bind(&key.key)
        .bind(key.principal_id.0)
        .bind(&key.endpoint_profile)
        .bind(&key.request_hash)
        .bind(&key.policy_revision)
        .bind(organization_id.0)
        .fetch_optional(&mut *tx)
        .await?;

        if let Some(row) = inserted {
            let record = row_to_idempotency_record(&row)?;
            tx.commit().await?;
            return Ok(record);
        }

        let row = sqlx::query(
            "SELECT id, idempotency_key, principal_id, endpoint_profile, request_hash, \
                    policy_revision, organization_id, run_id, status, response_ref, \
                    terminal_receipt_id, created_at, completed_at, expires_at \
             FROM idempotency_records \
             WHERE idempotency_key = $1 AND principal_id = $2 AND endpoint_profile = $3 \
             FOR UPDATE",
        )
        .bind(&key.key)
        .bind(key.principal_id.0)
        .bind(&key.endpoint_profile)
        .fetch_optional(&mut *tx)
        .await?
        .ok_or_else(|| {
            ExecutionStoreError::ConcurrencyConflict(
                "idempotency key is claimed outside the active tenant scope".into(),
            )
        })?;

        let existing = row_to_idempotency_record(&row)?;
        if existing.idempotency_key.request_hash != key.request_hash
            || existing.idempotency_key.policy_revision != key.policy_revision
        {
            return Err(ExecutionStoreError::InvalidInput(
                "idempotency key was previously bound to a different request or policy revision"
                    .into(),
            ));
        }

        match existing.status {
            IdempotencyStatus::Completed => {
                tx.commit().await?;
                Ok(existing)
            }
            IdempotencyStatus::InProgress => Err(ExecutionStoreError::ConcurrencyConflict(
                "idempotency key is already in progress".into(),
            )),
            IdempotencyStatus::Failed | IdempotencyStatus::Expired => {
                let row = sqlx::query(
                    "UPDATE idempotency_records SET status = 'in_progress', \
                     response_ref = NULL, terminal_receipt_id = NULL, completed_at = NULL, \
                     expires_at = now() + interval '24 hours' WHERE id = $1 \
                     RETURNING id, idempotency_key, principal_id, endpoint_profile, \
                               request_hash, policy_revision, organization_id, run_id, status, \
                               response_ref, terminal_receipt_id, created_at, completed_at, expires_at",
                )
                .bind(existing.id)
                .fetch_one(&mut *tx)
                .await?;
                let reclaimed = row_to_idempotency_record(&row)?;
                tx.commit().await?;
                Ok(reclaimed)
            }
        }
    }

    /// Complete a claim and bind it to durable response and receipt references.
    pub async fn complete_idempotency(
        &self,
        organization_id: OrganizationId,
        record_id: Uuid,
        response_ref: &str,
        receipt_id: Option<&str>,
    ) -> Result<(), ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let result = sqlx::query(
            "UPDATE idempotency_records SET status = 'completed', response_ref = $1, \
             terminal_receipt_id = $2, completed_at = now() \
             WHERE id = $3 AND status = 'in_progress'",
        )
        .bind(response_ref)
        .bind(receipt_id)
        .bind(record_id)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        ensure_one_row(result.rows_affected(), record_id)
    }

    /// Mark an in-progress idempotency claim as failed so the same input may retry.
    pub async fn fail_idempotency(
        &self,
        organization_id: OrganizationId,
        record_id: Uuid,
    ) -> Result<(), ExecutionStoreError> {
        self.update_idempotency_status(organization_id, record_id, "failed")
            .await
    }

    /// Expire stale in-progress idempotency claims for this tenant.
    pub async fn expire_idempotency_records(
        &self,
        organization_id: OrganizationId,
    ) -> Result<i32, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let result = sqlx::query(
            "UPDATE idempotency_records SET status = 'expired' \
             WHERE status = 'in_progress' AND expires_at < now()",
        )
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        Ok(result.rows_affected() as i32)
    }

    /// Retrieve an idempotency record by its externally visible scope.
    pub async fn get_idempotency_record(
        &self,
        organization_id: OrganizationId,
        key: &str,
        principal_id: PrincipalId,
        endpoint_profile: &str,
    ) -> Result<Option<IdempotencyRecord>, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, Some(principal_id)).await?;
        let row = sqlx::query(
            "SELECT id, idempotency_key, principal_id, endpoint_profile, request_hash, \
                    policy_revision, organization_id, run_id, status, response_ref, \
                    terminal_receipt_id, created_at, completed_at, expires_at \
             FROM idempotency_records \
             WHERE idempotency_key = $1 AND principal_id = $2 AND endpoint_profile = $3",
        )
        .bind(key)
        .bind(principal_id.0)
        .bind(endpoint_profile)
        .fetch_optional(&mut *tx)
        .await?;
        tx.commit().await?;
        row.map(|row| row_to_idempotency_record(&row)).transpose()
    }

    async fn update_idempotency_status(
        &self,
        organization_id: OrganizationId,
        record_id: Uuid,
        status: &str,
    ) -> Result<(), ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let result = sqlx::query(
            "UPDATE idempotency_records SET status = $1 \
             WHERE id = $2 AND status = 'in_progress'",
        )
        .bind(status)
        .bind(record_id)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        ensure_one_row(result.rows_affected(), record_id)
    }

    // ========================================================================
    // Cancellation Operations
    // ========================================================================

    /// Atomically cancel a run DAG and release/cancel all active resources.
    pub async fn request_cancellation(
        &self,
        organization_id: OrganizationId,
        request: CancellationRequest,
    ) -> Result<CancellationResult, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, Some(request.requested_by)).await?;

        let root_status: Option<String> =
            sqlx::query_scalar("SELECT status FROM runs WHERE id = $1 FOR UPDATE")
                .bind(request.run_id.0)
                .fetch_optional(&mut *tx)
                .await?;
        let root_status = root_status
            .ok_or_else(|| ExecutionStoreError::NotFound(request.run_id.0.to_string()))?;

        let descendant_rows = if request.propagate {
            sqlx::query(
                "WITH RECURSIVE descendants AS ( \
                    SELECT id FROM runs WHERE parent_run_id = $1 \
                    UNION ALL \
                    SELECT child.id FROM runs child \
                    JOIN descendants parent ON child.parent_run_id = parent.id \
                 ) SELECT id FROM descendants",
            )
            .bind(request.run_id.0)
            .fetch_all(&mut *tx)
            .await?
        } else {
            Vec::new()
        };
        let descendants: Vec<Uuid> = descendant_rows
            .iter()
            .map(|row| row.try_get("id"))
            .collect::<Result<_, _>>()?;
        let mut affected_runs = Vec::with_capacity(descendants.len() + 1);
        affected_runs.push(request.run_id.0);
        affected_runs.extend(descendants.iter().copied());

        let cancelled = !matches!(
            root_status.as_str(),
            "completed" | "failed" | "cancelled" | "timed_out"
        );
        sqlx::query(
            "UPDATE runs SET status = 'cancelled', completed_at = COALESCE(completed_at, $1) \
             WHERE id = ANY($2) AND status NOT IN ('completed','failed','cancelled','timed_out')",
        )
        .bind(request.requested_at)
        .bind(&affected_runs)
        .execute(&mut *tx)
        .await?;

        sqlx::query(
            "UPDATE steps SET status = 'cancelled', completed_at = COALESCE(completed_at, $1) \
             WHERE run_id = ANY($2) AND status NOT IN ('completed','failed','skipped','cancelled')",
        )
        .bind(request.requested_at)
        .bind(&affected_runs)
        .execute(&mut *tx)
        .await?;
        sqlx::query(
            "UPDATE attempts SET status = 'cancelled', completed_at = COALESCE(completed_at, $1) \
             WHERE step_id IN (SELECT id FROM steps WHERE run_id = ANY($2)) \
               AND status IN ('pending','running')",
        )
        .bind(request.requested_at)
        .bind(&affected_runs)
        .execute(&mut *tx)
        .await?;
        sqlx::query(
            "UPDATE work_items SET status = 'cancelled', lease_id = NULL, \
             lease_expires_at = NULL WHERE run_id = ANY($1) \
             AND status NOT IN ('completed','cancelled','dead_letter')",
        )
        .bind(&affected_runs)
        .execute(&mut *tx)
        .await?;
        sqlx::query(
            "UPDATE leases SET status = 'released', released_at = $1 \
             WHERE work_item_id IN (SELECT id FROM work_items WHERE run_id = ANY($2)) \
               AND status = 'active'",
        )
        .bind(request.requested_at)
        .bind(&affected_runs)
        .execute(&mut *tx)
        .await?;

        let reservation_rows = sqlx::query(
            "UPDATE reservations SET status = 'released', released_at = $1 \
             WHERE run_id = ANY($2) AND status = 'active' RETURNING id",
        )
        .bind(request.requested_at)
        .bind(&affected_runs)
        .fetch_all(&mut *tx)
        .await?;
        let provider_rows = sqlx::query(
            "UPDATE provider_calls SET status = 'cancelled', \
             completed_at = COALESCE(completed_at, $1) \
             WHERE attempt_id IN (SELECT a.id FROM attempts a JOIN steps s ON a.step_id = s.id \
                                  WHERE s.run_id = ANY($2)) \
               AND status NOT IN ('completed','failed','cancelled') RETURNING id",
        )
        .bind(request.requested_at)
        .bind(&affected_runs)
        .fetch_all(&mut *tx)
        .await?;
        let tool_rows = sqlx::query(
            "UPDATE tool_effects SET status = 'cancelled', \
             reconciled_at = COALESCE(reconciled_at, $1) \
             WHERE attempt_id IN (SELECT a.id FROM attempts a JOIN steps s ON a.step_id = s.id \
                                  WHERE s.run_id = ANY($2)) \
               AND status NOT IN ('applied','reconciled','compensated','cancelled') RETURNING id",
        )
        .bind(request.requested_at)
        .bind(&affected_runs)
        .fetch_all(&mut *tx)
        .await?;

        let released_reservations = uuid_rows(&reservation_rows, "id")?
            .into_iter()
            .map(ReservationId)
            .collect::<Vec<_>>();
        let cancelled_provider_calls = uuid_rows(&provider_rows, "id")?
            .into_iter()
            .map(ProviderCallId)
            .collect::<Vec<_>>();
        let cancelled_tool_effects = uuid_rows(&tool_rows, "id")?
            .into_iter()
            .map(ToolEffectId)
            .collect::<Vec<_>>();
        let cancelled_descendants = descendants.into_iter().map(RunId).collect::<Vec<_>>();

        sqlx::query(
            "INSERT INTO cancellation_log (run_id, reason, requested_by, requested_at, \
             processed_at, cancelled_descendants, released_reservations, \
             cancelled_provider_calls, cancelled_tool_effects) \
             VALUES ($1, $2, $3, $4, now(), $5, $6, $7, $8)",
        )
        .bind(request.run_id.0)
        .bind(cancellation_reason_to_str(request.reason))
        .bind(request.requested_by.0)
        .bind(request.requested_at)
        .bind(serde_json::to_value(&cancelled_descendants)?)
        .bind(serde_json::to_value(&released_reservations)?)
        .bind(serde_json::to_value(&cancelled_provider_calls)?)
        .bind(serde_json::to_value(&cancelled_tool_effects)?)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;

        Ok(CancellationResult {
            run_id: request.run_id,
            cancelled,
            cancelled_descendants,
            released_reservations,
            cancelled_provider_calls,
            cancelled_tool_effects,
        })
    }

    /// Read the cancellation audit history for a run.
    pub async fn get_cancellation_log(
        &self,
        organization_id: OrganizationId,
        run_id: RunId,
    ) -> Result<Vec<CancellationLogEntry>, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let rows = sqlx::query(
            "SELECT id, run_id, reason, requested_by, requested_at, processed_at, \
                    cancelled_descendants, released_reservations, cancelled_provider_calls, \
                    cancelled_tool_effects FROM cancellation_log \
             WHERE run_id = $1 ORDER BY requested_at",
        )
        .bind(run_id.0)
        .fetch_all(&mut *tx)
        .await?;
        tx.commit().await?;
        rows.iter().map(row_to_cancellation_log_entry).collect()
    }

    // ========================================================================
    // Effect Intent/Result Protocol
    // ========================================================================

    /// Durably record an effect intent before dispatch.
    pub async fn record_effect_request(
        &self,
        organization_id: OrganizationId,
        request: &EffectRequest,
        attempt_id: AttemptId,
    ) -> Result<(), ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let inserted = sqlx::query(
            "INSERT INTO effect_journal (effect_id, attempt_id, tool_id, intent, \
             request_hash, idempotency_key, status, created_at) \
             VALUES ($1, $2, $3, $4, $5, $6, 'unknown', $7) \
             ON CONFLICT (effect_id) DO NOTHING",
        )
        .bind(&request.effect_id)
        .bind(attempt_id.0)
        .bind(&request.tool_id)
        .bind(effect_intent_to_str(request.intent))
        .bind(&request.request_hash)
        .bind(&request.idempotency_key)
        .bind(request.created_at)
        .execute(&mut *tx)
        .await?;
        if inserted.rows_affected() == 0 {
            let existing = sqlx::query(
                "SELECT attempt_id, tool_id, intent, request_hash, idempotency_key \
                 FROM effect_journal WHERE effect_id = $1",
            )
            .bind(&request.effect_id)
            .fetch_optional(&mut *tx)
            .await?
            .ok_or_else(|| ExecutionStoreError::NotFound(request.effect_id.clone()))?;
            let matches = existing.try_get::<Uuid, _>("attempt_id")? == attempt_id.0
                && existing.try_get::<String, _>("tool_id")? == request.tool_id
                && existing.try_get::<String, _>("intent")? == effect_intent_to_str(request.intent)
                && existing.try_get::<String, _>("request_hash")? == request.request_hash
                && existing.try_get::<Option<String>, _>("idempotency_key")?
                    == request.idempotency_key;
            if !matches {
                return Err(ExecutionStoreError::InvalidInput(
                    "effect_id was previously bound to a different effect intent".into(),
                ));
            }
        }
        tx.commit().await?;
        Ok(())
    }

    /// Pair a durable result with a previously recorded effect intent.
    pub async fn record_effect_result(
        &self,
        organization_id: OrganizationId,
        effect_id: &str,
        result: &EffectResult,
    ) -> Result<(), ExecutionStoreError> {
        if effect_id != result.effect_id {
            return Err(ExecutionStoreError::InvalidInput(
                "effect result does not match the requested effect_id".into(),
            ));
        }
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let updated = sqlx::query(
            "UPDATE effect_journal SET status = $1, external_effect_id = $2, \
             response_hash = $3, reconciled_at = $4, error = $5 WHERE effect_id = $6",
        )
        .bind(effect_result_status_to_str(result.status))
        .bind(&result.external_effect_id)
        .bind(&result.response_hash)
        .bind(result.reconciled_at)
        .bind(&result.error)
        .bind(effect_id)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        if updated.rows_affected() == 0 {
            return Err(ExecutionStoreError::NotFound(effect_id.into()));
        }
        Ok(())
    }

    /// List effects whose downstream outcome remains unknown.
    pub async fn get_pending_unknown_effects(
        &self,
        organization_id: OrganizationId,
    ) -> Result<Vec<EffectResult>, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let rows = sqlx::query(
            "SELECT effect_id, status, external_effect_id, response_hash, reconciled_at, \
                    error, created_at FROM effect_journal WHERE status = 'unknown' \
             ORDER BY created_at",
        )
        .fetch_all(&mut *tx)
        .await?;
        tx.commit().await?;
        rows.iter().map(row_to_effect_result).collect()
    }

    /// Resolve an unknown effect after downstream reconciliation.
    pub async fn reconcile_effect(
        &self,
        organization_id: OrganizationId,
        effect_id: &str,
        status: EffectResultStatus,
        external_id: Option<&str>,
        response_hash: Option<&str>,
    ) -> Result<(), ExecutionStoreError> {
        if status == EffectResultStatus::Unknown {
            return Err(ExecutionStoreError::InvalidInput(
                "reconciliation must resolve unknown_effect_state".into(),
            ));
        }
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let result = sqlx::query(
            "UPDATE effect_journal SET status = $1, external_effect_id = $2, \
             response_hash = $3, reconciled_at = now() \
             WHERE effect_id = $4 AND status = 'unknown'",
        )
        .bind(effect_result_status_to_str(status))
        .bind(external_id)
        .bind(response_hash)
        .bind(effect_id)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        if result.rows_affected() == 0 {
            return Err(ExecutionStoreError::ConcurrencyConflict(
                "effect is missing or no longer in unknown_effect_state".into(),
            ));
        }
        Ok(())
    }

    // ========================================================================
    // Multi-Replica Worker Coordination
    // ========================================================================

    pub async fn register_worker(
        &self,
        organization_id: OrganizationId,
        worker_id: &str,
        version: &str,
        capabilities: &[String],
    ) -> Result<WorkerRegistration, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let row = sqlx::query(
            "INSERT INTO workers (worker_id, worker_version, capabilities, organization_id) \
             VALUES ($1, $2, $3, $4) ON CONFLICT (worker_id) DO UPDATE SET \
             worker_version = EXCLUDED.worker_version, capabilities = EXCLUDED.capabilities, \
             last_heartbeat_at = now() WHERE workers.organization_id = EXCLUDED.organization_id \
             RETURNING worker_id, worker_version, capabilities, registered_at, \
                       last_heartbeat_at, status",
        )
        .bind(worker_id)
        .bind(version)
        .bind(serde_json::to_value(capabilities)?)
        .bind(organization_id.0)
        .fetch_optional(&mut *tx)
        .await?
        .ok_or_else(|| {
            ExecutionStoreError::ConcurrencyConflict("worker id belongs to another tenant".into())
        })?;
        let registration = row_to_worker_registration(&row)?;
        tx.commit().await?;
        Ok(registration)
    }

    pub async fn heartbeat(
        &self,
        organization_id: OrganizationId,
        worker_id: &str,
    ) -> Result<(), ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let result = sqlx::query(
            "UPDATE workers SET last_heartbeat_at = now() WHERE worker_id = $1 \
             AND status IN ('active', 'draining')",
        )
        .bind(worker_id)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        ensure_worker_row(result.rows_affected(), worker_id)
    }

    pub async fn drain_worker(
        &self,
        organization_id: OrganizationId,
        worker_id: &str,
    ) -> Result<WorkerStatus, ExecutionStoreError> {
        self.transition_worker(
            organization_id,
            worker_id,
            WorkerStatus::Active,
            WorkerStatus::Draining,
        )
        .await
    }

    pub async fn complete_drain(
        &self,
        organization_id: OrganizationId,
        worker_id: &str,
    ) -> Result<WorkerStatus, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let row = sqlx::query(
            "UPDATE workers SET status = 'drained' WHERE worker_id = $1 \
             AND status = 'draining' AND NOT EXISTS (SELECT 1 FROM leases \
             WHERE worker_id = $1 AND status = 'active') RETURNING status",
        )
        .bind(worker_id)
        .fetch_optional(&mut *tx)
        .await?;
        tx.commit().await?;
        row.map(|_| WorkerStatus::Drained).ok_or_else(|| {
            ExecutionStoreError::ConcurrencyConflict(
                "worker is not draining or still has in-flight work".into(),
            )
        })
    }

    pub async fn fail_worker(
        &self,
        organization_id: OrganizationId,
        worker_id: &str,
    ) -> Result<WorkerStatus, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let row = sqlx::query(
            "UPDATE workers SET status = 'failed' WHERE worker_id = $1 \
             AND status IN ('active', 'draining') RETURNING status",
        )
        .bind(worker_id)
        .fetch_optional(&mut *tx)
        .await?;
        tx.commit().await?;
        row.map(|_| WorkerStatus::Failed).ok_or_else(|| {
            ExecutionStoreError::IllegalTransition("worker cannot transition to failed".into())
        })
    }

    pub async fn decommission_worker(
        &self,
        organization_id: OrganizationId,
        worker_id: &str,
    ) -> Result<(), ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let result = sqlx::query(
            "UPDATE workers SET status = 'decommissioned' WHERE worker_id = $1 \
             AND status IN ('drained', 'failed')",
        )
        .bind(worker_id)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        ensure_worker_row(result.rows_affected(), worker_id)
    }

    pub async fn get_unresponsive_workers(
        &self,
        organization_id: OrganizationId,
        timeout_secs: i64,
    ) -> Result<Vec<WorkerRegistration>, ExecutionStoreError> {
        if timeout_secs < 0 {
            return Err(ExecutionStoreError::InvalidInput(
                "timeout must be non-negative".into(),
            ));
        }
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let rows = sqlx::query(
            "SELECT worker_id, worker_version, capabilities, registered_at, \
                    last_heartbeat_at, status FROM workers WHERE status = 'active' \
             AND last_heartbeat_at < now() - ($1 || ' seconds')::interval \
             ORDER BY last_heartbeat_at",
        )
        .bind(timeout_secs)
        .fetch_all(&mut *tx)
        .await?;
        tx.commit().await?;
        rows.iter().map(row_to_worker_registration).collect()
    }

    async fn transition_worker(
        &self,
        organization_id: OrganizationId,
        worker_id: &str,
        from: WorkerStatus,
        to: WorkerStatus,
    ) -> Result<WorkerStatus, ExecutionStoreError> {
        if !from.can_transition(to) {
            return Err(ExecutionStoreError::IllegalTransition(format!(
                "{from:?} -> {to:?}"
            )));
        }
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let row = sqlx::query(
            "UPDATE workers SET status = $1 WHERE worker_id = $2 AND status = $3 RETURNING status",
        )
        .bind(worker_status_to_str(to))
        .bind(worker_id)
        .bind(worker_status_to_str(from))
        .fetch_optional(&mut *tx)
        .await?;
        tx.commit().await?;
        row.map(|_| to).ok_or_else(|| {
            ExecutionStoreError::IllegalTransition(format!("worker is not {from:?}"))
        })
    }

    pub async fn update_work_item_cas<F>(
        &self,
        organization_id: OrganizationId,
        work_item_id: WorkItemId,
        expected_version: i64,
        update_fn: F,
    ) -> Result<WorkItem, ExecutionStoreError>
    where
        F: FnOnce(&mut WorkItem),
    {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let current = sqlx::query(
            "SELECT id, run_id, step_id, item_type, payload, status, priority, lease_id, \
                    lease_expires_at, fencing_token, attempts, max_attempts, available_at, \
                    created_at, updated_at, version FROM work_items WHERE id = $1",
        )
        .bind(work_item_id.0)
        .fetch_optional(&mut *tx)
        .await?
        .ok_or_else(|| ExecutionStoreError::NotFound(work_item_id.0.to_string()))?;
        let mut item = row_to_work_item(&current)?;
        update_fn(&mut item);
        let row = sqlx::query(
            "UPDATE work_items SET item_type = $1, payload = $2, status = $3, priority = $4, \
                    lease_id = $5, lease_expires_at = $6, fencing_token = $7, attempts = $8, \
                    max_attempts = $9, available_at = $10, version = version + 1 \
             WHERE id = $11 AND version = $12 \
             RETURNING id, run_id, step_id, item_type, payload, status, priority, lease_id, \
                       lease_expires_at, fencing_token, attempts, max_attempts, available_at, \
                       created_at, updated_at, version",
        )
        .bind(&item.item_type)
        .bind(&item.payload)
        .bind(work_item_status_to_str(item.status))
        .bind(item.priority)
        .bind(&item.lease_id)
        .bind(item.lease_expires_at)
        .bind(item.fencing_token)
        .bind(item.attempts)
        .bind(item.max_attempts)
        .bind(item.available_at)
        .bind(work_item_id.0)
        .bind(expected_version)
        .fetch_optional(&mut *tx)
        .await?;
        if let Some(row) = row {
            let updated = row_to_work_item(&row)?;
            tx.commit().await?;
            return Ok(updated);
        }
        let owner_row = sqlx::query(
            "SELECT l.worker_id, w.fencing_token FROM work_items w LEFT JOIN leases l \
             ON l.lease_id = w.lease_id AND l.status = 'active' WHERE w.id = $1",
        )
        .bind(work_item_id.0)
        .fetch_one(&mut *tx)
        .await?;
        let report = ConflictReport {
            work_item_id,
            conflict_type: ConflictType::VersionMismatch,
            resolution: ConflictResolution::ReloadAndRetry,
            current_owner: owner_row.try_get("worker_id")?,
            fencing_token: owner_row
                .try_get::<Option<i64>, _>("fencing_token")?
                .unwrap_or(0),
            detected_at: Utc::now(),
        };
        tx.rollback().await?;
        Err(ExecutionStoreError::OptimisticConflict(report))
    }

    pub async fn claim_orphaned_work(
        &self,
        organization_id: OrganizationId,
        worker_id: &str,
        lease_duration_secs: i64,
    ) -> Result<Vec<(WorkItem, LeaseInfo)>, ExecutionStoreError> {
        if lease_duration_secs <= 0 {
            return Err(ExecutionStoreError::InvalidInput(
                "lease duration must be positive".into(),
            ));
        }
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let rows = sqlx::query(
            "WITH candidates AS (SELECT w.id FROM work_items w \
                 LEFT JOIN leases l ON l.lease_id = w.lease_id \
                 LEFT JOIN workers owner ON owner.worker_id = l.worker_id \
                 WHERE w.status IN ('claimed', 'running') AND (w.lease_expires_at < now() \
                    OR (l.status = 'active' AND owner.status = 'active' \
                        AND owner.last_heartbeat_at < now() - interval '60 seconds')) \
                   AND EXISTS (SELECT 1 FROM workers claimant \
                               WHERE claimant.worker_id = $1 AND claimant.status = 'active') \
                 FOR UPDATE OF w SKIP LOCKED) \
             UPDATE work_items w SET status = 'claimed', lease_id = gen_random_uuid()::text, \
                 lease_expires_at = now() + ($2 || ' seconds')::interval, \
                 fencing_token = COALESCE(w.fencing_token, 0) + 1, \
                 attempts = attempts + 1, version = version + 1 \
             FROM candidates c WHERE w.id = c.id \
             RETURNING w.id, w.run_id, w.step_id, w.item_type, w.payload, w.status, w.priority, \
                 w.lease_id, w.lease_expires_at, w.fencing_token, w.attempts, w.max_attempts, \
                 w.available_at, w.created_at, w.updated_at, w.version",
        )
        .bind(worker_id)
        .bind(lease_duration_secs)
        .fetch_all(&mut *tx)
        .await?;
        let now = Utc::now();
        let mut claimed = Vec::with_capacity(rows.len());
        for row in rows {
            let item = row_to_work_item(&row)?;
            sqlx::query(
                "UPDATE leases SET status = 'expired', released_at = now() \
                 WHERE work_item_id = $1 AND status = 'active'",
            )
            .bind(item.id.0)
            .execute(&mut *tx)
            .await?;
            let lease_id = item.lease_id.clone().ok_or_else(|| {
                ExecutionStoreError::InvalidInput("claimed item missing lease id".into())
            })?;
            let expires_at = item.lease_expires_at.ok_or_else(|| {
                ExecutionStoreError::InvalidInput("claimed item missing lease expiry".into())
            })?;
            let fencing_token = item.fencing_token.unwrap_or(0);
            sqlx::query(
                "INSERT INTO leases (work_item_id, worker_id, lease_id, fencing_token, \
                 acquired_at, expires_at, status) VALUES ($1,$2,$3,$4,$5,$6,'active')",
            )
            .bind(item.id.0)
            .bind(worker_id)
            .bind(&lease_id)
            .bind(fencing_token)
            .bind(now)
            .bind(expires_at)
            .execute(&mut *tx)
            .await?;
            claimed.push((
                item,
                LeaseInfo {
                    lease_id,
                    worker_id: worker_id.into(),
                    acquired_at: now,
                    expires_at,
                    heartbeat_at: None,
                    fencing_token,
                },
            ));
        }
        tx.commit().await?;
        Ok(claimed)
    }

    pub async fn acquire_stream(
        &self,
        organization_id: OrganizationId,
        stream_id: &str,
        worker_id: &str,
        fencing_token: i64,
        duration_secs: i64,
    ) -> Result<StreamOwnership, ExecutionStoreError> {
        if duration_secs <= 0 || fencing_token <= 0 {
            return Err(ExecutionStoreError::InvalidInput(
                "duration and fencing token must be positive".into(),
            ));
        }
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let row = sqlx::query(
            "INSERT INTO stream_ownership (stream_id, owner_worker_id, fencing_token, \
             expires_at, organization_id) SELECT $1, $2, $3, \
             now() + ($4 || ' seconds')::interval, $5 FROM workers \
             WHERE worker_id = $2 AND status = 'active' \
             ON CONFLICT (stream_id) DO UPDATE SET owner_worker_id = EXCLUDED.owner_worker_id, \
             acquired_at = now(), fencing_token = EXCLUDED.fencing_token, \
             expires_at = EXCLUDED.expires_at, last_heartbeat_at = NULL \
             WHERE stream_ownership.expires_at < now() \
               AND EXCLUDED.fencing_token > stream_ownership.fencing_token \
             RETURNING stream_id, owner_worker_id, acquired_at, fencing_token, \
                       expires_at, last_heartbeat_at",
        )
        .bind(stream_id)
        .bind(worker_id)
        .bind(fencing_token)
        .bind(duration_secs)
        .bind(organization_id.0)
        .fetch_optional(&mut *tx)
        .await?;
        tx.commit().await?;
        row.map(|row| row_to_stream_ownership(&row))
            .transpose()?
            .ok_or_else(|| {
                ExecutionStoreError::ConcurrencyConflict(
                    "stream is owned, fencing token is stale, or worker is not active".into(),
                )
            })
    }

    pub async fn renew_stream(
        &self,
        organization_id: OrganizationId,
        stream_id: &str,
        worker_id: &str,
        fencing_token: i64,
    ) -> Result<StreamOwnership, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let row = sqlx::query(
            "UPDATE stream_ownership SET last_heartbeat_at = now(), \
             expires_at = now() + (expires_at - acquired_at) WHERE stream_id = $1 \
             AND owner_worker_id = $2 AND fencing_token = $3 AND expires_at >= now() \
             RETURNING stream_id, owner_worker_id, acquired_at, fencing_token, \
                       expires_at, last_heartbeat_at",
        )
        .bind(stream_id)
        .bind(worker_id)
        .bind(fencing_token)
        .fetch_optional(&mut *tx)
        .await?;
        tx.commit().await?;
        row.map(|row| row_to_stream_ownership(&row))
            .transpose()?
            .ok_or_else(|| {
                ExecutionStoreError::ConcurrencyConflict(
                    "stream ownership lost or fencing token mismatch".into(),
                )
            })
    }

    pub async fn release_stream(
        &self,
        organization_id: OrganizationId,
        stream_id: &str,
        worker_id: &str,
        fencing_token: i64,
    ) -> Result<(), ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let result = sqlx::query(
            "DELETE FROM stream_ownership WHERE stream_id = $1 \
             AND owner_worker_id = $2 AND fencing_token = $3",
        )
        .bind(stream_id)
        .bind(worker_id)
        .bind(fencing_token)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        if result.rows_affected() == 1 {
            Ok(())
        } else {
            Err(ExecutionStoreError::ConcurrencyConflict(
                "stream ownership lost or fencing token mismatch".into(),
            ))
        }
    }

    pub async fn get_expired_streams(
        &self,
        organization_id: OrganizationId,
    ) -> Result<Vec<StreamOwnership>, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let rows = sqlx::query(
            "SELECT stream_id, owner_worker_id, acquired_at, fencing_token, expires_at, \
             last_heartbeat_at FROM stream_ownership WHERE expires_at < now() \
             ORDER BY expires_at",
        )
        .fetch_all(&mut *tx)
        .await?;
        tx.commit().await?;
        rows.iter().map(row_to_stream_ownership).collect()
    }

    pub async fn log_conflict(
        &self,
        organization_id: OrganizationId,
        report: &ConflictReport,
    ) -> Result<(), ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        sqlx::query(
            "INSERT INTO conflict_log (work_item_id, conflict_type, resolution, current_owner, \
             fencing_token, detected_at, organization_id) VALUES ($1,$2,$3,$4,$5,$6,$7)",
        )
        .bind(report.work_item_id.0)
        .bind(conflict_type_to_str(report.conflict_type))
        .bind(conflict_resolution_to_str(report.resolution))
        .bind(&report.current_owner)
        .bind(report.fencing_token)
        .bind(report.detected_at)
        .bind(organization_id.0)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        Ok(())
    }

    pub async fn resolve_conflict(
        &self,
        organization_id: OrganizationId,
        conflict_id: Uuid,
        resolution: ConflictResolution,
    ) -> Result<(), ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let result = sqlx::query(
            "UPDATE conflict_log SET resolution = $1, resolved_at = now() \
             WHERE id = $2 AND resolved_at IS NULL",
        )
        .bind(conflict_resolution_to_str(resolution))
        .bind(conflict_id)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        ensure_one_row(result.rows_affected(), conflict_id)
    }

    pub async fn check_database_health(&self) -> Result<DatabaseHealth, ExecutionStoreError> {
        let started = std::time::Instant::now();
        let healthy: bool = sqlx::query_scalar("SELECT true")
            .fetch_one(&self.pool)
            .await?;
        Ok(DatabaseHealth {
            healthy,
            latency_ms: i64::try_from(started.elapsed().as_millis()).unwrap_or(i64::MAX),
            checked_at: Utc::now(),
        })
    }

    pub async fn handle_failover(
        &self,
        organization_id: OrganizationId,
    ) -> Result<Vec<ConflictReport>, ExecutionStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let rows = sqlx::query(
            "UPDATE work_items SET status = 'pending', lease_id = NULL, \
             lease_expires_at = NULL, fencing_token = COALESCE(fencing_token, 0) + 1, \
             version = version + 1 WHERE status IN ('claimed', 'running') \
             RETURNING id, fencing_token",
        )
        .fetch_all(&mut *tx)
        .await?;
        sqlx::query(
            "UPDATE leases SET status = 'expired', released_at = now() WHERE status = 'active'",
        )
        .execute(&mut *tx)
        .await?;
        let mut reports = Vec::with_capacity(rows.len());
        for row in rows {
            let report = ConflictReport {
                work_item_id: WorkItemId(row.try_get("id")?),
                conflict_type: ConflictType::DatabaseFailover,
                resolution: ConflictResolution::ReloadAndRetry,
                current_owner: None,
                fencing_token: row.try_get("fencing_token")?,
                detected_at: Utc::now(),
            };
            sqlx::query(
                "INSERT INTO conflict_log (work_item_id, conflict_type, resolution, \
                 current_owner, fencing_token, detected_at, organization_id) \
                 VALUES ($1,'database_failover','reload_and_retry',NULL,$2,$3,$4)",
            )
            .bind(report.work_item_id.0)
            .bind(report.fencing_token)
            .bind(report.detected_at)
            .bind(organization_id.0)
            .execute(&mut *tx)
            .await?;
            reports.push(report);
        }
        tx.commit().await?;
        Ok(reports)
    }
}

// ============================================================================
// Row Conversion Helpers
// ============================================================================

fn row_to_run(row: &sqlx::postgres::PgRow) -> Result<Run, ExecutionStoreError> {
    let org_id_str: String = row.try_get("organization_id")?;
    let organization_id = OrganizationId(Uuid::parse_str(&org_id_str).map_err(|_| {
        ExecutionStoreError::Database(sqlx::Error::Decode("invalid organization_id UUID".into()))
    })?);
    let proj_id_str: Option<String> = row.try_get("project_id")?;
    let project_id = proj_id_str
        .map(|s| {
            Uuid::parse_str(&s).map_err(|_| {
                ExecutionStoreError::Database(sqlx::Error::Decode("invalid project_id UUID".into()))
            })
        })
        .transpose()?
        .map(ProjectId);

    Ok(Run {
        id: RunId(row.try_get("id")?),
        organization_id,
        project_id,
        environment_id: row
            .try_get::<Option<Uuid>, _>("environment_id")?
            .map(EnvironmentId),
        principal_id: PrincipalId(row.try_get("principal_id")?),
        contract_hash: row.try_get("contract_hash")?,
        context_manifest_hash: row.try_get("context_manifest_hash")?,
        policy_revision: row.try_get("policy_revision")?,
        status: str_to_run_status(row.try_get("status")?),
        parent_run_id: row.try_get::<Option<Uuid>, _>("parent_run_id")?.map(RunId),
        created_at: row.try_get("created_at")?,
        updated_at: row.try_get("updated_at")?,
        completed_at: row.try_get("completed_at")?,
        terminal_receipt_id: row.try_get("terminal_receipt_id")?,
    })
}

fn row_to_step(row: &sqlx::postgres::PgRow) -> Result<Step, ExecutionStoreError> {
    Ok(Step {
        id: StepId(row.try_get("id")?),
        run_id: RunId(row.try_get("run_id")?),
        parent_step_id: row
            .try_get::<Option<Uuid>, _>("parent_step_id")?
            .map(StepId),
        step_type: str_to_step_type(row.try_get("step_type")?),
        name: row.try_get("name")?,
        status: str_to_step_status(row.try_get("status")?),
        sequence: row.try_get("sequence")?,
        input_hash: row.try_get("input_hash")?,
        output_hash: row.try_get("output_hash")?,
        created_at: row.try_get("created_at")?,
        updated_at: row.try_get("updated_at")?,
        completed_at: row.try_get("completed_at")?,
    })
}

fn row_to_attempt(row: &sqlx::postgres::PgRow) -> Result<Attempt, ExecutionStoreError> {
    Ok(Attempt {
        id: AttemptId(row.try_get("id")?),
        step_id: StepId(row.try_get("step_id")?),
        attempt_number: row.try_get("attempt_number")?,
        status: str_to_attempt_status(row.try_get("status")?),
        provider_call_id: row
            .try_get::<Option<Uuid>, _>("provider_call_id")?
            .map(ProviderCallId),
        started_at: row.try_get("started_at")?,
        completed_at: row.try_get("completed_at")?,
        error: row.try_get("error")?,
        cost_micro_usd: row.try_get("cost_micro_usd")?,
    })
}

fn row_to_work_item(row: &sqlx::postgres::PgRow) -> Result<WorkItem, ExecutionStoreError> {
    Ok(WorkItem {
        id: WorkItemId(row.try_get("id")?),
        run_id: RunId(row.try_get("run_id")?),
        step_id: row.try_get::<Option<Uuid>, _>("step_id")?.map(StepId),
        item_type: row.try_get("item_type")?,
        payload: row.try_get("payload")?,
        status: str_to_work_item_status(row.try_get("status")?),
        priority: row.try_get("priority")?,
        lease_id: row.try_get("lease_id")?,
        lease_expires_at: row.try_get("lease_expires_at")?,
        fencing_token: row.try_get("fencing_token")?,
        attempts: row.try_get("attempts")?,
        max_attempts: row.try_get("max_attempts")?,
        available_at: row.try_get("available_at")?,
        created_at: row.try_get("created_at")?,
        updated_at: row.try_get("updated_at")?,
        version: row.try_get("version")?,
    })
}

fn row_to_worker_registration(
    row: &sqlx::postgres::PgRow,
) -> Result<WorkerRegistration, ExecutionStoreError> {
    Ok(WorkerRegistration {
        worker_id: row.try_get("worker_id")?,
        worker_version: row.try_get("worker_version")?,
        capabilities: serde_json::from_value(row.try_get("capabilities")?)?,
        registered_at: row.try_get("registered_at")?,
        last_heartbeat_at: row.try_get("last_heartbeat_at")?,
        status: str_to_worker_status(row.try_get::<String, _>("status")?.as_str()),
    })
}

fn row_to_stream_ownership(
    row: &sqlx::postgres::PgRow,
) -> Result<StreamOwnership, ExecutionStoreError> {
    Ok(StreamOwnership {
        stream_id: row.try_get("stream_id")?,
        owner_worker_id: row.try_get("owner_worker_id")?,
        acquired_at: row.try_get("acquired_at")?,
        fencing_token: row.try_get("fencing_token")?,
        expires_at: row.try_get("expires_at")?,
        last_heartbeat_at: row.try_get("last_heartbeat_at")?,
    })
}

fn row_to_reservation(row: &sqlx::postgres::PgRow) -> Result<Reservation, ExecutionStoreError> {
    Ok(Reservation {
        id: ReservationId(row.try_get("id")?),
        run_id: RunId(row.try_get("run_id")?),
        resource_type: row.try_get("resource_type")?,
        resource_id: row.try_get("resource_id")?,
        status: str_to_reservation_status(row.try_get("status")?),
        amount_micro_usd: row.try_get("amount_micro_usd")?,
        expires_at: row.try_get("expires_at")?,
        created_at: row.try_get("created_at")?,
        released_at: row.try_get("released_at")?,
    })
}

fn row_to_tool_effect(row: &sqlx::postgres::PgRow) -> Result<ToolEffect, ExecutionStoreError> {
    Ok(ToolEffect {
        id: ToolEffectId(row.try_get("id")?),
        attempt_id: AttemptId(row.try_get("attempt_id")?),
        tool_id: row.try_get("tool_id")?,
        effect_kind: row.try_get("effect_kind")?,
        effect_id_external: row.try_get("effect_id_external")?,
        status: row.try_get("status")?,
        request_hash: row.try_get("request_hash")?,
        response_hash: row.try_get("response_hash")?,
        created_at: row.try_get("created_at")?,
        reconciled_at: row.try_get("reconciled_at")?,
    })
}

fn row_to_provider_call(row: &sqlx::postgres::PgRow) -> Result<ProviderCall, ExecutionStoreError> {
    Ok(ProviderCall {
        id: ProviderCallId(row.try_get("id")?),
        attempt_id: AttemptId(row.try_get("attempt_id")?),
        provider: row.try_get("provider")?,
        model: row.try_get("model")?,
        request_hash: row.try_get("request_hash")?,
        response_hash: row.try_get("response_hash")?,
        usage_input_tokens: row.try_get("usage_input_tokens")?,
        usage_output_tokens: row.try_get("usage_output_tokens")?,
        status: row.try_get("status")?,
        started_at: row.try_get("started_at")?,
        completed_at: row.try_get("completed_at")?,
    })
}

fn row_to_outbox_event(row: &sqlx::postgres::PgRow) -> Result<OutboxEvent, ExecutionStoreError> {
    Ok(OutboxEvent {
        id: row.try_get("id")?,
        aggregate_id: row.try_get("aggregate_id")?,
        aggregate_type: row.try_get("aggregate_type")?,
        event_type: row.try_get("event_type")?,
        payload: row.try_get("payload")?,
        created_at: row.try_get("created_at")?,
        published_at: row.try_get("published_at")?,
        delivery_attempts: row.try_get("delivery_attempts")?,
        status: str_to_outbox_event_status(row.try_get("status")?),
    })
}

fn row_to_circuit_breaker(
    row: &sqlx::postgres::PgRow,
) -> Result<CircuitBreaker, ExecutionStoreError> {
    Ok(CircuitBreaker {
        scope: row.try_get("scope")?,
        state: str_to_circuit_state(row.try_get("state")?),
        failure_count: row.try_get("failure_count")?,
        success_count: row.try_get("success_count")?,
        failure_threshold: row.try_get("failure_threshold")?,
        success_threshold: row.try_get("success_threshold")?,
        open_until: row.try_get("open_until")?,
        cooldown_seconds: row.try_get::<i32, _>("cooldown_seconds")? as i64,
        last_failure_at: row.try_get("last_failure_at")?,
        last_success_at: row.try_get("last_success_at")?,
        created_at: row.try_get("created_at")?,
        updated_at: row.try_get("updated_at")?,
    })
}

fn row_to_lease_info(row: &sqlx::postgres::PgRow) -> Result<LeaseInfo, ExecutionStoreError> {
    Ok(LeaseInfo {
        lease_id: row.try_get("lease_id")?,
        worker_id: row.try_get("worker_id")?,
        acquired_at: row.try_get("acquired_at")?,
        expires_at: row.try_get("expires_at")?,
        heartbeat_at: row.try_get("heartbeat_at")?,
        fencing_token: row.try_get("fencing_token")?,
    })
}

fn row_to_idempotency_record(
    row: &sqlx::postgres::PgRow,
) -> Result<IdempotencyRecord, ExecutionStoreError> {
    Ok(IdempotencyRecord {
        id: row.try_get("id")?,
        idempotency_key: IdempotencyKey {
            key: row.try_get("idempotency_key")?,
            principal_id: PrincipalId(row.try_get("principal_id")?),
            endpoint_profile: row.try_get("endpoint_profile")?,
            request_hash: row.try_get("request_hash")?,
            policy_revision: row.try_get("policy_revision")?,
        },
        organization_id: OrganizationId(row.try_get("organization_id")?),
        run_id: row.try_get::<Option<Uuid>, _>("run_id")?.map(RunId),
        status: str_to_idempotency_status(row.try_get("status")?),
        response_ref: row.try_get("response_ref")?,
        terminal_receipt_id: row.try_get("terminal_receipt_id")?,
        created_at: row.try_get("created_at")?,
        completed_at: row.try_get("completed_at")?,
        expires_at: row.try_get("expires_at")?,
    })
}

fn row_to_cancellation_log_entry(
    row: &sqlx::postgres::PgRow,
) -> Result<CancellationLogEntry, ExecutionStoreError> {
    Ok(CancellationLogEntry {
        id: row.try_get("id")?,
        run_id: RunId(row.try_get("run_id")?),
        reason: str_to_cancellation_reason(row.try_get("reason")?),
        requested_by: PrincipalId(row.try_get("requested_by")?),
        requested_at: row.try_get("requested_at")?,
        processed_at: row.try_get("processed_at")?,
        cancelled_descendants: json_array_or_default(row, "cancelled_descendants")?,
        released_reservations: json_array_or_default(row, "released_reservations")?,
        cancelled_provider_calls: json_array_or_default(row, "cancelled_provider_calls")?,
        cancelled_tool_effects: json_array_or_default(row, "cancelled_tool_effects")?,
    })
}

fn row_to_effect_result(row: &sqlx::postgres::PgRow) -> Result<EffectResult, ExecutionStoreError> {
    Ok(EffectResult {
        effect_id: row.try_get("effect_id")?,
        status: str_to_effect_result_status(row.try_get("status")?),
        external_effect_id: row.try_get("external_effect_id")?,
        response_hash: row.try_get("response_hash")?,
        reconciled_at: row.try_get("reconciled_at")?,
        error: row.try_get("error")?,
        created_at: row.try_get("created_at")?,
    })
}

fn json_array_or_default<T>(
    row: &sqlx::postgres::PgRow,
    column: &str,
) -> Result<Vec<T>, ExecutionStoreError>
where
    T: serde::de::DeserializeOwned,
{
    match row.try_get::<Option<serde_json::Value>, _>(column)? {
        Some(value) => Ok(serde_json::from_value(value)?),
        None => Ok(Vec::new()),
    }
}

fn uuid_rows(
    rows: &[sqlx::postgres::PgRow],
    column: &str,
) -> Result<Vec<Uuid>, ExecutionStoreError> {
    rows.iter()
        .map(|row| row.try_get(column).map_err(Into::into))
        .collect()
}

fn ensure_one_row(rows_affected: u64, id: Uuid) -> Result<(), ExecutionStoreError> {
    if rows_affected == 1 {
        Ok(())
    } else {
        Err(ExecutionStoreError::ConcurrencyConflict(format!(
            "record {id} is missing or not in the required state"
        )))
    }
}

fn ensure_worker_row(rows_affected: u64, worker_id: &str) -> Result<(), ExecutionStoreError> {
    if rows_affected == 1 {
        Ok(())
    } else {
        Err(ExecutionStoreError::ConcurrencyConflict(format!(
            "worker {worker_id} is missing or not in the required state"
        )))
    }
}

// ============================================================================
// String Conversion Helpers
// ============================================================================

fn run_status_to_str(status: RunStatus) -> &'static str {
    match status {
        RunStatus::Pending => "pending",
        RunStatus::Running => "running",
        RunStatus::AwaitingApproval => "awaiting_approval",
        RunStatus::AwaitingVerification => "awaiting_verification",
        RunStatus::Completed => "completed",
        RunStatus::Failed => "failed",
        RunStatus::Cancelled => "cancelled",
        RunStatus::TimedOut => "timed_out",
    }
}

fn str_to_run_status(s: &str) -> RunStatus {
    match s {
        "running" => RunStatus::Running,
        "awaiting_approval" => RunStatus::AwaitingApproval,
        "awaiting_verification" => RunStatus::AwaitingVerification,
        "completed" => RunStatus::Completed,
        "failed" => RunStatus::Failed,
        "cancelled" => RunStatus::Cancelled,
        "timed_out" => RunStatus::TimedOut,
        _ => RunStatus::Pending,
    }
}

fn step_status_to_str(status: StepStatus) -> &'static str {
    match status {
        StepStatus::Pending => "pending",
        StepStatus::Ready => "ready",
        StepStatus::Running => "running",
        StepStatus::Completed => "completed",
        StepStatus::Failed => "failed",
        StepStatus::Skipped => "skipped",
        StepStatus::Cancelled => "cancelled",
    }
}

fn str_to_step_status(s: &str) -> StepStatus {
    match s {
        "ready" => StepStatus::Ready,
        "running" => StepStatus::Running,
        "completed" => StepStatus::Completed,
        "failed" => StepStatus::Failed,
        "skipped" => StepStatus::Skipped,
        "cancelled" => StepStatus::Cancelled,
        _ => StepStatus::Pending,
    }
}

fn attempt_status_to_str(status: AttemptStatus) -> &'static str {
    match status {
        AttemptStatus::Pending => "pending",
        AttemptStatus::Running => "running",
        AttemptStatus::Succeeded => "succeeded",
        AttemptStatus::Failed => "failed",
        AttemptStatus::Cancelled => "cancelled",
        AttemptStatus::TimedOut => "timed_out",
    }
}

fn str_to_attempt_status(s: &str) -> AttemptStatus {
    match s {
        "running" => AttemptStatus::Running,
        "succeeded" => AttemptStatus::Succeeded,
        "failed" => AttemptStatus::Failed,
        "cancelled" => AttemptStatus::Cancelled,
        "timed_out" => AttemptStatus::TimedOut,
        _ => AttemptStatus::Pending,
    }
}

#[allow(dead_code)]
fn work_item_status_to_str(status: WorkItemStatus) -> &'static str {
    match status {
        WorkItemStatus::Pending => "pending",
        WorkItemStatus::Claimed => "claimed",
        WorkItemStatus::Running => "running",
        WorkItemStatus::Completed => "completed",
        WorkItemStatus::Failed => "failed",
        WorkItemStatus::Cancelled => "cancelled",
        WorkItemStatus::DeadLetter => "dead_letter",
    }
}

fn str_to_work_item_status(s: &str) -> WorkItemStatus {
    match s {
        "claimed" => WorkItemStatus::Claimed,
        "running" => WorkItemStatus::Running,
        "completed" => WorkItemStatus::Completed,
        "failed" => WorkItemStatus::Failed,
        "cancelled" => WorkItemStatus::Cancelled,
        "dead_letter" => WorkItemStatus::DeadLetter,
        _ => WorkItemStatus::Pending,
    }
}

fn worker_status_to_str(status: WorkerStatus) -> &'static str {
    match status {
        WorkerStatus::Active => "active",
        WorkerStatus::Draining => "draining",
        WorkerStatus::Drained => "drained",
        WorkerStatus::Failed => "failed",
        WorkerStatus::Decommissioned => "decommissioned",
    }
}

fn str_to_worker_status(status: &str) -> WorkerStatus {
    match status {
        "draining" => WorkerStatus::Draining,
        "drained" => WorkerStatus::Drained,
        "failed" => WorkerStatus::Failed,
        "decommissioned" => WorkerStatus::Decommissioned,
        _ => WorkerStatus::Active,
    }
}

fn conflict_type_to_str(conflict_type: ConflictType) -> &'static str {
    match conflict_type {
        ConflictType::VersionMismatch => "version_mismatch",
        ConflictType::LeaseExpired => "lease_expired",
        ConflictType::WorkerUnresponsive => "worker_unresponsive",
        ConflictType::DatabaseFailover => "database_failover",
        ConflictType::StreamOwnershipLost => "stream_ownership_lost",
    }
}

fn conflict_resolution_to_str(resolution: ConflictResolution) -> &'static str {
    match resolution {
        ConflictResolution::ReloadAndRetry => "reload_and_retry",
        ConflictResolution::TakeOver => "take_over",
        ConflictResolution::Wait => "wait",
        ConflictResolution::Abort => "abort",
    }
}

#[allow(dead_code)]
fn reservation_status_to_str(status: ReservationStatus) -> &'static str {
    match status {
        ReservationStatus::Active => "active",
        ReservationStatus::Released => "released",
        ReservationStatus::Expired => "expired",
        ReservationStatus::Consumed => "consumed",
    }
}

fn str_to_reservation_status(s: &str) -> ReservationStatus {
    match s {
        "released" => ReservationStatus::Released,
        "expired" => ReservationStatus::Expired,
        "consumed" => ReservationStatus::Consumed,
        _ => ReservationStatus::Active,
    }
}

fn step_type_to_str(step_type: StepType) -> &'static str {
    match step_type {
        StepType::Contract => "contract",
        StepType::Route => "route",
        StepType::ProviderCall => "provider_call",
        StepType::ToolExecution => "tool_execution",
        StepType::Verification => "verification",
        StepType::Approval => "approval",
        StepType::ContextBuild => "context_build",
        StepType::ArtifactStore => "artifact_store",
        StepType::ReceiptSign => "receipt_sign",
    }
}

fn str_to_step_type(s: &str) -> StepType {
    match s {
        "route" => StepType::Route,
        "provider_call" => StepType::ProviderCall,
        "tool_execution" => StepType::ToolExecution,
        "verification" => StepType::Verification,
        "approval" => StepType::Approval,
        "context_build" => StepType::ContextBuild,
        "artifact_store" => StepType::ArtifactStore,
        "receipt_sign" => StepType::ReceiptSign,
        _ => StepType::Contract,
    }
}

fn outbox_event_status_to_str(status: OutboxEventStatus) -> &'static str {
    match status {
        OutboxEventStatus::Pending => "pending",
        OutboxEventStatus::Published => "published",
        OutboxEventStatus::Failed => "failed",
        OutboxEventStatus::DeadLettered => "dead_lettered",
    }
}

fn str_to_outbox_event_status(s: &str) -> OutboxEventStatus {
    match s {
        "published" => OutboxEventStatus::Published,
        "failed" => OutboxEventStatus::Failed,
        "dead_lettered" => OutboxEventStatus::DeadLettered,
        _ => OutboxEventStatus::Pending,
    }
}

fn str_to_circuit_state(s: &str) -> CircuitState {
    match s {
        "half_open" => CircuitState::HalfOpen,
        "open" => CircuitState::Open,
        _ => CircuitState::Closed,
    }
}

fn str_to_idempotency_status(status: &str) -> IdempotencyStatus {
    match status {
        "completed" => IdempotencyStatus::Completed,
        "failed" => IdempotencyStatus::Failed,
        "expired" => IdempotencyStatus::Expired,
        _ => IdempotencyStatus::InProgress,
    }
}

fn cancellation_reason_to_str(reason: CancellationReason) -> &'static str {
    match reason {
        CancellationReason::UserRequested => "user_requested",
        CancellationReason::Timeout => "timeout",
        CancellationReason::BudgetExceeded => "budget_exceeded",
        CancellationReason::PolicyViolation => "policy_violation",
        CancellationReason::ParentCancelled => "parent_cancelled",
        CancellationReason::DependencyFailed => "dependency_failed",
    }
}

fn str_to_cancellation_reason(reason: &str) -> CancellationReason {
    match reason {
        "timeout" => CancellationReason::Timeout,
        "budget_exceeded" => CancellationReason::BudgetExceeded,
        "policy_violation" => CancellationReason::PolicyViolation,
        "parent_cancelled" => CancellationReason::ParentCancelled,
        "dependency_failed" => CancellationReason::DependencyFailed,
        _ => CancellationReason::UserRequested,
    }
}

fn effect_intent_to_str(intent: EffectIntent) -> &'static str {
    match intent {
        EffectIntent::Read => "read",
        EffectIntent::Write => "write",
        EffectIntent::Delete => "delete",
    }
}

fn effect_result_status_to_str(status: EffectResultStatus) -> &'static str {
    match status {
        EffectResultStatus::Applied => "applied",
        EffectResultStatus::Rejected => "rejected",
        EffectResultStatus::Unknown => "unknown",
        EffectResultStatus::Reconciled => "reconciled",
        EffectResultStatus::Compensated => "compensated",
    }
}

fn str_to_effect_result_status(status: &str) -> EffectResultStatus {
    match status {
        "applied" => EffectResultStatus::Applied,
        "rejected" => EffectResultStatus::Rejected,
        "reconciled" => EffectResultStatus::Reconciled,
        "compensated" => EffectResultStatus::Compensated,
        _ => EffectResultStatus::Unknown,
    }
}

// ============================================================================
// Tests
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn run_status_conversion_roundtrips() {
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
            assert_eq!(str_to_run_status(run_status_to_str(status)), status);
        }
    }

    #[test]
    fn step_status_conversion_roundtrips() {
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
            assert_eq!(str_to_step_status(step_status_to_str(status)), status);
        }
    }

    #[test]
    fn attempt_status_conversion_roundtrips() {
        let cases = [
            AttemptStatus::Pending,
            AttemptStatus::Running,
            AttemptStatus::Succeeded,
            AttemptStatus::Failed,
            AttemptStatus::Cancelled,
            AttemptStatus::TimedOut,
        ];
        for status in cases {
            assert_eq!(str_to_attempt_status(attempt_status_to_str(status)), status);
        }
    }

    #[test]
    fn work_item_status_conversion_roundtrips() {
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
            assert_eq!(
                str_to_work_item_status(work_item_status_to_str(status)),
                status
            );
        }
    }

    #[test]
    fn reservation_status_conversion_roundtrips() {
        let cases = [
            ReservationStatus::Active,
            ReservationStatus::Released,
            ReservationStatus::Expired,
            ReservationStatus::Consumed,
        ];
        for status in cases {
            assert_eq!(
                str_to_reservation_status(reservation_status_to_str(status)),
                status
            );
        }
    }

    #[test]
    fn step_type_conversion_roundtrips() {
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
            assert_eq!(str_to_step_type(step_type_to_str(step_type)), step_type);
        }
    }
}
