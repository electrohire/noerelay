-- Migration: 0012_governance.sql
-- Description: Immutable governance revisions, impact graph links, exact run
--              revision pins, and evidence staleness records.

CREATE TABLE IF NOT EXISTS governance_revisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type TEXT NOT NULL CHECK (entity_type IN ('architecture_decision','requirement','acceptance_criterion','threat','control','test_harness','policy','risk_classification','work_order','implementation_artifact','evidence_requirement','release_baseline','component')),
    entity_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    revision_hash TEXT NOT NULL,
    lifecycle TEXT NOT NULL DEFAULT 'draft' CHECK (lifecycle IN ('draft','proposed','reviewed','approved','active','superseded','rejected')),
    title TEXT NOT NULL,
    content JSONB NOT NULL,
    parent_revision_id UUID REFERENCES governance_revisions(id),
    superseded_by_id UUID REFERENCES governance_revisions(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by UUID NOT NULL,
    approved_at TIMESTAMPTZ,
    approved_by UUID,
    activated_at TIMESTAMPTZ,
    organization_id UUID NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    UNIQUE(entity_id, revision)
);

CREATE TABLE IF NOT EXISTS governance_dependencies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_entity_id TEXT NOT NULL,
    source_revision INTEGER NOT NULL CHECK (source_revision > 0),
    target_entity_id TEXT NOT NULL,
    target_revision INTEGER NOT NULL CHECK (target_revision > 0),
    link_type TEXT NOT NULL CHECK (link_type IN ('requires','implements','tests','verifies','blocks','supersedes','evidence_for','approved_by')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    organization_id UUID NOT NULL,
    UNIQUE(source_entity_id, source_revision, target_entity_id, target_revision, link_type)
);

CREATE TABLE IF NOT EXISTS run_pins (
    run_id UUID PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    pinned_revisions JSONB NOT NULL,
    pinned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    organization_id UUID NOT NULL
);

-- Staleness is append-only evidence about a supersession, not a mutation of an
-- earlier evidence revision.
CREATE TABLE IF NOT EXISTS governance_stale_evidence (
    evidence_entity_id TEXT NOT NULL,
    governed_entity_id TEXT NOT NULL,
    governed_revision INTEGER NOT NULL,
    marked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    organization_id UUID NOT NULL,
    PRIMARY KEY (evidence_entity_id, governed_entity_id, governed_revision)
);

-- Governance content and pins are immutable. Lifecycle metadata is the only
-- mutable portion of a revision and is changed by the repository state machine.
CREATE OR REPLACE FUNCTION enforce_governance_revision_immutability()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'governance revisions are append-only';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.entity_type IS DISTINCT FROM OLD.entity_type
       OR NEW.entity_id IS DISTINCT FROM OLD.entity_id
       OR NEW.revision IS DISTINCT FROM OLD.revision
       OR NEW.revision_hash IS DISTINCT FROM OLD.revision_hash
       OR NEW.title IS DISTINCT FROM OLD.title
       OR NEW.content IS DISTINCT FROM OLD.content
       OR NEW.parent_revision_id IS DISTINCT FROM OLD.parent_revision_id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.created_by IS DISTINCT FROM OLD.created_by
       OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
       OR NEW.notes IS DISTINCT FROM OLD.notes THEN
        RAISE EXCEPTION 'governance revision content is immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER governance_revisions_immutable
    BEFORE UPDATE OR DELETE ON governance_revisions
    FOR EACH ROW EXECUTE FUNCTION enforce_governance_revision_immutability();

CREATE OR REPLACE FUNCTION reject_governance_row_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER governance_dependencies_immutable
    BEFORE UPDATE OR DELETE ON governance_dependencies
    FOR EACH ROW EXECUTE FUNCTION reject_governance_row_mutation();

CREATE TRIGGER run_pins_immutable
    BEFORE UPDATE OR DELETE ON run_pins
    FOR EACH ROW EXECUTE FUNCTION reject_governance_row_mutation();

ALTER TABLE governance_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE governance_revisions FORCE ROW LEVEL SECURITY;
CREATE POLICY governance_revisions_tenant ON governance_revisions
    USING (organization_id = current_setting('noerelay.organization_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('noerelay.organization_id', true)::uuid);

ALTER TABLE governance_dependencies ENABLE ROW LEVEL SECURITY;
ALTER TABLE governance_dependencies FORCE ROW LEVEL SECURITY;
CREATE POLICY governance_dependencies_tenant ON governance_dependencies
    USING (organization_id = current_setting('noerelay.organization_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('noerelay.organization_id', true)::uuid);

ALTER TABLE run_pins ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_pins FORCE ROW LEVEL SECURITY;
CREATE POLICY run_pins_tenant ON run_pins
    USING (organization_id = current_setting('noerelay.organization_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('noerelay.organization_id', true)::uuid);

ALTER TABLE governance_stale_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE governance_stale_evidence FORCE ROW LEVEL SECURITY;
CREATE POLICY governance_stale_evidence_tenant ON governance_stale_evidence
    USING (organization_id = current_setting('noerelay.organization_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('noerelay.organization_id', true)::uuid);

CREATE INDEX idx_gov_revisions_entity ON governance_revisions(entity_id, revision);
CREATE INDEX idx_gov_revisions_lifecycle ON governance_revisions(lifecycle);
CREATE UNIQUE INDEX idx_gov_revisions_active ON governance_revisions(entity_id) WHERE lifecycle = 'active';
CREATE INDEX idx_gov_deps_source ON governance_dependencies(source_entity_id, source_revision);
CREATE INDEX idx_gov_deps_target ON governance_dependencies(target_entity_id, target_revision);
CREATE INDEX idx_run_pins_org ON run_pins(organization_id);
CREATE INDEX idx_gov_stale_evidence_org ON governance_stale_evidence(organization_id, evidence_entity_id);

COMMENT ON TABLE governance_revisions IS 'Versioned governance content; historical content is never overwritten';
COMMENT ON TABLE governance_dependencies IS 'Revision-specific, bidirectionally queryable governance impact graph edges';
COMMENT ON TABLE run_pins IS 'Exact immutable governance revision set used by a run';

-- Rollback (manual, destructive, and only after governance evidence retention approval):
-- DROP TABLE governance_stale_evidence;
-- DROP TABLE run_pins;
-- DROP TABLE governance_dependencies;
-- DROP TABLE governance_revisions;
-- DROP FUNCTION reject_governance_row_mutation();
-- DROP FUNCTION enforce_governance_revision_immutability();
