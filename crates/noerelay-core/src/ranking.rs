//! Provider-neutral advisory ranking contract for NoeRelay.
//!
//! This module defines the generic [`RankingAdvice`] type, the [`AdvisoryRanker`]
//! trait, and validation logic. No specific ranker (LLMRouter or otherwise) is
//! referenced here — the contract is provider-neutral by design.
//!
//! # Architecture
//!
//! ```text
//! policy filtering → admissible candidates → optional advisory ranker
//!                  → NoeRelay deterministic selector → execution → verification
//! ```
//!
//! The ranker receives only candidates already deemed admissible. It returns
//! ranking advice that may reorder candidates. NoeRelay retains final authority
//! over selection. On any validation failure, the advice is discarded and
//! deterministic routing proceeds.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use thiserror::Error;
use uuid::Uuid;

// ---------------------------------------------------------------------------
// Ranker identity
// ---------------------------------------------------------------------------

/// Identifies a specific ranker implementation and revision.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RankerIdentity {
    /// Unique ranker identifier (e.g. "llmrouter", "wilson-lcb").
    pub ranker_id: String,
    /// Immutable revision of this ranker.
    pub revision: String,
    /// Human-readable display name.
    pub display_name: String,
}

// ---------------------------------------------------------------------------
// Candidate ranking
// ---------------------------------------------------------------------------

/// A single candidate's score from the advisory ranker.
///
/// Scores use integer representation to avoid floating-point ambiguity.
/// The interpretation is ranker-specific but must be documented by the ranker.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CandidateRanking {
    /// Must match a `candidate_id` in the admissible set.
    pub candidate_id: String,
    /// Ranker-assigned score in millionths (0..=1_000_000).
    /// Higher is better. 1_000_000 = maximum confidence.
    pub score_ppm: u32,
    /// Optional ranker-provided rationale for this score.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rationale: Option<String>,
}

// ---------------------------------------------------------------------------
// Ranking advice
// ---------------------------------------------------------------------------

/// Provider-neutral ranking advice from an advisory ranker.
///
/// This is the canonical contract. Every ranker implementation must produce
/// advice conforming to this structure. NoeRelay validates the advice before
/// considering it for route selection.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RankingAdvice {
    /// Schema version of this advice structure.
    pub schema_version: String,

    /// Identity and revision of the ranker that produced this advice.
    pub ranker: RankerIdentity,

    /// Unique identifier for this ranking run.
    pub run_id: Uuid,

    /// Cohort this advice applies to (e.g. "coding-tasks", "reasoning-tasks").
    pub cohort: String,

    /// SHA-256 hash of the feature schema used by the ranker.
    /// Must match the expected feature schema for this cohort.
    pub features_hash: String,

    /// SHA-256 hash of the admissible candidate set that was ranked.
    /// Must match the hash of the actual admissible set at routing time.
    pub candidate_set_hash: String,

    /// Per-candidate scores, ordered by ranker preference (best first).
    pub candidate_scores: Vec<CandidateRanking>,

    /// Unix timestamp (milliseconds) through which training data was collected.
    /// None if the ranker was not trained on historical data.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub trained_through_unix_ms: Option<u64>,

    /// Unix timestamp (milliseconds) when this advice was generated.
    pub generated_at_unix_ms: u64,

    /// Unix timestamp (milliseconds) when this advice expires.
    /// None if the advice does not expire.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub expires_at_unix_ms: Option<u64>,

    /// Must be `true`. Non-advisory advice is rejected.
    pub advisory_only: bool,
}

// ---------------------------------------------------------------------------
// Ranking mode
// ---------------------------------------------------------------------------

/// Production mode for learned ranking.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum RankingMode {
    /// No learned-ranker call; deterministic routing only.
    Disabled,
    /// Record advice without influencing selection.
    Shadow,
    /// Advice may reorder already-admissible candidates; NoeRelay retains final authority.
    Advisory,
}

// ---------------------------------------------------------------------------
// Ranking context
// ---------------------------------------------------------------------------

/// Context passed to the ranker for a ranking request.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RankingContext {
    /// Cohort identifier.
    pub cohort: String,
    /// Sanitized features for the ranker (no secrets, raw prompts, or customer code).
    pub features: serde_json::Value,
    /// Hash of the feature schema used.
    pub features_hash: String,
}

/// A candidate that has passed all policy filters and is admissible for ranking.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct AdmissibleCandidate {
    pub candidate_id: String,
    pub provider: String,
    pub capabilities: BTreeSet<String>,
    pub cost_total_microusd: u64,
    pub latency_p95_ms: u64,
    pub acceptance_lcb_ppm: u32,
}

// ---------------------------------------------------------------------------
// Ranker errors
// ---------------------------------------------------------------------------

#[derive(Debug, Error, PartialEq, Eq)]
pub enum RankerError {
    #[error("ranker is not available")]
    Unavailable,
    #[error("ranker timed out after {0} ms")]
    Timeout(u64),
    #[error("ranker returned a malformed response: {0}")]
    MalformedResponse(String),
    #[error("ranker is disabled")]
    Disabled,
    #[error("ranker circuit breaker is open")]
    CircuitBreakerOpen,
}

// ---------------------------------------------------------------------------
// Advisory ranker trait
// ---------------------------------------------------------------------------

/// Provider-neutral interface for advisory rankers.
///
/// Implementations may include deterministic baselines (Wilson LCB),
/// learned rankers (LLMRouter), or stub rankers for testing.
pub trait AdvisoryRanker {
    /// Rank the given admissible candidates and return optional advice.
    ///
    /// Returns `Ok(None)` if the ranker abstains (no opinion).
    /// Returns `Err(RankerError)` if the ranker fails.
    fn rank(
        &self,
        context: &RankingContext,
        candidates: &[AdmissibleCandidate],
    ) -> Result<Option<RankingAdvice>, RankerError>;
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

/// Current supported schema version.
pub const RANKING_ADVICE_SCHEMA_VERSION: &str = "1.0.0";

/// Maximum allowed score in parts per million.
pub const MAX_SCORE_PPM: u32 = 1_000_000;

#[derive(Debug, Error, PartialEq, Eq)]
pub enum AdviceValidationError {
    #[error("unsupported schema version: {0}")]
    UnsupportedSchemaVersion(String),
    #[error("advice is not marked as advisory-only")]
    NotAdvisoryOnly,
    #[error("advice has expired (expires_at={expires_at_ms}, now={now_ms})")]
    Expired { expires_at_ms: u64, now_ms: u64 },
    #[error("candidate set hash mismatch: expected {expected}, got {actual}")]
    CandidateSetHashMismatch { expected: String, actual: String },
    #[error("features hash mismatch: expected {expected}, got {actual}")]
    FeaturesHashMismatch { expected: String, actual: String },
    #[error("unknown candidate in advice: {0}")]
    UnknownCandidate(String),
    #[error("duplicate candidate in advice: {0}")]
    DuplicateCandidate(String),
    #[error("score out of bounds for candidate {candidate_id}: {score_ppm} (max {max})")]
    ScoreOutOfBounds {
        candidate_id: String,
        score_ppm: u32,
        max: u32,
    },
    #[error("empty candidate scores")]
    EmptyScores,
}

/// Validate ranking advice against the admissible candidate set and expected hashes.
///
/// Returns `Ok(())` if the advice is valid and may be considered for routing.
/// Returns `Err(AdviceValidationError)` with a structured reason on any failure.
pub fn validate_advice(
    advice: &RankingAdvice,
    admissible_ids: &BTreeSet<String>,
    expected_features_hash: &str,
    expected_candidate_set_hash: &str,
    now_unix_ms: u64,
) -> Result<(), AdviceValidationError> {
    // Schema version
    if advice.schema_version != RANKING_ADVICE_SCHEMA_VERSION {
        return Err(AdviceValidationError::UnsupportedSchemaVersion(
            advice.schema_version.clone(),
        ));
    }

    // Must be advisory-only
    if !advice.advisory_only {
        return Err(AdviceValidationError::NotAdvisoryOnly);
    }

    // Expiration check
    if let Some(expires_at) = advice.expires_at_unix_ms {
        if now_unix_ms > expires_at {
            return Err(AdviceValidationError::Expired {
                expires_at_ms: expires_at,
                now_ms: now_unix_ms,
            });
        }
    }

    // Feature hash must match
    if advice.features_hash != expected_features_hash {
        return Err(AdviceValidationError::FeaturesHashMismatch {
            expected: expected_features_hash.into(),
            actual: advice.features_hash.clone(),
        });
    }

    // Candidate set hash must match
    if advice.candidate_set_hash != expected_candidate_set_hash {
        return Err(AdviceValidationError::CandidateSetHashMismatch {
            expected: expected_candidate_set_hash.into(),
            actual: advice.candidate_set_hash.clone(),
        });
    }

    // Must have scores
    if advice.candidate_scores.is_empty() {
        return Err(AdviceValidationError::EmptyScores);
    }

    // Validate each candidate score
    let mut seen = BTreeSet::new();
    for ranking in &advice.candidate_scores {
        // Candidate must be in the admissible set
        if !admissible_ids.contains(&ranking.candidate_id) {
            return Err(AdviceValidationError::UnknownCandidate(
                ranking.candidate_id.clone(),
            ));
        }

        // No duplicates
        if !seen.insert(ranking.candidate_id.clone()) {
            return Err(AdviceValidationError::DuplicateCandidate(
                ranking.candidate_id.clone(),
            ));
        }

        // Score bounds
        if ranking.score_ppm > MAX_SCORE_PPM {
            return Err(AdviceValidationError::ScoreOutOfBounds {
                candidate_id: ranking.candidate_id.clone(),
                score_ppm: ranking.score_ppm,
                max: MAX_SCORE_PPM,
            });
        }
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// Hashing helpers
// ---------------------------------------------------------------------------

/// Compute the candidate set hash over a slice of admissible candidates.
///
/// The hash covers candidate IDs in sorted order to ensure determinism.
pub fn candidate_set_hash(candidates: &[AdmissibleCandidate]) -> String {
    let mut ids: Vec<&str> = candidates.iter().map(|c| c.candidate_id.as_str()).collect();
    ids.sort_unstable();
    let material = serde_json::to_vec(&ids).expect("candidate IDs are serializable");
    hex::encode(Sha256::digest(material))
}

/// Compute the features hash over a JSON value.
pub fn features_hash(features: &serde_json::Value) -> String {
    let material = serde_json::to_vec(features).expect("features are serializable");
    hex::encode(Sha256::digest(material))
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn admissible_ids() -> BTreeSet<String> {
        BTreeSet::from(["model-a".into(), "model-b".into(), "model-c".into()])
    }

    fn candidates() -> Vec<AdmissibleCandidate> {
        vec![
            AdmissibleCandidate {
                candidate_id: "model-a".into(),
                provider: "provider-a".into(),
                capabilities: BTreeSet::from(["coding".into()]),
                cost_total_microusd: 1000,
                latency_p95_ms: 100,
                acceptance_lcb_ppm: 950_000,
            },
            AdmissibleCandidate {
                candidate_id: "model-b".into(),
                provider: "provider-b".into(),
                capabilities: BTreeSet::from(["coding".into()]),
                cost_total_microusd: 500,
                latency_p95_ms: 200,
                acceptance_lcb_ppm: 900_000,
            },
            AdmissibleCandidate {
                candidate_id: "model-c".into(),
                provider: "provider-c".into(),
                capabilities: BTreeSet::from(["coding".into()]),
                cost_total_microusd: 2000,
                latency_p95_ms: 50,
                acceptance_lcb_ppm: 990_000,
            },
        ]
    }

    fn valid_advice() -> RankingAdvice {
        let cands = candidates();
        let csh = candidate_set_hash(&cands);
        RankingAdvice {
            schema_version: RANKING_ADVICE_SCHEMA_VERSION.into(),
            ranker: RankerIdentity {
                ranker_id: "test-ranker".into(),
                revision: "v1.0.0".into(),
                display_name: "Test Ranker".into(),
            },
            run_id: Uuid::new_v4(),
            cohort: "coding-tasks".into(),
            features_hash: "abc123".into(),
            candidate_set_hash: csh,
            candidate_scores: vec![
                CandidateRanking {
                    candidate_id: "model-c".into(),
                    score_ppm: 990_000,
                    rationale: Some("Best acceptance rate".into()),
                },
                CandidateRanking {
                    candidate_id: "model-a".into(),
                    score_ppm: 950_000,
                    rationale: None,
                },
                CandidateRanking {
                    candidate_id: "model-b".into(),
                    score_ppm: 900_000,
                    rationale: None,
                },
            ],
            trained_through_unix_ms: Some(1_700_000_000_000),
            generated_at_unix_ms: 1_700_000_001_000,
            expires_at_unix_ms: Some(1_700_000_002_000),
            advisory_only: true,
        }
    }

    // --- Validation tests ---

    #[test]
    fn valid_advice_passes_validation() {
        let advice = valid_advice();
        let csh = advice.candidate_set_hash.clone();
        assert_eq!(
            validate_advice(
                &advice,
                &admissible_ids(),
                "abc123",
                &csh,
                1_700_000_001_500
            ),
            Ok(())
        );
    }

    #[test]
    fn wrong_schema_version_is_rejected() {
        let mut advice = valid_advice();
        advice.schema_version = "0.9.0".into();
        let csh = advice.candidate_set_hash.clone();
        assert_eq!(
            validate_advice(
                &advice,
                &admissible_ids(),
                "abc123",
                &csh,
                1_700_000_001_500
            ),
            Err(AdviceValidationError::UnsupportedSchemaVersion(
                "0.9.0".into()
            ))
        );
    }

    #[test]
    fn non_advisory_advice_is_rejected() {
        let mut advice = valid_advice();
        advice.advisory_only = false;
        let csh = advice.candidate_set_hash.clone();
        assert_eq!(
            validate_advice(
                &advice,
                &admissible_ids(),
                "abc123",
                &csh,
                1_700_000_001_500
            ),
            Err(AdviceValidationError::NotAdvisoryOnly)
        );
    }

    #[test]
    fn expired_advice_is_rejected() {
        let mut advice = valid_advice();
        advice.expires_at_unix_ms = Some(1_700_000_001_000);
        let csh = advice.candidate_set_hash.clone();
        assert_eq!(
            validate_advice(
                &advice,
                &admissible_ids(),
                "abc123",
                &csh,
                1_700_000_002_000
            ),
            Err(AdviceValidationError::Expired {
                expires_at_ms: 1_700_000_001_000,
                now_ms: 1_700_000_002_000,
            })
        );
    }

    #[test]
    fn non_expired_advice_without_expiry_passes() {
        let mut advice = valid_advice();
        advice.expires_at_unix_ms = None;
        let csh = advice.candidate_set_hash.clone();
        assert_eq!(
            validate_advice(
                &advice,
                &admissible_ids(),
                "abc123",
                &csh,
                1_700_000_999_000
            ),
            Ok(())
        );
    }

    #[test]
    fn candidate_set_hash_mismatch_is_rejected() {
        let advice = valid_advice();
        assert_eq!(
            validate_advice(
                &advice,
                &admissible_ids(),
                "abc123",
                "wrong-hash",
                1_700_000_001_500
            ),
            Err(AdviceValidationError::CandidateSetHashMismatch {
                expected: "wrong-hash".into(),
                actual: advice.candidate_set_hash,
            })
        );
    }

    #[test]
    fn features_hash_mismatch_is_rejected() {
        let advice = valid_advice();
        let csh = advice.candidate_set_hash.clone();
        assert_eq!(
            validate_advice(
                &advice,
                &admissible_ids(),
                "wrong-features",
                &csh,
                1_700_000_001_500
            ),
            Err(AdviceValidationError::FeaturesHashMismatch {
                expected: "wrong-features".into(),
                actual: "abc123".into(),
            })
        );
    }

    #[test]
    fn unknown_candidate_is_rejected() {
        let mut advice = valid_advice();
        advice.candidate_scores.push(CandidateRanking {
            candidate_id: "model-unknown".into(),
            score_ppm: 500_000,
            rationale: None,
        });
        let csh = advice.candidate_set_hash.clone();
        assert_eq!(
            validate_advice(
                &advice,
                &admissible_ids(),
                "abc123",
                &csh,
                1_700_000_001_500
            ),
            Err(AdviceValidationError::UnknownCandidate(
                "model-unknown".into()
            ))
        );
    }

    #[test]
    fn duplicate_candidate_is_rejected() {
        let mut advice = valid_advice();
        advice.candidate_scores.push(CandidateRanking {
            candidate_id: "model-a".into(),
            score_ppm: 500_000,
            rationale: None,
        });
        let csh = advice.candidate_set_hash.clone();
        assert_eq!(
            validate_advice(
                &advice,
                &admissible_ids(),
                "abc123",
                &csh,
                1_700_000_001_500
            ),
            Err(AdviceValidationError::DuplicateCandidate("model-a".into()))
        );
    }

    #[test]
    fn score_out_of_bounds_is_rejected() {
        let mut advice = valid_advice();
        advice.candidate_scores[0].score_ppm = 1_000_001;
        let csh = advice.candidate_set_hash.clone();
        assert_eq!(
            validate_advice(
                &advice,
                &admissible_ids(),
                "abc123",
                &csh,
                1_700_000_001_500
            ),
            Err(AdviceValidationError::ScoreOutOfBounds {
                candidate_id: "model-c".into(),
                score_ppm: 1_000_001,
                max: 1_000_000,
            })
        );
    }

    #[test]
    fn empty_scores_is_rejected() {
        let mut advice = valid_advice();
        advice.candidate_scores.clear();
        let csh = advice.candidate_set_hash.clone();
        assert_eq!(
            validate_advice(
                &advice,
                &admissible_ids(),
                "abc123",
                &csh,
                1_700_000_001_500
            ),
            Err(AdviceValidationError::EmptyScores)
        );
    }

    // --- Hashing tests ---

    #[test]
    fn candidate_set_hash_is_deterministic() {
        let cands = candidates();
        let h1 = candidate_set_hash(&cands);
        let h2 = candidate_set_hash(&cands);
        assert_eq!(h1, h2);
    }

    #[test]
    fn candidate_set_hash_changes_with_different_candidates() {
        let mut cands = candidates();
        let h1 = candidate_set_hash(&cands);
        cands[0].candidate_id = "different".into();
        let h2 = candidate_set_hash(&cands);
        assert_ne!(h1, h2);
    }

    #[test]
    fn candidate_set_hash_is_order_independent() {
        let mut cands = candidates();
        let h1 = candidate_set_hash(&cands);
        cands.reverse();
        let h2 = candidate_set_hash(&cands);
        assert_eq!(h1, h2);
    }

    #[test]
    fn features_hash_is_deterministic() {
        let f = serde_json::json!({"cohort": "coding", "risk": "medium"});
        assert_eq!(features_hash(&f), features_hash(&f));
    }

    // --- RankerError tests ---

    #[test]
    fn ranker_error_display() {
        assert_eq!(
            RankerError::Unavailable.to_string(),
            "ranker is not available"
        );
        assert_eq!(
            RankerError::Timeout(5000).to_string(),
            "ranker timed out after 5000 ms"
        );
        assert_eq!(
            RankerError::MalformedResponse("bad json".into()).to_string(),
            "ranker returned a malformed response: bad json"
        );
    }
}
