# IAM-01 Design Document: Canonical Tenancy and Policy Scope

**Work Package:** IAM-01  
**Requirements:** NR-API-004, NR-IAM-001  
**Baseline:** `5a24249a9098a6c468da45d27a449fab380863b5` on `main`  
**Profile:** `single-region-org-v1-local-test`  
**Date:** 2026-08-21

---

## 1. Current State Analysis

### 1.1 Existing Identity Model

The current system uses a flat, string-based identity model defined in [`crates/noerelay-core/src/types.rs`](crates/noerelay-core/src/types.rs:30):

```rust
pub struct IdentityScope {
    pub organization_id: String,
    pub project_id: String,
    pub environment_id: String,
    pub user_id: String,
    pub session_id: String,
}
```

**Limitations:**
- All identifiers are opaque strings with no referential integrity
- No distinction between human users and service identities
- No role-based access control (RBAC) or permission model
- No quota or policy binding mechanism
- Scope is trusted from caller headers (via `default_scope` in gateway config)
- No audit trail for administrative actions

### 1.2 Existing Database Schema

Migration [`0001_authority.sql`](crates/noerelay-store/migrations/0001_authority.sql) creates:
- `organizations` — minimal table with only `organization_id` and `created_at`
- `projects` — composite PK `(organization_id, project_id)`
- `authority_snapshots`, `ledger_events`, `run_receipts`, `usage_records`, `model_observations`

All tenant-bearing tables have RLS enabled with `FORCE ROW LEVEL SECURITY` and policies using `current_setting('noerelay.organization_id', true)`.

Migration [`0002_usage_tokens.sql`](crates/noerelay-store/migrations/0002_usage_tokens.sql) adds token columns to `usage_records`.

### 1.3 Existing Store Implementation

[`PostgresAuthorityStore`](crates/noerelay-store/src/lib.rs:31) provides:
- `connect()` — runs migrations automatically
- `load()` / `save()` — authority snapshot persistence with optimistic concurrency
- `receipt()` — receipt retrieval
- `cost_rollups()` — usage aggregation

The `set_scope()` function sets `noerelay.organization_id` as a transaction-local GUC for RLS.

### 1.4 Gateway Integration

The gateway ([`crates/noerelay-gateway/src/lib.rs`](crates/noerelay-gateway/src/lib.rs)) currently:
- Uses a single `default_scope` from environment variables
- Validates API key via SHA-256 hash comparison
- Does not perform per-request identity resolution or authorization
- Persists all data under the single configured organization/project

---

## 2. Design Goals

1. **Normalized tenancy model** — organizations → projects → environments with proper referential integrity
2. **Principal abstraction** — unified model for human users and service identities
3. **RBAC** — roles, permissions, and memberships with scope-aware authorization
4. **Quota management** — configurable limits at org, project, and environment scopes
5. **Policy bindings** — flexible policy attachment to any scope
6. **Server-side scope derivation** — never trust caller-provided scope headers
7. **Comprehensive RLS** — tenant isolation on all tables including caches, streams, outbox, artifacts, reports, and derived data
8. **Audit attribution** — every administrative action logged with actor identity

---

## 3. PostgreSQL Migration: `0003_iam_tenancy.sql`

### 3.1 Design Decisions

- **UUID primary keys** for all IAM entities (except `permissions` which uses text for readability)
- **Soft deletes** via `deleted_at` on all tenant-scoped entities
- **Composite foreign keys** to maintain referential integrity across the tenancy hierarchy
- **RLS on ALL tables** including the IAM tables themselves (defense in depth)
- **Separate `iam_` schema prefix** is NOT used; tables remain in `public` for consistency with existing migrations
- **GUC-based tenant context** continues to use `noerelay.organization_id` for RLS, extended with `noerelay.principal_id` for audit

### 3.2 Full DDL

```sql
-- Migration: 0003_iam_tenancy.sql
-- Description: Normalized IAM model with organizations, projects, environments,
--              principals, memberships, roles, permissions, quotas, policy bindings,
--              and service identities. Includes RLS on all tenant-bearing tables.

-- ============================================================================
-- 1. EXTEND EXISTING TABLES
-- ============================================================================

-- Add columns to existing organizations table
ALTER TABLE organizations
    ADD COLUMN IF NOT EXISTS name text,
    ADD COLUMN IF NOT EXISTS slug text,
    ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'suspended', 'archived')),
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    ADD COLUMN IF NOT EXISTS deleted_at timestamptz;

-- Add columns to existing projects table
ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS name text,
    ADD COLUMN IF NOT EXISTS slug text,
    ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'suspended', 'archived')),
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    ADD COLUMN IF NOT EXISTS deleted_at timestamptz;

-- ============================================================================
-- 2. NEW IAM TABLES
-- ============================================================================

-- Environments: subdivision of projects (e.g., production, staging, development)
CREATE TABLE environments (
    environment_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id text NOT NULL REFERENCES organizations(organization_id),
    project_id text NOT NULL,
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 128),
    slug text NOT NULL CHECK (length(slug) BETWEEN 1 AND 64),
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'suspended', 'archived')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    deleted_at timestamptz,
    FOREIGN KEY (organization_id, project_id) REFERENCES projects(organization_id, project_id),
    UNIQUE (organization_id, project_id, slug)
);

-- Principals: unified identity for humans and services
CREATE TABLE principals (
    principal_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id text NOT NULL REFERENCES organizations(organization_id),
    principal_type text NOT NULL CHECK (principal_type IN ('human', 'service')),
    external_id text NOT NULL CHECK (length(external_id) BETWEEN 1 AND 256),
    display_name text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 256),
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'suspended', 'archived')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    deleted_at timestamptz,
    UNIQUE (organization_id, principal_type, external_id)
);

-- Roles: named collections of permissions, scoped to an organization
CREATE TABLE roles (
    role_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id text NOT NULL REFERENCES organizations(organization_id),
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 128),
    description text,
    is_system boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (organization_id, name)
);

-- Permissions: atomic actions on resources
CREATE TABLE permissions (
    permission_id text PRIMARY KEY CHECK (length(permission_id) BETWEEN 1 AND 128),
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 128),
    description text,
    resource text NOT NULL CHECK (length(resource) BETWEEN 1 AND 128),
    action text NOT NULL CHECK (length(action) BETWEEN 1 AND 64),
    UNIQUE (resource, action)
);

-- Role-Permission join table
CREATE TABLE role_permissions (
    role_id uuid NOT NULL REFERENCES roles(role_id) ON DELETE CASCADE,
    permission_id text NOT NULL REFERENCES permissions(permission_id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (role_id, permission_id)
);

-- Memberships: bind principals to roles at specific scopes
CREATE TABLE memberships (
    membership_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    principal_id uuid NOT NULL REFERENCES principals(principal_id),
    organization_id text NOT NULL REFERENCES organizations(organization_id),
    project_id text,
    environment_id uuid REFERENCES environments(environment_id),
    role_id uuid NOT NULL REFERENCES roles(role_id),
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'suspended', 'archived')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    -- Scope validation: environment requires project, project requires org
    CHECK (
        (environment_id IS NULL AND project_id IS NULL) OR
        (environment_id IS NULL AND project_id IS NOT NULL) OR
        (environment_id IS NOT NULL AND project_id IS NOT NULL)
    ),
    FOREIGN KEY (organization_id, project_id) REFERENCES projects(organization_id, project_id),
    UNIQUE (principal_id, organization_id, project_id, environment_id, role_id)
);

-- Quotas: resource limits at various scopes
CREATE TABLE quotas (
    quota_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_type text NOT NULL CHECK (scope_type IN ('organization', 'project', 'environment')),
    scope_id text NOT NULL,  -- organization_id, project_id, or environment_id::text
    resource_type text NOT NULL CHECK (length(resource_type) BETWEEN 1 AND 128),
    limit_value bigint NOT NULL CHECK (limit_value >= 0),
    period text NOT NULL CHECK (period IN ('daily', 'weekly', 'monthly', 'total')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (scope_type, scope_id, resource_type, period)
);

-- Policy bindings: attach policies to scopes
CREATE TABLE policy_bindings (
    binding_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_type text NOT NULL CHECK (scope_type IN ('organization', 'project', 'environment')),
    scope_id text NOT NULL,
    policy_type text NOT NULL CHECK (length(policy_type) BETWEEN 1 AND 128),
    policy_data jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (scope_type, scope_id, policy_type)
);

-- Service identities: credentials for service principals
CREATE TABLE service_identities (
    service_identity_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    principal_id uuid NOT NULL REFERENCES principals(principal_id) ON DELETE CASCADE,
    service_name text NOT NULL CHECK (length(service_name) BETWEEN 1 AND 128),
    credential_hash text NOT NULL CHECK (length(credential_hash) BETWEEN 32 AND 256),
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'suspended', 'revoked')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (principal_id, service_name)
);

-- Audit log for administrative actions
CREATE TABLE iam_audit_log (
    audit_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id text NOT NULL,
    actor_principal_id uuid,
    action text NOT NULL CHECK (length(action) BETWEEN 1 AND 128),
    resource_type text NOT NULL CHECK (length(resource_type) BETWEEN 1 AND 128),
    resource_id text NOT NULL,
    old_value jsonb,
    new_value jsonb,
    ip_address inet,
    user_agent text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

-- ============================================================================
-- 3. INDEXES
-- ============================================================================

-- Organizations
CREATE INDEX idx_organizations_slug ON organizations(slug) WHERE deleted_at IS NULL;
CREATE INDEX idx_organizations_status ON organizations(status) WHERE deleted_at IS NULL;

-- Projects
CREATE INDEX idx_projects_org_slug ON projects(organization_id, slug) WHERE deleted_at IS NULL;
CREATE INDEX idx_projects_status ON projects(organization_id, status) WHERE deleted_at IS NULL;

-- Environments
CREATE INDEX idx_environments_project ON environments(organization_id, project_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_environments_slug ON environments(organization_id, project_id, slug) WHERE deleted_at IS NULL;

-- Principals
CREATE INDEX idx_principals_org ON principals(organization_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_principals_external ON principals(organization_id, principal_type, external_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_principals_type ON principals(organization_id, principal_type) WHERE deleted_at IS NULL;

-- Memberships
CREATE INDEX idx_memberships_principal ON memberships(principal_id) WHERE status = 'active';
CREATE INDEX idx_memberships_scope ON memberships(organization_id, project_id, environment_id) WHERE status = 'active';
CREATE INDEX idx_memberships_role ON memberships(role_id) WHERE status = 'active';

-- Roles
CREATE INDEX idx_roles_org ON roles(organization_id);
CREATE INDEX idx_roles_system ON roles(organization_id, is_system);

-- Role permissions
CREATE INDEX idx_role_permissions_role ON role_permissions(role_id);
CREATE INDEX idx_role_permissions_permission ON role_permissions(permission_id);

-- Quotas
CREATE INDEX idx_quotas_scope ON quotas(scope_type, scope_id);
CREATE INDEX idx_quotas_resource ON quotas(scope_type, scope_id, resource_type);

-- Policy bindings
CREATE INDEX idx_policy_bindings_scope ON policy_bindings(scope_type, scope_id);
CREATE INDEX idx_policy_bindings_type ON policy_bindings(scope_type, scope_id, policy_type);

-- Service identities
CREATE INDEX idx_service_identities_principal ON service_identities(principal_id);
CREATE INDEX idx_service_identities_status ON service_identities(principal_id, status);

-- Audit log
CREATE INDEX idx_iam_audit_org_time ON iam_audit_log(organization_id, created_at);
CREATE INDEX idx_iam_audit_actor ON iam_audit_log(actor_principal_id, created_at);
CREATE INDEX idx_iam_audit_resource ON iam_audit_log(resource_type, resource_id, created_at);

-- ============================================================================
-- 4. ROW LEVEL SECURITY
-- ============================================================================

-- Enable and force RLS on all new IAM tables
ALTER TABLE environments ENABLE ROW LEVEL SECURITY;
ALTER TABLE environments FORCE ROW LEVEL SECURITY;

ALTER TABLE principals ENABLE ROW LEVEL SECURITY;
ALTER TABLE principals FORCE ROW LEVEL SECURITY;

ALTER TABLE roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE roles FORCE ROW LEVEL SECURITY;

ALTER TABLE permissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE permissions FORCE ROW LEVEL SECURITY;

ALTER TABLE role_permissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE role_permissions FORCE ROW LEVEL SECURITY;

ALTER TABLE memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE memberships FORCE ROW LEVEL SECURITY;

ALTER TABLE quotas ENABLE ROW LEVEL SECURITY;
ALTER TABLE quotas FORCE ROW LEVEL SECURITY;

ALTER TABLE policy_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE policy_bindings FORCE ROW LEVEL SECURITY;

ALTER TABLE service_identities ENABLE ROW LEVEL SECURITY;
ALTER TABLE service_identities FORCE ROW LEVEL SECURITY;

ALTER TABLE iam_audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE iam_audit_log FORCE ROW LEVEL SECURITY;

-- RLS Policies: tenant isolation via organization_id GUC
-- Pattern: USING (organization_id = current_setting('noerelay.organization_id', true))
--          WITH CHECK (organization_id = current_setting('noerelay.organization_id', true))

CREATE POLICY environments_tenant_scope ON environments
    USING (organization_id = current_setting('noerelay.organization_id', true))
    WITH CHECK (organization_id = current_setting('noerelay.organization_id', true));

CREATE POLICY principals_tenant_scope ON principals
    USING (organization_id = current_setting('noerelay.organization_id', true))
    WITH CHECK (organization_id = current_setting('noerelay.organization_id', true));

CREATE POLICY roles_tenant_scope ON roles
    USING (organization_id = current_setting('noerelay.organization_id', true))
    WITH CHECK (organization_id = current_setting('noerelay.organization_id', true));

-- Permissions are global (not tenant-scoped), but we still force RLS for consistency
-- and to prevent accidental cross-tenant data leakage if permissions become tenant-scoped
CREATE POLICY permissions_tenant_scope ON permissions
    USING (true)  -- Permissions are readable by all authenticated sessions
    WITH CHECK (true);

CREATE POLICY role_permissions_tenant_scope ON role_permissions
    USING (role_id IN (
        SELECT role_id FROM roles 
        WHERE organization_id = current_setting('noerelay.organization_id', true)
    ))
    WITH CHECK (role_id IN (
        SELECT role_id FROM roles 
        WHERE organization_id = current_setting('noerelay.organization_id', true)
    ));

CREATE POLICY memberships_tenant_scope ON memberships
    USING (organization_id = current_setting('noerelay.organization_id', true))
    WITH CHECK (organization_id = current_setting('noerelay.organization_id', true));

CREATE POLICY quotas_tenant_scope ON quotas
    USING (
        (scope_type = 'organization' AND scope_id = current_setting('noerelay.organization_id', true)) OR
        (scope_type = 'project' AND scope_id IN (
            SELECT project_id FROM projects 
            WHERE organization_id = current_setting('noerelay.organization_id', true)
        )) OR
        (scope_type = 'environment' AND scope_id IN (
            SELECT environment_id::text FROM environments 
            WHERE organization_id = current_setting('noerelay.organization_id', true)
        ))
    )
    WITH CHECK (
        (scope_type = 'organization' AND scope_id = current_setting('noerelay.organization_id', true)) OR
        (scope_type = 'project' AND scope_id IN (
            SELECT project_id FROM projects 
            WHERE organization_id = current_setting('noerelay.organization_id', true)
        )) OR
        (scope_type = 'environment' AND scope_id IN (
            SELECT environment_id::text FROM environments 
            WHERE organization_id = current_setting('noerelay.organization_id', true)
        ))
    );

CREATE POLICY policy_bindings_tenant_scope ON policy_bindings
    USING (
        (scope_type = 'organization' AND scope_id = current_setting('noerelay.organization_id', true)) OR
        (scope_type = 'project' AND scope_id IN (
            SELECT project_id FROM projects 
            WHERE organization_id = current_setting('noerelay.organization_id', true)
        )) OR
        (scope_type = 'environment' AND scope_id IN (
            SELECT environment_id::text FROM environments 
            WHERE organization_id = current_setting('noerelay.organization_id', true)
        ))
    )
    WITH CHECK (
        (scope_type = 'organization' AND scope_id = current_setting('noerelay.organization_id', true)) OR
        (scope_type = 'project' AND scope_id IN (
            SELECT project_id FROM projects 
            WHERE organization_id = current_setting('noerelay.organization_id', true)
        )) OR
        (scope_type = 'environment' AND scope_id IN (
            SELECT environment_id::text FROM environments 
            WHERE organization_id = current_setting('noerelay.organization_id', true)
        ))
    );

CREATE POLICY service_identities_tenant_scope ON service_identities
    USING (principal_id IN (
        SELECT principal_id FROM principals 
        WHERE organization_id = current_setting('noerelay.organization_id', true)
    ))
    WITH CHECK (principal_id IN (
        SELECT principal_id FROM principals 
        WHERE organization_id = current_setting('noerelay.organization_id', true)
    ));

CREATE POLICY iam_audit_log_tenant_scope ON iam_audit_log
    USING (organization_id = current_setting('noerelay.organization_id', true))
    WITH CHECK (organization_id = current_setting('noerelay.organization_id', true));

-- ============================================================================
-- 5. TRIGGERS FOR UPDATED_AT
-- ============================================================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = clock_timestamp();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_organizations_updated_at
    BEFORE UPDATE ON organizations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_projects_updated_at
    BEFORE UPDATE ON projects
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_environments_updated_at
    BEFORE UPDATE ON environments
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_principals_updated_at
    BEFORE UPDATE ON principals
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_roles_updated_at
    BEFORE UPDATE ON roles
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_memberships_updated_at
    BEFORE UPDATE ON memberships
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_quotas_updated_at
    BEFORE UPDATE ON quotas
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_policy_bindings_updated_at
    BEFORE UPDATE ON policy_bindings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_service_identities_updated_at
    BEFORE UPDATE ON service_identities
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- 6. SEED DATA: System Permissions
-- ============================================================================

INSERT INTO permissions (permission_id, name, description, resource, action) VALUES
    -- Organization management
    ('org:read', 'Read Organization', 'View organization details', 'organization', 'read'),
    ('org:update', 'Update Organization', 'Modify organization settings', 'organization', 'update'),
    ('org:delete', 'Delete Organization', 'Delete organization', 'organization', 'delete'),
    
    -- Project management
    ('project:create', 'Create Project', 'Create new projects', 'project', 'create'),
    ('project:read', 'Read Project', 'View project details', 'project', 'read'),
    ('project:update', 'Update Project', 'Modify project settings', 'project', 'update'),
    ('project:delete', 'Delete Project', 'Delete project', 'project', 'delete'),
    
    -- Environment management
    ('environment:create', 'Create Environment', 'Create new environments', 'environment', 'create'),
    ('environment:read', 'Read Environment', 'View environment details', 'environment', 'read'),
    ('environment:update', 'Update Environment', 'Modify environment settings', 'environment', 'update'),
    ('environment:delete', 'Delete Environment', 'Delete environment', 'environment', 'delete'),
    
    -- Principal management
    ('principal:create', 'Create Principal', 'Create new principals', 'principal', 'create'),
    ('principal:read', 'Read Principal', 'View principal details', 'principal', 'read'),
    ('principal:update', 'Update Principal', 'Modify principal settings', 'principal', 'update'),
    ('principal:delete', 'Delete Principal', 'Delete principal', 'principal', 'delete'),
    
    -- Membership management
    ('membership:create', 'Create Membership', 'Create new memberships', 'membership', 'create'),
    ('membership:read', 'Read Membership', 'View membership details', 'membership', 'read'),
    ('membership:update', 'Update Membership', 'Modify membership settings', 'membership', 'update'),
    ('membership:delete', 'Delete Membership', 'Delete membership', 'membership', 'delete'),
    
    -- Role management
    ('role:create', 'Create Role', 'Create new roles', 'role', 'create'),
    ('role:read', 'Read Role', 'View role details', 'role', 'read'),
    ('role:update', 'Update Role', 'Modify role settings', 'role', 'update'),
    ('role:delete', 'Delete Role', 'Delete role', 'role', 'delete'),
    
    -- Quota management
    ('quota:create', 'Create Quota', 'Create new quotas', 'quota', 'create'),
    ('quota:read', 'Read Quota', 'View quota details', 'quota', 'read'),
    ('quota:update', 'Update Quota', 'Modify quota settings', 'quota', 'update'),
    ('quota:delete', 'Delete Quota', 'Delete quota', 'quota', 'delete'),
    
    -- Policy management
    ('policy:create', 'Create Policy Binding', 'Create new policy bindings', 'policy', 'create'),
    ('policy:read', 'Read Policy Binding', 'View policy binding details', 'policy', 'read'),
    ('policy:update', 'Update Policy Binding', 'Modify policy binding settings', 'policy', 'update'),
    ('policy:delete', 'Delete Policy Binding', 'Delete policy binding', 'policy', 'delete'),
    
    -- Service identity management
    ('service_identity:create', 'Create Service Identity', 'Create new service identities', 'service_identity', 'create'),
    ('service_identity:read', 'Read Service Identity', 'View service identity details', 'service_identity', 'read'),
    ('service_identity:update', 'Update Service Identity', 'Modify service identity settings', 'service_identity', 'update'),
    ('service_identity:delete', 'Delete Service Identity', 'Delete service identity', 'service_identity', 'delete'),
    
    -- Audit access
    ('audit:read', 'Read Audit Log', 'View audit log entries', 'audit', 'read'),
    
    -- Runtime operations
    ('run:execute', 'Execute Run', 'Execute inference runs', 'run', 'execute'),
    ('run:read', 'Read Run', 'View run details and receipts', 'run', 'read'),
    ('report:read', 'Read Reports', 'View usage and cost reports', 'report', 'read')
ON CONFLICT (permission_id) DO NOTHING;

-- ============================================================================
-- 7. SEED DATA: System Roles
-- ============================================================================

-- Note: These are templates; actual roles are created per-organization
-- System roles are identified by is_system = true and have well-known names

-- ============================================================================
-- 8. COMMENTS
-- ============================================================================

COMMENT ON TABLE organizations IS 'Top-level tenant container';
COMMENT ON TABLE projects IS 'Projects within an organization';
COMMENT ON TABLE environments IS 'Environments within a project (e.g., prod, staging)';
COMMENT ON TABLE principals IS 'Unified identity for humans and services';
COMMENT ON TABLE memberships IS 'Role assignments at specific scopes';
COMMENT ON TABLE roles IS 'Named collections of permissions';
COMMENT ON TABLE permissions IS 'Atomic actions on resources';
COMMENT ON TABLE role_permissions IS 'Join table for role-permission assignments';
COMMENT ON TABLE quotas IS 'Resource limits at various scopes';
COMMENT ON TABLE policy_bindings IS 'Policy attachments to scopes';
COMMENT ON TABLE service_identities IS 'Credentials for service principals';
COMMENT ON TABLE iam_audit_log IS 'Audit trail for administrative actions';
```

### 3.3 Migration Statistics

| Metric | Count |
|--------|-------|
| New tables | 11 |
| Extended tables | 2 |
| Indexes | 24 |
| RLS policies | 11 |
| Triggers | 9 |
| Seed permissions | 35 |

---

## 4. Rust Module Structure

### 4.1 Core Domain Types: `crates/noerelay-core/src/iam.rs`

```rust
//! Canonical IAM domain types for NoeRelay tenancy and policy scope.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use thiserror::Error;
use uuid::Uuid;

// ============================================================================
// Identifier Types
// ============================================================================

/// Unique identifier for an organization (UUID v4)
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct OrganizationId(pub Uuid);

/// Unique identifier for a project (UUID v4)
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct ProjectId(pub Uuid);

/// Unique identifier for an environment (UUID v4)
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct EnvironmentId(pub Uuid);

/// Unique identifier for a principal (UUID v4)
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct PrincipalId(pub Uuid);

/// Unique identifier for a role (UUID v4)
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct RoleId(pub Uuid);

/// Unique identifier for a membership (UUID v4)
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct MembershipId(pub Uuid);

/// Unique identifier for a quota (UUID v4)
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct QuotaId(pub Uuid);

/// Unique identifier for a policy binding (UUID v4)
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct PolicyBindingId(pub Uuid);

/// Unique identifier for a service identity (UUID v4)
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct ServiceIdentityId(pub Uuid);

// ============================================================================
// Entity Status
// ============================================================================

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum EntityStatus {
    Active,
    Suspended,
    Archived,
}

impl Default for EntityStatus {
    fn default() -> Self {
        Self::Active
    }
}

// ============================================================================
// Principal Types
// ============================================================================

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum PrincipalType {
    Human,
    Service,
}

// ============================================================================
// Scope Types
// ============================================================================

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ScopeType {
    Organization,
    Project,
    Environment,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum Scope {
    Organization(OrganizationId),
    Project(OrganizationId, ProjectId),
    Environment(OrganizationId, ProjectId, EnvironmentId),
}

impl Scope {
    pub fn scope_type(&self) -> ScopeType {
        match self {
            Self::Organization(_) => ScopeType::Organization,
            Self::Project(_, _) => ScopeType::Project,
            Self::Environment(_, _, _) => ScopeType::Environment,
        }
    }

    pub fn organization_id(&self) -> OrganizationId {
        match self {
            Self::Organization(org_id) => *org_id,
            Self::Project(org_id, _) => *org_id,
            Self::Environment(org_id, _, _) => *org_id,
        }
    }
}

// ============================================================================
// Core Entities
// ============================================================================

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Organization {
    pub organization_id: OrganizationId,
    pub name: String,
    pub slug: String,
    pub status: EntityStatus,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
    pub deleted_at: Option<chrono::DateTime<chrono::Utc>>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Project {
    pub project_id: ProjectId,
    pub organization_id: OrganizationId,
    pub name: String,
    pub slug: String,
    pub status: EntityStatus,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
    pub deleted_at: Option<chrono::DateTime<chrono::Utc>>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Environment {
    pub environment_id: EnvironmentId,
    pub organization_id: OrganizationId,
    pub project_id: ProjectId,
    pub name: String,
    pub slug: String,
    pub status: EntityStatus,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
    pub deleted_at: Option<chrono::DateTime<chrono::Utc>>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Principal {
    pub principal_id: PrincipalId,
    pub organization_id: OrganizationId,
    pub principal_type: PrincipalType,
    pub external_id: String,
    pub display_name: String,
    pub status: EntityStatus,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
    pub deleted_at: Option<chrono::DateTime<chrono::Utc>>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Role {
    pub role_id: RoleId,
    pub organization_id: OrganizationId,
    pub name: String,
    pub description: Option<String>,
    pub is_system: bool,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Permission {
    pub permission_id: String,
    pub name: String,
    pub description: Option<String>,
    pub resource: String,
    pub action: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Membership {
    pub membership_id: MembershipId,
    pub principal_id: PrincipalId,
    pub organization_id: OrganizationId,
    pub project_id: Option<ProjectId>,
    pub environment_id: Option<EnvironmentId>,
    pub role_id: RoleId,
    pub status: EntityStatus,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

impl Membership {
    /// Returns the scope of this membership
    pub fn scope(&self) -> Scope {
        match (self.project_id, self.environment_id) {
            (None, None) => Scope::Organization(self.organization_id),
            (Some(proj_id), None) => Scope::Project(self.organization_id, proj_id),
            (Some(proj_id), Some(env_id)) => {
                Scope::Environment(self.organization_id, proj_id, env_id)
            }
            (None, Some(_)) => unreachable!("environment requires project per CHECK constraint"),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum QuotaPeriod {
    Daily,
    Weekly,
    Monthly,
    Total,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Quota {
    pub quota_id: QuotaId,
    pub scope_type: ScopeType,
    pub scope_id: String,
    pub resource_type: String,
    pub limit_value: u64,
    pub period: QuotaPeriod,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct PolicyBinding {
    pub binding_id: PolicyBindingId,
    pub scope_type: ScopeType,
    pub scope_id: String,
    pub policy_type: String,
    pub policy_data: serde_json::Value,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ServiceIdentity {
    pub service_identity_id: ServiceIdentityId,
    pub principal_id: PrincipalId,
    pub service_name: String,
    pub credential_hash: String,
    pub status: EntityStatus,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

// ============================================================================
// Resolved Identity (Server-Side)
// ============================================================================

/// Fully resolved identity with authorization context.
/// This is derived server-side from authenticated credentials,
/// never trusted from caller headers.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ResolvedIdentity {
    pub principal: Principal,
    pub memberships: Vec<Membership>,
    pub roles: Vec<Role>,
    pub permissions: Vec<Permission>,
    pub effective_scope: Scope,
}

impl ResolvedIdentity {
    /// Check if this identity has a specific permission at the given scope
    pub fn has_permission(&self, resource: &str, action: &str, scope: &Scope) -> bool {
        // Check if any membership at or above the requested scope grants the permission
        self.memberships.iter().any(|m| {
            m.status == EntityStatus::Active && self.scope_covers(&m.scope(), scope)
        }) && self
            .permissions
            .iter()
            .any(|p| p.resource == resource && p.action == action)
    }

    /// Check if a membership scope covers the requested scope
    fn scope_covers(&self, membership_scope: &Scope, requested_scope: &Scope) -> bool {
        match (membership_scope, requested_scope) {
            (Scope::Organization(org1), Scope::Organization(org2)) => org1 == org2,
            (Scope::Organization(org1), Scope::Project(org2, _)) => org1 == org2,
            (Scope::Organization(org1), Scope::Environment(org2, _, _)) => org1 == org2,
            (Scope::Project(org1, proj1), Scope::Project(org2, proj2)) => {
                org1 == org2 && proj1 == proj2
            }
            (Scope::Project(org1, proj1), Scope::Environment(org2, proj2, _)) => {
                org1 == org2 && proj1 == proj2
            }
            (Scope::Environment(org1, proj1, env1), Scope::Environment(org2, proj2, env2)) => {
                org1 == org2 && proj1 == proj2 && env1 == env2
            }
            _ => false,
        }
    }
}

// ============================================================================
// Audit Log Entry
// ============================================================================

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct AuditLogEntry {
    pub audit_id: Uuid,
    pub organization_id: String,
    pub actor_principal_id: Option<PrincipalId>,
    pub action: String,
    pub resource_type: String,
    pub resource_id: String,
    pub old_value: Option<serde_json::Value>,
    pub new_value: Option<serde_json::Value>,
    pub ip_address: Option<String>,
    pub user_agent: Option<String>,
    pub created_at: chrono::DateTime<chrono::Utc>,
}

// ============================================================================
// Errors
// ============================================================================

#[derive(Debug, Error, PartialEq, Eq)]
pub enum IamError {
    #[error("entity not found: {0}")]
    NotFound(String),
    #[error("entity already exists: {0}")]
    AlreadyExists(String),
    #[error("permission denied: {0}")]
    PermissionDenied(String),
    #[error("invalid scope: {0}")]
    InvalidScope(String),
    #[error("quota exceeded: {0}")]
    QuotaExceeded(String),
    #[error("validation failed: {0}")]
    Validation(String),
}

// ============================================================================
// Relationship to Legacy IdentityScope
// ============================================================================

/// Conversion from resolved identity to legacy IdentityScope for backward compatibility
impl From<&ResolvedIdentity> for crate::types::IdentityScope {
    fn from(identity: &ResolvedIdentity) -> Self {
        let scope = &identity.effective_scope;
        Self {
            organization_id: scope.organization_id().0.to_string(),
            project_id: match scope {
                Scope::Project(_, proj_id) | Scope::Environment(_, proj_id, _) => {
                    proj_id.0.to_string()
                }
                Scope::Organization(_) => String::new(),
            },
            environment_id: match scope {
                Scope::Environment(_, _, env_id) => env_id.0.to_string(),
                _ => String::new(),
            },
            user_id: identity.principal.external_id.clone(),
            session_id: String::new(), // Session managed separately
        }
    }
}
```

### 4.2 Repository Implementation: `crates/noerelay-store/src/iam.rs`

```rust
//! PostgreSQL repository implementations for IAM entities.

use noerelay_core::iam::*;
use sqlx::{PgPool, Postgres, Transaction};
use thiserror::Error;
use uuid::Uuid;

#[derive(Debug, Error)]
pub enum IamStoreError {
    #[error("database operation failed: {0}")]
    Database(#[from] sqlx::Error),
    #[error("entity not found: {0}")]
    NotFound(String),
    #[error("entity already exists: {0}")]
    AlreadyExists(String),
    #[error("optimistic concurrency conflict")]
    ConcurrencyConflict,
    #[error("invalid scope reference")]
    InvalidScope,
}

/// IAM repository providing CRUD operations for all IAM entities
#[derive(Clone)]
pub struct IamRepository {
    pool: PgPool,
}

impl IamRepository {
    pub fn new(pool: PgPool) -> Self {
        Self { pool }
    }

    // ========================================================================
    // Organization Operations
    // ========================================================================

    pub async fn create_organization(
        &self,
        name: &str,
        slug: &str,
    ) -> Result<Organization, IamStoreError>;

    pub async fn get_organization(
        &self,
        organization_id: OrganizationId,
    ) -> Result<Option<Organization>, IamStoreError>;

    pub async fn get_organization_by_slug(
        &self,
        slug: &str,
    ) -> Result<Option<Organization>, IamStoreError>;

    pub async fn update_organization(
        &self,
        organization: &Organization,
    ) -> Result<(), IamStoreError>;

    pub async fn delete_organization(
        &self,
        organization_id: OrganizationId,
    ) -> Result<(), IamStoreError>;

    pub async fn list_organizations(
        &self,
        limit: u32,
        offset: u32,
    ) -> Result<Vec<Organization>, IamStoreError>;

    // ========================================================================
    // Project Operations
    // ========================================================================

    pub async fn create_project(
        &self,
        organization_id: OrganizationId,
        name: &str,
        slug: &str,
    ) -> Result<Project, IamStoreError>;

    pub async fn get_project(
        &self,
        organization_id: OrganizationId,
        project_id: ProjectId,
    ) -> Result<Option<Project>, IamStoreError>;

    pub async fn get_project_by_slug(
        &self,
        organization_id: OrganizationId,
        slug: &str,
    ) -> Result<Option<Project>, IamStoreError>;

    pub async fn update_project(&self, project: &Project) -> Result<(), IamStoreError>;

    pub async fn delete_project(
        &self,
        organization_id: OrganizationId,
        project_id: ProjectId,
    ) -> Result<(), IamStoreError>;

    pub async fn list_projects(
        &self,
        organization_id: OrganizationId,
        limit: u32,
        offset: u32,
    ) -> Result<Vec<Project>, IamStoreError>;

    // ========================================================================
    // Environment Operations
    // ========================================================================

    pub async fn create_environment(
        &self,
        organization_id: OrganizationId,
        project_id: ProjectId,
        name: &str,
        slug: &str,
    ) -> Result<Environment, IamStoreError>;

    pub async fn get_environment(
        &self,
        environment_id: EnvironmentId,
    ) -> Result<Option<Environment>, IamStoreError>;

    pub async fn update_environment(&self, environment: &Environment)
        -> Result<(), IamStoreError>;

    pub async fn delete_environment(
        &self,
        environment_id: EnvironmentId,
    ) -> Result<(), IamStoreError>;

    pub async fn list_environments(
        &self,
        organization_id: OrganizationId,
        project_id: ProjectId,
    ) -> Result<Vec<Environment>, IamStoreError>;

    // ========================================================================
    // Principal Operations
    // ========================================================================

    pub async fn create_principal(
        &self,
        organization_id: OrganizationId,
        principal_type: PrincipalType,
        external_id: &str,
        display_name: &str,
    ) -> Result<Principal, IamStoreError>;

    pub async fn get_principal(
        &self,
        principal_id: PrincipalId,
    ) -> Result<Option<Principal>, IamStoreError>;

    pub async fn get_principal_by_external_id(
        &self,
        organization_id: OrganizationId,
        principal_type: PrincipalType,
        external_id: &str,
    ) -> Result<Option<Principal>, IamStoreError>;

    pub async fn update_principal(&self, principal: &Principal) -> Result<(), IamStoreError>;

    pub async fn delete_principal(&self, principal_id: PrincipalId) -> Result<(), IamStoreError>;

    pub async fn list_principals(
        &self,
        organization_id: OrganizationId,
        principal_type: Option<PrincipalType>,
        limit: u32,
        offset: u32,
    ) -> Result<Vec<Principal>, IamStoreError>;

    // ========================================================================
    // Role Operations
    // ========================================================================

    pub async fn create_role(
        &self,
        organization_id: OrganizationId,
        name: &str,
        description: Option<&str>,
        is_system: bool,
    ) -> Result<Role, IamStoreError>;

    pub async fn get_role(&self, role_id: RoleId) -> Result<Option<Role>, IamStoreError>;

    pub async fn get_role_by_name(
        &self,
        organization_id: OrganizationId,
        name: &str,
    ) -> Result<Option<Role>, IamStoreError>;

    pub async fn update_role(&self, role: &Role) -> Result<(), IamStoreError>;

    pub async fn delete_role(&self, role_id: RoleId) -> Result<(), IamStoreError>;

    pub async fn list_roles(
        &self,
        organization_id: OrganizationId,
    ) -> Result<Vec<Role>, IamStoreError>;

    pub async fn add_permission_to_role(
        &self,
        role_id: RoleId,
        permission_id: &str,
    ) -> Result<(), IamStoreError>;

    pub async fn remove_permission_from_role(
        &self,
        role_id: RoleId,
        permission_id: &str,
    ) -> Result<(), IamStoreError>;

    pub async fn get_role_permissions(
        &self,
        role_id: RoleId,
    ) -> Result<Vec<Permission>, IamStoreError>;

    // ========================================================================
    // Permission Operations
    // ========================================================================

    pub async fn get_permission(
        &self,
        permission_id: &str,
    ) -> Result<Option<Permission>, IamStoreError>;

    pub async fn list_permissions(&self) -> Result<Vec<Permission>, IamStoreError>;

    // ========================================================================
    // Membership Operations
    // ========================================================================

    pub async fn create_membership(
        &self,
        principal_id: PrincipalId,
        scope: &Scope,
        role_id: RoleId,
    ) -> Result<Membership, IamStoreError>;

    pub async fn get_membership(
        &self,
        membership_id: MembershipId,
    ) -> Result<Option<Membership>, IamStoreError>;

    pub async fn update_membership(&self, membership: &Membership)
        -> Result<(), IamStoreError>;

    pub async fn delete_membership(
        &self,
        membership_id: MembershipId,
    ) -> Result<(), IamStoreError>;

    pub async fn list_memberships_for_principal(
        &self,
        principal_id: PrincipalId,
    ) -> Result<Vec<Membership>, IamStoreError>;

    pub async fn list_memberships_at_scope(
        &self,
        scope: &Scope,
    ) -> Result<Vec<Membership>, IamStoreError>;

    // ========================================================================
    // Quota Operations
    // ========================================================================

    pub async fn create_quota(
        &self,
        scope_type: ScopeType,
        scope_id: &str,
        resource_type: &str,
        limit_value: u64,
        period: QuotaPeriod,
    ) -> Result<Quota, IamStoreError>;

    pub async fn get_quota(&self, quota_id: QuotaId) -> Result<Option<Quota>, IamStoreError>;

    pub async fn update_quota(&self, quota: &Quota) -> Result<(), IamStoreError>;

    pub async fn delete_quota(&self, quota_id: QuotaId) -> Result<(), IamStoreError>;

    pub async fn list_quotas_at_scope(
        &self,
        scope_type: ScopeType,
        scope_id: &str,
    ) -> Result<Vec<Quota>, IamStoreError>;

    pub async fn check_quota(
        &self,
        scope_type: ScopeType,
        scope_id: &str,
        resource_type: &str,
        requested: u64,
    ) -> Result<bool, IamStoreError>;

    // ========================================================================
    // Policy Binding Operations
    // ========================================================================

    pub async fn create_policy_binding(
        &self,
        scope_type: ScopeType,
        scope_id: &str,
        policy_type: &str,
        policy_data: serde_json::Value,
    ) -> Result<PolicyBinding, IamStoreError>;

    pub async fn get_policy_binding(
        &self,
        binding_id: PolicyBindingId,
    ) -> Result<Option<PolicyBinding>, IamStoreError>;

    pub async fn update_policy_binding(
        &self,
        binding: &PolicyBinding,
    ) -> Result<(), IamStoreError>;

    pub async fn delete_policy_binding(
        &self,
        binding_id: PolicyBindingId,
    ) -> Result<(), IamStoreError>;

    pub async fn list_policy_bindings_at_scope(
        &self,
        scope_type: ScopeType,
        scope_id: &str,
    ) -> Result<Vec<PolicyBinding>, IamStoreError>;

    // ========================================================================
    // Service Identity Operations
    // ========================================================================

    pub async fn create_service_identity(
        &self,
        principal_id: PrincipalId,
        service_name: &str,
        credential_hash: &str,
    ) -> Result<ServiceIdentity, IamStoreError>;

    pub async fn get_service_identity(
        &self,
        service_identity_id: ServiceIdentityId,
    ) -> Result<Option<ServiceIdentity>, IamStoreError>;

    pub async fn get_service_identity_by_name(
        &self,
        principal_id: PrincipalId,
        service_name: &str,
    ) -> Result<Option<ServiceIdentity>, IamStoreError>;

    pub async fn update_service_identity(
        &self,
        identity: &ServiceIdentity,
    ) -> Result<(), IamStoreError>;

    pub async fn delete_service_identity(
        &self,
        service_identity_id: ServiceIdentityId,
    ) -> Result<(), IamStoreError>;

    // ========================================================================
    // Identity Resolution
    // ========================================================================

    /// Resolve a principal to a full identity with memberships, roles, and permissions
    pub async fn resolve_identity(
        &self,
        principal_id: PrincipalId,
        requested_scope: &Scope,
    ) -> Result<Option<ResolvedIdentity>, IamStoreError>;

    /// Resolve identity by external ID (e.g., from API key or JWT)
    pub async fn resolve_identity_by_external_id(
        &self,
        organization_id: OrganizationId,
        principal_type: PrincipalType,
        external_id: &str,
        requested_scope: &Scope,
    ) -> Result<Option<ResolvedIdentity>, IamStoreError>;

    // ========================================================================
    // Audit Operations
    // ========================================================================

    pub async fn log_audit(
        &self,
        entry: &AuditLogEntry,
    ) -> Result<(), IamStoreError>;

    pub async fn list_audit_log(
        &self,
        organization_id: OrganizationId,
        limit: u32,
        offset: u32,
    ) -> Result<Vec<AuditLogEntry>, IamStoreError>;
}

// ============================================================================
// Helper Functions
// ============================================================================

/// Set tenant context for RLS within a transaction
pub async fn set_tenant_context(
    transaction: &mut Transaction<'_, Postgres>,
    organization_id: &str,
    principal_id: Option<PrincipalId>,
) -> Result<(), sqlx::Error>;

/// Clear tenant context (for connection pool safety)
pub async fn clear_tenant_context(
    transaction: &mut Transaction<'_, Postgres>,
) -> Result<(), sqlx::Error>;
```

### 4.3 Module Integration

**`crates/noerelay-core/src/lib.rs`** additions:
```rust
pub mod iam;
pub use iam::*;
```

**`crates/noerelay-store/src/lib.rs`** additions:
```rust
pub mod iam;
pub use iam::{IamRepository, IamStoreError};
```

---

## 5. RLS Policy Design

### 5.1 Policy Summary

| Table | Policy Name | Access Pattern |
|-------|-------------|----------------|
| `environments` | `environments_tenant_scope` | `organization_id = current_setting(...)` |
| `principals` | `principals_tenant_scope` | `organization_id = current_setting(...)` |
| `roles` | `roles_tenant_scope` | `organization_id = current_setting(...)` |
| `permissions` | `permissions_tenant_scope` | `true` (global read) |
| `role_permissions` | `role_permissions_tenant_scope` | Subquery on `roles` |
| `memberships` | `memberships_tenant_scope` | `organization_id = current_setting(...)` |
| `quotas` | `quotas_tenant_scope` | Scope-type specific subqueries |
| `policy_bindings` | `policy_bindings_tenant_scope` | Scope-type specific subqueries |
| `service_identities` | `service_identities_tenant_scope` | Subquery on `principals` |
| `iam_audit_log` | `iam_audit_log_tenant_scope` | `organization_id = current_setting(...)` |

### 5.2 GUC Variables

| Variable | Purpose | Set By |
|----------|---------|--------|
| `noerelay.organization_id` | Tenant isolation | `set_tenant_context()` |
| `noerelay.principal_id` | Audit attribution | `set_tenant_context()` |

### 5.3 Connection Pool Safety

All repository methods must:
1. Begin a transaction
2. Call `set_tenant_context()` with the resolved organization
3. Execute queries
4. Call `clear_tenant_context()` before commit (or rely on transaction-local GUC)
5. Commit or rollback

This ensures pooled connections never leak tenant context between requests.

---

## 6. Test Plan

### 6.1 Role × Route × Tenant × Project Matrix

| Test Name | Description |
|-----------|-------------|
| `test_org_admin_can_manage_all_scopes` | Org admin can CRUD orgs, projects, environments, principals, roles, memberships, quotas, policies |
| `test_project_admin_can_manage_project_scope` | Project admin can CRUD within their project only |
| `test_env_admin_can_manage_env_scope` | Environment admin can CRUD within their environment only |
| `test_member_can_read_only` | Member role can read but not write |
| `test_cross_tenant_access_denied` | Org A admin cannot access Org B resources |
| `test_cross_project_access_denied` | Project A admin cannot access Project B resources |
| `test_cross_env_access_denied` | Env A admin cannot access Env B resources |
| `test_unauthenticated_access_denied` | No valid principal = 401 |
| `test_suspended_principal_denied` | Suspended principal cannot authenticate |
| `test_suspended_org_denied` | Suspended org blocks all access |

### 6.2 Enumeration Attack Tests

| Test Name | Description |
|-----------|-------------|
| `test_direct_id_enumeration` | Random UUIDs return 404, not 403 (no existence leakage) |
| `test_list_pagination_consistency` | Pagination boundaries don't leak existence |
| `test_search_filter_enumeration` | Search filters don't leak cross-tenant data |
| `test_timing_attack_resistance` | Response times consistent for existing vs non-existing |
| `test_cache_key_isolation` | Cache keys include tenant context |
| `test_stream_resume_isolation` | Stream resume tokens are tenant-scoped |
| `test_export_isolation` | Export endpoints filter by tenant |
| `test_webhook_isolation` | Webhook URLs are tenant-scoped |
| `test_error_message_consistency` | Error messages don't leak existence |

### 6.3 RLS Tests

| Test Name | Description |
|-----------|-------------|
| `test_rls_with_distinct_db_roles` | Create test roles with different org contexts |
| `test_rls_pooled_connection_reset` | Verify GUC reset between pooled connections |
| `test_rls_force_on_all_tables` | Verify FORCE RLS on all tenant tables |
| `test_rls_bypass_denied` | Verify table owner cannot bypass RLS |
| `test_rls_policy_enforcement` | Direct SQL with wrong org context returns empty |

### 6.4 Quota and Policy Tests

| Test Name | Description |
|-----------|-------------|
| `test_quota_concurrent_enforcement` | Parallel requests respect quota limits |
| `test_quota_period_rollover` | Quotas reset correctly at period boundaries |
| `test_quota_scope_hierarchy` | Env quota < Project quota < Org quota |
| `test_policy_precedence` | More specific scope overrides less specific |
| `test_policy_conflict_resolution` | Deny overrides allow |

### 6.5 Audit Tests

| Test Name | Description |
|-----------|-------------|
| `test_audit_on_create` | Create operations logged with actor |
| `test_audit_on_update` | Update operations logged with old/new values |
| `test_audit_on_delete` | Delete operations logged |
| `test_audit_on_denied` | Denied operations logged with reason |
| `test_audit_immutability` | Audit log cannot be modified |

### 6.6 Test Count Summary

| Category | Count |
|----------|-------|
| Role × Route matrix | 10 |
| Enumeration attacks | 9 |
| RLS tests | 5 |
| Quota/Policy tests | 5 |
| Audit tests | 5 |
| **Total** | **34** |

---

## 7. File-by-File Implementation Plan

### Phase 1: Core Types

| File | Action | Description |
|------|--------|-------------|
| `crates/noerelay-core/src/iam.rs` | Create | All IAM domain types, errors, and `ResolvedIdentity` |
| `crates/noerelay-core/src/lib.rs` | Modify | Add `pub mod iam; pub use iam::*;` |
| `crates/noerelay-core/Cargo.toml` | Modify | Add `uuid`, `chrono` dependencies |

### Phase 2: Database Migration

| File | Action | Description |
|------|--------|-------------|
| `crates/noerelay-store/migrations/0003_iam_tenancy.sql` | Create | Full DDL from Section 3.2 |

### Phase 3: Store Repository

| File | Action | Description |
|------|--------|-------------|
| `crates/noerelay-store/src/iam.rs` | Create | `IamRepository` with all CRUD operations |
| `crates/noerelay-store/src/lib.rs` | Modify | Add `pub mod iam; pub use iam::*;` |
| `crates/noerelay-store/Cargo.toml` | Modify | Add `uuid` dependency |

### Phase 4: Gateway Integration

| File | Action | Description |
|------|--------|-------------|
| `crates/noerelay-gateway/src/iam.rs` | Create | Identity resolution middleware, scope derivation |
| `crates/noerelay-gateway/src/lib.rs` | Modify | Integrate IAM middleware, replace `default_scope` |
| `crates/noerelay-gateway/src/admin.rs` | Create | Admin API routes for IAM management |

### Phase 5: Tests

| File | Action | Description |
|------|--------|-------------|
| `crates/noerelay-store/tests/iam_repository.rs` | Create | Repository unit tests |
| `crates/noerelay-store/tests/iam_rls.rs` | Create | RLS integration tests |
| `crates/noerelay-gateway/tests/iam_matrix.rs` | Create | Role × route matrix tests |
| `crates/noerelay-gateway/tests/iam_enumeration.rs` | Create | Enumeration attack tests |
| `crates/noerelay-gateway/tests/iam_audit.rs` | Create | Audit attribution tests |

### Phase 6: Documentation

| File | Action | Description |
|------|--------|-------------|
| `docs/iam-01-design.md` | Create | This document |
| `docs/api-reference.md` | Modify | Add IAM API endpoints |
| `docs/architecture.md` | Modify | Update tenancy architecture section |

---

## 8. Dependency Additions

### `crates/noerelay-core/Cargo.toml`
```toml
[dependencies]
uuid = { version = "1", features = ["v4", "serde"] }
chrono = { version = "0.4", features = ["serde"] }
```

### `crates/noerelay-store/Cargo.toml`
```toml
[dependencies]
uuid = { version = "1", features = ["v4"] }
```

---

## 9. Security Considerations

1. **Server-side scope derivation**: The gateway must resolve identity from API keys/JWTs and derive scope from server-side bindings, never from `X-Organization-ID` or similar headers.

2. **RLS as defense in depth**: Even with application-level authorization, RLS provides a second layer of tenant isolation.

3. **Audit immutability**: The `iam_audit_log` table has no update/delete triggers, ensuring append-only semantics.

4. **Credential hashing**: Service identity credentials are stored as hashes (bcrypt/argon2), never plaintext.

5. **Soft deletes**: All entities support soft delete via `deleted_at`, preserving audit trails.

---

## 10. Open Questions

1. **Session management**: Should sessions be persisted in the database or remain stateless JWTs?
2. **Permission caching**: Should resolved permissions be cached in Redis or similar?
3. **Migration path**: How to migrate existing `organizations`/`projects` rows to the new schema?
4. **System role templates**: Should we provide default role templates (admin, member, viewer) per organization?

---

*End of IAM-01 Design Document*
