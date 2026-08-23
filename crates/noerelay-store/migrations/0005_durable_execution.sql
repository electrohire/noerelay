-- Migration: 0005_durable_execution.sql
-- Description: Normalized append-friendly execution entities: runs, steps,
--              attempts, work items, reservations, tool effects, and provider
--              calls. Each entity has a formal state machine with CHECK
--              constraints. Includes RLS on all tenant-bearing tables.

-- ============================================================================
-- 1. RUNS TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
    project_id TEXT,
    environment_id UUID REFERENCES environments(environment_id),
    principal_id UUID NOT NULL REFERENCES principals(principal_id),
    contract_hash TEXT NOT NULL,
    context_manifest_hash TEXT,
    policy_revision TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'awaiting_approval',
                          'awaiting_verification', 'completed', 'failed',
                          'cancelled', 'timed_out')),
    parent_run_id UUID REFERENCES runs(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    terminal_receipt_id TEXT
);

-- ============================================================================
-- 2. STEPS TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS steps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    parent_step_id UUID REFERENCES steps(id),
    step_type TEXT NOT NULL
        CHECK (step_type IN ('contract', 'route', 'provider_call',
                             'tool_execution', 'verification', 'approval',
                             'context_build', 'artifact_store', 'receipt_sign')),
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'ready', 'running', 'completed',
                          'failed', 'skipped', 'cancelled')),
    sequence INTEGER NOT NULL DEFAULT 0,
    input_hash TEXT,
    output_hash TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

-- ============================================================================
-- 3. ATTEMPTS TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    step_id UUID NOT NULL REFERENCES steps(id) ON DELETE CASCADE,
    attempt_number INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'succeeded', 'failed',
                          'cancelled', 'timed_out')),
    provider_call_id UUID,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    error TEXT,
    cost_micro_usd BIGINT,
    UNIQUE (step_id, attempt_number)
);

-- ============================================================================
-- 4. WORK ITEMS TABLE (DURABLE QUEUE)
-- ============================================================================

CREATE TABLE IF NOT EXISTS work_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step_id UUID REFERENCES steps(id),
    item_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'claimed', 'running', 'completed',
                          'failed', 'cancelled', 'dead_letter')),
    priority INTEGER NOT NULL DEFAULT 0,
    lease_id TEXT,
    lease_expires_at TIMESTAMPTZ,
    fencing_token BIGINT,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- 5. RESERVATIONS TABLE (BUDGET)
-- ============================================================================

CREATE TABLE IF NOT EXISTS reservations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'released', 'expired', 'consumed')),
    amount_micro_usd BIGINT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at TIMESTAMPTZ
);

-- ============================================================================
-- 6. TOOL EFFECTS TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS tool_effects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id UUID NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
    tool_id TEXT NOT NULL,
    effect_kind TEXT NOT NULL,
    effect_id_external TEXT,
    status TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_hash TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    reconciled_at TIMESTAMPTZ
);

-- ============================================================================
-- 7. PROVIDER CALLS TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS provider_calls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id UUID NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_hash TEXT,
    usage_input_tokens INTEGER,
    usage_output_tokens INTEGER,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

-- ============================================================================
-- 8. ROW-LEVEL SECURITY
-- ============================================================================

-- RLS on runs (direct tenant column)
ALTER TABLE runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE runs FORCE ROW LEVEL SECURITY;
CREATE POLICY runs_tenant ON runs
    USING (organization_id = current_setting('noerelay.organization_id', true))
    WITH CHECK (organization_id = current_setting('noerelay.organization_id', true));

-- RLS on steps (derived from parent run)
ALTER TABLE steps ENABLE ROW LEVEL SECURITY;
ALTER TABLE steps FORCE ROW LEVEL SECURITY;
CREATE POLICY steps_tenant ON steps
    USING (run_id IN (
        SELECT id FROM runs
        WHERE organization_id = current_setting('noerelay.organization_id', true)
    ))
    WITH CHECK (run_id IN (
        SELECT id FROM runs
        WHERE organization_id = current_setting('noerelay.organization_id', true)
    ));

-- RLS on attempts (derived from parent step → run)
ALTER TABLE attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE attempts FORCE ROW LEVEL SECURITY;
CREATE POLICY attempts_tenant ON attempts
    USING (step_id IN (
        SELECT id FROM steps
        WHERE run_id IN (
            SELECT id FROM runs
            WHERE organization_id = current_setting('noerelay.organization_id', true)
        )
    ))
    WITH CHECK (step_id IN (
        SELECT id FROM steps
        WHERE run_id IN (
            SELECT id FROM runs
            WHERE organization_id = current_setting('noerelay.organization_id', true)
        )
    ));

-- RLS on work_items (derived from parent run)
ALTER TABLE work_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE work_items FORCE ROW LEVEL SECURITY;
CREATE POLICY work_items_tenant ON work_items
    USING (run_id IN (
        SELECT id FROM runs
        WHERE organization_id = current_setting('noerelay.organization_id', true)
    ))
    WITH CHECK (run_id IN (
        SELECT id FROM runs
        WHERE organization_id = current_setting('noerelay.organization_id', true)
    ));

-- RLS on reservations (derived from parent run)
ALTER TABLE reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE reservations FORCE ROW LEVEL SECURITY;
CREATE POLICY reservations_tenant ON reservations
    USING (run_id IN (
        SELECT id FROM runs
        WHERE organization_id = current_setting('noerelay.organization_id', true)
    ))
    WITH CHECK (run_id IN (
        SELECT id FROM runs
        WHERE organization_id = current_setting('noerelay.organization_id', true)
    ));

-- RLS on tool_effects (derived from parent attempt → step → run)
ALTER TABLE tool_effects ENABLE ROW LEVEL SECURITY;
ALTER TABLE tool_effects FORCE ROW LEVEL SECURITY;
CREATE POLICY tool_effects_tenant ON tool_effects
    USING (attempt_id IN (
        SELECT id FROM attempts
        WHERE step_id IN (
            SELECT id FROM steps
            WHERE run_id IN (
                SELECT id FROM runs
                WHERE organization_id = current_setting('noerelay.organization_id', true)
            )
        )
    ))
    WITH CHECK (attempt_id IN (
        SELECT id FROM attempts
        WHERE step_id IN (
            SELECT id FROM steps
            WHERE run_id IN (
                SELECT id FROM runs
                WHERE organization_id = current_setting('noerelay.organization_id', true)
            )
        )
    ));

-- RLS on provider_calls (derived from parent attempt → step → run)
ALTER TABLE provider_calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE provider_calls FORCE ROW LEVEL SECURITY;
CREATE POLICY provider_calls_tenant ON provider_calls
    USING (attempt_id IN (
        SELECT id FROM attempts
        WHERE step_id IN (
            SELECT id FROM steps
            WHERE run_id IN (
                SELECT id FROM runs
                WHERE organization_id = current_setting('noerelay.organization_id', true)
            )
        )
    ))
    WITH CHECK (attempt_id IN (
        SELECT id FROM attempts
        WHERE step_id IN (
            SELECT id FROM steps
            WHERE run_id IN (
                SELECT id FROM runs
                WHERE organization_id = current_setting('noerelay.organization_id', true)
            )
        )
    ));

-- ============================================================================
-- 9. INDEXES
-- ============================================================================

-- Runs
CREATE INDEX idx_runs_org ON runs(organization_id);
CREATE INDEX idx_runs_status ON runs(status);
CREATE INDEX idx_runs_principal ON runs(principal_id);
CREATE INDEX idx_runs_parent ON runs(parent_run_id);

-- Steps
CREATE INDEX idx_steps_run ON steps(run_id);
CREATE INDEX idx_steps_status ON steps(status);
CREATE INDEX idx_steps_parent ON steps(parent_step_id);
CREATE INDEX idx_steps_run_seq ON steps(run_id, sequence);

-- Attempts
CREATE INDEX idx_attempts_step ON attempts(step_id);
CREATE INDEX idx_attempts_status ON attempts(status);

-- Work items (durable queue)
CREATE INDEX idx_work_items_status ON work_items(status);
CREATE INDEX idx_work_items_claim ON work_items(status, available_at, priority DESC)
    WHERE status = 'pending';
CREATE INDEX idx_work_items_run ON work_items(run_id);
CREATE INDEX idx_work_items_lease ON work_items(lease_id) WHERE lease_id IS NOT NULL;

-- Reservations
CREATE INDEX idx_reservations_run ON reservations(run_id);
CREATE INDEX idx_reservations_status ON reservations(status);
CREATE INDEX idx_reservations_expires ON reservations(status, expires_at)
    WHERE status = 'active';

-- Tool effects
CREATE INDEX idx_tool_effects_attempt ON tool_effects(attempt_id);
CREATE INDEX idx_tool_effects_tool ON tool_effects(tool_id);

-- Provider calls
CREATE INDEX idx_provider_calls_attempt ON provider_calls(attempt_id);
CREATE INDEX idx_provider_calls_provider ON provider_calls(provider);

-- ============================================================================
-- 10. TRIGGERS FOR UPDATED_AT
-- ============================================================================

CREATE TRIGGER update_runs_updated_at
    BEFORE UPDATE ON runs
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_steps_updated_at
    BEFORE UPDATE ON steps
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_work_items_updated_at
    BEFORE UPDATE ON work_items
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();