use std::collections::HashMap;

use chrono::Utc;
use noerelay_core::governance::*;
use noerelay_core::{OrganizationId, PrincipalId, RunId};
use noerelay_store::{GovernanceRepository, GovernanceStoreError};
use serde_json::json;
use sqlx::PgPool;
use uuid::Uuid;

async fn repository() -> (GovernanceRepository, PgPool, OrganizationId) {
    let url = std::env::var("DATABASE_URL").expect("DATABASE_URL required for ignored test");
    let pool = PgPool::connect(&url).await.unwrap();
    let org = OrganizationId(Uuid::new_v4());
    (GovernanceRepository::new(pool.clone(), org), pool, org)
}

fn revision(
    org: OrganizationId,
    entity_id: &str,
    number: i32,
    parent: Option<Uuid>,
    entity_type: GovernanceEntityType,
    lifecycle: GovernanceLifecycle,
    creator: PrincipalId,
) -> GovernanceRevision {
    GovernanceRevision {
        id: Uuid::new_v4(),
        entity_type,
        entity_id: entity_id.into(),
        revision: number,
        revision_hash: format!("sha256:{entity_id}:{number}"),
        lifecycle,
        title: format!("{entity_id} revision {number}"),
        content: json!({"entity": entity_id, "revision": number}),
        parent_revision_id: parent,
        superseded_by_id: None,
        created_at: Utc::now(),
        created_by: creator,
        approved_at: None,
        approved_by: None,
        activated_at: None,
        organization_id: org,
        notes: "integration test".into(),
    }
}

fn link(
    source: &GovernanceRevision,
    target: &GovernanceRevision,
    kind: DependencyType,
) -> DependencyLink {
    DependencyLink {
        id: Uuid::new_v4(),
        source_entity_id: source.entity_id.clone(),
        source_revision: source.revision,
        target_entity_id: target.entity_id.clone(),
        target_revision: target.revision,
        link_type: kind,
        created_at: Utc::now(),
    }
}

// Keep activation explicit without exposing a repository-only test API.
async fn activate_entity(
    repo: &GovernanceRepository,
    entity_id: &str,
    revision_id: Uuid,
    actor: PrincipalId,
) -> GovernanceRevision {
    for state in [
        GovernanceLifecycle::Proposed,
        GovernanceLifecycle::Reviewed,
        GovernanceLifecycle::Approved,
        GovernanceLifecycle::Active,
    ] {
        repo.transition_lifecycle(revision_id, state, actor)
            .await
            .unwrap();
    }
    repo.get_active_revision(entity_id).await.unwrap().unwrap()
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn creates_and_gets_revision() {
    let (repo, _, org) = repository().await;
    let expected = revision(
        org,
        &format!("REQ-{}", Uuid::new_v4()),
        1,
        None,
        GovernanceEntityType::Requirement,
        GovernanceLifecycle::Draft,
        PrincipalId(Uuid::new_v4()),
    );
    repo.create_revision(expected.clone()).await.unwrap();
    assert_eq!(
        repo.get_revision(&expected.entity_id, 1).await.unwrap(),
        expected
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn returns_revision_history_in_number_order() {
    let (repo, _, org) = repository().await;
    let creator = PrincipalId(Uuid::new_v4());
    let entity = format!("REQ-{}", Uuid::new_v4());
    let first = revision(
        org,
        &entity,
        1,
        None,
        GovernanceEntityType::Requirement,
        GovernanceLifecycle::Draft,
        creator,
    );
    repo.create_revision(first.clone()).await.unwrap();
    let second = revision(
        org,
        &entity,
        2,
        Some(first.id),
        GovernanceEntityType::Requirement,
        GovernanceLifecycle::Draft,
        creator,
    );
    repo.create_revision(second).await.unwrap();
    let history = repo.get_revision_history(&entity).await.unwrap();
    assert_eq!(
        history.iter().map(|item| item.revision).collect::<Vec<_>>(),
        vec![1, 2]
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn rejects_non_initial_first_revision() {
    let (repo, _, org) = repository().await;
    let invalid = revision(
        org,
        &format!("REQ-{}", Uuid::new_v4()),
        2,
        None,
        GovernanceEntityType::Requirement,
        GovernanceLifecycle::Draft,
        PrincipalId(Uuid::new_v4()),
    );
    assert!(matches!(
        repo.create_revision(invalid).await,
        Err(GovernanceStoreError::Governance(
            GovernanceError::InvalidRevision
        ))
    ));
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn rejects_wrong_parent_revision() {
    let (repo, _, org) = repository().await;
    let creator = PrincipalId(Uuid::new_v4());
    let entity = format!("REQ-{}", Uuid::new_v4());
    let first = revision(
        org,
        &entity,
        1,
        None,
        GovernanceEntityType::Requirement,
        GovernanceLifecycle::Draft,
        creator,
    );
    repo.create_revision(first).await.unwrap();
    let invalid = revision(
        org,
        &entity,
        2,
        Some(Uuid::new_v4()),
        GovernanceEntityType::Requirement,
        GovernanceLifecycle::Draft,
        creator,
    );
    assert!(matches!(
        repo.create_revision(invalid).await,
        Err(GovernanceStoreError::Governance(
            GovernanceError::InvalidRevision
        ))
    ));
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn active_lookup_never_returns_newer_draft() {
    let (repo, _, org) = repository().await;
    let creator = PrincipalId(Uuid::new_v4());
    let actor = PrincipalId(Uuid::new_v4());
    let entity = format!("REQ-{}", Uuid::new_v4());
    let first = revision(
        org,
        &entity,
        1,
        None,
        GovernanceEntityType::Requirement,
        GovernanceLifecycle::Draft,
        creator,
    );
    repo.create_revision(first.clone()).await.unwrap();
    activate_entity(&repo, &entity, first.id, actor).await;
    let draft = revision(
        org,
        &entity,
        2,
        Some(first.id),
        GovernanceEntityType::Requirement,
        GovernanceLifecycle::Draft,
        creator,
    );
    repo.create_revision(draft).await.unwrap();
    assert_eq!(
        repo.get_active_revision(&entity)
            .await
            .unwrap()
            .unwrap()
            .revision,
        1
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn transitions_full_lifecycle_to_active() {
    let (repo, _, org) = repository().await;
    let entity = format!("REQ-{}", Uuid::new_v4());
    let item = revision(
        org,
        &entity,
        1,
        None,
        GovernanceEntityType::Requirement,
        GovernanceLifecycle::Draft,
        PrincipalId(Uuid::new_v4()),
    );
    repo.create_revision(item.clone()).await.unwrap();
    let active = activate_entity(&repo, &entity, item.id, PrincipalId(Uuid::new_v4())).await;
    assert_eq!(active.lifecycle, GovernanceLifecycle::Active);
    assert!(active.activated_at.is_some());
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn rejects_illegal_lifecycle_transition() {
    let (repo, _, org) = repository().await;
    let item = revision(
        org,
        &format!("REQ-{}", Uuid::new_v4()),
        1,
        None,
        GovernanceEntityType::Requirement,
        GovernanceLifecycle::Draft,
        PrincipalId(Uuid::new_v4()),
    );
    repo.create_revision(item.clone()).await.unwrap();
    assert!(matches!(
        repo.transition_lifecycle(
            item.id,
            GovernanceLifecycle::Active,
            PrincipalId(Uuid::new_v4())
        )
        .await,
        Err(GovernanceStoreError::Governance(
            GovernanceError::IllegalTransition { .. }
        ))
    ));
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn rejected_revision_is_terminal() {
    let (repo, _, org) = repository().await;
    let item = revision(
        org,
        &format!("REQ-{}", Uuid::new_v4()),
        1,
        None,
        GovernanceEntityType::Requirement,
        GovernanceLifecycle::Draft,
        PrincipalId(Uuid::new_v4()),
    );
    repo.create_revision(item.clone()).await.unwrap();
    repo.transition_lifecycle(
        item.id,
        GovernanceLifecycle::Rejected,
        PrincipalId(Uuid::new_v4()),
    )
    .await
    .unwrap();
    assert!(
        repo.transition_lifecycle(
            item.id,
            GovernanceLifecycle::Proposed,
            PrincipalId(Uuid::new_v4())
        )
        .await
        .is_err()
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn creator_cannot_activate_high_risk_acceptance_revision() {
    let (repo, _, org) = repository().await;
    let creator = PrincipalId(Uuid::new_v4());
    let item = revision(
        org,
        &format!("AC-{}", Uuid::new_v4()),
        1,
        None,
        GovernanceEntityType::AcceptanceCriterion,
        GovernanceLifecycle::Draft,
        creator,
    );
    repo.create_revision(item.clone()).await.unwrap();
    repo.transition_lifecycle(item.id, GovernanceLifecycle::Proposed, creator)
        .await
        .unwrap();
    let reviewer = PrincipalId(Uuid::new_v4());
    repo.transition_lifecycle(item.id, GovernanceLifecycle::Reviewed, reviewer)
        .await
        .unwrap();
    repo.transition_lifecycle(item.id, GovernanceLifecycle::Approved, reviewer)
        .await
        .unwrap();
    assert!(matches!(
        repo.transition_lifecycle(item.id, GovernanceLifecycle::Active, creator)
            .await,
        Err(GovernanceStoreError::Governance(
            GovernanceError::UnauthorizedActivation
        ))
    ));
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn supersedes_old_revision_and_activates_new() {
    let (repo, _, org) = repository().await;
    let creator = PrincipalId(Uuid::new_v4());
    let actor = PrincipalId(Uuid::new_v4());
    let entity = format!("REQ-{}", Uuid::new_v4());
    let old = revision(
        org,
        &entity,
        1,
        None,
        GovernanceEntityType::Requirement,
        GovernanceLifecycle::Draft,
        creator,
    );
    repo.create_revision(old.clone()).await.unwrap();
    activate_entity(&repo, &entity, old.id, actor).await;
    let new = revision(
        org,
        &entity,
        2,
        Some(old.id),
        GovernanceEntityType::Requirement,
        GovernanceLifecycle::Draft,
        creator,
    );
    repo.create_revision(new.clone()).await.unwrap();
    for state in [
        GovernanceLifecycle::Proposed,
        GovernanceLifecycle::Reviewed,
        GovernanceLifecycle::Approved,
    ] {
        repo.transition_lifecycle(new.id, state, actor)
            .await
            .unwrap();
    }
    repo.supersede_revision(old.id, new.id, actor)
        .await
        .unwrap();
    assert_eq!(
        repo.get_revision(&entity, 1).await.unwrap().lifecycle,
        GovernanceLifecycle::Superseded
    );
    assert_eq!(
        repo.get_active_revision(&entity).await.unwrap().unwrap().id,
        new.id
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn adds_and_gets_dependencies() {
    let (repo, _, org) = repository().await;
    let creator = PrincipalId(Uuid::new_v4());
    let requirement = revision(
        org,
        &format!("REQ-{}", Uuid::new_v4()),
        1,
        None,
        GovernanceEntityType::Requirement,
        GovernanceLifecycle::Draft,
        creator,
    );
    let test = revision(
        org,
        &format!("TEST-{}", Uuid::new_v4()),
        1,
        None,
        GovernanceEntityType::TestHarness,
        GovernanceLifecycle::Draft,
        creator,
    );
    repo.create_revision(requirement.clone()).await.unwrap();
    repo.create_revision(test.clone()).await.unwrap();
    repo.add_dependency(link(&test, &requirement, DependencyType::Tests))
        .await
        .unwrap();
    assert_eq!(
        repo.get_dependencies(&test.entity_id, 1)
            .await
            .unwrap()
            .len(),
        1
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn gets_reverse_dependents() {
    let (repo, _, org) = repository().await;
    let creator = PrincipalId(Uuid::new_v4());
    let target = revision(
        org,
        &format!("REQ-{}", Uuid::new_v4()),
        1,
        None,
        GovernanceEntityType::Requirement,
        GovernanceLifecycle::Draft,
        creator,
    );
    let source = revision(
        org,
        &format!("WO-{}", Uuid::new_v4()),
        1,
        None,
        GovernanceEntityType::WorkOrder,
        GovernanceLifecycle::Draft,
        creator,
    );
    repo.create_revision(target.clone()).await.unwrap();
    repo.create_revision(source.clone()).await.unwrap();
    repo.add_dependency(link(&source, &target, DependencyType::Implements))
        .await
        .unwrap();
    assert_eq!(
        repo.get_dependents(&target.entity_id, 1).await.unwrap()[0].source_entity_id,
        source.entity_id
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn rejects_orphaned_dependency() {
    let (repo, _, _) = repository().await;
    let fake = GovernanceRevision {
        organization_id: OrganizationId(Uuid::new_v4()),
        ..revision(
            OrganizationId(Uuid::new_v4()),
            "FAKE",
            1,
            None,
            GovernanceEntityType::Requirement,
            GovernanceLifecycle::Draft,
            PrincipalId(Uuid::new_v4()),
        )
    };
    assert!(matches!(
        repo.add_dependency(link(&fake, &fake, DependencyType::Requires))
            .await,
        Err(GovernanceStoreError::Governance(_))
    ));
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn rejects_circular_dependency() {
    let (repo, _, org) = repository().await;
    let creator = PrincipalId(Uuid::new_v4());
    let a = revision(
        org,
        &format!("A-{}", Uuid::new_v4()),
        1,
        None,
        GovernanceEntityType::Component,
        GovernanceLifecycle::Draft,
        creator,
    );
    let b = revision(
        org,
        &format!("B-{}", Uuid::new_v4()),
        1,
        None,
        GovernanceEntityType::Component,
        GovernanceLifecycle::Draft,
        creator,
    );
    repo.create_revision(a.clone()).await.unwrap();
    repo.create_revision(b.clone()).await.unwrap();
    repo.add_dependency(link(&a, &b, DependencyType::Requires))
        .await
        .unwrap();
    assert!(matches!(
        repo.add_dependency(link(&b, &a, DependencyType::Requires))
            .await,
        Err(GovernanceStoreError::Governance(
            GovernanceError::CircularDependency
        ))
    ));
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn impact_analysis_finds_direct_work_order() {
    let (repo, _, org) = repository().await;
    let creator = PrincipalId(Uuid::new_v4());
    let req = revision(
        org,
        &format!("REQ-{}", Uuid::new_v4()),
        1,
        None,
        GovernanceEntityType::Requirement,
        GovernanceLifecycle::Draft,
        creator,
    );
    let work = revision(
        org,
        &format!("WO-{}", Uuid::new_v4()),
        1,
        None,
        GovernanceEntityType::WorkOrder,
        GovernanceLifecycle::Draft,
        creator,
    );
    repo.create_revision(req.clone()).await.unwrap();
    repo.create_revision(work.clone()).await.unwrap();
    repo.add_dependency(link(&work, &req, DependencyType::Implements))
        .await
        .unwrap();
    let impact = repo.analyze_impact(&req.entity_id, 1).await.unwrap();
    assert_eq!(impact.direct_dependents.len(), 1);
    assert_eq!(impact.affected_work_orders, vec![work.entity_id]);
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn impact_analysis_finds_transitive_dependents() {
    let (repo, _, org) = repository().await;
    let creator = PrincipalId(Uuid::new_v4());
    let req = revision(
        org,
        &format!("REQ-{}", Uuid::new_v4()),
        1,
        None,
        GovernanceEntityType::Requirement,
        GovernanceLifecycle::Draft,
        creator,
    );
    let work = revision(
        org,
        &format!("WO-{}", Uuid::new_v4()),
        1,
        None,
        GovernanceEntityType::WorkOrder,
        GovernanceLifecycle::Draft,
        creator,
    );
    let test = revision(
        org,
        &format!("TEST-{}", Uuid::new_v4()),
        1,
        None,
        GovernanceEntityType::TestHarness,
        GovernanceLifecycle::Draft,
        creator,
    );
    for item in [&req, &work, &test] {
        repo.create_revision(item.clone()).await.unwrap();
    }
    repo.add_dependency(link(&work, &req, DependencyType::Implements))
        .await
        .unwrap();
    repo.add_dependency(link(&test, &work, DependencyType::Verifies))
        .await
        .unwrap();
    assert_eq!(
        repo.analyze_impact(&req.entity_id, 1)
            .await
            .unwrap()
            .transitive_dependents
            .len(),
        1
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn supersession_marks_requirement_evidence_stale() {
    let (repo, _, org) = repository().await;
    let creator = PrincipalId(Uuid::new_v4());
    let actor = PrincipalId(Uuid::new_v4());
    let entity = format!("REQ-{}", Uuid::new_v4());
    let old = revision(
        org,
        &entity,
        1,
        None,
        GovernanceEntityType::Requirement,
        GovernanceLifecycle::Draft,
        creator,
    );
    let evidence = revision(
        org,
        &format!("EVID-{}", Uuid::new_v4()),
        1,
        None,
        GovernanceEntityType::EvidenceRequirement,
        GovernanceLifecycle::Draft,
        creator,
    );
    repo.create_revision(old.clone()).await.unwrap();
    repo.create_revision(evidence.clone()).await.unwrap();
    repo.add_dependency(link(&evidence, &old, DependencyType::EvidenceFor))
        .await
        .unwrap();
    activate_entity(&repo, &entity, old.id, actor).await;
    let new = revision(
        org,
        &entity,
        2,
        Some(old.id),
        GovernanceEntityType::Requirement,
        GovernanceLifecycle::Draft,
        creator,
    );
    repo.create_revision(new.clone()).await.unwrap();
    for state in [
        GovernanceLifecycle::Proposed,
        GovernanceLifecycle::Reviewed,
        GovernanceLifecycle::Approved,
    ] {
        repo.transition_lifecycle(new.id, state, actor)
            .await
            .unwrap();
    }
    repo.supersede_revision(old.id, new.id, actor)
        .await
        .unwrap();
    assert_eq!(
        repo.get_stale_evidence(org).await.unwrap(),
        vec![evidence.entity_id]
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn pins_and_validates_exact_run_revisions() {
    let (repo, pool, org) = repository().await;
    let creator = PrincipalId(Uuid::new_v4());
    let item = revision(
        org,
        &format!("REQ-{}", Uuid::new_v4()),
        1,
        None,
        GovernanceEntityType::Requirement,
        GovernanceLifecycle::Draft,
        creator,
    );
    repo.create_revision(item.clone()).await.unwrap();
    let org_text = org.0.to_string();
    sqlx::query("INSERT INTO organizations (organization_id) VALUES ($1) ON CONFLICT DO NOTHING")
        .bind(&org_text)
        .execute(&pool)
        .await
        .unwrap();
    sqlx::query("INSERT INTO principals (principal_id, organization_id, principal_type, external_id, display_name) VALUES ($1,$2,'human',$3,'Governance test')").bind(creator.0).bind(&org_text).bind(Uuid::new_v4().to_string()).execute(&pool).await.unwrap();
    let run_id = RunId(Uuid::new_v4());
    sqlx::query("INSERT INTO runs (id, organization_id, principal_id, contract_hash, policy_revision) VALUES ($1,$2,$3,'test-contract','test-policy')").bind(run_id.0).bind(&org_text).bind(creator.0).execute(&pool).await.unwrap();
    let pin = repo
        .pin_run(
            run_id,
            HashMap::from([(item.entity_id.clone(), (1, item.id))]),
        )
        .await
        .unwrap();
    assert_eq!(repo.get_run_pin(run_id).await.unwrap(), Some(pin));
    assert!(repo.validate_pin(run_id).await.unwrap());
}
