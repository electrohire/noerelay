//! PostgreSQL repository implementations for IAM entities.
//!
//! Provides [`IamRepository`] with full CRUD operations for all IAM entities,
//! identity resolution, permission checking, quota enforcement, and audit logging.
//!
//! All repository methods use transaction-scoped GUC variables for RLS:
//! - `noerelay.organization_id` — tenant isolation
//! - `noerelay.principal_id` — audit attribution

use noerelay_core::iam::*;
use sqlx::{PgPool, Postgres, Row, Transaction};
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
    #[error("step-up approval has expired")]
    StepUpExpired,
    #[error("step-up approval has already been used or revoked")]
    StepUpUnavailable,
    #[error("separation-of-duties rule was violated")]
    SeparationOfDutiesViolation,
}

/// IAM repository providing CRUD operations for all IAM entities.
///
/// All methods that interact with tenant-bearing tables begin a transaction,
/// set the RLS context via [`set_tenant_context`], execute queries, and
/// commit. This ensures pooled connections never leak tenant context.
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
    ) -> Result<Organization, IamStoreError> {
        let org_id = Uuid::new_v4();
        let org_id_str = org_id.to_string();
        let mut tx = self.pool.begin().await?;
        // No tenant context needed for org creation (bootstrap operation)
        let row = sqlx::query(
            "INSERT INTO organizations (organization_id, name, slug, status) \
             VALUES ($1, $2, $3, 'active') \
             RETURNING organization_id, name, slug, status, created_at, updated_at, deleted_at",
        )
        .bind(&org_id_str)
        .bind(name)
        .bind(slug)
        .fetch_one(&mut *tx)
        .await?;
        tx.commit().await?;
        row_to_organization(&row)
    }

    pub async fn get_organization(
        &self,
        organization_id: OrganizationId,
    ) -> Result<Option<Organization>, IamStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let row = sqlx::query(
            "SELECT organization_id, name, slug, status, created_at, updated_at, deleted_at \
             FROM organizations WHERE organization_id = $1 AND deleted_at IS NULL",
        )
        .bind(&org_id_str)
        .fetch_optional(&mut *tx)
        .await?;
        tx.commit().await?;
        row.map(|r| row_to_organization(&r)).transpose()
    }

    pub async fn get_organization_by_slug(
        &self,
        slug: &str,
    ) -> Result<Option<Organization>, IamStoreError> {
        let mut tx = self.pool.begin().await?;
        // Slug lookup may cross tenants; RLS will filter
        let row = sqlx::query(
            "SELECT organization_id, name, slug, status, created_at, updated_at, deleted_at \
             FROM organizations WHERE slug = $1 AND deleted_at IS NULL",
        )
        .bind(slug)
        .fetch_optional(&mut *tx)
        .await?;
        tx.commit().await?;
        row.map(|r| row_to_organization(&r)).transpose()
    }

    pub async fn update_organization(
        &self,
        organization: &Organization,
    ) -> Result<(), IamStoreError> {
        let org_id_str = organization.organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let result = sqlx::query(
            "UPDATE organizations SET name = $1, slug = $2, status = $3 \
             WHERE organization_id = $4 AND deleted_at IS NULL",
        )
        .bind(&organization.name)
        .bind(&organization.slug)
        .bind(entity_status_to_str(organization.status))
        .bind(&org_id_str)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        if result.rows_affected() == 0 {
            return Err(IamStoreError::NotFound(org_id_str));
        }
        Ok(())
    }

    pub async fn delete_organization(
        &self,
        organization_id: OrganizationId,
    ) -> Result<(), IamStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let result = sqlx::query(
            "UPDATE organizations SET deleted_at = clock_timestamp(), status = 'archived' \
             WHERE organization_id = $1 AND deleted_at IS NULL",
        )
        .bind(&org_id_str)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        if result.rows_affected() == 0 {
            return Err(IamStoreError::NotFound(org_id_str));
        }
        Ok(())
    }

    pub async fn list_organizations(
        &self,
        limit: u32,
        offset: u32,
    ) -> Result<Vec<Organization>, IamStoreError> {
        let mut tx = self.pool.begin().await?;
        // List is filtered by RLS via tenant context
        let rows = sqlx::query(
            "SELECT organization_id, name, slug, status, created_at, updated_at, deleted_at \
             FROM organizations WHERE deleted_at IS NULL \
             ORDER BY created_at DESC LIMIT $1 OFFSET $2",
        )
        .bind(limit as i64)
        .bind(offset as i64)
        .fetch_all(&mut *tx)
        .await?;
        tx.commit().await?;
        rows.iter().map(row_to_organization).collect()
    }

    // ========================================================================
    // Project Operations
    // ========================================================================

    pub async fn create_project(
        &self,
        organization_id: OrganizationId,
        name: &str,
        slug: &str,
    ) -> Result<Project, IamStoreError> {
        let org_id_str = organization_id.0.to_string();
        let proj_id = Uuid::new_v4();
        let proj_id_str = proj_id.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let row = sqlx::query(
            "INSERT INTO projects (organization_id, project_id, name, slug, status) \
             VALUES ($1, $2, $3, $4, 'active') \
             RETURNING organization_id, project_id, name, slug, status, created_at, updated_at, deleted_at",
        )
        .bind(&org_id_str)
        .bind(&proj_id_str)
        .bind(name)
        .bind(slug)
        .fetch_one(&mut *tx)
        .await?;
        tx.commit().await?;
        row_to_project(&row, organization_id)
    }

    pub async fn get_project(
        &self,
        organization_id: OrganizationId,
        project_id: ProjectId,
    ) -> Result<Option<Project>, IamStoreError> {
        let org_id_str = organization_id.0.to_string();
        let proj_id_str = project_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let row = sqlx::query(
            "SELECT organization_id, project_id, name, slug, status, created_at, updated_at, deleted_at \
             FROM projects WHERE organization_id = $1 AND project_id = $2 AND deleted_at IS NULL",
        )
        .bind(&org_id_str)
        .bind(&proj_id_str)
        .fetch_optional(&mut *tx)
        .await?;
        tx.commit().await?;
        row.map(|r| row_to_project(&r, organization_id)).transpose()
    }

    pub async fn get_project_by_slug(
        &self,
        organization_id: OrganizationId,
        slug: &str,
    ) -> Result<Option<Project>, IamStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let row = sqlx::query(
            "SELECT organization_id, project_id, name, slug, status, created_at, updated_at, deleted_at \
             FROM projects WHERE organization_id = $1 AND slug = $2 AND deleted_at IS NULL",
        )
        .bind(&org_id_str)
        .bind(slug)
        .fetch_optional(&mut *tx)
        .await?;
        tx.commit().await?;
        row.map(|r| row_to_project(&r, organization_id)).transpose()
    }

    pub async fn update_project(&self, project: &Project) -> Result<(), IamStoreError> {
        let org_id_str = project.organization_id.0.to_string();
        let proj_id_str = project.project_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let result = sqlx::query(
            "UPDATE projects SET name = $1, slug = $2, status = $3 \
             WHERE organization_id = $4 AND project_id = $5 AND deleted_at IS NULL",
        )
        .bind(&project.name)
        .bind(&project.slug)
        .bind(entity_status_to_str(project.status))
        .bind(&org_id_str)
        .bind(&proj_id_str)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        if result.rows_affected() == 0 {
            return Err(IamStoreError::NotFound(proj_id_str));
        }
        Ok(())
    }

    pub async fn delete_project(
        &self,
        organization_id: OrganizationId,
        project_id: ProjectId,
    ) -> Result<(), IamStoreError> {
        let org_id_str = organization_id.0.to_string();
        let proj_id_str = project_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let result = sqlx::query(
            "UPDATE projects SET deleted_at = clock_timestamp(), status = 'archived' \
             WHERE organization_id = $1 AND project_id = $2 AND deleted_at IS NULL",
        )
        .bind(&org_id_str)
        .bind(&proj_id_str)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        if result.rows_affected() == 0 {
            return Err(IamStoreError::NotFound(proj_id_str));
        }
        Ok(())
    }

    pub async fn list_projects(
        &self,
        organization_id: OrganizationId,
        limit: u32,
        offset: u32,
    ) -> Result<Vec<Project>, IamStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let rows = sqlx::query(
            "SELECT organization_id, project_id, name, slug, status, created_at, updated_at, deleted_at \
             FROM projects WHERE organization_id = $1 AND deleted_at IS NULL \
             ORDER BY created_at DESC LIMIT $2 OFFSET $3",
        )
        .bind(&org_id_str)
        .bind(limit as i64)
        .bind(offset as i64)
        .fetch_all(&mut *tx)
        .await?;
        tx.commit().await?;
        rows.iter()
            .map(|r| row_to_project(r, organization_id))
            .collect()
    }

    // ========================================================================
    // Environment Operations
    // ========================================================================

    pub async fn create_environment(
        &self,
        organization_id: OrganizationId,
        project_id: ProjectId,
        name: &str,
        slug: &str,
    ) -> Result<Environment, IamStoreError> {
        let org_id_str = organization_id.0.to_string();
        let proj_id_str = project_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let row = sqlx::query(
            "INSERT INTO environments (organization_id, project_id, name, slug, status) \
             VALUES ($1, $2, $3, $4, 'active') \
             RETURNING environment_id, organization_id, project_id, name, slug, status, \
                       created_at, updated_at, deleted_at",
        )
        .bind(&org_id_str)
        .bind(&proj_id_str)
        .bind(name)
        .bind(slug)
        .fetch_one(&mut *tx)
        .await?;
        tx.commit().await?;
        row_to_environment(&row, organization_id, project_id)
    }

    pub async fn get_environment(
        &self,
        environment_id: EnvironmentId,
    ) -> Result<Option<Environment>, IamStoreError> {
        let mut tx = self.pool.begin().await?;
        let row = sqlx::query(
            "SELECT environment_id, organization_id, project_id, name, slug, status, \
                    created_at, updated_at, deleted_at \
             FROM environments WHERE environment_id = $1 AND deleted_at IS NULL",
        )
        .bind(environment_id.0)
        .fetch_optional(&mut *tx)
        .await?;
        tx.commit().await?;
        row.map(|r| {
            let org_id_str: String = r.try_get("organization_id")?;
            let proj_id_str: String = r.try_get("project_id")?;
            let org_id = OrganizationId(
                Uuid::parse_str(&org_id_str)
                    .map_err(|_| sqlx::Error::Decode("invalid organization_id UUID".into()))?,
            );
            let proj_id = ProjectId(
                Uuid::parse_str(&proj_id_str)
                    .map_err(|_| sqlx::Error::Decode("invalid project_id UUID".into()))?,
            );
            row_to_environment(&r, org_id, proj_id)
        })
        .transpose()
    }

    pub async fn update_environment(&self, environment: &Environment) -> Result<(), IamStoreError> {
        let org_id_str = environment.organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let result = sqlx::query(
            "UPDATE environments SET name = $1, slug = $2, status = $3 \
             WHERE environment_id = $4 AND deleted_at IS NULL",
        )
        .bind(&environment.name)
        .bind(&environment.slug)
        .bind(entity_status_to_str(environment.status))
        .bind(environment.environment_id.0)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        if result.rows_affected() == 0 {
            return Err(IamStoreError::NotFound(
                environment.environment_id.0.to_string(),
            ));
        }
        Ok(())
    }

    pub async fn delete_environment(
        &self,
        environment_id: EnvironmentId,
    ) -> Result<(), IamStoreError> {
        let mut tx = self.pool.begin().await?;
        let result = sqlx::query(
            "UPDATE environments SET deleted_at = clock_timestamp(), status = 'archived' \
             WHERE environment_id = $1 AND deleted_at IS NULL",
        )
        .bind(environment_id.0)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        if result.rows_affected() == 0 {
            return Err(IamStoreError::NotFound(environment_id.0.to_string()));
        }
        Ok(())
    }

    pub async fn list_environments(
        &self,
        organization_id: OrganizationId,
        project_id: ProjectId,
    ) -> Result<Vec<Environment>, IamStoreError> {
        let org_id_str = organization_id.0.to_string();
        let proj_id_str = project_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let rows = sqlx::query(
            "SELECT environment_id, organization_id, project_id, name, slug, status, \
                    created_at, updated_at, deleted_at \
             FROM environments \
             WHERE organization_id = $1 AND project_id = $2 AND deleted_at IS NULL \
             ORDER BY created_at DESC",
        )
        .bind(&org_id_str)
        .bind(&proj_id_str)
        .fetch_all(&mut *tx)
        .await?;
        tx.commit().await?;
        rows.iter()
            .map(|r| row_to_environment(r, organization_id, project_id))
            .collect()
    }

    // ========================================================================
    // Principal Operations
    // ========================================================================

    pub async fn create_principal(
        &self,
        organization_id: OrganizationId,
        principal_type: PrincipalType,
        external_id: &str,
        display_name: &str,
    ) -> Result<Principal, IamStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let row = sqlx::query(
            "INSERT INTO principals (organization_id, principal_type, external_id, display_name, status) \
             VALUES ($1, $2, $3, $4, 'active') \
             RETURNING principal_id, organization_id, principal_type, external_id, display_name, \
                       status, created_at, updated_at, deleted_at",
        )
        .bind(&org_id_str)
        .bind(principal_type_to_str(principal_type))
        .bind(external_id)
        .bind(display_name)
        .fetch_one(&mut *tx)
        .await?;
        tx.commit().await?;
        row_to_principal(&row, organization_id)
    }

    pub async fn get_principal(
        &self,
        principal_id: PrincipalId,
    ) -> Result<Option<Principal>, IamStoreError> {
        let mut tx = self.pool.begin().await?;
        let row = sqlx::query(
            "SELECT principal_id, organization_id, principal_type, external_id, display_name, \
                    status, created_at, updated_at, deleted_at \
             FROM principals WHERE principal_id = $1 AND deleted_at IS NULL",
        )
        .bind(principal_id.0)
        .fetch_optional(&mut *tx)
        .await?;
        tx.commit().await?;
        row.map(|r| {
            let org_id_str: String = r.try_get("organization_id")?;
            let org_id = OrganizationId(
                Uuid::parse_str(&org_id_str)
                    .map_err(|_| sqlx::Error::Decode("invalid organization_id UUID".into()))?,
            );
            row_to_principal(&r, org_id)
        })
        .transpose()
    }

    pub async fn get_principal_by_external_id(
        &self,
        organization_id: OrganizationId,
        principal_type: PrincipalType,
        external_id: &str,
    ) -> Result<Option<Principal>, IamStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let row = sqlx::query(
            "SELECT principal_id, organization_id, principal_type, external_id, display_name, \
                    status, created_at, updated_at, deleted_at \
             FROM principals \
             WHERE organization_id = $1 AND principal_type = $2 AND external_id = $3 \
               AND deleted_at IS NULL",
        )
        .bind(&org_id_str)
        .bind(principal_type_to_str(principal_type))
        .bind(external_id)
        .fetch_optional(&mut *tx)
        .await?;
        tx.commit().await?;
        row.map(|r| row_to_principal(&r, organization_id))
            .transpose()
    }

    pub async fn update_principal(&self, principal: &Principal) -> Result<(), IamStoreError> {
        let org_id_str = principal.organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let result = sqlx::query(
            "UPDATE principals SET display_name = $1, status = $2 \
             WHERE principal_id = $3 AND deleted_at IS NULL",
        )
        .bind(&principal.display_name)
        .bind(entity_status_to_str(principal.status))
        .bind(principal.principal_id.0)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        if result.rows_affected() == 0 {
            return Err(IamStoreError::NotFound(
                principal.principal_id.0.to_string(),
            ));
        }
        Ok(())
    }

    pub async fn delete_principal(&self, principal_id: PrincipalId) -> Result<(), IamStoreError> {
        let mut tx = self.pool.begin().await?;
        let result = sqlx::query(
            "UPDATE principals SET deleted_at = clock_timestamp(), status = 'archived' \
             WHERE principal_id = $1 AND deleted_at IS NULL",
        )
        .bind(principal_id.0)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        if result.rows_affected() == 0 {
            return Err(IamStoreError::NotFound(principal_id.0.to_string()));
        }
        Ok(())
    }

    pub async fn list_principals(
        &self,
        organization_id: OrganizationId,
        principal_type: Option<PrincipalType>,
        limit: u32,
        offset: u32,
    ) -> Result<Vec<Principal>, IamStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let rows = if let Some(pt) = principal_type {
            sqlx::query(
                "SELECT principal_id, organization_id, principal_type, external_id, display_name, \
                        status, created_at, updated_at, deleted_at \
                 FROM principals \
                 WHERE organization_id = $1 AND principal_type = $2 AND deleted_at IS NULL \
                 ORDER BY created_at DESC LIMIT $3 OFFSET $4",
            )
            .bind(&org_id_str)
            .bind(principal_type_to_str(pt))
            .bind(limit as i64)
            .bind(offset as i64)
            .fetch_all(&mut *tx)
            .await?
        } else {
            sqlx::query(
                "SELECT principal_id, organization_id, principal_type, external_id, display_name, \
                        status, created_at, updated_at, deleted_at \
                 FROM principals \
                 WHERE organization_id = $1 AND deleted_at IS NULL \
                 ORDER BY created_at DESC LIMIT $2 OFFSET $3",
            )
            .bind(&org_id_str)
            .bind(limit as i64)
            .bind(offset as i64)
            .fetch_all(&mut *tx)
            .await?
        };
        tx.commit().await?;
        rows.iter()
            .map(|r| row_to_principal(r, organization_id))
            .collect()
    }

    // ========================================================================
    // Role Operations
    // ========================================================================

    pub async fn create_role(
        &self,
        organization_id: OrganizationId,
        name: &str,
        description: Option<&str>,
        is_system: bool,
    ) -> Result<Role, IamStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let row = sqlx::query(
            "INSERT INTO roles (organization_id, name, description, is_system) \
             VALUES ($1, $2, $3, $4) \
             RETURNING role_id, organization_id, name, description, is_system, created_at, updated_at",
        )
        .bind(&org_id_str)
        .bind(name)
        .bind(description)
        .bind(is_system)
        .fetch_one(&mut *tx)
        .await?;
        tx.commit().await?;
        row_to_role(&row, organization_id)
    }

    pub async fn get_role(&self, role_id: RoleId) -> Result<Option<Role>, IamStoreError> {
        let mut tx = self.pool.begin().await?;
        let row = sqlx::query(
            "SELECT role_id, organization_id, name, description, is_system, created_at, updated_at \
             FROM roles WHERE role_id = $1",
        )
        .bind(role_id.0)
        .fetch_optional(&mut *tx)
        .await?;
        tx.commit().await?;
        row.map(|r| {
            let org_id_str: String = r.try_get("organization_id")?;
            let org_id = OrganizationId(
                Uuid::parse_str(&org_id_str)
                    .map_err(|_| sqlx::Error::Decode("invalid organization_id UUID".into()))?,
            );
            row_to_role(&r, org_id)
        })
        .transpose()
    }

    pub async fn get_role_by_name(
        &self,
        organization_id: OrganizationId,
        name: &str,
    ) -> Result<Option<Role>, IamStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let row = sqlx::query(
            "SELECT role_id, organization_id, name, description, is_system, created_at, updated_at \
             FROM roles WHERE organization_id = $1 AND name = $2",
        )
        .bind(&org_id_str)
        .bind(name)
        .fetch_optional(&mut *tx)
        .await?;
        tx.commit().await?;
        row.map(|r| row_to_role(&r, organization_id)).transpose()
    }

    pub async fn update_role(&self, role: &Role) -> Result<(), IamStoreError> {
        let org_id_str = role.organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let result = sqlx::query(
            "UPDATE roles SET name = $1, description = $2 \
             WHERE role_id = $3",
        )
        .bind(&role.name)
        .bind(&role.description)
        .bind(role.role_id.0)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        if result.rows_affected() == 0 {
            return Err(IamStoreError::NotFound(role.role_id.0.to_string()));
        }
        Ok(())
    }

    pub async fn delete_role(&self, role_id: RoleId) -> Result<(), IamStoreError> {
        let mut tx = self.pool.begin().await?;
        let result = sqlx::query("DELETE FROM roles WHERE role_id = $1")
            .bind(role_id.0)
            .execute(&mut *tx)
            .await?;
        tx.commit().await?;
        if result.rows_affected() == 0 {
            return Err(IamStoreError::NotFound(role_id.0.to_string()));
        }
        Ok(())
    }

    pub async fn list_roles(
        &self,
        organization_id: OrganizationId,
    ) -> Result<Vec<Role>, IamStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let rows = sqlx::query(
            "SELECT role_id, organization_id, name, description, is_system, created_at, updated_at \
             FROM roles WHERE organization_id = $1 ORDER BY name",
        )
        .bind(&org_id_str)
        .fetch_all(&mut *tx)
        .await?;
        tx.commit().await?;
        rows.iter()
            .map(|r| row_to_role(r, organization_id))
            .collect()
    }

    pub async fn add_permission_to_role(
        &self,
        role_id: RoleId,
        permission_id: &str,
    ) -> Result<(), IamStoreError> {
        let mut tx = self.pool.begin().await?;
        sqlx::query(
            "INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) \
             ON CONFLICT DO NOTHING",
        )
        .bind(role_id.0)
        .bind(permission_id)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        Ok(())
    }

    pub async fn remove_permission_from_role(
        &self,
        role_id: RoleId,
        permission_id: &str,
    ) -> Result<(), IamStoreError> {
        let mut tx = self.pool.begin().await?;
        sqlx::query("DELETE FROM role_permissions WHERE role_id = $1 AND permission_id = $2")
            .bind(role_id.0)
            .bind(permission_id)
            .execute(&mut *tx)
            .await?;
        tx.commit().await?;
        Ok(())
    }

    pub async fn get_role_permissions(
        &self,
        role_id: RoleId,
    ) -> Result<Vec<Permission>, IamStoreError> {
        let mut tx = self.pool.begin().await?;
        let rows = sqlx::query(
            "SELECT p.permission_id, p.name, p.description, p.resource, p.action \
             FROM permissions p \
             JOIN role_permissions rp ON p.permission_id = rp.permission_id \
             WHERE rp.role_id = $1 \
             ORDER BY p.resource, p.action",
        )
        .bind(role_id.0)
        .fetch_all(&mut *tx)
        .await?;
        tx.commit().await?;
        rows.iter().map(row_to_permission).collect()
    }

    // ========================================================================
    // Permission Operations
    // ========================================================================

    pub async fn get_permission(
        &self,
        permission_id: &str,
    ) -> Result<Option<Permission>, IamStoreError> {
        let mut tx = self.pool.begin().await?;
        let row = sqlx::query(
            "SELECT permission_id, name, description, resource, action \
             FROM permissions WHERE permission_id = $1",
        )
        .bind(permission_id)
        .fetch_optional(&mut *tx)
        .await?;
        tx.commit().await?;
        row.map(|r| row_to_permission(&r)).transpose()
    }

    pub async fn list_permissions(&self) -> Result<Vec<Permission>, IamStoreError> {
        let mut tx = self.pool.begin().await?;
        let rows = sqlx::query(
            "SELECT permission_id, name, description, resource, action \
             FROM permissions ORDER BY resource, action",
        )
        .fetch_all(&mut *tx)
        .await?;
        tx.commit().await?;
        rows.iter().map(row_to_permission).collect()
    }

    // ========================================================================
    // Membership Operations
    // ========================================================================

    pub async fn create_membership(
        &self,
        principal_id: PrincipalId,
        scope: &Scope,
        role_id: RoleId,
    ) -> Result<Membership, IamStoreError> {
        let org_id_str = scope.organization_id().0.to_string();
        let (proj_id_str, env_id) = match scope {
            Scope::Organization(_) => (None, None),
            Scope::Project(_, proj_id) => (Some(proj_id.0.to_string()), None),
            Scope::Environment(_, proj_id, env_id) => (Some(proj_id.0.to_string()), Some(env_id.0)),
        };
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let row = sqlx::query(
            "INSERT INTO memberships \
             (principal_id, organization_id, project_id, environment_id, role_id, status) \
             VALUES ($1, $2, $3, $4, $5, 'active') \
             RETURNING membership_id, principal_id, organization_id, project_id, environment_id, \
                       role_id, status, created_at, updated_at",
        )
        .bind(principal_id.0)
        .bind(&org_id_str)
        .bind(&proj_id_str)
        .bind(env_id)
        .bind(role_id.0)
        .fetch_one(&mut *tx)
        .await?;
        tx.commit().await?;
        row_to_membership(&row)
    }

    pub async fn get_membership(
        &self,
        membership_id: MembershipId,
    ) -> Result<Option<Membership>, IamStoreError> {
        let mut tx = self.pool.begin().await?;
        let row = sqlx::query(
            "SELECT membership_id, principal_id, organization_id, project_id, environment_id, \
                    role_id, status, created_at, updated_at \
             FROM memberships WHERE membership_id = $1",
        )
        .bind(membership_id.0)
        .fetch_optional(&mut *tx)
        .await?;
        tx.commit().await?;
        row.map(|r| row_to_membership(&r)).transpose()
    }

    pub async fn update_membership(&self, membership: &Membership) -> Result<(), IamStoreError> {
        let org_id_str = membership.organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let result = sqlx::query(
            "UPDATE memberships SET status = $1 \
             WHERE membership_id = $2",
        )
        .bind(entity_status_to_str(membership.status))
        .bind(membership.membership_id.0)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        if result.rows_affected() == 0 {
            return Err(IamStoreError::NotFound(
                membership.membership_id.0.to_string(),
            ));
        }
        Ok(())
    }

    pub async fn delete_membership(
        &self,
        membership_id: MembershipId,
    ) -> Result<(), IamStoreError> {
        let mut tx = self.pool.begin().await?;
        let result = sqlx::query("DELETE FROM memberships WHERE membership_id = $1")
            .bind(membership_id.0)
            .execute(&mut *tx)
            .await?;
        tx.commit().await?;
        if result.rows_affected() == 0 {
            return Err(IamStoreError::NotFound(membership_id.0.to_string()));
        }
        Ok(())
    }

    pub async fn list_memberships_for_principal(
        &self,
        principal_id: PrincipalId,
    ) -> Result<Vec<Membership>, IamStoreError> {
        let mut tx = self.pool.begin().await?;
        let rows = sqlx::query(
            "SELECT membership_id, principal_id, organization_id, project_id, environment_id, \
                    role_id, status, created_at, updated_at \
             FROM memberships WHERE principal_id = $1 AND status = 'active'",
        )
        .bind(principal_id.0)
        .fetch_all(&mut *tx)
        .await?;
        tx.commit().await?;
        rows.iter().map(row_to_membership).collect()
    }

    pub async fn list_memberships_at_scope(
        &self,
        scope: &Scope,
    ) -> Result<Vec<Membership>, IamStoreError> {
        let org_id_str = scope.organization_id().0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let rows = match scope {
            Scope::Organization(_) => sqlx::query(
                "SELECT membership_id, principal_id, organization_id, project_id, environment_id, \
                            role_id, status, created_at, updated_at \
                     FROM memberships \
                     WHERE organization_id = $1 AND project_id IS NULL AND environment_id IS NULL \
                       AND status = 'active'",
            )
            .bind(&org_id_str)
            .fetch_all(&mut *tx)
            .await?,
            Scope::Project(_, proj_id) => {
                let proj_id_str = proj_id.0.to_string();
                sqlx::query(
                    "SELECT membership_id, principal_id, organization_id, project_id, environment_id, \
                            role_id, status, created_at, updated_at \
                     FROM memberships \
                     WHERE organization_id = $1 AND project_id = $2 AND environment_id IS NULL \
                       AND status = 'active'",
                )
                .bind(&org_id_str)
                .bind(&proj_id_str)
                .fetch_all(&mut *tx)
                .await?
            }
            Scope::Environment(_, proj_id, env_id) => {
                let proj_id_str = proj_id.0.to_string();
                sqlx::query(
                    "SELECT membership_id, principal_id, organization_id, project_id, environment_id, \
                            role_id, status, created_at, updated_at \
                     FROM memberships \
                     WHERE organization_id = $1 AND project_id = $2 AND environment_id = $3 \
                       AND status = 'active'",
                )
                .bind(&org_id_str)
                .bind(&proj_id_str)
                .bind(env_id.0)
                .fetch_all(&mut *tx)
                .await?
            }
        };
        tx.commit().await?;
        rows.iter().map(row_to_membership).collect()
    }

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
    ) -> Result<Quota, IamStoreError> {
        let mut tx = self.pool.begin().await?;
        let row = sqlx::query(
            "INSERT INTO quotas (scope_type, scope_id, resource_type, limit_value, period) \
             VALUES ($1, $2, $3, $4, $5) \
             RETURNING quota_id, scope_type, scope_id, resource_type, limit_value, period, \
                       created_at, updated_at",
        )
        .bind(scope_type_to_str(scope_type))
        .bind(scope_id)
        .bind(resource_type)
        .bind(limit_value as i64)
        .bind(quota_period_to_str(period))
        .fetch_one(&mut *tx)
        .await?;
        tx.commit().await?;
        row_to_quota(&row)
    }

    pub async fn get_quota(&self, quota_id: QuotaId) -> Result<Option<Quota>, IamStoreError> {
        let mut tx = self.pool.begin().await?;
        let row = sqlx::query(
            "SELECT quota_id, scope_type, scope_id, resource_type, limit_value, period, \
                    created_at, updated_at \
             FROM quotas WHERE quota_id = $1",
        )
        .bind(quota_id.0)
        .fetch_optional(&mut *tx)
        .await?;
        tx.commit().await?;
        row.map(|r| row_to_quota(&r)).transpose()
    }

    pub async fn update_quota(&self, quota: &Quota) -> Result<(), IamStoreError> {
        let mut tx = self.pool.begin().await?;
        let result =
            sqlx::query("UPDATE quotas SET limit_value = $1, period = $2 WHERE quota_id = $3")
                .bind(quota.limit_value as i64)
                .bind(quota_period_to_str(quota.period))
                .bind(quota.quota_id.0)
                .execute(&mut *tx)
                .await?;
        tx.commit().await?;
        if result.rows_affected() == 0 {
            return Err(IamStoreError::NotFound(quota.quota_id.0.to_string()));
        }
        Ok(())
    }

    pub async fn delete_quota(&self, quota_id: QuotaId) -> Result<(), IamStoreError> {
        let mut tx = self.pool.begin().await?;
        let result = sqlx::query("DELETE FROM quotas WHERE quota_id = $1")
            .bind(quota_id.0)
            .execute(&mut *tx)
            .await?;
        tx.commit().await?;
        if result.rows_affected() == 0 {
            return Err(IamStoreError::NotFound(quota_id.0.to_string()));
        }
        Ok(())
    }

    pub async fn list_quotas_at_scope(
        &self,
        scope_type: ScopeType,
        scope_id: &str,
    ) -> Result<Vec<Quota>, IamStoreError> {
        let mut tx = self.pool.begin().await?;
        let rows = sqlx::query(
            "SELECT quota_id, scope_type, scope_id, resource_type, limit_value, period, \
                    created_at, updated_at \
             FROM quotas WHERE scope_type = $1 AND scope_id = $2 ORDER BY resource_type",
        )
        .bind(scope_type_to_str(scope_type))
        .bind(scope_id)
        .fetch_all(&mut *tx)
        .await?;
        tx.commit().await?;
        rows.iter().map(row_to_quota).collect()
    }

    pub async fn check_quota(
        &self,
        scope_type: ScopeType,
        scope_id: &str,
        resource_type: &str,
        requested: u64,
    ) -> Result<bool, IamStoreError> {
        let mut tx = self.pool.begin().await?;
        let row = sqlx::query(
            "SELECT limit_value FROM quotas \
             WHERE scope_type = $1 AND scope_id = $2 AND resource_type = $3",
        )
        .bind(scope_type_to_str(scope_type))
        .bind(scope_id)
        .bind(resource_type)
        .fetch_optional(&mut *tx)
        .await?;
        tx.commit().await?;
        match row {
            Some(r) => {
                let limit: i64 = r.try_get("limit_value")?;
                Ok(requested <= limit as u64)
            }
            None => Ok(true), // No quota defined = allowed
        }
    }

    // ========================================================================
    // Policy Binding Operations
    // ========================================================================

    pub async fn create_policy_binding(
        &self,
        scope_type: ScopeType,
        scope_id: &str,
        policy_type: &str,
        policy_data: serde_json::Value,
    ) -> Result<PolicyBinding, IamStoreError> {
        let mut tx = self.pool.begin().await?;
        let row = sqlx::query(
            "INSERT INTO policy_bindings (scope_type, scope_id, policy_type, policy_data) \
             VALUES ($1, $2, $3, $4) \
             RETURNING binding_id, scope_type, scope_id, policy_type, policy_data, \
                       created_at, updated_at",
        )
        .bind(scope_type_to_str(scope_type))
        .bind(scope_id)
        .bind(policy_type)
        .bind(&policy_data)
        .fetch_one(&mut *tx)
        .await?;
        tx.commit().await?;
        row_to_policy_binding(&row)
    }

    pub async fn get_policy_binding(
        &self,
        binding_id: PolicyBindingId,
    ) -> Result<Option<PolicyBinding>, IamStoreError> {
        let mut tx = self.pool.begin().await?;
        let row = sqlx::query(
            "SELECT binding_id, scope_type, scope_id, policy_type, policy_data, \
                    created_at, updated_at \
             FROM policy_bindings WHERE binding_id = $1",
        )
        .bind(binding_id.0)
        .fetch_optional(&mut *tx)
        .await?;
        tx.commit().await?;
        row.map(|r| row_to_policy_binding(&r)).transpose()
    }

    pub async fn update_policy_binding(
        &self,
        binding: &PolicyBinding,
    ) -> Result<(), IamStoreError> {
        let mut tx = self.pool.begin().await?;
        let result =
            sqlx::query("UPDATE policy_bindings SET policy_data = $1 WHERE binding_id = $2")
                .bind(&binding.policy_data)
                .bind(binding.binding_id.0)
                .execute(&mut *tx)
                .await?;
        tx.commit().await?;
        if result.rows_affected() == 0 {
            return Err(IamStoreError::NotFound(binding.binding_id.0.to_string()));
        }
        Ok(())
    }

    pub async fn delete_policy_binding(
        &self,
        binding_id: PolicyBindingId,
    ) -> Result<(), IamStoreError> {
        let mut tx = self.pool.begin().await?;
        let result = sqlx::query("DELETE FROM policy_bindings WHERE binding_id = $1")
            .bind(binding_id.0)
            .execute(&mut *tx)
            .await?;
        tx.commit().await?;
        if result.rows_affected() == 0 {
            return Err(IamStoreError::NotFound(binding_id.0.to_string()));
        }
        Ok(())
    }

    pub async fn list_policy_bindings_at_scope(
        &self,
        scope_type: ScopeType,
        scope_id: &str,
    ) -> Result<Vec<PolicyBinding>, IamStoreError> {
        let mut tx = self.pool.begin().await?;
        let rows = sqlx::query(
            "SELECT binding_id, scope_type, scope_id, policy_type, policy_data, \
                    created_at, updated_at \
             FROM policy_bindings WHERE scope_type = $1 AND scope_id = $2 ORDER BY policy_type",
        )
        .bind(scope_type_to_str(scope_type))
        .bind(scope_id)
        .fetch_all(&mut *tx)
        .await?;
        tx.commit().await?;
        rows.iter().map(row_to_policy_binding).collect()
    }

    // ========================================================================
    // Service Identity Operations
    // ========================================================================

    pub async fn create_service_identity(
        &self,
        principal_id: PrincipalId,
        service_name: &str,
        credential_hash: &str,
    ) -> Result<ServiceIdentity, IamStoreError> {
        let mut tx = self.pool.begin().await?;
        let row = sqlx::query(
            "INSERT INTO service_identities (principal_id, service_name, credential_hash, status) \
             VALUES ($1, $2, $3, 'active') \
             RETURNING service_identity_id, principal_id, service_name, credential_hash, \
                       status, created_at, updated_at",
        )
        .bind(principal_id.0)
        .bind(service_name)
        .bind(credential_hash)
        .fetch_one(&mut *tx)
        .await?;
        tx.commit().await?;
        row_to_service_identity(&row)
    }

    pub async fn get_service_identity(
        &self,
        service_identity_id: ServiceIdentityId,
    ) -> Result<Option<ServiceIdentity>, IamStoreError> {
        let mut tx = self.pool.begin().await?;
        let row = sqlx::query(
            "SELECT service_identity_id, principal_id, service_name, credential_hash, \
                    status, created_at, updated_at \
             FROM service_identities WHERE service_identity_id = $1",
        )
        .bind(service_identity_id.0)
        .fetch_optional(&mut *tx)
        .await?;
        tx.commit().await?;
        row.map(|r| row_to_service_identity(&r)).transpose()
    }

    pub async fn get_service_identity_by_name(
        &self,
        principal_id: PrincipalId,
        service_name: &str,
    ) -> Result<Option<ServiceIdentity>, IamStoreError> {
        let mut tx = self.pool.begin().await?;
        let row = sqlx::query(
            "SELECT service_identity_id, principal_id, service_name, credential_hash, \
                    status, created_at, updated_at \
             FROM service_identities WHERE principal_id = $1 AND service_name = $2",
        )
        .bind(principal_id.0)
        .bind(service_name)
        .fetch_optional(&mut *tx)
        .await?;
        tx.commit().await?;
        row.map(|r| row_to_service_identity(&r)).transpose()
    }

    pub async fn update_service_identity(
        &self,
        identity: &ServiceIdentity,
    ) -> Result<(), IamStoreError> {
        let mut tx = self.pool.begin().await?;
        let result = sqlx::query(
            "UPDATE service_identities SET credential_hash = $1, status = $2 \
             WHERE service_identity_id = $3",
        )
        .bind(&identity.credential_hash)
        .bind(entity_status_to_str(identity.status))
        .bind(identity.service_identity_id.0)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        if result.rows_affected() == 0 {
            return Err(IamStoreError::NotFound(
                identity.service_identity_id.0.to_string(),
            ));
        }
        Ok(())
    }

    pub async fn delete_service_identity(
        &self,
        service_identity_id: ServiceIdentityId,
    ) -> Result<(), IamStoreError> {
        let mut tx = self.pool.begin().await?;
        let result = sqlx::query("DELETE FROM service_identities WHERE service_identity_id = $1")
            .bind(service_identity_id.0)
            .execute(&mut *tx)
            .await?;
        tx.commit().await?;
        if result.rows_affected() == 0 {
            return Err(IamStoreError::NotFound(service_identity_id.0.to_string()));
        }
        Ok(())
    }

    // ========================================================================
    // Identity Resolution
    // ========================================================================

    /// Resolve a principal to a full identity with memberships, roles, and permissions.
    pub async fn resolve_identity(
        &self,
        principal_id: PrincipalId,
        requested_scope: &Scope,
    ) -> Result<Option<ResolvedIdentity>, IamStoreError> {
        let principal = match self.get_principal(principal_id).await? {
            Some(p) => p,
            None => return Ok(None),
        };

        if principal.status != EntityStatus::Active {
            return Ok(None);
        }

        let org_id_str = principal.organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, Some(principal_id)).await?;

        // Load active memberships
        let membership_rows = sqlx::query(
            "SELECT membership_id, principal_id, organization_id, project_id, environment_id, \
                    role_id, status, created_at, updated_at \
             FROM memberships \
             WHERE principal_id = $1 AND status = 'active'",
        )
        .bind(principal_id.0)
        .fetch_all(&mut *tx)
        .await?;

        let memberships: Vec<Membership> = membership_rows
            .iter()
            .map(row_to_membership)
            .collect::<Result<Vec<_>, _>>()?;

        // Load roles from memberships
        let mut roles = Vec::new();
        let mut all_permissions = Vec::new();
        let mut seen_role_ids = std::collections::HashSet::new();

        for membership in &memberships {
            if seen_role_ids.insert(membership.role_id) {
                if let Some(role) = self.get_role(membership.role_id).await? {
                    let perms = self.get_role_permissions(membership.role_id).await?;
                    all_permissions.extend(perms);
                    roles.push(role);
                }
            }
        }

        tx.commit().await?;

        Ok(Some(ResolvedIdentity {
            principal,
            memberships,
            roles,
            permissions: all_permissions,
            effective_scope: requested_scope.clone(),
        }))
    }

    /// Resolve identity by external ID (e.g., from API key or JWT).
    pub async fn resolve_identity_by_external_id(
        &self,
        organization_id: OrganizationId,
        principal_type: PrincipalType,
        external_id: &str,
        requested_scope: &Scope,
    ) -> Result<Option<ResolvedIdentity>, IamStoreError> {
        let principal = match self
            .get_principal_by_external_id(organization_id, principal_type, external_id)
            .await?
        {
            Some(p) => p,
            None => return Ok(None),
        };

        self.resolve_identity(principal.principal_id, requested_scope)
            .await
    }

    // ========================================================================
    // Audit Operations
    // ========================================================================

    pub async fn log_audit(&self, entry: &AuditLogEntry) -> Result<(), IamStoreError> {
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &entry.organization_id, entry.actor_principal_id).await?;
        sqlx::query(
            "INSERT INTO iam_audit_log \
             (audit_id, organization_id, actor_principal_id, action, resource_type, resource_id, \
              old_value, new_value, ip_address, user_agent) \
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
        )
        .bind(entry.audit_id)
        .bind(&entry.organization_id)
        .bind(entry.actor_principal_id.map(|id| id.0))
        .bind(&entry.action)
        .bind(&entry.resource_type)
        .bind(&entry.resource_id)
        .bind(&entry.old_value)
        .bind(&entry.new_value)
        .bind(&entry.ip_address)
        .bind(&entry.user_agent)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        Ok(())
    }

    pub async fn list_audit_log(
        &self,
        organization_id: OrganizationId,
        limit: u32,
        offset: u32,
    ) -> Result<Vec<AuditLogEntry>, IamStoreError> {
        let org_id_str = organization_id.0.to_string();
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &org_id_str, None).await?;
        let rows = sqlx::query(
            "SELECT audit_id, organization_id, actor_principal_id, action, resource_type, \
                    resource_id, old_value, new_value, ip_address, user_agent, created_at \
             FROM iam_audit_log \
             WHERE organization_id = $1 \
             ORDER BY created_at DESC LIMIT $2 OFFSET $3",
        )
        .bind(&org_id_str)
        .bind(limit as i64)
        .bind(offset as i64)
        .fetch_all(&mut *tx)
        .await?;
        tx.commit().await?;
        rows.iter().map(row_to_audit_log_entry).collect()
    }

    // ========================================================================
    // OIDC Provider Configuration
    // ========================================================================

    pub async fn get_oidc_config(
        &self,
        organization_id: OrganizationId,
        issuer: &str,
    ) -> Result<OidcConfig, IamStoreError> {
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &organization_id.0.to_string(), None).await?;
        let row = sqlx::query(
            "SELECT issuer, audience, jwks_url, claim_to_scope, clock_skew_seconds, require_nonce \
             FROM oidc_providers WHERE organization_id = $1 AND issuer = $2",
        )
        .bind(organization_id.0)
        .bind(issuer)
        .fetch_optional(&mut *tx)
        .await?;
        tx.commit().await?;
        row.map(|row| row_to_oidc_config(&row))
            .transpose()?
            .ok_or_else(|| IamStoreError::NotFound(issuer.to_owned()))
    }

    pub async fn create_oidc_config(
        &self,
        config: OidcConfig,
        organization_id: OrganizationId,
    ) -> Result<(), IamStoreError> {
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &organization_id.0.to_string(), None).await?;
        sqlx::query(
            "INSERT INTO oidc_providers \
             (organization_id, issuer, audience, jwks_url, claim_to_scope, clock_skew_seconds, require_nonce) \
             VALUES ($1, $2, $3, $4, $5, $6, $7)",
        )
        .bind(organization_id.0)
        .bind(&config.issuer)
        .bind(&config.audience)
        .bind(&config.jwks_url)
        .bind(serde_json::to_value(&config.claim_to_scope).map_err(|error| {
            IamStoreError::Database(sqlx::Error::Encode(Box::new(error)))
        })?)
        .bind(config.clock_skew_seconds)
        .bind(config.require_nonce)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        Ok(())
    }

    pub async fn list_oidc_configs(
        &self,
        organization_id: OrganizationId,
    ) -> Result<Vec<OidcConfig>, IamStoreError> {
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &organization_id.0.to_string(), None).await?;
        let rows = sqlx::query(
            "SELECT issuer, audience, jwks_url, claim_to_scope, clock_skew_seconds, require_nonce \
             FROM oidc_providers WHERE organization_id = $1 ORDER BY issuer",
        )
        .bind(organization_id.0)
        .fetch_all(&mut *tx)
        .await?;
        tx.commit().await?;
        rows.iter().map(row_to_oidc_config).collect()
    }

    // ========================================================================
    // Step-up Approvals
    // ========================================================================

    pub async fn create_step_up_approval(
        &self,
        request: StepUpRequest,
        approver_id: PrincipalId,
    ) -> Result<StepUpApproval, IamStoreError> {
        if request.expiry_seconds <= 0 {
            return Err(IamStoreError::StepUpExpired);
        }
        if request.separation_of_duties && request.requester_id == approver_id {
            return Err(IamStoreError::SeparationOfDutiesViolation);
        }

        let organization_id = request.scope.organization_id();
        let scope_id = scope_leaf_id(&request.scope);
        let expires_at = chrono::Utc::now()
            .checked_add_signed(chrono::Duration::seconds(request.expiry_seconds))
            .ok_or(IamStoreError::StepUpExpired)?;
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &organization_id.0.to_string(), Some(approver_id)).await?;
        let row = sqlx::query(
            "INSERT INTO step_up_approvals \
             (approver_id, organization_id, action_hash, action_description, scope_type, scope_id, \
              granted_permissions, expires_at, separation_of_duties) \
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) \
             RETURNING id, approver_id, organization_id, action_hash, action_description, \
                       scope_type, scope_id, granted_permissions, expires_at, \
                       separation_of_duties, created_at, used_at, revoked_at",
        )
        .bind(approver_id.0)
        .bind(organization_id.0)
        .bind(&request.action_hash)
        .bind(&request.action_description)
        .bind(scope_type_to_str(request.scope.scope_type()))
        .bind(scope_id)
        .bind(
            serde_json::to_value(&request.required_permissions)
                .map_err(|error| IamStoreError::Database(sqlx::Error::Encode(Box::new(error))))?,
        )
        .bind(expires_at)
        .bind(request.separation_of_duties)
        .fetch_one(&mut *tx)
        .await?;
        let approval = row_to_step_up_approval(&row, project_id_for_scope(&mut tx, &row).await?)?;
        tx.commit().await?;
        Ok(approval)
    }

    pub async fn validate_step_up_approval(
        &self,
        action_hash: &str,
        requester_id: PrincipalId,
    ) -> Result<StepUpApproval, IamStoreError> {
        let mut tx = self.pool.begin().await?;
        let row = sqlx::query(
            "SELECT id, approver_id, organization_id, action_hash, action_description, \
                    scope_type, scope_id, granted_permissions, expires_at, separation_of_duties, \
                    created_at, used_at, revoked_at \
             FROM step_up_approvals \
             WHERE action_hash = $1 AND used_at IS NULL AND revoked_at IS NULL \
             ORDER BY created_at DESC LIMIT 1",
        )
        .bind(action_hash)
        .fetch_optional(&mut *tx)
        .await?
        .ok_or_else(|| IamStoreError::NotFound(action_hash.to_owned()))?;

        let expires_at: chrono::DateTime<chrono::Utc> = row.try_get("expires_at")?;
        if expires_at <= chrono::Utc::now() {
            return Err(IamStoreError::StepUpExpired);
        }
        let approver_id = PrincipalId(row.try_get("approver_id")?);
        let separation_of_duties: bool = row.try_get("separation_of_duties")?;
        if separation_of_duties && approver_id == requester_id {
            return Err(IamStoreError::SeparationOfDutiesViolation);
        }

        let project_id = project_id_for_scope(&mut tx, &row).await?;
        let approval = row_to_step_up_approval(&row, project_id)?;
        tx.commit().await?;
        Ok(approval)
    }

    pub async fn use_step_up_approval(&self, approval_id: Uuid) -> Result<(), IamStoreError> {
        let result = sqlx::query(
            "UPDATE step_up_approvals SET used_at = clock_timestamp() \
             WHERE id = $1 AND used_at IS NULL AND revoked_at IS NULL AND expires_at > clock_timestamp()",
        )
        .bind(approval_id)
        .execute(&self.pool)
        .await?;
        if result.rows_affected() == 0 {
            return Err(IamStoreError::StepUpUnavailable);
        }
        Ok(())
    }

    pub async fn revoke_step_up_approval(&self, approval_id: Uuid) -> Result<(), IamStoreError> {
        let result = sqlx::query(
            "UPDATE step_up_approvals SET revoked_at = clock_timestamp() \
             WHERE id = $1 AND used_at IS NULL AND revoked_at IS NULL",
        )
        .bind(approval_id)
        .execute(&self.pool)
        .await?;
        if result.rows_affected() == 0 {
            return Err(IamStoreError::StepUpUnavailable);
        }
        Ok(())
    }

    pub async fn cleanup_expired_approvals(&self) -> Result<i32, IamStoreError> {
        let result =
            sqlx::query("DELETE FROM step_up_approvals WHERE expires_at <= clock_timestamp()")
                .execute(&self.pool)
                .await?;
        i32::try_from(result.rows_affected()).map_err(|_| IamStoreError::ConcurrencyConflict)
    }
}

// ============================================================================
// Helper Functions
// ============================================================================

/// Set tenant context for RLS within a transaction.
///
/// Sets `noerelay.organization_id` for tenant isolation and optionally
/// `noerelay.principal_id` for audit attribution. These are transaction-local
/// GUC variables that are automatically cleared on commit/rollback.
pub async fn set_tenant_context(
    transaction: &mut Transaction<'_, Postgres>,
    organization_id: &str,
    principal_id: Option<PrincipalId>,
) -> Result<(), sqlx::Error> {
    sqlx::query("SELECT set_config('noerelay.organization_id', $1, true)")
        .bind(organization_id)
        .execute(&mut **transaction)
        .await?;
    if let Some(pid) = principal_id {
        sqlx::query("SELECT set_config('noerelay.principal_id', $1, true)")
            .bind(pid.0.to_string())
            .execute(&mut **transaction)
            .await?;
    }
    Ok(())
}

/// Clear tenant context (for connection pool safety).
///
/// Resets both `noerelay.organization_id` and `noerelay.principal_id` to empty
/// strings. Called before transaction commit to ensure pooled connections
/// never leak tenant context between requests.
pub async fn clear_tenant_context(
    transaction: &mut Transaction<'_, Postgres>,
) -> Result<(), sqlx::Error> {
    sqlx::query("SELECT set_config('noerelay.organization_id', '', true)")
        .execute(&mut **transaction)
        .await?;
    sqlx::query("SELECT set_config('noerelay.principal_id', '', true)")
        .execute(&mut **transaction)
        .await?;
    Ok(())
}

// ============================================================================
// Row Mapping Helpers
// ============================================================================

fn row_to_organization(row: &sqlx::postgres::PgRow) -> Result<Organization, IamStoreError> {
    let org_id_str: String = row.try_get("organization_id")?;
    let organization_id = OrganizationId(Uuid::parse_str(&org_id_str).map_err(|_| {
        IamStoreError::Database(sqlx::Error::Decode("invalid organization_id UUID".into()))
    })?);
    Ok(Organization {
        organization_id,
        name: row.try_get("name")?,
        slug: row.try_get("slug")?,
        status: str_to_entity_status(row.try_get("status")?),
        created_at: row.try_get("created_at")?,
        updated_at: row.try_get("updated_at")?,
        deleted_at: row.try_get("deleted_at")?,
    })
}

fn row_to_project(
    row: &sqlx::postgres::PgRow,
    organization_id: OrganizationId,
) -> Result<Project, IamStoreError> {
    let proj_id_str: String = row.try_get("project_id")?;
    let project_id = ProjectId(Uuid::parse_str(&proj_id_str).map_err(|_| {
        IamStoreError::Database(sqlx::Error::Decode("invalid project_id UUID".into()))
    })?);
    Ok(Project {
        project_id,
        organization_id,
        name: row.try_get("name")?,
        slug: row.try_get("slug")?,
        status: str_to_entity_status(row.try_get("status")?),
        created_at: row.try_get("created_at")?,
        updated_at: row.try_get("updated_at")?,
        deleted_at: row.try_get("deleted_at")?,
    })
}

fn row_to_environment(
    row: &sqlx::postgres::PgRow,
    organization_id: OrganizationId,
    project_id: ProjectId,
) -> Result<Environment, IamStoreError> {
    Ok(Environment {
        environment_id: EnvironmentId(row.try_get("environment_id")?),
        organization_id,
        project_id,
        name: row.try_get("name")?,
        slug: row.try_get("slug")?,
        status: str_to_entity_status(row.try_get("status")?),
        created_at: row.try_get("created_at")?,
        updated_at: row.try_get("updated_at")?,
        deleted_at: row.try_get("deleted_at")?,
    })
}

fn row_to_principal(
    row: &sqlx::postgres::PgRow,
    organization_id: OrganizationId,
) -> Result<Principal, IamStoreError> {
    Ok(Principal {
        principal_id: PrincipalId(row.try_get("principal_id")?),
        organization_id,
        principal_type: str_to_principal_type(row.try_get("principal_type")?),
        external_id: row.try_get("external_id")?,
        display_name: row.try_get("display_name")?,
        status: str_to_entity_status(row.try_get("status")?),
        created_at: row.try_get("created_at")?,
        updated_at: row.try_get("updated_at")?,
        deleted_at: row.try_get("deleted_at")?,
    })
}

fn row_to_role(
    row: &sqlx::postgres::PgRow,
    organization_id: OrganizationId,
) -> Result<Role, IamStoreError> {
    Ok(Role {
        role_id: RoleId(row.try_get("role_id")?),
        organization_id,
        name: row.try_get("name")?,
        description: row.try_get("description")?,
        is_system: row.try_get("is_system")?,
        created_at: row.try_get("created_at")?,
        updated_at: row.try_get("updated_at")?,
    })
}

fn row_to_permission(row: &sqlx::postgres::PgRow) -> Result<Permission, IamStoreError> {
    Ok(Permission {
        permission_id: row.try_get("permission_id")?,
        name: row.try_get("name")?,
        description: row.try_get("description")?,
        resource: row.try_get("resource")?,
        action: row.try_get("action")?,
    })
}

fn row_to_membership(row: &sqlx::postgres::PgRow) -> Result<Membership, IamStoreError> {
    let org_id_str: String = row.try_get("organization_id")?;
    let organization_id = OrganizationId(Uuid::parse_str(&org_id_str).map_err(|_| {
        IamStoreError::Database(sqlx::Error::Decode("invalid organization_id UUID".into()))
    })?);
    let proj_id_str: Option<String> = row.try_get("project_id")?;
    let project_id = proj_id_str
        .map(|s| {
            Uuid::parse_str(&s).map_err(|_| {
                IamStoreError::Database(sqlx::Error::Decode("invalid project_id UUID".into()))
            })
        })
        .transpose()?
        .map(ProjectId);
    Ok(Membership {
        membership_id: MembershipId(row.try_get("membership_id")?),
        principal_id: PrincipalId(row.try_get("principal_id")?),
        organization_id,
        project_id,
        environment_id: row
            .try_get::<Option<Uuid>, _>("environment_id")?
            .map(EnvironmentId),
        role_id: RoleId(row.try_get("role_id")?),
        status: str_to_entity_status(row.try_get("status")?),
        created_at: row.try_get("created_at")?,
        updated_at: row.try_get("updated_at")?,
    })
}

fn row_to_quota(row: &sqlx::postgres::PgRow) -> Result<Quota, IamStoreError> {
    let limit: i64 = row.try_get("limit_value")?;
    Ok(Quota {
        quota_id: QuotaId(row.try_get("quota_id")?),
        scope_type: str_to_scope_type(row.try_get("scope_type")?),
        scope_id: row.try_get("scope_id")?,
        resource_type: row.try_get("resource_type")?,
        limit_value: limit as u64,
        period: str_to_quota_period(row.try_get("period")?),
        created_at: row.try_get("created_at")?,
        updated_at: row.try_get("updated_at")?,
    })
}

fn row_to_policy_binding(row: &sqlx::postgres::PgRow) -> Result<PolicyBinding, IamStoreError> {
    Ok(PolicyBinding {
        binding_id: PolicyBindingId(row.try_get("binding_id")?),
        scope_type: str_to_scope_type(row.try_get("scope_type")?),
        scope_id: row.try_get("scope_id")?,
        policy_type: row.try_get("policy_type")?,
        policy_data: row.try_get("policy_data")?,
        created_at: row.try_get("created_at")?,
        updated_at: row.try_get("updated_at")?,
    })
}

fn row_to_service_identity(row: &sqlx::postgres::PgRow) -> Result<ServiceIdentity, IamStoreError> {
    Ok(ServiceIdentity {
        service_identity_id: ServiceIdentityId(row.try_get("service_identity_id")?),
        principal_id: PrincipalId(row.try_get("principal_id")?),
        service_name: row.try_get("service_name")?,
        credential_hash: row.try_get("credential_hash")?,
        status: str_to_entity_status(row.try_get("status")?),
        created_at: row.try_get("created_at")?,
        updated_at: row.try_get("updated_at")?,
    })
}

fn row_to_audit_log_entry(row: &sqlx::postgres::PgRow) -> Result<AuditLogEntry, IamStoreError> {
    Ok(AuditLogEntry {
        audit_id: row.try_get("audit_id")?,
        organization_id: row.try_get("organization_id")?,
        actor_principal_id: row
            .try_get::<Option<Uuid>, _>("actor_principal_id")?
            .map(PrincipalId),
        action: row.try_get("action")?,
        resource_type: row.try_get("resource_type")?,
        resource_id: row.try_get("resource_id")?,
        old_value: row.try_get("old_value")?,
        new_value: row.try_get("new_value")?,
        ip_address: row.try_get::<Option<String>, _>("ip_address")?,
        user_agent: row.try_get("user_agent")?,
        created_at: row.try_get("created_at")?,
    })
}

fn row_to_oidc_config(row: &sqlx::postgres::PgRow) -> Result<OidcConfig, IamStoreError> {
    let mapping: serde_json::Value = row.try_get("claim_to_scope")?;
    let claim_to_scope = serde_json::from_value(mapping)
        .map_err(|error| IamStoreError::Database(sqlx::Error::Decode(Box::new(error))))?;
    Ok(OidcConfig {
        issuer: row.try_get("issuer")?,
        audience: row.try_get("audience")?,
        jwks_url: row.try_get("jwks_url")?,
        claim_to_scope,
        clock_skew_seconds: row.try_get::<i32, _>("clock_skew_seconds")?.into(),
        require_nonce: row.try_get("require_nonce")?,
    })
}

fn scope_leaf_id(scope: &Scope) -> Uuid {
    match scope {
        Scope::Organization(id) => id.0,
        Scope::Project(_, id) => id.0,
        Scope::Environment(_, _, id) => id.0,
    }
}

async fn project_id_for_scope(
    transaction: &mut Transaction<'_, Postgres>,
    row: &sqlx::postgres::PgRow,
) -> Result<Option<ProjectId>, IamStoreError> {
    let scope_type: String = row.try_get("scope_type")?;
    if scope_type != "environment" {
        return Ok(None);
    }
    let scope_id: Uuid = row.try_get("scope_id")?;
    let project_id: Option<String> =
        sqlx::query_scalar("SELECT project_id FROM environments WHERE environment_id = $1")
            .bind(scope_id)
            .fetch_optional(&mut **transaction)
            .await?;
    project_id
        .map(|id| {
            Uuid::parse_str(&id)
                .map(ProjectId)
                .map_err(|_| IamStoreError::InvalidScope)
        })
        .transpose()
}

fn row_to_step_up_approval(
    row: &sqlx::postgres::PgRow,
    environment_project_id: Option<ProjectId>,
) -> Result<StepUpApproval, IamStoreError> {
    let organization_id = OrganizationId(row.try_get("organization_id")?);
    let scope_id: Uuid = row.try_get("scope_id")?;
    let scope = match row.try_get::<String, _>("scope_type")?.as_str() {
        "organization" => Scope::Organization(organization_id),
        "project" => Scope::Project(organization_id, ProjectId(scope_id)),
        "environment" => Scope::Environment(
            organization_id,
            environment_project_id.ok_or(IamStoreError::InvalidScope)?,
            EnvironmentId(scope_id),
        ),
        _ => return Err(IamStoreError::InvalidScope),
    };
    let permissions: serde_json::Value = row.try_get("granted_permissions")?;
    Ok(StepUpApproval {
        id: row.try_get("id")?,
        approver_id: PrincipalId(row.try_get("approver_id")?),
        organization_id,
        action_hash: row.try_get("action_hash")?,
        action_description: row.try_get("action_description")?,
        scope,
        granted_permissions: serde_json::from_value(permissions)
            .map_err(|error| IamStoreError::Database(sqlx::Error::Decode(Box::new(error))))?,
        expires_at: row.try_get("expires_at")?,
        separation_of_duties: row.try_get("separation_of_duties")?,
        created_at: row.try_get("created_at")?,
        used_at: row.try_get("used_at")?,
        revoked_at: row.try_get("revoked_at")?,
    })
}

// ============================================================================
// String Conversion Helpers
// ============================================================================

fn entity_status_to_str(status: EntityStatus) -> &'static str {
    match status {
        EntityStatus::Active => "active",
        EntityStatus::Suspended => "suspended",
        EntityStatus::Archived => "archived",
        EntityStatus::Revoked => "revoked",
    }
}

fn str_to_entity_status(s: &str) -> EntityStatus {
    match s {
        "suspended" => EntityStatus::Suspended,
        "archived" => EntityStatus::Archived,
        "revoked" => EntityStatus::Revoked,
        _ => EntityStatus::Active,
    }
}

fn principal_type_to_str(pt: PrincipalType) -> &'static str {
    match pt {
        PrincipalType::Human => "human",
        PrincipalType::Service => "service",
    }
}

fn str_to_principal_type(s: &str) -> PrincipalType {
    match s {
        "service" => PrincipalType::Service,
        _ => PrincipalType::Human,
    }
}

fn scope_type_to_str(st: ScopeType) -> &'static str {
    match st {
        ScopeType::Organization => "organization",
        ScopeType::Project => "project",
        ScopeType::Environment => "environment",
    }
}

fn str_to_scope_type(s: &str) -> ScopeType {
    match s {
        "project" => ScopeType::Project,
        "environment" => ScopeType::Environment,
        _ => ScopeType::Organization,
    }
}

fn quota_period_to_str(qp: QuotaPeriod) -> &'static str {
    match qp {
        QuotaPeriod::Daily => "daily",
        QuotaPeriod::Weekly => "weekly",
        QuotaPeriod::Monthly => "monthly",
        QuotaPeriod::Total => "total",
    }
}

fn str_to_quota_period(s: &str) -> QuotaPeriod {
    match s {
        "weekly" => QuotaPeriod::Weekly,
        "monthly" => QuotaPeriod::Monthly,
        "total" => QuotaPeriod::Total,
        _ => QuotaPeriod::Daily,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn entity_status_conversion_roundtrips() {
        let cases = [
            EntityStatus::Active,
            EntityStatus::Suspended,
            EntityStatus::Archived,
        ];
        for status in cases {
            assert_eq!(str_to_entity_status(entity_status_to_str(status)), status);
        }
    }

    #[test]
    fn principal_type_conversion_roundtrips() {
        assert_eq!(
            str_to_principal_type(principal_type_to_str(PrincipalType::Human)),
            PrincipalType::Human
        );
        assert_eq!(
            str_to_principal_type(principal_type_to_str(PrincipalType::Service)),
            PrincipalType::Service
        );
    }

    #[test]
    fn scope_type_conversion_roundtrips() {
        let cases = [
            ScopeType::Organization,
            ScopeType::Project,
            ScopeType::Environment,
        ];
        for st in cases {
            assert_eq!(str_to_scope_type(scope_type_to_str(st)), st);
        }
    }

    #[test]
    fn quota_period_conversion_roundtrips() {
        let cases = [
            QuotaPeriod::Daily,
            QuotaPeriod::Weekly,
            QuotaPeriod::Monthly,
            QuotaPeriod::Total,
        ];
        for qp in cases {
            assert_eq!(str_to_quota_period(quota_period_to_str(qp)), qp);
        }
    }
}
