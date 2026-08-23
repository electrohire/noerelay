-- Migration: 0013_registry.sql
-- Description: Immutable, tenant-scoped model/provider/agent/tool revisions.

CREATE TABLE IF NOT EXISTS registry_revisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type TEXT NOT NULL CHECK (entity_type IN ('model','provider','agent','tool')),
    entity_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    revision_hash TEXT NOT NULL,
    lifecycle TEXT NOT NULL DEFAULT 'draft' CHECK (lifecycle IN ('draft','proposed','reviewed','approved','active','quarantined','superseded','rejected')),
    content JSONB NOT NULL,
    display_name TEXT NOT NULL,
    organization_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by UUID NOT NULL,
    activated_at TIMESTAMPTZ,
    activated_by UUID,
    superseded_by UUID REFERENCES registry_revisions(id),
    quarantine_reason TEXT,
    notes TEXT NOT NULL DEFAULT '',
    UNIQUE(entity_type, entity_id, revision)
);

-- Revision identity and entity content are immutable. Only lifecycle metadata
-- may change through the Rust registry state machine.
CREATE OR REPLACE FUNCTION enforce_registry_revision_immutability()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'registry revisions are append-only';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.entity_type IS DISTINCT FROM OLD.entity_type
       OR NEW.entity_id IS DISTINCT FROM OLD.entity_id
       OR NEW.revision IS DISTINCT FROM OLD.revision
       OR NEW.revision_hash IS DISTINCT FROM OLD.revision_hash
       OR NEW.content IS DISTINCT FROM OLD.content
       OR NEW.display_name IS DISTINCT FROM OLD.display_name
       OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.created_by IS DISTINCT FROM OLD.created_by
       OR NEW.notes IS DISTINCT FROM OLD.notes THEN
        RAISE EXCEPTION 'registry revision content is immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER registry_revisions_immutable
    BEFORE UPDATE OR DELETE ON registry_revisions
    FOR EACH ROW EXECUTE FUNCTION enforce_registry_revision_immutability();

ALTER TABLE registry_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE registry_revisions FORCE ROW LEVEL SECURITY;
CREATE POLICY registry_tenant ON registry_revisions
    USING (organization_id = current_setting('noerelay.organization_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('noerelay.organization_id', true)::uuid);

CREATE INDEX idx_registry_entity ON registry_revisions(entity_type, entity_id, revision);
CREATE UNIQUE INDEX idx_registry_active ON registry_revisions(entity_type, entity_id)
    WHERE lifecycle = 'active';
CREATE INDEX idx_registry_lifecycle ON registry_revisions(lifecycle);
CREATE INDEX idx_registry_org ON registry_revisions(organization_id);

COMMENT ON TABLE registry_revisions IS 'Immutable model/provider/agent/tool revisions with mutable lifecycle metadata';

-- Rollback (manual and destructive; export registry history first):
-- DROP TABLE registry_revisions;
-- DROP FUNCTION enforce_registry_revision_immutability();
