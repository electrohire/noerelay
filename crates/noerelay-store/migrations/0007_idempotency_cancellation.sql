-- Migration: 0007_idempotency_cancellation.sql
-- Description: Scoped idempotency, cancellation audit, and effect journal.

CREATE TABLE IF NOT EXISTS idempotency_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key TEXT NOT NULL,
    principal_id UUID NOT NULL,
    endpoint_profile TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    policy_revision TEXT NOT NULL,
    organization_id UUID NOT NULL,
    run_id UUID REFERENCES runs(id),
    status TEXT NOT NULL DEFAULT 'in_progress'
        CHECK (status IN ('in_progress', 'completed', 'failed', 'expired')),
    response_ref TEXT,
    terminal_receipt_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    UNIQUE (idempotency_key, principal_id, endpoint_profile)
);

CREATE TABLE IF NOT EXISTS cancellation_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    reason TEXT NOT NULL
        CHECK (reason IN ('user_requested', 'timeout', 'budget_exceeded',
                          'policy_violation', 'parent_cancelled',
                          'dependency_failed')),
    requested_by UUID NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ,
    cancelled_descendants JSONB,
    released_reservations JSONB,
    cancelled_provider_calls JSONB,
    cancelled_tool_effects JSONB
);

-- The intent is inserted with status "unknown" before dispatch. A terminal
-- result updates the same row; unknown rows are explicitly reconciled.
CREATE TABLE IF NOT EXISTS effect_journal (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    effect_id TEXT NOT NULL,
    attempt_id UUID NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
    tool_id TEXT NOT NULL,
    intent TEXT NOT NULL CHECK (intent IN ('read', 'write', 'delete')),
    request_hash TEXT NOT NULL,
    idempotency_key TEXT,
    status TEXT NOT NULL
        CHECK (status IN ('applied', 'rejected', 'unknown', 'reconciled',
                          'compensated')),
    external_effect_id TEXT,
    response_hash TEXT,
    reconciled_at TIMESTAMPTZ,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (effect_id, attempt_id),
    UNIQUE (effect_id)
);

ALTER TABLE idempotency_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE idempotency_records FORCE ROW LEVEL SECURITY;
CREATE POLICY idempotency_tenant ON idempotency_records
    USING (organization_id = current_setting('noerelay.organization_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('noerelay.organization_id', true)::uuid);

ALTER TABLE cancellation_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE cancellation_log FORCE ROW LEVEL SECURITY;
CREATE POLICY cancellation_tenant ON cancellation_log
    USING (run_id IN (
        SELECT id FROM runs
        WHERE organization_id = current_setting('noerelay.organization_id', true)
    ))
    WITH CHECK (run_id IN (
        SELECT id FROM runs
        WHERE organization_id = current_setting('noerelay.organization_id', true)
    ));

ALTER TABLE effect_journal ENABLE ROW LEVEL SECURITY;
ALTER TABLE effect_journal FORCE ROW LEVEL SECURITY;
CREATE POLICY effect_journal_tenant ON effect_journal
    USING (attempt_id IN (
        SELECT id FROM attempts WHERE step_id IN (
            SELECT id FROM steps WHERE run_id IN (
                SELECT id FROM runs
                WHERE organization_id = current_setting('noerelay.organization_id', true)
            )
        )
    ))
    WITH CHECK (attempt_id IN (
        SELECT id FROM attempts WHERE step_id IN (
            SELECT id FROM steps WHERE run_id IN (
                SELECT id FROM runs
                WHERE organization_id = current_setting('noerelay.organization_id', true)
            )
        )
    ));

CREATE INDEX idx_idempotency_lookup
    ON idempotency_records(idempotency_key, principal_id, endpoint_profile);
CREATE INDEX idx_idempotency_status ON idempotency_records(status);
CREATE INDEX idx_idempotency_expires ON idempotency_records(expires_at)
    WHERE status = 'in_progress';
CREATE INDEX idx_cancellation_run ON cancellation_log(run_id);
CREATE INDEX idx_effect_journal_effect ON effect_journal(effect_id);
CREATE INDEX idx_effect_journal_attempt ON effect_journal(attempt_id);
CREATE INDEX idx_effect_journal_status ON effect_journal(status)
    WHERE status = 'unknown';
