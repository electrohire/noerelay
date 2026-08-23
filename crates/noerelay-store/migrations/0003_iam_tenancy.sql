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
-- 7. COMMENTS
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