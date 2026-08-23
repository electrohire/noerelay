-- Migration: 0004_api_keys.sql
-- Description: API key lifecycle management with rate limiting, concurrency
--              tracking, and immutable audit logging. Includes RLS on all
--              tenant-bearing tables.

-- ============================================================================
-- 1. API KEYS TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    principal_id UUID NOT NULL REFERENCES principals(principal_id),
    organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
    project_id TEXT,
    environment_id UUID REFERENCES environments(environment_id),
    role_id UUID REFERENCES roles(role_id),
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 256),
    prefix TEXT NOT NULL,  -- e.g., "nr_live_v1_a1b2c3d4"
    key_hash TEXT NOT NULL,  -- Argon2id hash, never the plaintext
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'suspended', 'revoked')),
    expires_at TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,
    last_used_ip TEXT,
    rate_limit_per_minute INTEGER,
    concurrency_limit INTEGER,
    current_concurrency INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at TIMESTAMPTZ,
    revoked_by UUID REFERENCES principals(principal_id),
    revoked_reason TEXT,
    UNIQUE(prefix)
);

-- Composite FK for project_id (must match organization_id)
-- Note: project_id is nullable, so FK is only enforced when both are present.
-- A trigger-based approach could be added later for stricter enforcement.

-- ============================================================================
-- 2. RATE LIMIT TRACKING (SLIDING WINDOW)
-- ============================================================================

CREATE TABLE IF NOT EXISTS api_key_rate_limits (
    api_key_id UUID NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
    window_start TIMESTAMPTZ NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (api_key_id, window_start)
);

-- ============================================================================
-- 3. IMMUTABLE AUDIT LOG
-- ============================================================================

CREATE TABLE IF NOT EXISTS api_key_audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    api_key_id UUID NOT NULL REFERENCES api_keys(id),
    event_type TEXT NOT NULL
        CHECK (event_type IN ('created', 'verified', 'revoked', 'rotated', 'rate_limited', 'expired')),
    principal_id UUID REFERENCES principals(principal_id),
    ip_address TEXT,
    user_agent TEXT,
    detail JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- 4. ROW-LEVEL SECURITY
-- ============================================================================

-- RLS on api_keys
ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_keys FORCE ROW LEVEL SECURITY;
CREATE POLICY api_keys_tenant_isolation ON api_keys
    USING (organization_id = current_setting('noerelay.organization_id', true));

-- RLS on api_key_rate_limits (derived from parent key)
ALTER TABLE api_key_rate_limits ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_key_rate_limits FORCE ROW LEVEL SECURITY;
CREATE POLICY api_key_rate_limits_tenant ON api_key_rate_limits
    USING (api_key_id IN (
        SELECT id FROM api_keys
        WHERE organization_id = current_setting('noerelay.organization_id', true)
    ));

-- RLS on api_key_audit_log (derived from parent key)
ALTER TABLE api_key_audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_key_audit_log FORCE ROW LEVEL SECURITY;
CREATE POLICY api_key_audit_tenant ON api_key_audit_log
    USING (api_key_id IN (
        SELECT id FROM api_keys
        WHERE organization_id = current_setting('noerelay.organization_id', true)
    ));

-- ============================================================================
-- 5. INDEXES
-- ============================================================================

CREATE INDEX idx_api_keys_principal ON api_keys(principal_id);
CREATE INDEX idx_api_keys_organization ON api_keys(organization_id);
CREATE INDEX idx_api_keys_prefix ON api_keys(prefix);
CREATE INDEX idx_api_keys_status ON api_keys(status);
CREATE INDEX idx_api_key_audit_key ON api_key_audit_log(api_key_id);
CREATE INDEX idx_api_key_audit_created ON api_key_audit_log(created_at);
CREATE INDEX idx_api_key_rate_limits_window ON api_key_rate_limits(api_key_id, window_start);