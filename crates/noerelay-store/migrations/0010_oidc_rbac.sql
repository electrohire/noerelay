-- IAM-03: OIDC provider configuration and step-up approvals.

CREATE TABLE IF NOT EXISTS oidc_providers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL,
    issuer TEXT NOT NULL,
    audience TEXT NOT NULL,
    jwks_url TEXT NOT NULL,
    claim_to_scope JSONB NOT NULL DEFAULT '{}',
    clock_skew_seconds INTEGER NOT NULL DEFAULT 30,
    require_nonce BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(organization_id, issuer)
);

CREATE TABLE IF NOT EXISTS step_up_approvals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    approver_id UUID NOT NULL,
    organization_id UUID NOT NULL,
    action_hash TEXT NOT NULL,
    action_description TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_id UUID NOT NULL,
    granted_permissions JSONB NOT NULL DEFAULT '[]',
    expires_at TIMESTAMPTZ NOT NULL,
    separation_of_duties BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    UNIQUE(action_hash, approver_id)
);

ALTER TABLE oidc_providers ENABLE ROW LEVEL SECURITY;
ALTER TABLE oidc_providers FORCE ROW LEVEL SECURITY;
CREATE POLICY oidc_providers_tenant ON oidc_providers
    USING (organization_id = current_setting('noerelay.organization_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('noerelay.organization_id', true)::uuid);

ALTER TABLE step_up_approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE step_up_approvals FORCE ROW LEVEL SECURITY;
CREATE POLICY step_up_approvals_tenant ON step_up_approvals
    USING (organization_id = current_setting('noerelay.organization_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('noerelay.organization_id', true)::uuid);

CREATE INDEX idx_oidc_providers_org ON oidc_providers(organization_id);
CREATE INDEX idx_oidc_providers_issuer ON oidc_providers(issuer);
CREATE INDEX idx_step_up_approvals_approver ON step_up_approvals(approver_id);
CREATE INDEX idx_step_up_approvals_action ON step_up_approvals(action_hash);
CREATE INDEX idx_step_up_approvals_expires ON step_up_approvals(expires_at)
    WHERE used_at IS NULL AND revoked_at IS NULL;
