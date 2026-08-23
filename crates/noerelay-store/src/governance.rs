//! Tenant-scoped persistence for immutable governance revisions and impact links.

use std::collections::{HashMap, HashSet, VecDeque};

use noerelay_core::RunId;
use noerelay_core::governance::{
    DependencyLink, DependencyType, GovernanceEntityType, GovernanceError, GovernanceLifecycle,
    GovernanceRevision, ImpactAnalysis, RunPin,
};
use noerelay_core::iam::{OrganizationId, PrincipalId};
use sqlx::{PgPool, Postgres, Row, Transaction};
use thiserror::Error;
use uuid::Uuid;

use crate::iam::set_tenant_context;

const REVISION_COLUMNS: &str = "id, entity_type, entity_id, revision, revision_hash, lifecycle, \
    title, content, parent_revision_id, superseded_by_id, created_at, created_by, approved_at, \
    approved_by, activated_at, organization_id, notes";
const DEPENDENCY_COLUMNS: &str = "id, source_entity_id, source_revision, target_entity_id, \
    target_revision, link_type, created_at";

#[derive(Debug, Error)]
pub enum GovernanceStoreError {
    #[error("database operation failed: {0}")]
    Database(#[from] sqlx::Error),
    #[error("governance serialization failed: {0}")]
    Serialization(#[from] serde_json::Error),
    #[error("governance operation failed: {0:?}")]
    Governance(GovernanceError),
    #[error("invalid governance value in database: {0}")]
    InvalidValue(String),
}

impl From<GovernanceError> for GovernanceStoreError {
    fn from(value: GovernanceError) -> Self {
        Self::Governance(value)
    }
}

#[derive(Clone)]
pub struct GovernanceRepository {
    pool: PgPool,
    organization_id: OrganizationId,
}

impl GovernanceRepository {
    pub fn new(pool: PgPool, organization_id: OrganizationId) -> Self {
        Self {
            pool,
            organization_id,
        }
    }

    pub async fn create_revision(
        &self,
        revision: GovernanceRevision,
    ) -> Result<GovernanceRevision, GovernanceStoreError> {
        if revision.organization_id != self.organization_id
            || revision.revision < 1
            || revision.entity_id.trim().is_empty()
            || revision.revision_hash.trim().is_empty()
            || revision.title.trim().is_empty()
            || revision.lifecycle != GovernanceLifecycle::Draft
            || revision.superseded_by_id.is_some()
            || revision.approved_at.is_some()
            || revision.approved_by.is_some()
            || revision.activated_at.is_some()
        {
            return Err(GovernanceError::InvalidRevision.into());
        }
        let mut tx = self.transaction(Some(revision.created_by)).await?;
        sqlx::query("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))")
            .bind(&revision.entity_id)
            .execute(&mut *tx)
            .await?;
        let current: Option<i32> = sqlx::query_scalar(
            "SELECT MAX(revision) FROM governance_revisions WHERE entity_id = $1",
        )
        .bind(&revision.entity_id)
        .fetch_one(&mut *tx)
        .await?;
        let expected =
            current
                .unwrap_or(0)
                .checked_add(1)
                .ok_or(GovernanceStoreError::Governance(
                    GovernanceError::InvalidRevision,
                ))?;
        if revision.revision != expected {
            return Err(GovernanceError::InvalidRevision.into());
        }
        if revision.revision == 1 && revision.parent_revision_id.is_some()
            || revision.revision > 1 && revision.parent_revision_id.is_none()
        {
            return Err(GovernanceError::InvalidRevision.into());
        }
        if let Some(parent_id) = revision.parent_revision_id {
            let parent: Option<(String, i32)> = sqlx::query_as(
                "SELECT entity_id, revision FROM governance_revisions WHERE id = $1",
            )
            .bind(parent_id)
            .fetch_optional(&mut *tx)
            .await?;
            if parent.as_ref() != Some(&(revision.entity_id.clone(), revision.revision - 1)) {
                return Err(GovernanceError::InvalidRevision.into());
            }
        }
        let query = format!(
            "INSERT INTO governance_revisions (id, entity_type, entity_id, revision, revision_hash, \
             lifecycle, title, content, parent_revision_id, superseded_by_id, created_at, created_by, \
             approved_at, approved_by, activated_at, organization_id, notes) \
             VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17) \
             RETURNING {REVISION_COLUMNS}"
        );
        let row = sqlx::query(&query)
            .bind(revision.id)
            .bind(entity_type_to_str(revision.entity_type))
            .bind(&revision.entity_id)
            .bind(revision.revision)
            .bind(&revision.revision_hash)
            .bind(lifecycle_to_str(revision.lifecycle))
            .bind(&revision.title)
            .bind(&revision.content)
            .bind(revision.parent_revision_id)
            .bind(revision.superseded_by_id)
            .bind(revision.created_at)
            .bind(revision.created_by.0)
            .bind(revision.approved_at)
            .bind(revision.approved_by.map(|id| id.0))
            .bind(revision.activated_at)
            .bind(revision.organization_id.0)
            .bind(&revision.notes)
            .fetch_one(&mut *tx)
            .await?;
        let created = row_to_revision(&row)?;
        tx.commit().await?;
        Ok(created)
    }

    pub async fn get_revision(
        &self,
        entity_id: &str,
        revision: i32,
    ) -> Result<GovernanceRevision, GovernanceStoreError> {
        let mut tx = self.transaction(None).await?;
        let query = format!(
            "SELECT {REVISION_COLUMNS} FROM governance_revisions \
             WHERE entity_id = $1 AND revision = $2"
        );
        let row = sqlx::query(&query)
            .bind(entity_id)
            .bind(revision)
            .fetch_optional(&mut *tx)
            .await?
            .ok_or(GovernanceError::NotFound)?;
        let result = row_to_revision(&row)?;
        tx.commit().await?;
        Ok(result)
    }

    pub async fn get_active_revision(
        &self,
        entity_id: &str,
    ) -> Result<Option<GovernanceRevision>, GovernanceStoreError> {
        let mut tx = self.transaction(None).await?;
        let query = format!(
            "SELECT {REVISION_COLUMNS} FROM governance_revisions \
             WHERE entity_id = $1 AND lifecycle = 'active'"
        );
        let row = sqlx::query(&query)
            .bind(entity_id)
            .fetch_optional(&mut *tx)
            .await?;
        let result = row.map(|row| row_to_revision(&row)).transpose()?;
        tx.commit().await?;
        Ok(result)
    }

    pub async fn get_revision_history(
        &self,
        entity_id: &str,
    ) -> Result<Vec<GovernanceRevision>, GovernanceStoreError> {
        let mut tx = self.transaction(None).await?;
        let query = format!(
            "SELECT {REVISION_COLUMNS} FROM governance_revisions \
             WHERE entity_id = $1 ORDER BY revision"
        );
        let rows = sqlx::query(&query)
            .bind(entity_id)
            .fetch_all(&mut *tx)
            .await?;
        let result = rows.iter().map(row_to_revision).collect();
        tx.commit().await?;
        result
    }

    pub async fn transition_lifecycle(
        &self,
        revision_id: Uuid,
        target: GovernanceLifecycle,
        actor_id: PrincipalId,
    ) -> Result<GovernanceRevision, GovernanceStoreError> {
        let mut tx = self.transaction(Some(actor_id)).await?;
        let revision = get_revision_by_id(&mut tx, revision_id, true).await?;
        if revision.lifecycle == GovernanceLifecycle::Active {
            return Err(GovernanceError::AlreadyActive.into());
        }
        if revision.lifecycle == GovernanceLifecycle::Superseded {
            return Err(GovernanceError::AlreadySuperseded.into());
        }
        if !revision.lifecycle.can_transition(&target) {
            return Err(GovernanceError::IllegalTransition {
                from: revision.lifecycle,
                to: target,
            }
            .into());
        }
        if matches!(
            target,
            GovernanceLifecycle::Reviewed
                | GovernanceLifecycle::Approved
                | GovernanceLifecycle::Active
        ) && is_high_risk(revision.entity_type)
            && actor_id == revision.created_by
        {
            return Err(GovernanceError::UnauthorizedActivation.into());
        }
        if target == GovernanceLifecycle::Active {
            let active: Option<Uuid> = sqlx::query_scalar(
                "SELECT id FROM governance_revisions WHERE entity_id = $1 \
                 AND lifecycle = 'active' AND id <> $2",
            )
            .bind(&revision.entity_id)
            .bind(revision.id)
            .fetch_optional(&mut *tx)
            .await?;
            if active.is_some() {
                return Err(GovernanceError::AlreadyActive.into());
            }
        }
        let query = format!(
            "UPDATE governance_revisions SET lifecycle = $2, \
             approved_at = CASE WHEN $2 = 'approved' THEN now() ELSE approved_at END, \
             approved_by = CASE WHEN $2 = 'approved' THEN $3 ELSE approved_by END, \
             activated_at = CASE WHEN $2 = 'active' THEN now() ELSE activated_at END \
             WHERE id = $1 RETURNING {REVISION_COLUMNS}"
        );
        let row = sqlx::query(&query)
            .bind(revision_id)
            .bind(lifecycle_to_str(target))
            .bind(actor_id.0)
            .fetch_one(&mut *tx)
            .await?;
        let updated = row_to_revision(&row)?;
        tx.commit().await?;
        Ok(updated)
    }

    pub async fn supersede_revision(
        &self,
        old_id: Uuid,
        new_id: Uuid,
        actor_id: PrincipalId,
    ) -> Result<(), GovernanceStoreError> {
        let mut tx = self.transaction(Some(actor_id)).await?;
        let old = get_revision_by_id(&mut tx, old_id, true).await?;
        let new = get_revision_by_id(&mut tx, new_id, true).await?;
        if old.lifecycle == GovernanceLifecycle::Superseded {
            return Err(GovernanceError::AlreadySuperseded.into());
        }
        if old.lifecycle != GovernanceLifecycle::Active
            || new.lifecycle != GovernanceLifecycle::Approved
            || old.entity_id != new.entity_id
            || old.entity_type != new.entity_type
            || new.revision != old.revision + 1
            || new.parent_revision_id != Some(old.id)
        {
            return Err(GovernanceError::InvalidRevision.into());
        }
        if is_high_risk(new.entity_type) && actor_id == new.created_by {
            return Err(GovernanceError::UnauthorizedSupersession.into());
        }
        sqlx::query(
            "UPDATE governance_revisions SET lifecycle = 'superseded', superseded_by_id = $2 \
             WHERE id = $1",
        )
        .bind(old.id)
        .bind(new.id)
        .execute(&mut *tx)
        .await?;
        sqlx::query(
            "UPDATE governance_revisions SET lifecycle = 'active', activated_at = now() WHERE id = $1",
        )
        .bind(new.id)
        .execute(&mut *tx)
        .await?;
        mark_evidence_stale_in_tx(&mut tx, &old.entity_id, old.revision, self.organization_id)
            .await?;
        tx.commit().await?;
        Ok(())
    }

    pub async fn add_dependency(&self, link: DependencyLink) -> Result<(), GovernanceStoreError> {
        if link.source_entity_id == link.target_entity_id
            && link.source_revision == link.target_revision
        {
            return Err(GovernanceError::CircularDependency.into());
        }
        let mut tx = self.transaction(None).await?;
        for (entity_id, revision) in [
            (&link.source_entity_id, link.source_revision),
            (&link.target_entity_id, link.target_revision),
        ] {
            let exists: bool = sqlx::query_scalar(
                "SELECT EXISTS(SELECT 1 FROM governance_revisions \
                 WHERE entity_id = $1 AND revision = $2)",
            )
            .bind(entity_id)
            .bind(revision)
            .fetch_one(&mut *tx)
            .await?;
            if !exists {
                return Err(GovernanceError::OrphanedDependency.into());
            }
        }
        let links = all_dependencies(&mut tx).await?;
        if path_exists(
            &links,
            (&link.target_entity_id, link.target_revision),
            (&link.source_entity_id, link.source_revision),
        ) {
            return Err(GovernanceError::CircularDependency.into());
        }
        sqlx::query(
            "INSERT INTO governance_dependencies \
             (id, source_entity_id, source_revision, target_entity_id, target_revision, link_type, \
              created_at, organization_id) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
        )
        .bind(link.id)
        .bind(&link.source_entity_id)
        .bind(link.source_revision)
        .bind(&link.target_entity_id)
        .bind(link.target_revision)
        .bind(dependency_type_to_str(link.link_type))
        .bind(link.created_at)
        .bind(self.organization_id.0)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        Ok(())
    }

    pub async fn get_dependencies(
        &self,
        entity_id: &str,
        revision: i32,
    ) -> Result<Vec<DependencyLink>, GovernanceStoreError> {
        self.query_links("source_entity_id", entity_id, revision)
            .await
    }

    pub async fn get_dependents(
        &self,
        entity_id: &str,
        revision: i32,
    ) -> Result<Vec<DependencyLink>, GovernanceStoreError> {
        self.query_links("target_entity_id", entity_id, revision)
            .await
    }

    pub async fn analyze_impact(
        &self,
        entity_id: &str,
        revision: i32,
    ) -> Result<ImpactAnalysis, GovernanceStoreError> {
        let mut tx = self.transaction(None).await?;
        let revisions = all_revisions(&mut tx).await?;
        if !revisions
            .iter()
            .any(|item| item.entity_id == entity_id && item.revision == revision)
        {
            return Err(GovernanceError::NotFound.into());
        }
        let links = all_dependencies(&mut tx).await?;
        let direct_dependents: Vec<_> = links
            .iter()
            .filter(|link| link.target_entity_id == entity_id && link.target_revision == revision)
            .cloned()
            .collect();
        let direct_ids: HashSet<_> = direct_dependents.iter().map(|link| link.id).collect();
        let mut queue: VecDeque<(String, i32)> = direct_dependents
            .iter()
            .map(|link| (link.source_entity_id.clone(), link.source_revision))
            .collect();
        let mut visited = HashSet::from([(entity_id.to_owned(), revision)]);
        let mut transitive_dependents = Vec::new();
        while let Some(node) = queue.pop_front() {
            if !visited.insert(node.clone()) {
                continue;
            }
            for link in links
                .iter()
                .filter(|link| link.target_entity_id == node.0 && link.target_revision == node.1)
            {
                if !direct_ids.contains(&link.id) {
                    transitive_dependents.push(link.clone());
                }
                queue.push_back((link.source_entity_id.clone(), link.source_revision));
            }
        }
        let affected_nodes: HashSet<_> = direct_dependents
            .iter()
            .chain(transitive_dependents.iter())
            .map(|link| (link.source_entity_id.clone(), link.source_revision))
            .collect();
        let mut affected_evidence = Vec::new();
        let mut affected_work_orders = Vec::new();
        let mut orphaned_tests = Vec::new();
        for affected in &affected_nodes {
            let Some(item) = revisions
                .iter()
                .find(|item| item.entity_id == affected.0 && item.revision == affected.1)
            else {
                continue;
            };
            match item.entity_type {
                GovernanceEntityType::EvidenceRequirement => {
                    affected_evidence.push(item.entity_id.clone());
                }
                GovernanceEntityType::WorkOrder => {
                    affected_work_orders.push(item.entity_id.clone())
                }
                GovernanceEntityType::TestHarness => {
                    let has_active_requirement = links.iter().any(|link| {
                        link.source_entity_id == item.entity_id
                            && link.source_revision == item.revision
                            && link.link_type == DependencyType::Tests
                            && revisions.iter().any(|target| {
                                target.entity_id == link.target_entity_id
                                    && target.revision == link.target_revision
                                    && target.entity_type == GovernanceEntityType::Requirement
                                    && target.lifecycle == GovernanceLifecycle::Active
                            })
                    });
                    if !has_active_requirement {
                        orphaned_tests.push(item.entity_id.clone());
                    }
                }
                _ => {}
            }
            if links.iter().any(|link| {
                link.source_entity_id == item.entity_id
                    && link.source_revision == item.revision
                    && link.link_type == DependencyType::EvidenceFor
            }) {
                affected_evidence.push(item.entity_id.clone());
            }
        }
        affected_evidence.sort();
        affected_evidence.dedup();
        affected_work_orders.sort();
        affected_work_orders.dedup();
        orphaned_tests.sort();
        orphaned_tests.dedup();
        tx.commit().await?;
        Ok(ImpactAnalysis {
            entity_id: entity_id.into(),
            revision,
            direct_dependents,
            transitive_dependents,
            affected_evidence,
            affected_work_orders,
            orphaned_tests,
        })
    }

    pub async fn detect_cycles(
        &self,
        entity_id: &str,
    ) -> Result<Vec<Vec<String>>, GovernanceStoreError> {
        let mut tx = self.transaction(None).await?;
        let links = all_dependencies(&mut tx).await?;
        tx.commit().await?;
        Ok(find_cycles(&links, entity_id))
    }

    pub async fn pin_run(
        &self,
        run_id: RunId,
        revisions: HashMap<String, (i32, Uuid)>,
    ) -> Result<RunPin, GovernanceStoreError> {
        if revisions.is_empty() {
            return Err(GovernanceError::InvalidRevision.into());
        }
        let mut tx = self.transaction(None).await?;
        for (entity_id, (revision, revision_id)) in &revisions {
            let valid: bool = sqlx::query_scalar(
                "SELECT EXISTS(SELECT 1 FROM governance_revisions \
                 WHERE entity_id = $1 AND revision = $2 AND id = $3)",
            )
            .bind(entity_id)
            .bind(revision)
            .bind(revision_id)
            .fetch_one(&mut *tx)
            .await?;
            if !valid {
                return Err(GovernanceError::InvalidRevision.into());
            }
        }
        let pinned_at = chrono::Utc::now();
        sqlx::query(
            "INSERT INTO run_pins (run_id, pinned_revisions, pinned_at, organization_id) \
             VALUES ($1,$2,$3,$4)",
        )
        .bind(run_id.0)
        .bind(serde_json::to_value(&revisions)?)
        .bind(pinned_at)
        .bind(self.organization_id.0)
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        Ok(RunPin {
            run_id,
            pinned_revisions: revisions,
            pinned_at,
        })
    }

    pub async fn get_run_pin(&self, run_id: RunId) -> Result<Option<RunPin>, GovernanceStoreError> {
        let mut tx = self.transaction(None).await?;
        let row = sqlx::query("SELECT pinned_revisions, pinned_at FROM run_pins WHERE run_id = $1")
            .bind(run_id.0)
            .fetch_optional(&mut *tx)
            .await?;
        let result = row
            .map(|row| {
                Ok::<RunPin, GovernanceStoreError>(RunPin {
                    run_id,
                    pinned_revisions: serde_json::from_value(row.try_get("pinned_revisions")?)?,
                    pinned_at: row.try_get("pinned_at")?,
                })
            })
            .transpose()?;
        tx.commit().await?;
        Ok(result)
    }

    pub async fn validate_pin(&self, run_id: RunId) -> Result<bool, GovernanceStoreError> {
        let Some(pin) = self.get_run_pin(run_id).await? else {
            return Ok(false);
        };
        let mut tx = self.transaction(None).await?;
        for (entity_id, (revision, revision_id)) in pin.pinned_revisions {
            let valid: bool = sqlx::query_scalar(
                "SELECT EXISTS(SELECT 1 FROM governance_revisions WHERE entity_id = $1 \
                 AND revision = $2 AND id = $3 AND lifecycle <> 'superseded')",
            )
            .bind(entity_id)
            .bind(revision)
            .bind(revision_id)
            .fetch_one(&mut *tx)
            .await?;
            if !valid {
                tx.commit().await?;
                return Ok(false);
            }
        }
        tx.commit().await?;
        Ok(true)
    }

    pub async fn mark_evidence_stale(
        &self,
        entity_id: &str,
        old_revision: i32,
    ) -> Result<i32, GovernanceStoreError> {
        let mut tx = self.transaction(None).await?;
        let count =
            mark_evidence_stale_in_tx(&mut tx, entity_id, old_revision, self.organization_id)
                .await?;
        tx.commit().await?;
        Ok(count)
    }

    pub async fn get_stale_evidence(
        &self,
        org_id: OrganizationId,
    ) -> Result<Vec<String>, GovernanceStoreError> {
        if org_id != self.organization_id {
            return Err(GovernanceError::NotFound.into());
        }
        let mut tx = self.transaction(None).await?;
        let values = sqlx::query_scalar(
            "SELECT DISTINCT evidence_entity_id FROM governance_stale_evidence \
             WHERE organization_id = $1 ORDER BY evidence_entity_id",
        )
        .bind(org_id.0)
        .fetch_all(&mut *tx)
        .await?;
        tx.commit().await?;
        Ok(values)
    }

    async fn query_links(
        &self,
        column: &str,
        entity_id: &str,
        revision: i32,
    ) -> Result<Vec<DependencyLink>, GovernanceStoreError> {
        let mut tx = self.transaction(None).await?;
        let query = format!(
            "SELECT {DEPENDENCY_COLUMNS} FROM governance_dependencies \
             WHERE {column} = $1 AND {} = $2 ORDER BY created_at, id",
            if column == "source_entity_id" {
                "source_revision"
            } else {
                "target_revision"
            }
        );
        let rows = sqlx::query(&query)
            .bind(entity_id)
            .bind(revision)
            .fetch_all(&mut *tx)
            .await?;
        let result = rows.iter().map(row_to_dependency).collect();
        tx.commit().await?;
        result
    }

    async fn transaction(
        &self,
        principal_id: Option<PrincipalId>,
    ) -> Result<Transaction<'_, Postgres>, GovernanceStoreError> {
        let mut tx = self.pool.begin().await?;
        set_tenant_context(&mut tx, &self.organization_id.0.to_string(), principal_id).await?;
        Ok(tx)
    }
}

async fn get_revision_by_id(
    tx: &mut Transaction<'_, Postgres>,
    revision_id: Uuid,
    lock: bool,
) -> Result<GovernanceRevision, GovernanceStoreError> {
    let query = format!(
        "SELECT {REVISION_COLUMNS} FROM governance_revisions WHERE id = $1{}",
        if lock { " FOR UPDATE" } else { "" }
    );
    let row = sqlx::query(&query)
        .bind(revision_id)
        .fetch_optional(&mut **tx)
        .await?
        .ok_or(GovernanceError::NotFound)?;
    row_to_revision(&row)
}

async fn all_revisions(
    tx: &mut Transaction<'_, Postgres>,
) -> Result<Vec<GovernanceRevision>, GovernanceStoreError> {
    let query = format!("SELECT {REVISION_COLUMNS} FROM governance_revisions");
    let rows = sqlx::query(&query).fetch_all(&mut **tx).await?;
    rows.iter().map(row_to_revision).collect()
}

async fn all_dependencies(
    tx: &mut Transaction<'_, Postgres>,
) -> Result<Vec<DependencyLink>, GovernanceStoreError> {
    let query = format!("SELECT {DEPENDENCY_COLUMNS} FROM governance_dependencies");
    let rows = sqlx::query(&query).fetch_all(&mut **tx).await?;
    rows.iter().map(row_to_dependency).collect()
}

async fn mark_evidence_stale_in_tx(
    tx: &mut Transaction<'_, Postgres>,
    entity_id: &str,
    old_revision: i32,
    organization_id: OrganizationId,
) -> Result<i32, GovernanceStoreError> {
    let result = sqlx::query(
        "INSERT INTO governance_stale_evidence \
         (evidence_entity_id, governed_entity_id, governed_revision, organization_id) \
         SELECT DISTINCT source_entity_id, $1, $2, $3 FROM governance_dependencies \
         WHERE target_entity_id = $1 AND target_revision = $2 AND link_type = 'evidence_for' \
         ON CONFLICT DO NOTHING",
    )
    .bind(entity_id)
    .bind(old_revision)
    .bind(organization_id.0)
    .execute(&mut **tx)
    .await?;
    i32::try_from(result.rows_affected())
        .map_err(|_| GovernanceStoreError::InvalidValue("stale evidence count overflow".into()))
}

fn path_exists(links: &[DependencyLink], start: (&str, i32), goal: (&str, i32)) -> bool {
    let mut stack = vec![(start.0.to_owned(), start.1)];
    let mut visited = HashSet::new();
    while let Some(node) = stack.pop() {
        if node.0 == goal.0 && node.1 == goal.1 {
            return true;
        }
        if !visited.insert(node.clone()) {
            continue;
        }
        stack.extend(
            links
                .iter()
                .filter(|link| link.source_entity_id == node.0 && link.source_revision == node.1)
                .map(|link| (link.target_entity_id.clone(), link.target_revision)),
        );
    }
    false
}

fn find_cycles(links: &[DependencyLink], entity_id: &str) -> Vec<Vec<String>> {
    fn visit(
        node: &(String, i32),
        links: &[DependencyLink],
        path: &mut Vec<(String, i32)>,
        cycles: &mut Vec<Vec<String>>,
        filter: &str,
    ) {
        if let Some(position) = path.iter().position(|item| item == node) {
            let mut cycle: Vec<String> = path[position..]
                .iter()
                .map(|(id, revision)| format!("{id}@{revision}"))
                .collect();
            cycle.push(format!("{}@{}", node.0, node.1));
            if path[position..].iter().any(|(id, _)| id == filter) && !cycles.contains(&cycle) {
                cycles.push(cycle);
            }
            return;
        }
        path.push(node.clone());
        for link in links
            .iter()
            .filter(|link| link.source_entity_id == node.0 && link.source_revision == node.1)
        {
            visit(
                &(link.target_entity_id.clone(), link.target_revision),
                links,
                path,
                cycles,
                filter,
            );
        }
        path.pop();
    }

    let mut cycles = Vec::new();
    let starts: HashSet<_> = links
        .iter()
        .filter(|link| link.source_entity_id == entity_id)
        .map(|link| (link.source_entity_id.clone(), link.source_revision))
        .collect();
    for start in starts {
        visit(&start, links, &mut Vec::new(), &mut cycles, entity_id);
    }
    cycles
}

fn row_to_revision(
    row: &sqlx::postgres::PgRow,
) -> Result<GovernanceRevision, GovernanceStoreError> {
    Ok(GovernanceRevision {
        id: row.try_get("id")?,
        entity_type: str_to_entity_type(&row.try_get::<String, _>("entity_type")?)?,
        entity_id: row.try_get("entity_id")?,
        revision: row.try_get("revision")?,
        revision_hash: row.try_get("revision_hash")?,
        lifecycle: str_to_lifecycle(&row.try_get::<String, _>("lifecycle")?)?,
        title: row.try_get("title")?,
        content: row.try_get("content")?,
        parent_revision_id: row.try_get("parent_revision_id")?,
        superseded_by_id: row.try_get("superseded_by_id")?,
        created_at: row.try_get("created_at")?,
        created_by: PrincipalId(row.try_get("created_by")?),
        approved_at: row.try_get("approved_at")?,
        approved_by: row
            .try_get::<Option<Uuid>, _>("approved_by")?
            .map(PrincipalId),
        activated_at: row.try_get("activated_at")?,
        organization_id: OrganizationId(row.try_get("organization_id")?),
        notes: row.try_get("notes")?,
    })
}

fn row_to_dependency(row: &sqlx::postgres::PgRow) -> Result<DependencyLink, GovernanceStoreError> {
    Ok(DependencyLink {
        id: row.try_get("id")?,
        source_entity_id: row.try_get("source_entity_id")?,
        source_revision: row.try_get("source_revision")?,
        target_entity_id: row.try_get("target_entity_id")?,
        target_revision: row.try_get("target_revision")?,
        link_type: str_to_dependency_type(&row.try_get::<String, _>("link_type")?)?,
        created_at: row.try_get("created_at")?,
    })
}

fn is_high_risk(entity_type: GovernanceEntityType) -> bool {
    matches!(
        entity_type,
        GovernanceEntityType::AcceptanceCriterion
            | GovernanceEntityType::Threat
            | GovernanceEntityType::Control
            | GovernanceEntityType::Policy
            | GovernanceEntityType::RiskClassification
            | GovernanceEntityType::EvidenceRequirement
            | GovernanceEntityType::ReleaseBaseline
    )
}

fn entity_type_to_str(value: GovernanceEntityType) -> &'static str {
    match value {
        GovernanceEntityType::ArchitectureDecision => "architecture_decision",
        GovernanceEntityType::Requirement => "requirement",
        GovernanceEntityType::AcceptanceCriterion => "acceptance_criterion",
        GovernanceEntityType::Threat => "threat",
        GovernanceEntityType::Control => "control",
        GovernanceEntityType::TestHarness => "test_harness",
        GovernanceEntityType::Policy => "policy",
        GovernanceEntityType::RiskClassification => "risk_classification",
        GovernanceEntityType::WorkOrder => "work_order",
        GovernanceEntityType::ImplementationArtifact => "implementation_artifact",
        GovernanceEntityType::EvidenceRequirement => "evidence_requirement",
        GovernanceEntityType::ReleaseBaseline => "release_baseline",
        GovernanceEntityType::Component => "component",
    }
}

fn str_to_entity_type(value: &str) -> Result<GovernanceEntityType, GovernanceStoreError> {
    match value {
        "architecture_decision" => Ok(GovernanceEntityType::ArchitectureDecision),
        "requirement" => Ok(GovernanceEntityType::Requirement),
        "acceptance_criterion" => Ok(GovernanceEntityType::AcceptanceCriterion),
        "threat" => Ok(GovernanceEntityType::Threat),
        "control" => Ok(GovernanceEntityType::Control),
        "test_harness" => Ok(GovernanceEntityType::TestHarness),
        "policy" => Ok(GovernanceEntityType::Policy),
        "risk_classification" => Ok(GovernanceEntityType::RiskClassification),
        "work_order" => Ok(GovernanceEntityType::WorkOrder),
        "implementation_artifact" => Ok(GovernanceEntityType::ImplementationArtifact),
        "evidence_requirement" => Ok(GovernanceEntityType::EvidenceRequirement),
        "release_baseline" => Ok(GovernanceEntityType::ReleaseBaseline),
        "component" => Ok(GovernanceEntityType::Component),
        invalid => Err(GovernanceStoreError::InvalidValue(invalid.into())),
    }
}

fn lifecycle_to_str(value: GovernanceLifecycle) -> &'static str {
    match value {
        GovernanceLifecycle::Draft => "draft",
        GovernanceLifecycle::Proposed => "proposed",
        GovernanceLifecycle::Reviewed => "reviewed",
        GovernanceLifecycle::Approved => "approved",
        GovernanceLifecycle::Active => "active",
        GovernanceLifecycle::Superseded => "superseded",
        GovernanceLifecycle::Rejected => "rejected",
    }
}

fn str_to_lifecycle(value: &str) -> Result<GovernanceLifecycle, GovernanceStoreError> {
    match value {
        "draft" => Ok(GovernanceLifecycle::Draft),
        "proposed" => Ok(GovernanceLifecycle::Proposed),
        "reviewed" => Ok(GovernanceLifecycle::Reviewed),
        "approved" => Ok(GovernanceLifecycle::Approved),
        "active" => Ok(GovernanceLifecycle::Active),
        "superseded" => Ok(GovernanceLifecycle::Superseded),
        "rejected" => Ok(GovernanceLifecycle::Rejected),
        invalid => Err(GovernanceStoreError::InvalidValue(invalid.into())),
    }
}

fn dependency_type_to_str(value: DependencyType) -> &'static str {
    match value {
        DependencyType::Requires => "requires",
        DependencyType::Implements => "implements",
        DependencyType::Tests => "tests",
        DependencyType::Verifies => "verifies",
        DependencyType::Blocks => "blocks",
        DependencyType::Supersedes => "supersedes",
        DependencyType::EvidenceFor => "evidence_for",
        DependencyType::ApprovedBy => "approved_by",
    }
}

fn str_to_dependency_type(value: &str) -> Result<DependencyType, GovernanceStoreError> {
    match value {
        "requires" => Ok(DependencyType::Requires),
        "implements" => Ok(DependencyType::Implements),
        "tests" => Ok(DependencyType::Tests),
        "verifies" => Ok(DependencyType::Verifies),
        "blocks" => Ok(DependencyType::Blocks),
        "supersedes" => Ok(DependencyType::Supersedes),
        "evidence_for" => Ok(DependencyType::EvidenceFor),
        "approved_by" => Ok(DependencyType::ApprovedBy),
        invalid => Err(GovernanceStoreError::InvalidValue(invalid.into())),
    }
}
