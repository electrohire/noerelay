//! Generalized route targets — model revisions and agent revisions.
//!
//! Extends the existing `Candidate` type with a `RouteTarget` enum that can
//! represent either a cloud model (existing behavior) or a governed local agent
//! (new behavior per mission §9).

use crate::registry::{AgentRevision, ModelRevision};
use crate::routing::Candidate;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

/// A route target that may be either a cloud model or a governed local agent.
///
/// This generalizes the existing `Candidate` type without breaking backward
/// compatibility. The `Model` variant wraps the existing `Candidate` struct.
/// The `Agent` variant wraps an `AgentRevision` with dispatch metadata.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields, tag = "kind")]
pub enum RouteTarget {
    /// A cloud model candidate (existing behavior).
    #[serde(rename = "model")]
    Model(RouteTargetModel),
    /// A governed local agent.
    #[serde(rename = "agent")]
    Agent(RouteTargetAgent),
}

/// Cloud model route target — wraps the existing Candidate.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RouteTargetModel {
    pub candidate_id: String,
    pub openrouter_model_id: String,
    pub provider: String,
    pub capabilities: Vec<String>,
    pub cost_total_microusd: u64,
    pub latency_p95_ms: u64,
    pub acceptance_lcb_ppm: u32,
    pub supports_independent_verification: bool,
}

impl From<&Candidate> for RouteTargetModel {
    fn from(c: &Candidate) -> Self {
        Self {
            candidate_id: c.candidate_id.clone(),
            openrouter_model_id: c.openrouter_model_id.clone(),
            provider: c.provider.clone(),
            capabilities: c.capabilities.iter().cloned().collect(),
            cost_total_microusd: c.cost.total_microusd().unwrap_or(u64::MAX),
            latency_p95_ms: c.latency_p95_ms,
            acceptance_lcb_ppm: c.acceptance_lcb_ppm,
            supports_independent_verification: c.supports_independent_verification,
        }
    }
}

/// Governed local agent route target.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RouteTargetAgent {
    pub agent_id: String,
    pub revision: i32,
    pub revision_hash: String,
    pub display_name: String,
    pub endpoint: Option<String>,
    pub capabilities: Vec<String>,
    pub allowed_models: Vec<String>,
    pub provider_family: String,
    pub maximum_data_class: String,
    pub context_budget_tokens: Option<i32>,
    pub budget_ceiling_microusd: Option<u64>,
    pub supports_independent_verification: bool,
}

impl From<&AgentRevision> for RouteTargetAgent {
    fn from(a: &AgentRevision) -> Self {
        Self {
            agent_id: a.entity_id.clone(),
            revision: a.revision,
            revision_hash: a.revision_hash.clone(),
            display_name: a.display_name.clone(),
            endpoint: a.endpoint.clone(),
            capabilities: a.capabilities.clone(),
            allowed_models: a.allowed_models.clone(),
            provider_family: a.agent_type.clone(),
            maximum_data_class: "confidential".into(), // Default; override from policy
            context_budget_tokens: None,
            budget_ceiling_microusd: None,
            supports_independent_verification: true,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::routing::Candidate;
    use crate::types::DataClass;
    use crate::usage::CostBreakdown;
    use std::collections::BTreeSet;

    #[test]
    fn route_target_model_from_candidate() {
        let c = Candidate {
            candidate_id: "test".into(),
            openrouter_model_id: "vendor/test".into(),
            provider: "provider-a".into(),
            available: true,
            capabilities: BTreeSet::from(["coding".into()]),
            maximum_data_class: DataClass::Confidential,
            cost: CostBreakdown {
                inference_microusd: 100,
                ..Default::default()
            },
            latency_p95_ms: 50,
            acceptance_lcb_ppm: 950_000,
            supports_independent_verification: true,
        };
        let target = RouteTargetModel::from(&c);
        assert_eq!(target.candidate_id, "test");
        assert_eq!(target.cost_total_microusd, 100);
        assert_eq!(target.capabilities, vec!["coding"]);
    }

    #[test]
    fn route_target_serializes_with_kind_tag() {
        let c = Candidate {
            candidate_id: "test".into(),
            openrouter_model_id: "vendor/test".into(),
            provider: "provider-a".into(),
            available: true,
            capabilities: BTreeSet::from(["coding".into()]),
            maximum_data_class: DataClass::Confidential,
            cost: CostBreakdown {
                inference_microusd: 100,
                ..Default::default()
            },
            latency_p95_ms: 50,
            acceptance_lcb_ppm: 950_000,
            supports_independent_verification: true,
        };
        let target = RouteTarget::Model(RouteTargetModel::from(&c));
        let json = serde_json::to_string(&target).unwrap();
        assert!(json.contains("\"kind\":\"model\""));
        assert!(json.contains("\"candidate_id\":\"test\""));
    }
}