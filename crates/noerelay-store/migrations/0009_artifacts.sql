-- Migration: 0009_artifacts.sql
-- Description: Tenant-scoped content-addressed artifact metadata, retention,
--              encryption state, legal holds, and run/receipt binding support.

CREATE TABLE IF NOT EXISTS artifacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL,
    artifact_type TEXT NOT NULL CHECK (artifact_type IN ('request','response','provider_log','tool_output','verification_log','media','test_log','evidence','receipt','context')),
    content_hash TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    storage_key TEXT NOT NULL,
    storage_backend TEXT NOT NULL DEFAULT 'local' CHECK (storage_backend IN ('local','s3','minio')),
    encrypted BOOLEAN NOT NULL DEFAULT false,
    encryption_key_id TEXT,
    encryption_algorithm TEXT,
    retention_policy_id TEXT,
    retain_days INTEGER,
    delete_after TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by UUID NOT NULL,
    run_id UUID REFERENCES runs(id) ON DELETE SET NULL,
    deleted_at TIMESTAMPTZ,
    legal_hold BOOLEAN NOT NULL DEFAULT false,
    UNIQUE(content_hash, organization_id)
);

ALTER TABLE artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE artifacts FORCE ROW LEVEL SECURITY;
CREATE POLICY artifacts_tenant ON artifacts
    USING (organization_id = current_setting('noerelay.organization_id', true)::uuid);

CREATE INDEX idx_artifacts_org ON artifacts(organization_id);
CREATE INDEX idx_artifacts_hash ON artifacts(content_hash);
CREATE INDEX idx_artifacts_run ON artifacts(run_id);
CREATE INDEX idx_artifacts_type ON artifacts(artifact_type);
CREATE INDEX idx_artifacts_delete_after ON artifacts(delete_after) WHERE deleted_at IS NULL AND legal_hold = false;
CREATE INDEX idx_artifacts_legal_hold ON artifacts(legal_hold) WHERE legal_hold = true;
