-- Migration: 0011_lifecycle.sql
-- Description: Versioned tenant lifecycle policy, deletion jobs, exports, and
--              deletion tombstones. Rollback is the reverse-order DROP TABLE
--              sequence documented at the end of this file.

CREATE TABLE IF NOT EXISTS lifecycle_policies (
    id TEXT PRIMARY KEY,
    organization_id UUID NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('prompts','outputs','artifacts','caches','traces','logs','receipts','ledger_events','recommendations','exports','provider_copies','audit_events','context_nodes','usage_records')),
    action TEXT NOT NULL CHECK (action IN ('retain','delete','cryptographic_delete','archive','export')),
    retain_days INTEGER,
    delete_after TIMESTAMPTZ,
    description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    version INTEGER NOT NULL DEFAULT 1,
    active BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS deletion_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('prompts','outputs','artifacts','caches','traces','logs','receipts','ledger_events','recommendations','exports','provider_copies','audit_events','context_nodes','usage_records')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','in_progress','completed','failed','partially_completed','cancelled')),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    items_total BIGINT NOT NULL DEFAULT 0,
    items_deleted BIGINT NOT NULL DEFAULT 0,
    items_failed BIGINT NOT NULL DEFAULT 0,
    items_skipped_legal_hold BIGINT NOT NULL DEFAULT 0,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by UUID NOT NULL
);

CREATE TABLE IF NOT EXISTS export_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL,
    requested_by UUID NOT NULL,
    categories JSONB NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','in_progress','completed','failed','expired')),
    artifact_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS tombstones (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL,
    original_table TEXT NOT NULL,
    original_id TEXT NOT NULL,
    deleted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_by UUID NOT NULL,
    deletion_job_id UUID REFERENCES deletion_jobs(id),
    reason TEXT NOT NULL,
    UNIQUE(original_table, original_id, organization_id)
);

ALTER TABLE lifecycle_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE lifecycle_policies FORCE ROW LEVEL SECURITY;
CREATE POLICY lifecycle_policies_tenant ON lifecycle_policies
    USING (organization_id = current_setting('noerelay.organization_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('noerelay.organization_id', true)::uuid);

ALTER TABLE deletion_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE deletion_jobs FORCE ROW LEVEL SECURITY;
CREATE POLICY deletion_jobs_tenant ON deletion_jobs
    USING (organization_id = current_setting('noerelay.organization_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('noerelay.organization_id', true)::uuid);

ALTER TABLE export_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE export_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY export_requests_tenant ON export_requests
    USING (organization_id = current_setting('noerelay.organization_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('noerelay.organization_id', true)::uuid);

ALTER TABLE tombstones ENABLE ROW LEVEL SECURITY;
ALTER TABLE tombstones FORCE ROW LEVEL SECURITY;
CREATE POLICY tombstones_tenant ON tombstones
    USING (organization_id = current_setting('noerelay.organization_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('noerelay.organization_id', true)::uuid);

CREATE INDEX idx_lifecycle_policies_org ON lifecycle_policies(organization_id, active);
CREATE INDEX idx_lifecycle_policies_category ON lifecycle_policies(category);
CREATE INDEX idx_deletion_jobs_org ON deletion_jobs(organization_id);
CREATE INDEX idx_deletion_jobs_status ON deletion_jobs(status);
CREATE INDEX idx_export_requests_org ON export_requests(organization_id);
CREATE INDEX idx_export_requests_status ON export_requests(status);
CREATE INDEX idx_tombstones_org ON tombstones(organization_id);
CREATE INDEX idx_tombstones_original ON tombstones(original_table, original_id);

COMMENT ON TABLE lifecycle_policies IS 'Versioned tenant lifecycle rules; superseded rows remain inactive for auditability';
COMMENT ON TABLE tombstones IS 'Minimal deletion proofs; contains no deleted payload';
COMMENT ON TABLE deletion_jobs IS 'Operational deletion progress, including items retained under legal hold';
COMMENT ON TABLE export_requests IS 'Tenant export requests whose generated bundles are artifact records';

-- Rollback (manual, after confirming no lifecycle evidence must be retained):
-- DROP TABLE tombstones;
-- DROP TABLE export_requests;
-- DROP TABLE deletion_jobs;
-- DROP TABLE lifecycle_policies;
