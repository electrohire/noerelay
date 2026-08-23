use std::collections::HashMap;

use chrono::Utc;
use noerelay_core::governance::*;
use noerelay_core::{OrganizationId, PrincipalId, RunId};
use serde_json::json;
use uuid::Uuid;

fn revision() -> GovernanceRevision {
    GovernanceRevision {
        id: Uuid::new_v4(),
        entity_type: GovernanceEntityType::Requirement,
        entity_id: "NR-SPEC-001".into(),
        revision: 1,
        revision_hash: "sha256:abc".into(),
        lifecycle: GovernanceLifecycle::Draft,
        title: "Immutable revision pinning".into(),
        content: json!({"shall": "pin exact revisions"}),
        parent_revision_id: None,
        superseded_by_id: None,
        created_at: Utc::now(),
        created_by: PrincipalId(Uuid::new_v4()),
        approved_at: None,
        approved_by: None,
        activated_at: None,
        organization_id: OrganizationId(Uuid::new_v4()),
        notes: String::new(),
    }
}

#[test]
fn draft_transitions_to_proposed_or_rejected() {
    assert_eq!(
        GovernanceLifecycle::Draft.legal_transitions(),
        &[GovernanceLifecycle::Proposed, GovernanceLifecycle::Rejected]
    );
}

#[test]
fn proposed_transitions_to_reviewed_or_rejected() {
    assert_eq!(
        GovernanceLifecycle::Proposed.legal_transitions(),
        &[GovernanceLifecycle::Reviewed, GovernanceLifecycle::Rejected]
    );
}

#[test]
fn reviewed_transitions_to_approved_or_rejected() {
    assert_eq!(
        GovernanceLifecycle::Reviewed.legal_transitions(),
        &[GovernanceLifecycle::Approved, GovernanceLifecycle::Rejected]
    );
}

#[test]
fn approved_transitions_to_active_or_rejected() {
    assert_eq!(
        GovernanceLifecycle::Approved.legal_transitions(),
        &[GovernanceLifecycle::Active, GovernanceLifecycle::Rejected]
    );
}

#[test]
fn active_transitions_only_to_superseded() {
    assert_eq!(
        GovernanceLifecycle::Active.legal_transitions(),
        &[GovernanceLifecycle::Superseded]
    );
}

#[test]
fn rejected_is_terminal() {
    assert!(GovernanceLifecycle::Rejected.is_terminal());
    assert!(GovernanceLifecycle::Rejected.legal_transitions().is_empty());
}

#[test]
fn superseded_is_terminal() {
    assert!(GovernanceLifecycle::Superseded.is_terminal());
    assert!(
        GovernanceLifecycle::Superseded
            .legal_transitions()
            .is_empty()
    );
}

#[test]
fn all_unlisted_transitions_are_illegal() {
    let states = [
        GovernanceLifecycle::Draft,
        GovernanceLifecycle::Proposed,
        GovernanceLifecycle::Reviewed,
        GovernanceLifecycle::Approved,
        GovernanceLifecycle::Active,
        GovernanceLifecycle::Superseded,
        GovernanceLifecycle::Rejected,
    ];
    for from in states {
        for to in states {
            assert_eq!(
                from.can_transition(&to),
                from.legal_transitions().contains(&to),
                "unexpected transition {from:?} -> {to:?}"
            );
        }
    }
}

#[test]
fn only_active_state_reports_active() {
    assert!(GovernanceLifecycle::Active.is_active());
    assert!(!GovernanceLifecycle::Approved.is_active());
    assert!(!GovernanceLifecycle::Superseded.is_active());
}

#[test]
fn governance_entity_type_has_all_registry_variants() {
    let variants = [
        GovernanceEntityType::ArchitectureDecision,
        GovernanceEntityType::Requirement,
        GovernanceEntityType::AcceptanceCriterion,
        GovernanceEntityType::Threat,
        GovernanceEntityType::Control,
        GovernanceEntityType::TestHarness,
        GovernanceEntityType::Policy,
        GovernanceEntityType::RiskClassification,
        GovernanceEntityType::WorkOrder,
        GovernanceEntityType::ImplementationArtifact,
        GovernanceEntityType::EvidenceRequirement,
        GovernanceEntityType::ReleaseBaseline,
        GovernanceEntityType::Component,
    ];
    assert_eq!(variants.len(), 13);
    assert_eq!(
        serde_json::to_value(variants[0]).unwrap(),
        "architecture_decision"
    );
    assert_eq!(serde_json::to_value(variants[12]).unwrap(), "component");
}

#[test]
fn dependency_type_has_all_graph_edge_variants() {
    let variants = [
        DependencyType::Requires,
        DependencyType::Implements,
        DependencyType::Tests,
        DependencyType::Verifies,
        DependencyType::Blocks,
        DependencyType::Supersedes,
        DependencyType::EvidenceFor,
        DependencyType::ApprovedBy,
    ];
    assert_eq!(variants.len(), 8);
    assert_eq!(serde_json::to_value(variants[6]).unwrap(), "evidence_for");
}

#[test]
fn governance_revision_serialization_round_trips() {
    let expected = revision();
    let value = serde_json::to_value(&expected).unwrap();
    let actual: GovernanceRevision = serde_json::from_value(value).unwrap();
    assert_eq!(actual, expected);
}

#[test]
fn governance_revision_rejects_unknown_fields() {
    let mut value = serde_json::to_value(revision()).unwrap();
    value["latest"] = json!(true);
    assert!(serde_json::from_value::<GovernanceRevision>(value).is_err());
}

#[test]
fn dependency_link_serialization_round_trips() {
    let expected = DependencyLink {
        id: Uuid::new_v4(),
        source_entity_id: "TEST-1".into(),
        source_revision: 2,
        target_entity_id: "NR-SPEC-001".into(),
        target_revision: 1,
        link_type: DependencyType::Tests,
        created_at: Utc::now(),
    };
    let value = serde_json::to_value(&expected).unwrap();
    assert_eq!(
        serde_json::from_value::<DependencyLink>(value).unwrap(),
        expected
    );
}

#[test]
fn impact_analysis_serialization_round_trips() {
    let expected = ImpactAnalysis {
        entity_id: "NR-SPEC-001".into(),
        revision: 1,
        direct_dependents: Vec::new(),
        transitive_dependents: Vec::new(),
        affected_evidence: vec!["EVIDENCE-1".into()],
        affected_work_orders: vec!["GOV-01".into()],
        orphaned_tests: vec!["TEST-OLD".into()],
    };
    let value = serde_json::to_value(&expected).unwrap();
    assert_eq!(
        serde_json::from_value::<ImpactAnalysis>(value).unwrap(),
        expected
    );
}

#[test]
fn run_pin_serialization_round_trips_exact_revision_uuid() {
    let revision_id = Uuid::new_v4();
    let expected = RunPin {
        run_id: RunId(Uuid::new_v4()),
        pinned_revisions: HashMap::from([("NR-SPEC-001".into(), (3, revision_id))]),
        pinned_at: Utc::now(),
    };
    let value = serde_json::to_value(&expected).unwrap();
    let actual: RunPin = serde_json::from_value(value).unwrap();
    assert_eq!(actual, expected);
    assert_eq!(actual.pinned_revisions["NR-SPEC-001"], (3, revision_id));
}

#[test]
fn illegal_transition_error_serializes_with_states() {
    let error = GovernanceError::IllegalTransition {
        from: GovernanceLifecycle::Draft,
        to: GovernanceLifecycle::Active,
    };
    let value = serde_json::to_value(error).unwrap();
    assert_eq!(value["illegal_transition"]["from"], "draft");
    assert_eq!(value["illegal_transition"]["to"], "active");
}
