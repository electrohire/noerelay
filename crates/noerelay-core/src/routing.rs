use crate::types::DataClass;
use crate::usage::CostBreakdown;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use std::cmp::Reverse;
use std::collections::BTreeSet;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Candidate {
    pub candidate_id: String,
    pub openrouter_model_id: String,
    pub provider: String,
    pub available: bool,
    pub capabilities: BTreeSet<String>,
    pub maximum_data_class: DataClass,
    pub cost: CostBreakdown,
    pub latency_p95_ms: u64,
    /// Calibrated lower confidence bound, in millionths (0..=1_000_000).
    pub acceptance_lcb_ppm: u32,
    pub supports_independent_verification: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Constraints {
    pub required_capabilities: BTreeSet<String>,
    pub data_class: DataClass,
    pub allowed_providers: BTreeSet<String>,
    pub max_total_cost_microusd: Option<u64>,
    pub max_latency_ms: Option<u64>,
    pub min_acceptance_lcb_ppm: u32,
    pub require_independent_verification: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum RejectionReason {
    Unavailable,
    MissingCapability(String),
    DataPolicy,
    ProviderDenied,
    InvalidModelIdentity,
    CostCap,
    LatencyCap,
    AcceptanceThreshold,
    IndependentVerification,
    ArithmeticOverflow,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CandidateRejection {
    pub candidate_id: String,
    pub reasons: Vec<RejectionReason>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RouteDecision {
    pub selected_candidate_id: Option<String>,
    pub selected_openrouter_model_id: Option<String>,
    pub expected_total_cost_microusd: Option<u64>,
    pub rejections: Vec<CandidateRejection>,
}

#[derive(Debug, Default, Clone, Copy)]
pub struct Router;

impl Router {
    pub fn select(&self, candidates: &[Candidate], constraints: &Constraints) -> RouteDecision {
        let mut admissible: Vec<(&Candidate, u64)> = Vec::new();
        let mut rejections = Vec::new();

        for candidate in candidates {
            let (reasons, total) = rejection_reasons(candidate, constraints);
            if reasons.is_empty() {
                admissible.push((candidate, total.expect("admissible cost must be present")));
            } else {
                rejections.push(CandidateRejection {
                    candidate_id: candidate.candidate_id.clone(),
                    reasons,
                });
            }
        }

        admissible.sort_by_key(|(candidate, total)| {
            (
                *total,
                candidate.latency_p95_ms,
                Reverse(candidate.acceptance_lcb_ppm),
                candidate.candidate_id.as_str(),
            )
        });
        rejections.sort_by(|left, right| left.candidate_id.cmp(&right.candidate_id));

        let selected = admissible.first();
        RouteDecision {
            selected_candidate_id: selected.map(|(candidate, _)| candidate.candidate_id.clone()),
            selected_openrouter_model_id: selected
                .map(|(candidate, _)| candidate.openrouter_model_id.clone()),
            expected_total_cost_microusd: selected.map(|(_, total)| *total),
            rejections,
        }
    }
}

fn rejection_reasons(
    candidate: &Candidate,
    constraints: &Constraints,
) -> (Vec<RejectionReason>, Option<u64>) {
    let mut reasons = Vec::new();
    if !candidate.available {
        reasons.push(RejectionReason::Unavailable);
    }
    for capability in constraints
        .required_capabilities
        .difference(&candidate.capabilities)
    {
        reasons.push(RejectionReason::MissingCapability(capability.clone()));
    }
    if candidate.maximum_data_class < constraints.data_class {
        reasons.push(RejectionReason::DataPolicy);
    }
    if !constraints.allowed_providers.is_empty()
        && !constraints.allowed_providers.contains(&candidate.provider)
    {
        reasons.push(RejectionReason::ProviderDenied);
    }
    if candidate.openrouter_model_id.trim().is_empty()
        || candidate.openrouter_model_id == "openrouter/auto"
    {
        reasons.push(RejectionReason::InvalidModelIdentity);
    }
    let total = candidate.cost.total_microusd();
    if total.is_none() {
        reasons.push(RejectionReason::ArithmeticOverflow);
    }
    if constraints
        .max_total_cost_microusd
        .zip(total)
        .is_some_and(|(cap, value)| value > cap)
    {
        reasons.push(RejectionReason::CostCap);
    }
    if constraints
        .max_latency_ms
        .is_some_and(|cap| candidate.latency_p95_ms > cap)
    {
        reasons.push(RejectionReason::LatencyCap);
    }
    if candidate.acceptance_lcb_ppm < constraints.min_acceptance_lcb_ppm {
        reasons.push(RejectionReason::AcceptanceThreshold);
    }
    if constraints.require_independent_verification && !candidate.supports_independent_verification
    {
        reasons.push(RejectionReason::IndependentVerification);
    }
    (reasons, total)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn candidate(id: &str, cost: u64, lcb: u32) -> Candidate {
        Candidate {
            candidate_id: id.into(),
            openrouter_model_id: format!("vendor/{id}"),
            provider: "provider-a".into(),
            available: true,
            capabilities: BTreeSet::from(["coding".into()]),
            maximum_data_class: DataClass::Confidential,
            cost: CostBreakdown {
                inference_microusd: cost,
                ..Default::default()
            },
            latency_p95_ms: 100,
            acceptance_lcb_ppm: lcb,
            supports_independent_verification: true,
        }
    }

    fn constraints() -> Constraints {
        Constraints {
            required_capabilities: BTreeSet::from(["coding".into()]),
            data_class: DataClass::Internal,
            allowed_providers: BTreeSet::new(),
            max_total_cost_microusd: None,
            max_latency_ms: None,
            min_acceptance_lcb_ppm: 900_000,
            require_independent_verification: false,
        }
    }

    #[test]
    fn cheapest_admissible_candidate_wins() {
        let result = Router.select(
            &[
                candidate("expensive", 10_000, 990_000),
                candidate("cheap", 1_000, 910_000),
            ],
            &constraints(),
        );
        assert_eq!(result.selected_candidate_id.as_deref(), Some("cheap"));
    }

    #[test]
    fn inadmissible_cheapest_never_wins() {
        let mut cheap = candidate("cheap", 1, 990_000);
        cheap.capabilities.clear();
        let result = Router.select(&[cheap, candidate("valid", 5_000, 920_000)], &constraints());
        assert_eq!(result.selected_candidate_id.as_deref(), Some("valid"));
        assert_eq!(
            result.rejections[0].reasons,
            vec![RejectionReason::MissingCapability("coding".into())]
        );
    }

    #[test]
    fn every_hard_failure_is_recorded() {
        let mut invalid = candidate("invalid", 500, 100);
        invalid.available = false;
        invalid.openrouter_model_id = "openrouter/auto".into();
        invalid.provider = "denied".into();
        invalid.maximum_data_class = DataClass::Public;
        invalid.supports_independent_verification = false;
        let mut limits = constraints();
        limits.allowed_providers.insert("allowed".into());
        limits.max_total_cost_microusd = Some(10);
        limits.max_latency_ms = Some(10);
        limits.require_independent_verification = true;
        let result = Router.select(&[invalid], &limits);
        assert!(result.selected_candidate_id.is_none());
        assert_eq!(result.rejections[0].reasons.len(), 8);
    }

    #[test]
    fn selection_is_stable_for_equal_scores() {
        let result = Router.select(
            &[
                candidate("z", 1_000, 950_000),
                candidate("a", 1_000, 950_000),
            ],
            &constraints(),
        );
        assert_eq!(result.selected_candidate_id.as_deref(), Some("a"));
    }
}
