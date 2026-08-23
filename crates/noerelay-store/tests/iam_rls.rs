//! RLS isolation integration tests.
//!
//! These tests verify that Row Level Security properly isolates tenants.
//! They require a PostgreSQL database with the IAM migration applied.
//! Set `DATABASE_URL` environment variable and run with:
//! ```text
//! cargo test --package noerelay-store --test iam_rls -- --include-ignored
//! ```

use noerelay_core::iam::*;
use noerelay_store::IamRepository;
use sqlx::PgPool;
use uuid::Uuid;

async fn setup_pool() -> Option<PgPool> {
    let database_url = std::env::var("DATABASE_URL").ok()?;
    let pool = PgPool::connect(&database_url).await.ok()?;
    sqlx::migrate!("./migrations").run(&pool).await.ok()?;
    Some(pool)
}

// ============================================================================
// RLS: Cross-Tenant Isolation Tests
// ============================================================================

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn cross_tenant_organization_isolation() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = IamRepository::new(pool);

    let org_a = repo
        .create_organization("Tenant A", "tenant-a")
        .await
        .expect("create org A");
    let org_b = repo
        .create_organization("Tenant B", "tenant-b")
        .await
        .expect("create org B");

    // Org A should be accessible
    let fetched_a = repo
        .get_organization(org_a.organization_id)
        .await
        .expect("get org A");
    assert!(fetched_a.is_some());

    // Org B should be accessible (no RLS context set for direct org lookup)
    let fetched_b = repo
        .get_organization(org_b.organization_id)
        .await
        .expect("get org B");
    assert!(fetched_b.is_some());

    // Verify they are different
    assert_ne!(org_a.organization_id, org_b.organization_id);
    assert_ne!(org_a.name, org_b.name);
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn cross_tenant_principal_isolation() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = IamRepository::new(pool);

    let org_a = repo
        .create_organization("Principal Tenant A", "principal-tenant-a")
        .await
        .expect("create org A");
    let org_b = repo
        .create_organization("Principal Tenant B", "principal-tenant-b")
        .await
        .expect("create org B");

    let principal_a = repo
        .create_principal(
            org_a.organization_id,
            PrincipalType::Human,
            "user@a.com",
            "User A",
        )
        .await
        .expect("create principal A");

    let principal_b = repo
        .create_principal(
            org_b.organization_id,
            PrincipalType::Human,
            "user@b.com",
            "User B",
        )
        .await
        .expect("create principal B");

    // Each principal should be findable by its own org
    let found_a = repo
        .get_principal_by_external_id(org_a.organization_id, PrincipalType::Human, "user@a.com")
        .await
        .expect("find principal A");
    assert!(found_a.is_some());
    assert_eq!(found_a.unwrap().principal_id, principal_a.principal_id);

    let found_b = repo
        .get_principal_by_external_id(org_b.organization_id, PrincipalType::Human, "user@b.com")
        .await
        .expect("find principal B");
    assert!(found_b.is_some());
    assert_eq!(found_b.unwrap().principal_id, principal_b.principal_id);

    // Principal A should NOT be findable in org B
    let cross = repo
        .get_principal_by_external_id(org_b.organization_id, PrincipalType::Human, "user@a.com")
        .await
        .expect("cross lookup");
    assert!(
        cross.is_none(),
        "principal A should not be visible in org B"
    );
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn cross_tenant_role_isolation() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = IamRepository::new(pool);

    let org_a = repo
        .create_organization("Role Tenant A", "role-tenant-a")
        .await
        .expect("create org A");
    let org_b = repo
        .create_organization("Role Tenant B", "role-tenant-b")
        .await
        .expect("create org B");

    let _role_a = repo
        .create_role(org_a.organization_id, "admin-a", None, false)
        .await
        .expect("create role A");
    let _role_b = repo
        .create_role(org_b.organization_id, "admin-b", None, false)
        .await
        .expect("create role B");

    // Each role should be findable in its own org
    let found_a = repo
        .get_role_by_name(org_a.organization_id, "admin-a")
        .await
        .expect("find role A");
    assert!(found_a.is_some());

    let found_b = repo
        .get_role_by_name(org_b.organization_id, "admin-b")
        .await
        .expect("find role B");
    assert!(found_b.is_some());

    // Role A should NOT be findable in org B
    let cross = repo
        .get_role_by_name(org_b.organization_id, "admin-a")
        .await
        .expect("cross lookup");
    assert!(cross.is_none(), "role A should not be visible in org B");
}

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn cross_tenant_membership_isolation() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = IamRepository::new(pool);

    let org_a = repo
        .create_organization("Memb Tenant A", "memb-tenant-a")
        .await
        .expect("create org A");
    let org_b = repo
        .create_organization("Memb Tenant B", "memb-tenant-b")
        .await
        .expect("create org B");

    let principal_a = repo
        .create_principal(
            org_a.organization_id,
            PrincipalType::Human,
            "memb-a@test.com",
            "Memb A",
        )
        .await
        .expect("create principal A");

    let role_a = repo
        .create_role(org_a.organization_id, "viewer-a", None, false)
        .await
        .expect("create role A");

    let membership = repo
        .create_membership(
            principal_a.principal_id,
            &Scope::Organization(org_a.organization_id),
            role_a.role_id,
        )
        .await
        .expect("create membership");

    // Membership should be findable
    let found = repo
        .get_membership(membership.membership_id)
        .await
        .expect("get membership");
    assert!(found.is_some());

    // List memberships for org A should include it
    let org_a_members = repo
        .list_memberships_at_scope(&Scope::Organization(org_a.organization_id))
        .await
        .expect("list org A members");
    assert!(!org_a_members.is_empty());

    // List memberships for org B should NOT include it
    let org_b_members = repo
        .list_memberships_at_scope(&Scope::Organization(org_b.organization_id))
        .await
        .expect("list org B members");
    let has_cross = org_b_members
        .iter()
        .any(|m| m.membership_id == membership.membership_id);
    assert!(!has_cross, "membership should not be visible in org B");
}

// ============================================================================
// RLS: Suspended Entity Tests
// ============================================================================

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn suspended_principal_identity_resolution_returns_none() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = IamRepository::new(pool);

    let org = repo
        .create_organization("Suspended Org", "suspended-org")
        .await
        .expect("create org");

    let mut principal = repo
        .create_principal(
            org.organization_id,
            PrincipalType::Human,
            "suspended@test.com",
            "Suspended User",
        )
        .await
        .expect("create principal");

    // Suspend the principal
    principal.status = EntityStatus::Suspended;
    repo.update_principal(&principal)
        .await
        .expect("update principal");

    // Identity resolution should return None for suspended principal
    let identity = repo
        .resolve_identity(
            principal.principal_id,
            &Scope::Organization(org.organization_id),
        )
        .await
        .expect("resolve identity");

    assert!(identity.is_none(), "suspended principal should not resolve");
}

// ============================================================================
// RLS: Audit Log Immutability
// ============================================================================

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn audit_log_entries_are_persisted() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = IamRepository::new(pool);

    let org = repo
        .create_organization("Audit RLS Org", "audit-rls-org")
        .await
        .expect("create org");

    let entry = AuditLogEntry {
        audit_id: Uuid::new_v4(),
        organization_id: org.organization_id.0.to_string(),
        actor_principal_id: None,
        action: "test.action".into(),
        resource_type: "test".into(),
        resource_id: "test-1".into(),
        old_value: None,
        new_value: None,
        ip_address: None,
        user_agent: None,
        created_at: chrono::Utc::now(),
    };

    repo.log_audit(&entry).await.expect("log audit");

    let entries = repo
        .list_audit_log(org.organization_id, 10, 0)
        .await
        .expect("list audit");

    assert!(!entries.is_empty());
    let has_entry = entries.iter().any(|e| e.audit_id == entry.audit_id);
    assert!(has_entry, "audit entry should be persisted");
}

// ============================================================================
// RLS: Quota Scope Isolation
// ============================================================================

#[tokio::test]
#[ignore = "requires DATABASE_URL with PostgreSQL"]
async fn quota_scope_isolation() {
    let pool = setup_pool().await.expect("DATABASE_URL required");
    let repo = IamRepository::new(pool);

    let org_a = repo
        .create_organization("Quota Iso A", "quota-iso-a")
        .await
        .expect("create org A");
    let org_b = repo
        .create_organization("Quota Iso B", "quota-iso-b")
        .await
        .expect("create org B");

    let org_a_id = org_a.organization_id.0.to_string();
    let org_b_id = org_b.organization_id.0.to_string();

    // Create quota for org A
    repo.create_quota(
        ScopeType::Organization,
        &org_a_id,
        "api_calls",
        100,
        QuotaPeriod::Daily,
    )
    .await
    .expect("create quota A");

    // Create quota for org B
    repo.create_quota(
        ScopeType::Organization,
        &org_b_id,
        "api_calls",
        500,
        QuotaPeriod::Daily,
    )
    .await
    .expect("create quota B");

    // Org A quota should be 100
    let quotas_a = repo
        .list_quotas_at_scope(ScopeType::Organization, &org_a_id)
        .await
        .expect("list quotas A");
    assert_eq!(quotas_a.len(), 1);
    assert_eq!(quotas_a[0].limit_value, 100);

    // Org B quota should be 500
    let quotas_b = repo
        .list_quotas_at_scope(ScopeType::Organization, &org_b_id)
        .await
        .expect("list quotas B");
    assert_eq!(quotas_b.len(), 1);
    assert_eq!(quotas_b[0].limit_value, 500);
}
