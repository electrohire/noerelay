-- Migration: 0008_multi_replica.sql
-- Description: Multi-replica worker coordination, fenced stream ownership,
--              optimistic concurrency, and conflict audit logging.

CREATE TABLE IF NOT EXISTS workers (
    worker_id TEXT PRIMARY KEY,
    worker_version TEXT NOT NULL,
    capabilities JSONB NOT NULL DEFAULT '[]',
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active','draining','drained','failed','decommissioned')),
    organization_id UUID NOT NULL
);

CREATE TABLE IF NOT EXISTS stream_ownership (
    stream_id TEXT PRIMARY KEY,
    owner_worker_id TEXT NOT NULL REFERENCES workers(worker_id),
    acquired_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    fencing_token BIGINT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    last_heartbeat_at TIMESTAMPTZ,
    organization_id UUID NOT NULL
);

CREATE TABLE IF NOT EXISTS conflict_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    work_item_id UUID NOT NULL,
    conflict_type TEXT NOT NULL
        CHECK (conflict_type IN ('version_mismatch','lease_expired','worker_unresponsive',
                                 'database_failover','stream_ownership_lost')),
    resolution TEXT NOT NULL
        CHECK (resolution IN ('reload_and_retry','take_over','wait','abort')),
    current_owner TEXT,
    fencing_token BIGINT NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    organization_id UUID NOT NULL
);

ALTER TABLE work_items ADD COLUMN IF NOT EXISTS version BIGINT NOT NULL DEFAULT 0;

ALTER TABLE workers ENABLE ROW LEVEL SECURITY;
ALTER TABLE workers FORCE ROW LEVEL SECURITY;
CREATE POLICY workers_tenant ON workers
    USING (organization_id = current_setting('noerelay.organization_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('noerelay.organization_id', true)::uuid);

ALTER TABLE stream_ownership ENABLE ROW LEVEL SECURITY;
ALTER TABLE stream_ownership FORCE ROW LEVEL SECURITY;
CREATE POLICY stream_ownership_tenant ON stream_ownership
    USING (organization_id = current_setting('noerelay.organization_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('noerelay.organization_id', true)::uuid);

ALTER TABLE conflict_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE conflict_log FORCE ROW LEVEL SECURITY;
CREATE POLICY conflict_log_tenant ON conflict_log
    USING (organization_id = current_setting('noerelay.organization_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('noerelay.organization_id', true)::uuid);

CREATE INDEX idx_workers_status ON workers(status);
CREATE INDEX idx_workers_heartbeat ON workers(last_heartbeat_at) WHERE status = 'active';
CREATE INDEX idx_stream_ownership_owner ON stream_ownership(owner_worker_id);
CREATE INDEX idx_stream_ownership_expires ON stream_ownership(expires_at);
CREATE INDEX idx_conflict_log_work_item ON conflict_log(work_item_id);
CREATE INDEX idx_conflict_log_detected ON conflict_log(detected_at);
