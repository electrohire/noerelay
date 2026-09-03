//! Evaluator result contract types conforming to the spec-kit-evaluator schema.
//!
//! Provides Rust-native types for constructing, serializing, and composing
//! evaluator results with evidence classification, uncertainty representation,
//! and contradiction preservation. These types are used by the gateway to
//! produce evaluator-contract-compliant findings in the EPR response envelope.
//!
//! Design rules (from the evaluator contract):
//! 1. Generated assertions MUST remain distinguishable from observed evidence.
//! 2. Model self-attestation MUST NOT satisfy an evidence gate by itself.
//! 3. Contradictions MUST be preserved, not collapsed into a single answer.
//! 4. Insufficient evidence MUST be represented explicitly.
//! 5. Deterministic checks SHOULD run before probabilistic review.
//! 6. Higher-risk work MAY require evaluator independence.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// Schema version for evaluator results.
pub const EVALUATOR_SCHEMA_VERSION: &str = "1.0";

// ---------------------------------------------------------------------------
// Evidence reference
// ---------------------------------------------------------------------------

/// Nature of the evidence supporting or contradicting a finding.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum EvidenceKind {
    /// Directly observed from an artifact or command output.
    Observed,
    /// Logically derived from observed evidence.
    Inferred,
    /// Claimed by a model or agent without direct observation.
    Asserted,
    /// Conflicts with other observed evidence.
    Contradicted,
    /// No evidence found to support or refute.
    Unsupported,
}

/// A reference to evidence supporting or contradicting a finding.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EvidenceRef {
    /// File path, URL, or artifact identifier.
    pub r#ref: String,
    /// Nature of the evidence.
    pub kind: EvidenceKind,
    /// Brief description of what the evidence shows.
    #[serde(skip_serializing_if = "String::is_empty", default)]
    pub description: String,
}

// ---------------------------------------------------------------------------
// Finding
// ---------------------------------------------------------------------------

/// Severity of a finding.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum FindingSeverity {
    Critical,
    High,
    Medium,
    Low,
    Info,
}

/// Classification of a finding.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum FindingKind {
    #[serde(rename = "unsupported_claim")]
    UnsupportedClaim,
    Contradiction,
    #[serde(rename = "missing_evidence")]
    MissingEvidence,
    #[serde(rename = "ambiguous_requirement")]
    AmbiguousRequirement,
    #[serde(rename = "unverified_assertion")]
    UnverifiedAssertion,
    #[serde(rename = "provenance_gap")]
    ProvenanceGap,
    #[serde(rename = "schema_violation")]
    SchemaViolation,
    #[serde(rename = "policy_violation")]
    PolicyViolation,
    #[serde(rename = "security_concern")]
    SecurityConcern,
    #[serde(rename = "coverage_gap")]
    CoverageGap,
    #[serde(rename = "traceability_gap")]
    TraceabilityGap,
    #[serde(rename = "risk_unaddressed")]
    RiskUnaddressed,
    #[serde(rename = "assumption_unvalidated")]
    AssumptionUnvalidated,
    Other,
}

/// Level of uncertainty about a finding.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum Uncertainty {
    None,
    Low,
    Medium,
    High,
    #[serde(rename = "insufficient_evidence")]
    InsufficientEvidence,
}

/// Recommended action for a finding.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum RecommendedAction {
    None,
    #[serde(rename = "gather_evidence")]
    GatherEvidence,
    Clarify,
    Revise,
    Iterate,
    Escalate,
    #[serde(rename = "accept_risk")]
    AcceptRisk,
    Block,
}

/// An individual finding from an evaluator.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Finding {
    /// Unique finding identifier within this result (e.g., EPI-001).
    pub id: String,
    /// Finding severity.
    pub severity: FindingSeverity,
    /// Classification of the finding.
    pub kind: FindingKind,
    /// Identifier of the artifact element the finding relates to.
    pub subject: String,
    /// Human-readable description.
    #[serde(skip_serializing_if = "String::is_empty", default)]
    pub description: String,
    /// References to observed evidence.
    #[serde(skip_serializing_if = "Vec::is_empty", default)]
    pub evidence_refs: Vec<EvidenceRef>,
    /// References to source artifacts.
    #[serde(skip_serializing_if = "Vec::is_empty", default)]
    pub provenance_refs: Vec<String>,
    /// Level of uncertainty about the finding.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub uncertainty: Option<Uncertainty>,
    /// Recommended action for this finding.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub recommended_action: Option<RecommendedAction>,
    /// Brief rationale for the finding and recommendation.
    #[serde(skip_serializing_if = "String::is_empty", default)]
    pub rationale: String,
}

// ---------------------------------------------------------------------------
// Evaluator result
// ---------------------------------------------------------------------------

/// Aggregate evaluator outcome.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum EvaluatorOutcome {
    Pass,
    Warn,
    Iterate,
    Clarify,
    #[serde(rename = "gather_evidence")]
    GatherEvidence,
    Block,
}

/// Lifecycle phase when the evaluator ran.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum EvaluatorPhase {
    #[serde(rename = "after_specify")]
    AfterSpecify,
    #[serde(rename = "after_plan")]
    AfterPlan,
    #[serde(rename = "after_tasks")]
    AfterTasks,
    #[serde(rename = "after_implement")]
    AfterImplement,
    #[serde(rename = "after_analyze")]
    AfterAnalyze,
    #[serde(rename = "after_checklist")]
    AfterChecklist,
    #[serde(rename = "after_clarify")]
    AfterClarify,
    #[serde(rename = "after_constitution")]
    AfterConstitution,
    #[serde(rename = "after_converge")]
    AfterConverge,
    #[serde(rename = "after_taskstoissues")]
    AfterTasksToIssues,
}

/// Metadata about the evaluator that produced a result.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EvaluatorInfo {
    /// Unique evaluator identifier.
    pub id: String,
    /// Semantic version of the evaluator.
    pub version: String,
    /// Human-readable evaluator name.
    #[serde(skip_serializing_if = "String::is_empty", default)]
    pub name: String,
    /// Evaluator homepage or documentation URL.
    #[serde(skip_serializing_if = "String::is_empty", default)]
    pub url: String,
}

/// Recommended next action for the workflow.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct NextAction {
    /// Type of next action.
    pub kind: EvaluatorOutcome,
    /// Target phase to iterate back to (for iterate actions).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub target_phase: Option<String>,
    /// Human-readable message about the next action.
    #[serde(skip_serializing_if = "String::is_empty", default)]
    pub message: String,
}

/// Additional metadata about the evaluation run.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EvaluatorMetadata {
    /// ISO 8601 timestamp of the evaluation.
    #[serde(skip_serializing_if = "String::is_empty", default)]
    pub timestamp: String,
    /// Evaluation duration in milliseconds.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub duration_ms: Option<u64>,
    /// List of artifacts that were evaluated.
    #[serde(skip_serializing_if = "Vec::is_empty", default)]
    pub artifacts_evaluated: Vec<String>,
    /// AI model used for the evaluation, if applicable.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    /// Whether the evaluator is deterministic.
    #[serde(default = "default_deterministic")]
    pub deterministic: bool,
}

fn default_deterministic() -> bool {
    true
}

/// A complete evaluator result conforming to the evaluator contract.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EvaluatorResult {
    /// Schema version, always "1.0".
    pub schema_version: String,
    /// Metadata about the evaluator.
    pub evaluator: EvaluatorInfo,
    /// Lifecycle phase when the evaluator ran.
    pub phase: EvaluatorPhase,
    /// Aggregate evaluator outcome.
    pub outcome: EvaluatorOutcome,
    /// Individual findings from the evaluation.
    pub findings: Vec<Finding>,
    /// One-paragraph human-readable summary.
    #[serde(skip_serializing_if = "String::is_empty", default)]
    pub summary: String,
    /// Recommended next action for the workflow.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub next_action: Option<NextAction>,
    /// Additional metadata about the evaluation run.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub metadata: Option<EvaluatorMetadata>,
    /// Opaque state for pause/resume.
    #[serde(skip_serializing_if = "BTreeMap::is_empty", default)]
    pub state: BTreeMap<String, serde_json::Value>,
}

// ---------------------------------------------------------------------------
// Builder helpers
// ---------------------------------------------------------------------------

impl EvaluatorResult {
    /// Create a minimal evaluator result from a list of findings.
    pub fn from_findings(
        evaluator_id: &str,
        evaluator_version: &str,
        phase: EvaluatorPhase,
        findings: Vec<Finding>,
    ) -> Self {
        let outcome = Self::derive_outcome(&findings);
        let summary = Self::build_summary(evaluator_id, &findings, outcome);
        let next_action = Self::derive_next_action(outcome, None);

        Self {
            schema_version: EVALUATOR_SCHEMA_VERSION.into(),
            evaluator: EvaluatorInfo {
                id: evaluator_id.into(),
                version: evaluator_version.into(),
                name: evaluator_id.into(),
                url: String::new(),
            },
            phase,
            outcome,
            findings,
            summary,
            next_action: Some(next_action),
            metadata: Some(EvaluatorMetadata {
                timestamp: String::new(),
                duration_ms: None,
                artifacts_evaluated: vec![],
                model: None,
                deterministic: true,
            }),
            state: BTreeMap::new(),
        }
    }

    /// Derive the aggregate outcome from a list of findings.
    pub fn derive_outcome(findings: &[Finding]) -> EvaluatorOutcome {
        let has_critical = findings
            .iter()
            .any(|f| f.severity == FindingSeverity::Critical);
        let has_high = findings.iter().any(|f| f.severity == FindingSeverity::High);
        let has_medium = findings
            .iter()
            .any(|f| f.severity == FindingSeverity::Medium);

        if has_critical {
            EvaluatorOutcome::Block
        } else if has_high {
            EvaluatorOutcome::Iterate
        } else if has_medium {
            EvaluatorOutcome::Warn
        } else {
            EvaluatorOutcome::Pass
        }
    }

    /// Derive a NextAction from an outcome.
    pub fn derive_next_action(
        outcome: EvaluatorOutcome,
        target_phase: Option<String>,
    ) -> NextAction {
        let message = match outcome {
            EvaluatorOutcome::Pass => "All checks passed. Proceed to next phase.",
            EvaluatorOutcome::Warn => {
                "Issues found but not blocking. Continue with warnings recorded."
            }
            EvaluatorOutcome::Iterate => "Issues require revisiting a prior phase.",
            EvaluatorOutcome::Clarify => {
                "Ambiguities need human resolution. Pause for human input."
            }
            EvaluatorOutcome::GatherEvidence => {
                "Insufficient evidence to decide. Pause for evidence collection."
            }
            EvaluatorOutcome::Block => "Hard blocker. Cannot proceed.",
        };
        NextAction {
            kind: outcome,
            target_phase: if outcome == EvaluatorOutcome::Iterate {
                target_phase
            } else {
                None
            },
            message: message.into(),
        }
    }

    fn build_summary(
        evaluator_id: &str,
        findings: &[Finding],
        outcome: EvaluatorOutcome,
    ) -> String {
        if findings.is_empty() {
            return format!("{evaluator_id} found no issues.");
        }
        let mut by_sev: BTreeMap<&str, usize> = BTreeMap::new();
        for f in findings {
            let key = match f.severity {
                FindingSeverity::Critical => "critical",
                FindingSeverity::High => "high",
                FindingSeverity::Medium => "medium",
                FindingSeverity::Low => "low",
                FindingSeverity::Info => "info",
            };
            *by_sev.entry(key).or_default() += 1;
        }
        let parts: Vec<String> = ["critical", "high", "medium", "low", "info"]
            .iter()
            .filter_map(|sev| by_sev.get(sev).map(|count| format!("{count} {sev}")))
            .collect();
        format!(
            "{evaluator_id} found {} issue(s) ({}). Outcome: {outcome:?}.",
            findings.len(),
            parts.join(", ")
        )
    }
}

// ---------------------------------------------------------------------------
// Conversion from CheckResult
// ---------------------------------------------------------------------------

impl Finding {
    /// Build a Finding from a CheckResult, mapping verification status to
    /// evaluator-contract fields.
    pub fn from_check_result(
        check: &crate::verification::CheckResult,
        check_kind: &crate::verification::CheckKind,
        subject: &str,
    ) -> Self {
        let (severity, kind, description, recommended_action, evidence_kind, uncertainty) =
            match check.status {
                crate::verification::CheckStatus::Passed => (
                    FindingSeverity::Info,
                    FindingKind::Other,
                    format!("Check '{}' passed.", check.check_id),
                    RecommendedAction::None,
                    EvidenceKind::Observed,
                    Uncertainty::None,
                ),
                crate::verification::CheckStatus::Failed => (
                    FindingSeverity::High,
                    match check_kind {
                        crate::verification::CheckKind::Schema => FindingKind::SchemaViolation,
                        crate::verification::CheckKind::Policy => FindingKind::PolicyViolation,
                        crate::verification::CheckKind::IndependentReview => {
                            FindingKind::UnverifiedAssertion
                        }
                        _ => FindingKind::Other,
                    },
                    format!("Check '{}' failed.", check.check_id),
                    RecommendedAction::Revise,
                    EvidenceKind::Observed,
                    Uncertainty::None,
                ),
                crate::verification::CheckStatus::NotRun => (
                    FindingSeverity::Medium,
                    FindingKind::MissingEvidence,
                    format!("Check '{}' was not run.", check.check_id),
                    RecommendedAction::GatherEvidence,
                    EvidenceKind::Unsupported,
                    Uncertainty::InsufficientEvidence,
                ),
                crate::verification::CheckStatus::Claimed => (
                    FindingSeverity::Medium,
                    FindingKind::UnverifiedAssertion,
                    format!("Check '{}' was claimed but not observed.", check.check_id),
                    RecommendedAction::GatherEvidence,
                    EvidenceKind::Asserted,
                    Uncertainty::Medium,
                ),
            };

        let evidence_refs = if let Some(ref evidence_id) = check.observed_evidence_id {
            vec![EvidenceRef {
                r#ref: evidence_id.clone(),
                kind: evidence_kind,
                description: format!(
                    "{} evidence for check '{}'",
                    match evidence_kind {
                        EvidenceKind::Observed => "Observed",
                        EvidenceKind::Inferred => "Inferred",
                        EvidenceKind::Asserted => "Asserted",
                        EvidenceKind::Contradicted => "Contradicted",
                        EvidenceKind::Unsupported => "Unsupported",
                    },
                    check.check_id
                ),
            }]
        } else {
            vec![]
        };

        Finding {
            id: format!("CHK-{}", &check.check_id[..check.check_id.len().min(8)]),
            severity,
            kind,
            subject: subject.into(),
            description,
            evidence_refs,
            provenance_refs: vec![subject.into()],
            uncertainty: Some(uncertainty),
            recommended_action: Some(recommended_action),
            rationale: String::new(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::verification::{CheckKind, CheckResult, CheckStatus};

    #[test]
    fn derive_outcome_from_findings() {
        let info = Finding {
            id: "F-1".into(),
            severity: FindingSeverity::Info,
            kind: FindingKind::Other,
            subject: "test".into(),
            description: String::new(),
            evidence_refs: vec![],
            provenance_refs: vec![],
            uncertainty: None,
            recommended_action: None,
            rationale: String::new(),
        };
        assert_eq!(
            EvaluatorResult::derive_outcome(&[info.clone()]),
            EvaluatorOutcome::Pass
        );

        let medium = Finding {
            severity: FindingSeverity::Medium,
            ..info.clone()
        };
        assert_eq!(
            EvaluatorResult::derive_outcome(&[medium]),
            EvaluatorOutcome::Warn
        );

        let high = Finding {
            severity: FindingSeverity::High,
            ..info.clone()
        };
        assert_eq!(
            EvaluatorResult::derive_outcome(&[high]),
            EvaluatorOutcome::Iterate
        );

        let critical = Finding {
            severity: FindingSeverity::Critical,
            ..info
        };
        assert_eq!(
            EvaluatorResult::derive_outcome(&[critical]),
            EvaluatorOutcome::Block
        );
    }

    #[test]
    fn finding_from_passed_check_result() {
        let check = CheckResult {
            check_id: "schema".into(),
            status: CheckStatus::Passed,
            observed_evidence_id: Some("sha256:abc".into()),
            verifier_family: None,
            evidence_kind: None,
            uncertainty: None,
            recommended_action: None,
            finding_severity: None,
            finding_kind: None,
            description: None,
            rationale: None,
        };
        let finding = Finding::from_check_result(&check, &CheckKind::Schema, "run-1");
        assert_eq!(finding.severity, FindingSeverity::Info);
        assert_eq!(finding.recommended_action, Some(RecommendedAction::None));
        assert!(!finding.evidence_refs.is_empty());
    }

    #[test]
    fn finding_from_failed_check_result() {
        let check = CheckResult {
            check_id: "policy".into(),
            status: CheckStatus::Failed,
            observed_evidence_id: None,
            verifier_family: None,
            evidence_kind: None,
            uncertainty: None,
            recommended_action: None,
            finding_severity: None,
            finding_kind: None,
            description: None,
            rationale: None,
        };
        let finding = Finding::from_check_result(&check, &CheckKind::Policy, "run-1");
        assert_eq!(finding.severity, FindingSeverity::High);
        assert_eq!(finding.kind, FindingKind::PolicyViolation);
        assert_eq!(finding.recommended_action, Some(RecommendedAction::Revise));
    }

    #[test]
    fn evaluator_result_serializes_to_json() {
        let result = EvaluatorResult::from_findings(
            "noerelay-gateway",
            "1.0.0",
            EvaluatorPhase::AfterImplement,
            vec![],
        );
        let json = serde_json::to_string(&result).unwrap();
        assert!(json.contains("\"schema_version\":\"1.0\""));
        assert!(json.contains("\"outcome\":\"pass\""));
    }
}
