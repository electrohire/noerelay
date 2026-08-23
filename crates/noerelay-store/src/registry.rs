//! Tenant-scoped persistence for immutable registry revisions.

use noerelay_core::iam::{OrganizationId, PrincipalId};
use noerelay_core::registry::{
    AgentRevision, ModelRevision, ProviderRevision, RegistryEntityType, RegistryError,
    RegistryLifecycle, ToolRevision,
};
use serde::de::DeserializeOwned;
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use sqlx::{PgPool, Postgres, Row, Transaction};
use thiserror::Error;
use uuid::Uuid;

use crate::iam::set_tenant_context;

const REVISION_COLUMNS: &str = "id, entity_type, entity_id, revision, revision_hash, lifecycle, \
    content, display_name, organization_id, created_at, created_by, activated_at, activated_by, \
    superseded_by, quarantine_reason, notes";

#[derive(Debug, Error)]
pub enum RegistryStoreError {
    #[error("database operation failed: {0}")]
    Database(#[from] sqlx::Error),
    #[error("registry serialization failed: {0}")]
    Serialization(#[from] serde_json::Error),
    #[error("registry operation failed: {0:?}")]
    Registry(RegistryError),
    #[error("invalid registry value in database: {0}")]
    InvalidValue(String),
}

impl From<RegistryError> for RegistryStoreError {
    fn from(value: RegistryError) -> Self {
        Self::Registry(value)
    }
}

#[derive(Clone)]
pub struct RegistryRepository {
    pool: PgPool,
    organization_id: OrganizationId,
}

impl RegistryRepository {
    pub fn new(pool: PgPool, organization_id: OrganizationId) -> Self {
        Self {
            pool,
            organization_id,
        }
    }

    pub async fn create_revision(
        &self,
        entity_type: RegistryEntityType,
        entity_id: &str,
        content: Value,
        display_name: &str,
        organization_id: OrganizationId,
        created_by: PrincipalId,
    ) -> Result<Uuid, RegistryStoreError> {
        if organization_id != self.organization_id
            || entity_id.trim().is_empty()
            || display_name.trim().is_empty()
            || !content.is_object()
        {
            return Err(RegistryError::InvalidRevision.into());
        }
        let content_bytes = serde_json::to_vec(&content)?;
        let revision_hash = format!("sha256:{:x}", Sha256::digest(content_bytes));
        let notes = content
            .get("notes")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned();
        let id = Uuid::new_v4();
        let entity_type_value = entity_type_to_str(entity_type);
        let mut tx = self.transaction(Some(created_by)).await?;
        let lock_key = format!("registry:{entity_type_value}:{entity_id}");
        sqlx::query("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))")
            .bind(lock_key)
            .execute(&mut *tx)
            .await?;
        let current: Option<i32> = sqlx::query_scalar(
            "SELECT MAX(revision) FROM registry_revisions \
             WHERE entity_type = $1 AND entity_id = $2",
        )
        .bind(entity_type_value)
        .bind(entity_id)
        .fetch_one(&mut *tx)
        .await?;
        let revision = current
            .unwrap_or(0)
            .checked_add(1)
            .ok_or(RegistryError::InvalidRevision)?;
        sqlx::query(
            "INSERT INTO registry_revisions \
             (id, entity_type, entity_id, revision, revision_hash, lifecycle, content, \
              display_name, organization_id, created_by, notes) \
             VALUES ($1,$2,$3,$4,$5,'draft',$6,$7,$8,$9,$10)",
        )
        .bind(id)
        .bind(entity_type_value)
        .bind(entity_id)
        .bind(revision)
        .bind(revision_hash)
        .bind(content)
        .bind(display_name)
        .bind(organization_id.0)
        .bind(created_by.0)
        .bind(notes)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        Ok(id)
    }

    pub async fn get_revision(&self, id: Uuid) -> Result<Value, RegistryStoreError> {
        let mut tx = self.transaction(None).await?;
        let row = get_revision_row(&mut tx, id, false).await?;
        let value = row_to_value(&row)?;
        tx.commit().await?;
        Ok(value)
    }

    pub async fn get_revision_by_entity(
        &self,
        entity_type: RegistryEntityType,
        entity_id: &str,
        revision: i32,
    ) -> Result<Value, RegistryStoreError> {
        let mut tx = self.transaction(None).await?;
        let query = format!(
            "SELECT {REVISION_COLUMNS} FROM registry_revisions \
             WHERE entity_type = $1 AND entity_id = $2 AND revision = $3"
        );
        let row = sqlx::query(&query)
            .bind(entity_type_to_str(entity_type))
            .bind(entity_id)
            .bind(revision)
            .fetch_optional(&mut *tx)
            .await?
            .ok_or(RegistryError::NotFound)?;
        let value = row_to_value(&row)?;
        tx.commit().await?;
        Ok(value)
    }

    pub async fn get_active_revision(
        &self,
        entity_type: RegistryEntityType,
        entity_id: &str,
    ) -> Result<Option<Value>, RegistryStoreError> {
        let mut tx = self.transaction(None).await?;
        let query = format!(
            "SELECT {REVISION_COLUMNS} FROM registry_revisions \
             WHERE entity_type = $1 AND entity_id = $2 AND lifecycle = 'active'"
        );
        let row = sqlx::query(&query)
            .bind(entity_type_to_str(entity_type))
            .bind(entity_id)
            .fetch_optional(&mut *tx)
            .await?;
        let value = row.as_ref().map(row_to_value).transpose()?;
        tx.commit().await?;
        Ok(value)
    }

    pub async fn get_revision_history(
        &self,
        entity_type: RegistryEntityType,
        entity_id: &str,
    ) -> Result<Vec<Value>, RegistryStoreError> {
        let mut tx = self.transaction(None).await?;
        let query = format!(
            "SELECT {REVISION_COLUMNS} FROM registry_revisions \
             WHERE entity_type = $1 AND entity_id = $2 ORDER BY revision"
        );
        let rows = sqlx::query(&query)
            .bind(entity_type_to_str(entity_type))
            .bind(entity_id)
            .fetch_all(&mut *tx)
            .await?;
        let values = rows.iter().map(row_to_value).collect::<Result<_, _>>()?;
        tx.commit().await?;
        Ok(values)
    }

    pub async fn transition_lifecycle(
        &self,
        id: Uuid,
        target: RegistryLifecycle,
        actor_id: PrincipalId,
    ) -> Result<(), RegistryStoreError> {
        let mut tx = self.transaction(Some(actor_id)).await?;
        let row = get_revision_row(&mut tx, id, true).await?;
        let lifecycle = str_to_lifecycle(&row.try_get::<String, _>("lifecycle")?)?;
        if lifecycle == RegistryLifecycle::Active {
            return Err(RegistryError::AlreadyActive.into());
        }
        if lifecycle == RegistryLifecycle::Superseded {
            return Err(RegistryError::AlreadySuperseded.into());
        }
        if lifecycle == RegistryLifecycle::Quarantined {
            return Err(RegistryError::Quarantined {
                reason: row
                    .try_get::<Option<String>, _>("quarantine_reason")?
                    .unwrap_or_else(|| "unspecified".into()),
            }
            .into());
        }
        if !lifecycle.can_transition(&target) {
            return Err(RegistryError::IllegalTransition {
                from: lifecycle,
                to: target,
            }
            .into());
        }
        let created_by = PrincipalId(row.try_get("created_by")?);
        if target == RegistryLifecycle::Active && actor_id == created_by {
            return Err(RegistryError::UnauthorizedActivation.into());
        }
        if target == RegistryLifecycle::Active {
            let active: Option<Uuid> = sqlx::query_scalar(
                "SELECT id FROM registry_revisions WHERE entity_type = $1 AND entity_id = $2 \
                 AND lifecycle = 'active' AND id <> $3",
            )
            .bind(row.try_get::<String, _>("entity_type")?)
            .bind(row.try_get::<String, _>("entity_id")?)
            .bind(id)
            .fetch_optional(&mut *tx)
            .await?;
            if active.is_some() {
                return Err(RegistryError::AlreadyActive.into());
            }
        }
        sqlx::query(
            "UPDATE registry_revisions SET lifecycle = $2, \
             activated_at = CASE WHEN $2 = 'active' THEN now() ELSE activated_at END, \
             activated_by = CASE WHEN $2 = 'active' THEN $3 ELSE activated_by END \
             WHERE id = $1",
        )
        .bind(id)
        .bind(lifecycle_to_str(target))
        .bind(actor_id.0)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        Ok(())
    }

    pub async fn quarantine(&self, id: Uuid, reason: &str) -> Result<(), RegistryStoreError> {
        if reason.trim().is_empty() {
            return Err(RegistryError::InvalidRevision.into());
        }
        let mut tx = self.transaction(None).await?;
        let row = get_revision_row(&mut tx, id, true).await?;
        let lifecycle = str_to_lifecycle(&row.try_get::<String, _>("lifecycle")?)?;
        if lifecycle == RegistryLifecycle::Quarantined {
            return Err(RegistryError::Quarantined {
                reason: row
                    .try_get::<Option<String>, _>("quarantine_reason")?
                    .unwrap_or_else(|| "unspecified".into()),
            }
            .into());
        }
        if !lifecycle.can_transition(&RegistryLifecycle::Quarantined) {
            return Err(RegistryError::IllegalTransition {
                from: lifecycle,
                to: RegistryLifecycle::Quarantined,
            }
            .into());
        }
        sqlx::query(
            "UPDATE registry_revisions SET lifecycle = 'quarantined', quarantine_reason = $2 \
             WHERE id = $1",
        )
        .bind(id)
        .bind(reason.trim())
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        Ok(())
    }

    pub async fn supersede(
        &self,
        old_id: Uuid,
        new_id: Uuid,
        actor_id: PrincipalId,
    ) -> Result<(), RegistryStoreError> {
        let mut tx = self.transaction(Some(actor_id)).await?;
        let old = get_revision_row(&mut tx, old_id, true).await?;
        let new = get_revision_row(&mut tx, new_id, true).await?;
        let old_lifecycle = str_to_lifecycle(&old.try_get::<String, _>("lifecycle")?)?;
        let new_lifecycle = str_to_lifecycle(&new.try_get::<String, _>("lifecycle")?)?;
        if old_lifecycle == RegistryLifecycle::Superseded {
            return Err(RegistryError::AlreadySuperseded.into());
        }
        if old_lifecycle != RegistryLifecycle::Active
            || new_lifecycle != RegistryLifecycle::Approved
            || old.try_get::<String, _>("entity_type")?
                != new.try_get::<String, _>("entity_type")?
            || old.try_get::<String, _>("entity_id")? != new.try_get::<String, _>("entity_id")?
            || new.try_get::<i32, _>("revision")? != old.try_get::<i32, _>("revision")? + 1
        {
            return Err(RegistryError::InvalidRevision.into());
        }
        if PrincipalId(new.try_get("created_by")?) == actor_id {
            return Err(RegistryError::UnauthorizedSupersession.into());
        }
        sqlx::query(
            "UPDATE registry_revisions SET lifecycle = 'superseded', superseded_by = $2 \
             WHERE id = $1",
        )
        .bind(old_id)
        .bind(new_id)
        .execute(&mut *tx)
        .await?;
        sqlx::query(
            "UPDATE registry_revisions SET lifecycle = 'active', activated_at = now(), \
             activated_by = $2 WHERE id = $1",
        )
        .bind(new_id)
        .bind(actor_id.0)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        Ok(())
    }

    pub async fn list_active(
        &self,
        entity_type: RegistryEntityType,
        organization_id: OrganizationId,
    ) -> Result<Vec<Value>, RegistryStoreError> {
        self.ensure_organization(organization_id)?;
        self.list_by_lifecycle(Some(entity_type), "active").await
    }

    pub async fn get_active_model(
        &self,
        entity_id: &str,
    ) -> Result<Option<ModelRevision>, RegistryStoreError> {
        self.get_active_typed(RegistryEntityType::Model, entity_id)
            .await
    }

    pub async fn get_active_provider(
        &self,
        entity_id: &str,
    ) -> Result<Option<ProviderRevision>, RegistryStoreError> {
        self.get_active_typed(RegistryEntityType::Provider, entity_id)
            .await
    }

    pub async fn get_active_agent(
        &self,
        entity_id: &str,
    ) -> Result<Option<AgentRevision>, RegistryStoreError> {
        self.get_active_typed(RegistryEntityType::Agent, entity_id)
            .await
    }

    pub async fn get_active_tool(
        &self,
        entity_id: &str,
    ) -> Result<Option<ToolRevision>, RegistryStoreError> {
        self.get_active_typed(RegistryEntityType::Tool, entity_id)
            .await
    }

    pub async fn list_active_models(
        &self,
        organization_id: OrganizationId,
    ) -> Result<Vec<ModelRevision>, RegistryStoreError> {
        self.list_active_typed(RegistryEntityType::Model, organization_id)
            .await
    }

    pub async fn list_active_tools(
        &self,
        organization_id: OrganizationId,
    ) -> Result<Vec<ToolRevision>, RegistryStoreError> {
        self.list_active_typed(RegistryEntityType::Tool, organization_id)
            .await
    }

    pub async fn check_quarantine(
        &self,
        entity_type: RegistryEntityType,
        entity_id: &str,
    ) -> Result<Option<String>, RegistryStoreError> {
        let mut tx = self.transaction(None).await?;
        let reason = sqlx::query_scalar(
            "SELECT quarantine_reason FROM registry_revisions \
             WHERE entity_type = $1 AND entity_id = $2 AND lifecycle = 'quarantined' \
             ORDER BY revision DESC LIMIT 1",
        )
        .bind(entity_type_to_str(entity_type))
        .bind(entity_id)
        .fetch_optional(&mut *tx)
        .await?
        .flatten();
        tx.commit().await?;
        Ok(reason)
    }

    pub async fn list_quarantined(
        &self,
        organization_id: OrganizationId,
    ) -> Result<Vec<Value>, RegistryStoreError> {
        self.ensure_organization(organization_id)?;
        self.list_by_lifecycle(None, "quarantined").await
    }

    async fn get_active_typed<T: DeserializeOwned>(
        &self,
        entity_type: RegistryEntityType,
        entity_id: &str,
    ) -> Result<Option<T>, RegistryStoreError> {
        self.get_active_revision(entity_type, entity_id)
            .await?
            .map(serde_json::from_value)
            .transpose()
            .map_err(Into::into)
    }

    async fn list_active_typed<T: DeserializeOwned>(
        &self,
        entity_type: RegistryEntityType,
        organization_id: OrganizationId,
    ) -> Result<Vec<T>, RegistryStoreError> {
        self.list_active(entity_type, organization_id)
            .await?
            .into_iter()
            .map(serde_json::from_value)
            .collect::<Result<_, _>>()
            .map_err(Into::into)
    }

    async fn list_by_lifecycle(
        &self,
        entity_type: Option<RegistryEntityType>,
        lifecycle: &str,
    ) -> Result<Vec<Value>, RegistryStoreError> {
        let mut tx = self.transaction(None).await?;
        let query = format!(
            "SELECT {REVISION_COLUMNS} FROM registry_revisions \
             WHERE lifecycle = $1 AND ($2::text IS NULL OR entity_type = $2) \
             ORDER BY entity_type, entity_id, revision"
        );
        let rows = sqlx::query(&query)
            .bind(lifecycle)
            .bind(entity_type.map(entity_type_to_str))
            .fetch_all(&mut *tx)
            .await?;
        let values = rows.iter().map(row_to_value).collect::<Result<_, _>>()?;
        tx.commit().await?;
        Ok(values)
    }

    fn ensure_organization(
        &self,
        organization_id: OrganizationId,
    ) -> Result<(), RegistryStoreError> {
        if organization_id != self.organization_id {
            return Err(RegistryError::InvalidRevision.into());
        }
        Ok(())
    }

    async fn transaction(
        &self,
        principal_id: Option<PrincipalId>,
    ) -> Result<Transaction<'_, Postgres>, RegistryStoreError> {
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &self.organization_id.0.to_string(), principal_id).await?;
        Ok(tx)
    }
}

async fn get_revision_row(
    tx: &mut Transaction<'_, Postgres>,
    id: Uuid,
    lock: bool,
) -> Result<sqlx::postgres::PgRow, RegistryStoreError> {
    let query = format!(
        "SELECT {REVISION_COLUMNS} FROM registry_revisions WHERE id = $1{}",
        if lock { " FOR UPDATE" } else { "" }
    );
    sqlx::query(&query)
        .bind(id)
        .fetch_optional(&mut **tx)
        .await?
        .ok_or_else(|| RegistryError::NotFound.into())
}

fn row_to_value(row: &sqlx::postgres::PgRow) -> Result<Value, RegistryStoreError> {
    let content: Value = row.try_get("content")?;
    let mut object = content.as_object().cloned().ok_or_else(|| {
        RegistryStoreError::InvalidValue("registry content must be a JSON object".into())
    })?;
    insert(&mut object, "id", row.try_get::<Uuid, _>("id")?)?;
    insert(
        &mut object,
        "entity_type",
        row.try_get::<String, _>("entity_type")?,
    )?;
    insert(
        &mut object,
        "entity_id",
        row.try_get::<String, _>("entity_id")?,
    )?;
    insert(&mut object, "revision", row.try_get::<i32, _>("revision")?)?;
    insert(
        &mut object,
        "revision_hash",
        row.try_get::<String, _>("revision_hash")?,
    )?;
    insert(
        &mut object,
        "lifecycle",
        row.try_get::<String, _>("lifecycle")?,
    )?;
    insert(
        &mut object,
        "display_name",
        row.try_get::<String, _>("display_name")?,
    )?;
    insert(
        &mut object,
        "organization_id",
        OrganizationId(row.try_get("organization_id")?),
    )?;
    insert(
        &mut object,
        "created_at",
        row.try_get::<chrono::DateTime<chrono::Utc>, _>("created_at")?,
    )?;
    insert(
        &mut object,
        "created_by",
        PrincipalId(row.try_get("created_by")?),
    )?;
    insert(
        &mut object,
        "activated_at",
        row.try_get::<Option<chrono::DateTime<chrono::Utc>>, _>("activated_at")?,
    )?;
    insert(
        &mut object,
        "activated_by",
        row.try_get::<Option<Uuid>, _>("activated_by")?
            .map(PrincipalId),
    )?;
    insert(
        &mut object,
        "superseded_by",
        row.try_get::<Option<Uuid>, _>("superseded_by")?,
    )?;
    insert(
        &mut object,
        "quarantine_reason",
        row.try_get::<Option<String>, _>("quarantine_reason")?,
    )?;
    insert(&mut object, "notes", row.try_get::<String, _>("notes")?)?;
    Ok(Value::Object(object))
}

fn insert<T: serde::Serialize>(
    object: &mut Map<String, Value>,
    key: &str,
    value: T,
) -> Result<(), RegistryStoreError> {
    object.insert(key.into(), serde_json::to_value(value)?);
    Ok(())
}

fn entity_type_to_str(value: RegistryEntityType) -> &'static str {
    match value {
        RegistryEntityType::Model => "model",
        RegistryEntityType::Provider => "provider",
        RegistryEntityType::Agent => "agent",
        RegistryEntityType::Tool => "tool",
    }
}

fn lifecycle_to_str(value: RegistryLifecycle) -> &'static str {
    match value {
        RegistryLifecycle::Draft => "draft",
        RegistryLifecycle::Proposed => "proposed",
        RegistryLifecycle::Reviewed => "reviewed",
        RegistryLifecycle::Approved => "approved",
        RegistryLifecycle::Active => "active",
        RegistryLifecycle::Quarantined => "quarantined",
        RegistryLifecycle::Superseded => "superseded",
        RegistryLifecycle::Rejected => "rejected",
    }
}

fn str_to_lifecycle(value: &str) -> Result<RegistryLifecycle, RegistryStoreError> {
    match value {
        "draft" => Ok(RegistryLifecycle::Draft),
        "proposed" => Ok(RegistryLifecycle::Proposed),
        "reviewed" => Ok(RegistryLifecycle::Reviewed),
        "approved" => Ok(RegistryLifecycle::Approved),
        "active" => Ok(RegistryLifecycle::Active),
        "quarantined" => Ok(RegistryLifecycle::Quarantined),
        "superseded" => Ok(RegistryLifecycle::Superseded),
        "rejected" => Ok(RegistryLifecycle::Rejected),
        invalid => Err(RegistryStoreError::InvalidValue(invalid.into())),
    }
}
