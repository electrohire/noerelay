-- Migration: 0006_outbox_leases.sql
-- Description: Transactional outbox for reliable event publishing, circuit
--              breakers for downstream dependency health, and lease tracking
--              for distributed worker coordination with fencing tokens.

-- ============================================================================
-- 1. OUTBOX TABLE (Transactional Event Publishing)
-- ============================================================================

CREATE TABLE IF NOT EXISTS outbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    aggregate_id TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ,
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'published', 'failed', 'dead_lettered')),
    last_error TEXT,
    organization_id TEXT NOT NULL REFERENCES organizations(organization_id)
);

-- ============================================================================
-- 2. CIRCUIT BREAKERS TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS circuit_breakers (
    scope TEXT PRIMARY KEY,
    state TEXT NOT NULL DEFAULT 'closed'
        CHECK (state IN ('closed', 'half_open', 'open')),
    failure_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_threshold INTEGER NOT NULL DEFAULT 5,
    success_threshold INTEGER NOT NULL DEFAULT 2,
    open_until TIMESTAMPTZ,
    cooldown_seconds INTEGER NOT NULL DEFAULT 30,
    last_failure_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- 3. LEASES TABLE (Distributed Worker Coordination)
-- ============================================================================

CREATE TABLE IF NOT EXISTS leases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    work_item_id UUID NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
    worker_id TEXT NOT NULL,
    lease_id TEXT NOT NULL UNIQUE,
    fencing_token BIGINT NOT NULL,
    acquired_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    heartbeat_at TIMESTAMPTZ,
    released_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'released', 'expired'))
);

-- ============================================================================
-- 4. ROW-LEVEL SECURITY
-- ============================================================================

-- RLS on outbox (direct tenant column)
ALTER TABLE outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbox FORCE ROW LEVEL SECURITY;
CREATE POLICY outbox_tenant ON outbox
    USING (organization_id = current_setting('noerelay.organization_id', true))
    WITH CHECK (organization_id = current_setting('noerelay.organization_id', true));

-- RLS on circuit_breakers (global table, read-only for all tenants)
ALTER TABLE circuit_breakers ENABLE ROW LEVEL SECURITY;
ALTER TABLE circuit_breakers FORCE ROW LEVEL SECURITY;
CREATE POLICY circuit_breakers_read ON circuit_breakers
    FOR SELECT USING (true);

-- RLS on leases (derived from parent work_item → run)
ALTER TABLE leases ENABLE ROW LEVEL SECURITY;
ALTER TABLE leases FORCE ROW LEVEL SECURITY;
CREATE POLICY leases_tenant ON leases
    USING (work_item_id IN (
        SELECT id FROM work_items
        WHERE run_id IN (
            SELECT id FROM runs
            WHERE organization_id = current_setting('noerelay.organization_id', true)
        )
    ))
    WITH CHECK (work_item_id IN (
        SELECT id FROM work_items
        WHERE run_id IN (
            SELECT id FROM runs
            WHERE organization_id = current_setting('noerelay.organization_id', true)
        )
    ));

-- ============================================================================
-- 5. INDEXES
-- ============================================================================

-- Outbox
CREATE INDEX idx_outbox_status ON outbox(status, created_at) WHERE status = 'pending';
CREATE INDEX idx_outbox_org ON outbox(organization_id);

-- Circuit breakers
CREATE INDEX idx_circuit_breakers_state ON circuit_breakers(state);

-- Leases
CREATE INDEX idx_leases_work_item ON leases(work_item_id);
CREATE INDEX idx_leases_expires ON leases(expires_at) WHERE status = 'active';
CREATE INDEX idx_leases_worker ON leases(worker_id);

-- ============================================================================
-- 6. TRIGGERS FOR UPDATED_AT
-- ============================================================================

CREATE TRIGGER update_circuit_breakers_updated_at
    BEFORE UPDATE ON circuit_breakers
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();