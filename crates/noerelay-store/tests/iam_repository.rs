//! Integration tests for IAM repository CRUD operations.
//!
//! These tests require a PostgreSQL database with the IAM migration applied.
//! Set `DATABASE_URL` environment variable and run with:
//! ```text
//! cargo test --package noerelay-store --test iam_repository -- --include-ignored
//! ```
//!
//! Without `DATABASE_URL`, these tests are skipped.

use noerelay_core::iam::*;
use noerelay_store::IamRepository;
use sqlx::PgPool;
use uuid::Uuid;

async fn setup_pool() -> Option<PgPool> {
    let database_url = std::env::var("DATABASE_URL").ok()?;
    let pool = PgPool::connect(&database_url).await.ok()?;
    // Run migrations
    sqlx::migrate!("./migrations").run(&pool).await.ok()?;
    Some(pool)
}

#[allow(dead_code)]
fn make_org_id() -> OrganizationId {
    OrganizationId(Uuid::new_v4())
}

#[allow(dead_code)]
fn make_proj_id() -> ProjectId {
    ProjectId(Uuid::new_v4())
}

#[allow(dead_code)]
fn make_env_id() -> EnvironmentId {
    EnvironmentId(Uuid::new_v4())
}

// ============================================================================
// Organization CRUD Tests
// ============================================================================

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn create_and_get_organization() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = IamRepository::new(pool);

    let org = repo
        .create_organization("Test Org", "test-org")
        .await
        .expect("create organization");

    assert_eq!(org.name, "Test Org");
    assert_eq!(org.slug, "test-org");
    assert_eq!(org.status, EntityStatus::Active);
    assert!(org.deleted_at.is_none());

    let fetched = repo
        .get_organization(org.organization_id)
        .await
        .expect("get organization")
        .expect("organization exists");

    assert_eq!(fetched.organization_id, org.organization_id);
    assert_eq!(fetched.name, "Test Org");
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn list_organizations() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = IamRepository::new(pool);

    let org1 = repo
        .create_organization("Org A", "org-a")
        .await
        .expect("create org A");
    let org2 = repo
        .create_organization("Org B", "org-b")
        .await
        .expect("create org B");

    let orgs = repo.list_organizations(10, 0).await.expect("list orgs");
    assert!(orgs.len() >= 2);
    let ids: Vec<_> = orgs.iter().map(|o| o.organization_id).collect();
    assert!(ids.contains(&org1.organization_id));
    assert!(ids.contains(&org2.organization_id));
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn soft_delete_organization() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = IamRepository::new(pool);

    let org = repo
        .create_organization("To Delete", "to-delete")
        .await
        .expect("create org");

    repo.delete_organization(org.organization_id)
        .await
        .expect("delete org");

    let fetched = repo
        .get_organization(org.organization_id)
        .await
        .expect("get org");

    assert!(fetched.is_none(), "soft-deleted org should not be returned");
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn update_organization() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = IamRepository::new(pool);

    let mut org = repo
        .create_organization("Original", "original")
        .await
        .expect("create org");

    org.name = "Updated".into();
    org.slug = "updated".into();
    repo.update_organization(&org).await.expect("update org");

    let fetched = repo
        .get_organization(org.organization_id)
        .await
        .expect("get org")
        .expect("org exists");

    assert_eq!(fetched.name, "Updated");
    assert_eq!(fetched.slug, "updated");
}

// ============================================================================
// Project CRUD Tests
// ============================================================================

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn create_and_get_project() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = IamRepository::new(pool);

    let org = repo
        .create_organization("Project Org", "project-org")
        .await
        .expect("create org");

    let proj = repo
        .create_project(org.organization_id, "Test Project", "test-project")
        .await
        .expect("create project");

    assert_eq!(proj.name, "Test Project");
    assert_eq!(proj.organization_id, org.organization_id);

    let fetched = repo
        .get_project(org.organization_id, proj.project_id)
        .await
        .expect("get project")
        .expect("project exists");

    assert_eq!(fetched.project_id, proj.project_id);
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn list_projects_by_organization() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = IamRepository::new(pool);

    let org = repo
        .create_organization("List Projects Org", "list-proj-org")
        .await
        .expect("create org");

    let p1 = repo
        .create_project(org.organization_id, "Project 1", "proj-1")
        .await
        .expect("create proj 1");
    let p2 = repo
        .create_project(org.organization_id, "Project 2", "proj-2")
        .await
        .expect("create proj 2");

    let projects = repo
        .list_projects(org.organization_id, 10, 0)
        .await
        .expect("list projects");

    assert!(projects.len() >= 2);
    let ids: Vec<_> = projects.iter().map(|p| p.project_id).collect();
    assert!(ids.contains(&p1.project_id));
    assert!(ids.contains(&p2.project_id));
}

// ============================================================================
// Environment CRUD Tests
// ============================================================================

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn create_and_get_environment() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = IamRepository::new(pool);

    let org = repo
        .create_organization("Env Org", "env-org")
        .await
        .expect("create org");
    let proj = repo
        .create_project(org.organization_id, "Env Project", "env-proj")
        .await
        .expect("create project");

    let env = repo
        .create_environment(org.organization_id, proj.project_id, "Production", "prod")
        .await
        .expect("create environment");

    assert_eq!(env.name, "Production");
    assert_eq!(env.slug, "prod");
    assert_eq!(env.organization_id, org.organization_id);
    assert_eq!(env.project_id, proj.project_id);

    let fetched = repo
        .get_environment(env.environment_id)
        .await
        .expect("get env")
        .expect("env exists");

    assert_eq!(fetched.environment_id, env.environment_id);
}

// ============================================================================
// Principal CRUD Tests
// ============================================================================

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn create_and_get_principal() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = IamRepository::new(pool);

    let org = repo
        .create_organization("Principal Org", "principal-org")
        .await
        .expect("create org");

    let principal = repo
        .create_principal(
            org.organization_id,
            PrincipalType::Human,
            "user@example.com",
            "Test User",
        )
        .await
        .expect("create principal");

    assert_eq!(principal.external_id, "user@example.com");
    assert_eq!(principal.display_name, "Test User");
    assert_eq!(principal.principal_type, PrincipalType::Human);

    let fetched = repo
        .get_principal(principal.principal_id)
        .await
        .expect("get principal")
        .expect("principal exists");

    assert_eq!(fetched.principal_id, principal.principal_id);
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn get_principal_by_external_id() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = IamRepository::new(pool);

    let org = repo
        .create_organization("ExtId Org", "extid-org")
        .await
        .expect("create org");

    let principal = repo
        .create_principal(
            org.organization_id,
            PrincipalType::Service,
            "svc-001",
            "Service Account",
        )
        .await
        .expect("create principal");

    let fetched = repo
        .get_principal_by_external_id(org.organization_id, PrincipalType::Service, "svc-001")
        .await
        .expect("get by external id")
        .expect("principal exists");

    assert_eq!(fetched.principal_id, principal.principal_id);
}

// ============================================================================
// Role CRUD Tests
// ============================================================================

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn create_and_get_role() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = IamRepository::new(pool);

    let org = repo
        .create_organization("Role Org", "role-org")
        .await
        .expect("create org");

    let role = repo
        .create_role(
            org.organization_id,
            "admin",
            Some("Administrator role"),
            false,
        )
        .await
        .expect("create role");

    assert_eq!(role.name, "admin");
    assert_eq!(role.description, Some("Administrator role".into()));
    assert!(!role.is_system);

    let fetched = repo
        .get_role(role.role_id)
        .await
        .expect("get role")
        .expect("role exists");

    assert_eq!(fetched.role_id, role.role_id);
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn add_and_get_role_permissions() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = IamRepository::new(pool);

    let org = repo
        .create_organization("Perm Org", "perm-org")
        .await
        .expect("create org");

    let role = repo
        .create_role(org.organization_id, "viewer", None, false)
        .await
        .expect("create role");

    repo.add_permission_to_role(role.role_id, "project:read")
        .await
        .expect("add permission");
    repo.add_permission_to_role(role.role_id, "run:read")
        .await
        .expect("add permission");

    let permissions = repo
        .get_role_permissions(role.role_id)
        .await
        .expect("get permissions");

    assert!(permissions.len() >= 2);
    let perm_ids: Vec<_> = permissions
        .iter()
        .map(|p| p.permission_id.as_str())
        .collect();
    assert!(perm_ids.contains(&"project:read"));
    assert!(perm_ids.contains(&"run:read"));
}

// ============================================================================
// Membership CRUD Tests
// ============================================================================

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn create_and_get_membership() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = IamRepository::new(pool);

    let org = repo
        .create_organization("Membership Org", "membership-org")
        .await
        .expect("create org");

    let principal = repo
        .create_principal(
            org.organization_id,
            PrincipalType::Human,
            "member@example.com",
            "Member User",
        )
        .await
        .expect("create principal");

    let role = repo
        .create_role(org.organization_id, "member", None, false)
        .await
        .expect("create role");

    let scope = Scope::Organization(org.organization_id);
    let membership = repo
        .create_membership(principal.principal_id, &scope, role.role_id)
        .await
        .expect("create membership");

    assert_eq!(membership.principal_id, principal.principal_id);
    assert_eq!(membership.role_id, role.role_id);
    assert_eq!(membership.status, EntityStatus::Active);

    let fetched = repo
        .get_membership(membership.membership_id)
        .await
        .expect("get membership")
        .expect("membership exists");

    assert_eq!(fetched.membership_id, membership.membership_id);
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn list_memberships_for_principal() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = IamRepository::new(pool);

    let org = repo
        .create_organization("List Memb Org", "list-memb-org")
        .await
        .expect("create org");

    let principal = repo
        .create_principal(
            org.organization_id,
            PrincipalType::Human,
            "multi@example.com",
            "Multi Role User",
        )
        .await
        .expect("create principal");

    let role1 = repo
        .create_role(org.organization_id, "role1", None, false)
        .await
        .expect("create role1");
    let role2 = repo
        .create_role(org.organization_id, "role2", None, false)
        .await
        .expect("create role2");

    repo.create_membership(
        principal.principal_id,
        &Scope::Organization(org.organization_id),
        role1.role_id,
    )
    .await
    .expect("create membership 1");
    repo.create_membership(
        principal.principal_id,
        &Scope::Organization(org.organization_id),
        role2.role_id,
    )
    .await
    .expect("create membership 2");

    let memberships = repo
        .list_memberships_for_principal(principal.principal_id)
        .await
        .expect("list memberships");

    assert_eq!(memberships.len(), 2);
}

// ============================================================================
// Quota CRUD Tests
// ============================================================================

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn create_and_check_quota() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = IamRepository::new(pool);

    let org = repo
        .create_organization("Quota Org", "quota-org")
        .await
        .expect("create org");

    let org_id_str = org.organization_id.0.to_string();
    let quota = repo
        .create_quota(
            ScopeType::Organization,
            &org_id_str,
            "api_requests",
            1000,
            QuotaPeriod::Daily,
        )
        .await
        .expect("create quota");

    assert_eq!(quota.resource_type, "api_requests");
    assert_eq!(quota.limit_value, 1000);

    // Check quota: within limit
    let allowed = repo
        .check_quota(ScopeType::Organization, &org_id_str, "api_requests", 500)
        .await
        .expect("check quota");
    assert!(allowed);

    // Check quota: exceeds limit
    let allowed = repo
        .check_quota(ScopeType::Organization, &org_id_str, "api_requests", 2000)
        .await
        .expect("check quota");
    assert!(!allowed);
}

// ============================================================================
// Policy Binding CRUD Tests
// ============================================================================

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn create_and_get_policy_binding() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = IamRepository::new(pool);

    let org = repo
        .create_organization("Policy Org", "policy-org")
        .await
        .expect("create org");

    let org_id_str = org.organization_id.0.to_string();
    let policy_data = serde_json::json!({"max_tokens": 4096, "allowed_models": ["gpt-4"]});

    let binding = repo
        .create_policy_binding(
            ScopeType::Organization,
            &org_id_str,
            "token_limit",
            policy_data.clone(),
        )
        .await
        .expect("create policy binding");

    assert_eq!(binding.policy_type, "token_limit");
    assert_eq!(binding.policy_data, policy_data);

    let fetched = repo
        .get_policy_binding(binding.binding_id)
        .await
        .expect("get binding")
        .expect("binding exists");

    assert_eq!(fetched.binding_id, binding.binding_id);
}

// ============================================================================
// Service Identity CRUD Tests
// ============================================================================

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn create_and_get_service_identity() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = IamRepository::new(pool);

    let org = repo
        .create_organization("SvcId Org", "svcid-org")
        .await
        .expect("create org");

    let principal = repo
        .create_principal(
            org.organization_id,
            PrincipalType::Service,
            "ci-bot",
            "CI Bot",
        )
        .await
        .expect("create principal");

    let si = repo
        .create_service_identity(
            principal.principal_id,
            "github-actions",
            "hashed-credential-value-here-32chars",
        )
        .await
        .expect("create service identity");

    assert_eq!(si.service_name, "github-actions");
    assert_eq!(si.principal_id, principal.principal_id);

    let fetched = repo
        .get_service_identity(si.service_identity_id)
        .await
        .expect("get si")
        .expect("si exists");

    assert_eq!(fetched.service_identity_id, si.service_identity_id);
}

// ============================================================================
// Identity Resolution Tests
// ============================================================================

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn resolve_identity_with_memberships() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = IamRepository::new(pool);

    let org = repo
        .create_organization("Resolve Org", "resolve-org")
        .await
        .expect("create org");

    let principal = repo
        .create_principal(
            org.organization_id,
            PrincipalType::Human,
            "resolver@example.com",
            "Resolver",
        )
        .await
        .expect("create principal");

    let role = repo
        .create_role(org.organization_id, "reader", None, false)
        .await
        .expect("create role");

    repo.add_permission_to_role(role.role_id, "project:read")
        .await
        .expect("add permission");

    repo.create_membership(
        principal.principal_id,
        &Scope::Organization(org.organization_id),
        role.role_id,
    )
    .await
    .expect("create membership");

    let identity = repo
        .resolve_identity(
            principal.principal_id,
            &Scope::Organization(org.organization_id),
        )
        .await
        .expect("resolve identity")
        .expect("identity exists");

    assert_eq!(identity.principal.principal_id, principal.principal_id);
    assert!(!identity.memberships.is_empty());
    assert!(!identity.roles.is_empty());
    assert!(!identity.permissions.is_empty());
}

// ============================================================================
// Audit Log Tests
// ============================================================================

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn log_and_list_audit_entries() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = IamRepository::new(pool);

    let org = repo
        .create_organization("Audit Org", "audit-org")
        .await
        .expect("create org");

    let entry = AuditLogEntry {
        audit_id: Uuid::new_v4(),
        organization_id: org.organization_id.0.to_string(),
        actor_principal_id: None,
        action: "org.create".into(),
        resource_type: "organization".into(),
        resource_id: org.organization_id.0.to_string(),
        old_value: None,
        new_value: Some(serde_json::json!({"name": "Audit Org"})),
        ip_address: Some("127.0.0.1".into()),
        user_agent: Some("test/1.0".into()),
        created_at: chrono::Utc::now(),
    };

    repo.log_audit(&entry).await.expect("log audit");

    let entries = repo
        .list_audit_log(org.organization_id, 10, 0)
        .await
        .expect("list audit log");

    assert!(!entries.is_empty());
    assert_eq!(entries[0].action, "org.create");
}

// ============================================================================
// Permission List Tests
// ============================================================================

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn list_all_permissions() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = IamRepository::new(pool);

    let permissions = repo.list_permissions().await.expect("list permissions");

    // Should have at least the 35 seed permissions
    assert!(permissions.len() >= 35);
    let perm_ids: Vec<_> = permissions
        .iter()
        .map(|p| p.permission_id.as_str())
        .collect();
    assert!(perm_ids.contains(&"org:read"));
    assert!(perm_ids.contains(&"project:create"));
    assert!(perm_ids.contains(&"run:execute"));
    assert!(perm_ids.contains(&"audit:read"));
}
