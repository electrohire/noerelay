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

// ---------------------------------------------------------------------------
// Staged router with optional advisory ranking
// ---------------------------------------------------------------------------

/// Decision provenance: records whether and how ranking advice was used.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RankingProvenance {
    /// Whether a ranker was consulted.
    pub ranker_consulted: bool,
    /// Ranker identity, if consulted.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ranker_id: Option<String>,
    /// Ranker revision, if consulted.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ranker_revision: Option<String>,
    /// Whether the ranker returned valid advice.
    pub advice_received: bool,
    /// Whether NoeRelay followed the ranker's top recommendation.
    pub advice_followed: bool,
    /// Why the advice was overridden, if applicable.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub override_reason: Option<String>,
    /// Validation error, if advice was rejected.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub validation_error: Option<String>,
}

impl RankingProvenance {
    pub fn not_consulted() -> Self {
        Self {
            ranker_consulted: false,
            ranker_id: None,
            ranker_revision: None,
            advice_received: false,
            advice_followed: false,
            override_reason: None,
            validation_error: None,
        }
    }
}

/// Extended route decision with ranking provenance.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct StagedRouteDecision {
    pub selected_candidate_id: Option<String>,
    pub selected_openrouter_model_id: Option<String>,
    pub expected_total_cost_microusd: Option<u64>,
    pub rejections: Vec<CandidateRejection>,
    pub ranking_provenance: RankingProvenance,
}

impl StagedRouteDecision {
    pub fn from_decision(decision: RouteDecision, provenance: RankingProvenance) -> Self {
        Self {
            selected_candidate_id: decision.selected_candidate_id,
            selected_openrouter_model_id: decision.selected_openrouter_model_id,
            expected_total_cost_microusd: decision.expected_total_cost_microusd,
            rejections: decision.rejections,
            ranking_provenance: provenance,
        }
    }
}

/// A staged router that inserts an optional advisory ranking stage between
/// filtering and deterministic selection.
///
/// When ranking is disabled or the ranker fails, the output is identical to
/// the existing [`Router::select`].
#[derive(Debug, Default, Clone)]
pub struct StagedRouter {
    inner: Router,
}

impl StagedRouter {
    pub fn new() -> Self {
        Self { inner: Router }
    }

    /// Select a candidate with optional advisory ranking.
    ///
    /// If `ranker` is `None` or `mode` is `Disabled`, this is equivalent to
    /// [`Router::select`]. If advice is provided and valid, admissible
    /// candidates are reordered by the ranker's scores before deterministic
    /// tie-breaking.
    pub fn select_with_ranking(
        &self,
        candidates: &[Candidate],
        constraints: &Constraints,
        ranker: Option<&dyn crate::ranking::AdvisoryRanker>,
        mode: crate::ranking::RankingMode,
        context: Option<&crate::ranking::RankingContext>,
    ) -> StagedRouteDecision {
        // Stage 1: Filter — identical to existing Router
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

        // Stage 2: Optional ranking
        let mut provenance = RankingProvenance::not_consulted();

        if mode != crate::ranking::RankingMode::Disabled {
            if let (Some(ranker), Some(ctx)) = (ranker, context) {
                provenance.ranker_consulted = true;

                // Build admissible candidate list for the ranker
                let admissible_candidates: Vec<crate::ranking::AdmissibleCandidate> = admissible
                    .iter()
                    .map(|(c, _)| crate::ranking::AdmissibleCandidate {
                        candidate_id: c.candidate_id.clone(),
                        provider: c.provider.clone(),
                        capabilities: c.capabilities.clone(),
                        cost_total_microusd: c.cost.total_microusd().unwrap_or(u64::MAX),
                        latency_p95_ms: c.latency_p95_ms,
                        acceptance_lcb_ppm: c.acceptance_lcb_ppm,
                    })
                    .collect();

                match ranker.rank(ctx, &admissible_candidates) {
                    Ok(Some(advice)) => {
                        provenance.ranker_id = Some(advice.ranker.ranker_id.clone());
                        provenance.ranker_revision = Some(advice.ranker.revision.clone());

                        // Validate advice
                        let admissible_ids: std::collections::BTreeSet<String> = admissible
                            .iter()
                            .map(|(c, _)| c.candidate_id.clone())
                            .collect();
                        let expected_csh =
                            crate::ranking::candidate_set_hash(&admissible_candidates);
                        let now_ms = std::time::SystemTime::now()
                            .duration_since(std::time::UNIX_EPOCH)
                            .unwrap_or_default()
                            .as_millis() as u64;

                        match crate::ranking::validate_advice(
                            &advice,
                            &admissible_ids,
                            &ctx.features_hash,
                            &expected_csh,
                            now_ms,
                        ) {
                            Ok(()) => {
                                provenance.advice_received = true;

                                if mode == crate::ranking::RankingMode::Advisory {
                                    // Reorder admissible candidates by ranker scores
                                    let score_map: std::collections::BTreeMap<&str, u32> =
                                        advice
                                            .candidate_scores
                                            .iter()
                                            .map(|cr| (cr.candidate_id.as_str(), cr.score_ppm))
                                            .collect();

                                    admissible.sort_by(|(a, a_cost), (b, b_cost)| {
                                        let score_a =
                                            score_map.get(a.candidate_id.as_str()).copied();
                                        let score_b =
                                            score_map.get(b.candidate_id.as_str()).copied();
                                        match (score_a, score_b) {
                                            (Some(sa), Some(sb)) if sa != sb => {
                                                sb.cmp(&sa) // Higher score first
                                            }
                                            _ => {
                                                // Fall back to deterministic ordering
                                                a_cost
                                                    .cmp(b_cost)
                                                    .then(a.latency_p95_ms.cmp(&b.latency_p95_ms))
                                                    .then(b.acceptance_lcb_ppm.cmp(
                                                        &a.acceptance_lcb_ppm,
                                                    ))
                                                    .then(a.candidate_id.cmp(&b.candidate_id))
                                            }
                                        }
                                    });

                                    // Check if top pick changed
                                    let top_id = admissible.first().map(|(c, _)| &c.candidate_id);
                                    let advice_top = advice.candidate_scores.first().map(|cr| &cr.candidate_id);
                                    provenance.advice_followed = top_id == advice_top;
                                    if !provenance.advice_followed {
                                        provenance.override_reason = Some(
                                            "ranker top pick was not the deterministic winner after tie-breaking"
                                                .into(),
                                        );
                                    }
                                }
                                // In shadow mode, advice is recorded but doesn't affect selection
                            }
                            Err(e) => {
                                provenance.validation_error = Some(e.to_string());
                            }
                        }
                    }
                    Ok(None) => {
                        // Ranker abstained
                    }
                    Err(e) => {
                        provenance.validation_error = Some(e.to_string());
                    }
                }
            }
        }

        // Stage 3: Deterministic selection (always runs)
        rejections.sort_by(|left, right| left.candidate_id.cmp(&right.candidate_id));
        let selected = admissible.first();
        let decision = RouteDecision {
            selected_candidate_id: selected.map(|(c, _)| c.candidate_id.clone()),
            selected_openrouter_model_id: selected
                .map(|(c, _)| c.openrouter_model_id.clone()),
            expected_total_cost_microusd: selected.map(|(_, total)| *total),
            rejections,
        };

        StagedRouteDecision::from_decision(decision, provenance)
    }
}

#[cfg(test)]
mod staged_tests {
    use super::*;
    use crate::ranking::{
        AdmissibleCandidate, AdvisoryRanker, RankingAdvice, RankingContext, RankingMode,
        RankerError,
    };

    struct StubRanker {
        advice: Option<RankingAdvice>,
        should_fail: bool,
    }

    impl AdvisoryRanker for StubRanker {
        fn rank(
            &self,
            _context: &RankingContext,
            _candidates: &[AdmissibleCandidate],
        ) -> Result<Option<RankingAdvice>, RankerError> {
            if self.should_fail {
                Err(RankerError::Unavailable)
            } else {
                Ok(self.advice.clone())
            }
        }
    }

    fn test_candidates() -> Vec<Candidate> {
        vec![
            Candidate {
                candidate_id: "cheap".into(),
                openrouter_model_id: "vendor/cheap".into(),
                provider: "provider-a".into(),
                available: true,
                capabilities: BTreeSet::from(["coding".into()]),
                maximum_data_class: DataClass::Confidential,
                cost: CostBreakdown {
                    inference_microusd: 100,
                    ..Default::default()
                },
                latency_p95_ms: 200,
                acceptance_lcb_ppm: 900_000,
                supports_independent_verification: true,
            },
            Candidate {
                candidate_id: "fast".into(),
                openrouter_model_id: "vendor/fast".into(),
                provider: "provider-b".into(),
                available: true,
                capabilities: BTreeSet::from(["coding".into()]),
                maximum_data_class: DataClass::Confidential,
                cost: CostBreakdown {
                    inference_microusd: 200,
                    ..Default::default()
                },
                latency_p95_ms: 50,
                acceptance_lcb_ppm: 950_000,
                supports_independent_verification: true,
            },
        ]
    }

    fn test_constraints() -> Constraints {
        Constraints {
            required_capabilities: BTreeSet::from(["coding".into()]),
            data_class: DataClass::Internal,
            allowed_providers: BTreeSet::new(),
            max_total_cost_microusd: None,
            max_latency_ms: None,
            min_acceptance_lcb_ppm: 800_000,
            require_independent_verification: false,
        }
    }

    #[test]
    fn disabled_ranking_is_identical_to_baseline() {
        let router = StagedRouter::new();
        let baseline = Router.select(&test_candidates(), &test_constraints());
        let staged = router.select_with_ranking(
            &test_candidates(),
            &test_constraints(),
            None,
            RankingMode::Disabled,
            None,
        );
        assert_eq!(
            staged.selected_candidate_id,
            baseline.selected_candidate_id
        );
        assert!(!staged.ranking_provenance.ranker_consulted);
    }

    #[test]
    fn ranker_failure_falls_back_to_deterministic() {
        let router = StagedRouter::new();
        let ranker = StubRanker {
            advice: None,
            should_fail: true,
        };
        let context = RankingContext {
            cohort: "coding".into(),
            features: serde_json::json!({}),
            features_hash: "abc".into(),
        };
        let result = router.select_with_ranking(
            &test_candidates(),
            &test_constraints(),
            Some(&ranker),
            RankingMode::Advisory,
            Some(&context),
        );
        // Should still select the cheapest admissible candidate
        assert_eq!(result.selected_candidate_id.as_deref(), Some("cheap"));
        assert!(result.ranking_provenance.ranker_consulted);
        assert!(!result.ranking_provenance.advice_received);
        assert!(result.ranking_provenance.validation_error.is_some());
    }

    #[test]
    fn shadow_mode_records_but_does_not_affect_selection() {
        let router = StagedRouter::new();
        let cands = test_candidates();
        let csh = crate::ranking::candidate_set_hash(&[
            AdmissibleCandidate {
                candidate_id: "cheap".into(),
                provider: "provider-a".into(),
                capabilities: BTreeSet::from(["coding".into()]),
                cost_total_microusd: 100,
                latency_p95_ms: 200,
                acceptance_lcb_ppm: 900_000,
            },
            AdmissibleCandidate {
                candidate_id: "fast".into(),
                provider: "provider-b".into(),
                capabilities: BTreeSet::from(["coding".into()]),
                cost_total_microusd: 200,
                latency_p95_ms: 50,
                acceptance_lcb_ppm: 950_000,
            },
        ]);

        let advice = RankingAdvice {
            schema_version: crate::ranking::RANKING_ADVICE_SCHEMA_VERSION.into(),
            ranker: crate::ranking::RankerIdentity {
                ranker_id: "test".into(),
                revision: "v1".into(),
                display_name: "Test".into(),
            },
            run_id: uuid::Uuid::new_v4(),
            cohort: "coding".into(),
            features_hash: "abc".into(),
            candidate_set_hash: csh,
            candidate_scores: vec![
                crate::ranking::CandidateRanking {
                    candidate_id: "fast".into(),
                    score_ppm: 990_000,
                    rationale: None,
                },
                crate::ranking::CandidateRanking {
                    candidate_id: "cheap".into(),
                    score_ppm: 500_000,
                    rationale: None,
                },
            ],
            trained_through_unix_ms: None,
            generated_at_unix_ms: 1,
            expires_at_unix_ms: None,
            advisory_only: true,
        };

        let ranker = StubRanker {
            advice: Some(advice),
            should_fail: false,
        };
        let context = RankingContext {
            cohort: "coding".into(),
            features: serde_json::json!({}),
            features_hash: "abc".into(),
        };

        let result = router.select_with_ranking(
            &cands,
            &test_constraints(),
            Some(&ranker),
            RankingMode::Shadow,
            Some(&context),
        );

        // Shadow mode: advice is recorded but cheapest still wins
        assert_eq!(result.selected_candidate_id.as_deref(), Some("cheap"));
        assert!(result.ranking_provenance.ranker_consulted);
        assert!(result.ranking_provenance.advice_received);
        // In shadow mode, advice_followed is false because we don't reorder
    }

    #[test]
    fn advisory_mode_reorders_by_ranker_scores() {
        let router = StagedRouter::new();
        let cands = test_candidates();
        let csh = crate::ranking::candidate_set_hash(&[
            AdmissibleCandidate {
                candidate_id: "cheap".into(),
                provider: "provider-a".into(),
                capabilities: BTreeSet::from(["coding".into()]),
                cost_total_microusd: 100,
                latency_p95_ms: 200,
                acceptance_lcb_ppm: 900_000,
            },
            AdmissibleCandidate {
                candidate_id: "fast".into(),
                provider: "provider-b".into(),
                capabilities: BTreeSet::from(["coding".into()]),
                cost_total_microusd: 200,
                latency_p95_ms: 50,
                acceptance_lcb_ppm: 950_000,
            },
        ]);

        let advice = RankingAdvice {
            schema_version: crate::ranking::RANKING_ADVICE_SCHEMA_VERSION.into(),
            ranker: crate::ranking::RankerIdentity {
                ranker_id: "test".into(),
                revision: "v1".into(),
                display_name: "Test".into(),
            },
            run_id: uuid::Uuid::new_v4(),
            cohort: "coding".into(),
            features_hash: "abc".into(),
            candidate_set_hash: csh,
            candidate_scores: vec![
                crate::ranking::CandidateRanking {
                    candidate_id: "fast".into(),
                    score_ppm: 990_000,
                    rationale: None,
                },
                crate::ranking::CandidateRanking {
                    candidate_id: "cheap".into(),
                    score_ppm: 500_000,
                    rationale: None,
                },
            ],
            trained_through_unix_ms: None,
            generated_at_unix_ms: 1,
            expires_at_unix_ms: None,
            advisory_only: true,
        };

        let ranker = StubRanker {
            advice: Some(advice),
            should_fail: false,
        };
        let context = RankingContext {
            cohort: "coding".into(),
            features: serde_json::json!({}),
            features_hash: "abc".into(),
        };

        let result = router.select_with_ranking(
            &cands,
            &test_constraints(),
            Some(&ranker),
            RankingMode::Advisory,
            Some(&context),
        );

        // Advisory mode: ranker's top pick ("fast") should win
        assert_eq!(result.selected_candidate_id.as_deref(), Some("fast"));
        assert!(result.ranking_provenance.ranker_consulted);
        assert!(result.ranking_provenance.advice_received);
        assert!(result.ranking_provenance.advice_followed);
    }
}
