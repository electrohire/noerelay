//! Immutable governance revisions, lifecycle rules, impact links, and run pins.

use std::collections::HashMap;

use chrono::{DateTime, Utc};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::execution::RunId;
use crate::iam::{OrganizationId, PrincipalId};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum GovernanceEntityType {
    ArchitectureDecision,
    Requirement,
    AcceptanceCriterion,
    Threat,
    Control,
    TestHarness,
    Policy,
    RiskClassification,
    WorkOrder,
    ImplementationArtifact,
    EvidenceRequirement,
    ReleaseBaseline,
    Component,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum GovernanceLifecycle {
    Draft,
    Proposed,
    Reviewed,
    Approved,
    Active,
    Superseded,
    Rejected,
}

impl GovernanceLifecycle {
    pub fn legal_transitions(&self) -> &'static [GovernanceLifecycle] {
        use GovernanceLifecycle::{Active, Approved, Proposed, Rejected, Reviewed, Superseded};

        match self {
            Self::Draft => &[Proposed, Rejected],
            Self::Proposed => &[Reviewed, Rejected],
            Self::Reviewed => &[Approved, Rejected],
            Self::Approved => &[Active, Rejected],
            Self::Active => &[Superseded],
            Self::Superseded | Self::Rejected => &[],
        }
    }

    pub fn can_transition(&self, to: &GovernanceLifecycle) -> bool {
        self.legal_transitions().contains(to)
    }

    pub fn is_terminal(&self) -> bool {
        matches!(self, Self::Superseded | Self::Rejected)
    }

    pub fn is_active(&self) -> bool {
        matches!(self, Self::Active)
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct GovernanceRevision {
    pub id: Uuid,
    pub entity_type: GovernanceEntityType,
    pub entity_id: String,
    pub revision: i32,
    pub revision_hash: String,
    pub lifecycle: GovernanceLifecycle,
    pub title: String,
    pub content: serde_json::Value,
    pub parent_revision_id: Option<Uuid>,
    pub superseded_by_id: Option<Uuid>,
    pub created_at: DateTime<Utc>,
    pub created_by: PrincipalId,
    pub approved_at: Option<DateTime<Utc>>,
    pub approved_by: Option<PrincipalId>,
    pub activated_at: Option<DateTime<Utc>>,
    pub organization_id: OrganizationId,
    pub notes: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct DependencyLink {
    pub id: Uuid,
    pub source_entity_id: String,
    pub source_revision: i32,
    pub target_entity_id: String,
    pub target_revision: i32,
    pub link_type: DependencyType,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum DependencyType {
    Requires,
    Implements,
    Tests,
    Verifies,
    Blocks,
    Supersedes,
    EvidenceFor,
    ApprovedBy,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ImpactAnalysis {
    pub entity_id: String,
    pub revision: i32,
    pub direct_dependents: Vec<DependencyLink>,
    pub transitive_dependents: Vec<DependencyLink>,
    pub affected_evidence: Vec<String>,
    pub affected_work_orders: Vec<String>,
    pub orphaned_tests: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RunPin {
    pub run_id: RunId,
    pub pinned_revisions: HashMap<String, (i32, Uuid)>,
    pub pinned_at: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum GovernanceError {
    NotFound,
    IllegalTransition {
        from: GovernanceLifecycle,
        to: GovernanceLifecycle,
    },
    AlreadyActive,
    AlreadySuperseded,
    UnauthorizedActivation,
    UnauthorizedSupersession,
    CircularDependency,
    OrphanedDependency,
    StaleEvidence,
    InvalidRevision,
}
