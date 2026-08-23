CREATE TABLE organizations (
    organization_id text PRIMARY KEY CHECK (length(organization_id) BETWEEN 1 AND 128),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE projects (
    organization_id text NOT NULL REFERENCES organizations(organization_id),
    project_id text NOT NULL CHECK (length(project_id) BETWEEN 1 AND 128),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (organization_id, project_id)
);

CREATE TABLE authority_snapshots (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    storage_version bigint NOT NULL CHECK (storage_version > 0),
    snapshot jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (organization_id, project_id),
    FOREIGN KEY (organization_id, project_id) REFERENCES projects(organization_id, project_id)
);

CREATE TABLE ledger_events (
    organization_id text NOT NULL REFERENCES organizations(organization_id),
    project_id text NOT NULL,
    sequence bigint NOT NULL CHECK (sequence > 0),
    occurred_at_unix_ms bigint NOT NULL CHECK (occurred_at_unix_ms >= 0),
    run_id text NOT NULL CHECK (length(run_id) BETWEEN 1 AND 128),
    event_kind text NOT NULL,
    payload jsonb NOT NULL,
    previous_hash char(64) NOT NULL,
    event_hash char(64) NOT NULL,
    inserted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (organization_id, sequence),
    UNIQUE (organization_id, event_hash),
    FOREIGN KEY (organization_id, project_id) REFERENCES projects(organization_id, project_id)
);

CREATE INDEX ledger_events_project_run_idx
    ON ledger_events (organization_id, project_id, run_id, sequence);

CREATE TABLE run_receipts (
    organization_id text NOT NULL REFERENCES organizations(organization_id),
    project_id text NOT NULL,
    user_id text NOT NULL CHECK (length(user_id) BETWEEN 1 AND 128),
    run_id text NOT NULL CHECK (length(run_id) BETWEEN 1 AND 128),
    receipt_hash char(64) NOT NULL,
    receipt jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (organization_id, run_id),
    UNIQUE (organization_id, receipt_hash),
    FOREIGN KEY (organization_id, project_id) REFERENCES projects(organization_id, project_id)
);

CREATE INDEX run_receipts_rollup_idx
    ON run_receipts (organization_id, project_id, user_id, created_at);

CREATE TABLE usage_records (
    organization_id text NOT NULL REFERENCES organizations(organization_id),
    project_id text NOT NULL,
    user_id text NOT NULL,
    run_id text NOT NULL,
    cost_microusd bigint NOT NULL CHECK (cost_microusd >= 0),
    source text NOT NULL CHECK (source IN ('estimated', 'provider_reported', 'billed')),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (organization_id, run_id, source),
    FOREIGN KEY (organization_id, run_id) REFERENCES run_receipts(organization_id, run_id)
);

CREATE TABLE model_observations (
    organization_id text NOT NULL REFERENCES organizations(organization_id),
    project_id text NOT NULL,
    cohort_id text NOT NULL,
    model_id text NOT NULL,
    contract_hash char(64) NOT NULL,
    accepted boolean NOT NULL,
    cost_microusd bigint NOT NULL CHECK (cost_microusd >= 0),
    latency_ms bigint NOT NULL CHECK (latency_ms >= 0),
    evidence_id text NOT NULL,
    observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (organization_id, evidence_id),
    FOREIGN KEY (organization_id, project_id) REFERENCES projects(organization_id, project_id)
);

CREATE OR REPLACE FUNCTION reject_ledger_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'ledger_events is append-only';
END;
$$;

CREATE TRIGGER ledger_events_no_update
    BEFORE UPDATE OR DELETE ON ledger_events
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE projects FORCE ROW LEVEL SECURITY;
ALTER TABLE authority_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE authority_snapshots FORCE ROW LEVEL SECURITY;
ALTER TABLE ledger_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE ledger_events FORCE ROW LEVEL SECURITY;
ALTER TABLE run_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_receipts FORCE ROW LEVEL SECURITY;
ALTER TABLE usage_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_records FORCE ROW LEVEL SECURITY;
ALTER TABLE model_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_observations FORCE ROW LEVEL SECURITY;

CREATE POLICY project_tenant_scope ON projects
    USING (organization_id = current_setting('noerelay.organization_id', true))
    WITH CHECK (organization_id = current_setting('noerelay.organization_id', true));
CREATE POLICY snapshot_tenant_scope ON authority_snapshots
    USING (organization_id = current_setting('noerelay.organization_id', true))
    WITH CHECK (organization_id = current_setting('noerelay.organization_id', true));
CREATE POLICY ledger_tenant_scope ON ledger_events
    USING (organization_id = current_setting('noerelay.organization_id', true))
    WITH CHECK (organization_id = current_setting('noerelay.organization_id', true));
CREATE POLICY receipt_tenant_scope ON run_receipts
    USING (organization_id = current_setting('noerelay.organization_id', true))
    WITH CHECK (organization_id = current_setting('noerelay.organization_id', true));
CREATE POLICY usage_tenant_scope ON usage_records
    USING (organization_id = current_setting('noerelay.organization_id', true))
    WITH CHECK (organization_id = current_setting('noerelay.organization_id', true));
CREATE POLICY observation_tenant_scope ON model_observations
    USING (organization_id = current_setting('noerelay.organization_id', true))
    WITH CHECK (organization_id = current_setting('noerelay.organization_id', true));
