use crate::types::RiskClass;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet, VecDeque};
use thiserror::Error;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum CheckKind {
    Schema,
    Policy,
    DeterministicAcceptance,
    IndependentReview,
    HumanApproval,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct VerificationCheck {
    pub check_id: String,
    pub kind: CheckKind,
    pub depends_on: BTreeSet<String>,
    pub verifier_family: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum CheckStatus {
    Passed,
    Failed,
    NotRun,
    Claimed,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CheckResult {
    pub check_id: String,
    pub status: CheckStatus,
    pub observed_evidence_id: Option<String>,
    pub verifier_family: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ReleaseOutcome {
    Accepted,
    RepairRequired,
    EscalationRequired,
    HumanApprovalRequired,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum VerificationError {
    #[error("duplicate check {0}")]
    DuplicateCheck(String),
    #[error("check {check_id} depends on unknown check {dependency}")]
    UnknownDependency {
        check_id: String,
        dependency: String,
    },
    #[error("verification graph contains a cycle")]
    Cycle,
    #[error("result references unknown check {0}")]
    UnknownResult(String),
    #[error("duplicate result for check {0}")]
    DuplicateResult(String),
    #[error("check {0} was reported before its dependencies passed")]
    DependencyNotPassed(String),
    #[error("high-risk independent review reused worker family {0}")]
    NonIndependentVerifier(String),
}

#[derive(Debug, Clone)]
pub struct VerificationDag {
    checks: BTreeMap<String, VerificationCheck>,
    order: Vec<String>,
}

impl VerificationDag {
    pub fn new(
        checks: impl IntoIterator<Item = VerificationCheck>,
    ) -> Result<Self, VerificationError> {
        let mut by_id = BTreeMap::new();
        for check in checks {
            let id = check.check_id.clone();
            if by_id.insert(id.clone(), check).is_some() {
                return Err(VerificationError::DuplicateCheck(id));
            }
        }
        for check in by_id.values() {
            for dependency in &check.depends_on {
                if !by_id.contains_key(dependency) {
                    return Err(VerificationError::UnknownDependency {
                        check_id: check.check_id.clone(),
                        dependency: dependency.clone(),
                    });
                }
            }
        }
        let order = topological_order(&by_id)?;
        Ok(Self {
            checks: by_id,
            order,
        })
    }

    pub fn order(&self) -> &[String] {
        &self.order
    }

    pub fn evaluate(
        &self,
        risk: RiskClass,
        worker_family: &str,
        results: &[CheckResult],
    ) -> Result<ReleaseOutcome, VerificationError> {
        let mut by_id: BTreeMap<&str, &CheckResult> = BTreeMap::new();
        for result in results {
            if !self.checks.contains_key(&result.check_id) {
                return Err(VerificationError::UnknownResult(result.check_id.clone()));
            }
            if by_id.insert(&result.check_id, result).is_some() {
                return Err(VerificationError::DuplicateResult(result.check_id.clone()));
            }
        }

        for check_id in &self.order {
            let check = &self.checks[check_id];
            let Some(result) = by_id.get(check_id.as_str()) else {
                continue;
            };
            if check.depends_on.iter().any(|dependency| {
                by_id
                    .get(dependency.as_str())
                    .is_none_or(|value| value.status != CheckStatus::Passed)
            }) {
                return Err(VerificationError::DependencyNotPassed(check_id.clone()));
            }
            if check.kind == CheckKind::IndependentReview
                && matches!(risk, RiskClass::High | RiskClass::Critical)
                && result.verifier_family.as_deref() == Some(worker_family)
            {
                return Err(VerificationError::NonIndependentVerifier(
                    worker_family.into(),
                ));
            }
        }

        if self.checks.values().any(|check| {
            by_id
                .get(check.check_id.as_str())
                .is_some_and(|result| result.status == CheckStatus::Failed)
        }) {
            return Ok(ReleaseOutcome::RepairRequired);
        }
        if matches!(risk, RiskClass::Critical)
            && !passed_kind(&self.checks, &by_id, CheckKind::HumanApproval)
        {
            return Ok(ReleaseOutcome::HumanApprovalRequired);
        }
        if matches!(risk, RiskClass::High | RiskClass::Critical)
            && !passed_kind(&self.checks, &by_id, CheckKind::IndependentReview)
        {
            return Ok(ReleaseOutcome::EscalationRequired);
        }
        let all_required_passed = self.checks.values().all(|check| {
            by_id.get(check.check_id.as_str()).is_some_and(|result| {
                result.status == CheckStatus::Passed && result.observed_evidence_id.is_some()
            })
        });
        if all_required_passed {
            Ok(ReleaseOutcome::Accepted)
        } else {
            Ok(ReleaseOutcome::RepairRequired)
        }
    }
}

fn passed_kind(
    checks: &BTreeMap<String, VerificationCheck>,
    results: &BTreeMap<&str, &CheckResult>,
    kind: CheckKind,
) -> bool {
    checks.values().any(|check| {
        check.kind == kind
            && results.get(check.check_id.as_str()).is_some_and(|result| {
                result.status == CheckStatus::Passed && result.observed_evidence_id.is_some()
            })
    })
}

fn topological_order(
    checks: &BTreeMap<String, VerificationCheck>,
) -> Result<Vec<String>, VerificationError> {
    let mut indegree: BTreeMap<&str, usize> = checks
        .iter()
        .map(|(id, check)| (id.as_str(), check.depends_on.len()))
        .collect();
    let mut dependents: BTreeMap<&str, Vec<&str>> = BTreeMap::new();
    for (id, check) in checks {
        for dependency in &check.depends_on {
            dependents.entry(dependency).or_default().push(id.as_str());
        }
    }
    let mut ready: VecDeque<&str> = indegree
        .iter()
        .filter_map(|(id, count)| (*count == 0).then_some(*id))
        .collect();
    let mut order = Vec::new();
    while let Some(id) = ready.pop_front() {
        order.push(id.to_owned());
        if let Some(items) = dependents.get(id) {
            for dependent in items {
                let count = indegree.get_mut(dependent).expect("dependent is known");
                *count -= 1;
                if *count == 0 {
                    ready.push_back(dependent);
                }
            }
        }
    }
    if order.len() != checks.len() {
        return Err(VerificationError::Cycle);
    }
    Ok(order)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn check(id: &str, kind: CheckKind, dependencies: &[&str]) -> VerificationCheck {
        VerificationCheck {
            check_id: id.into(),
            kind,
            depends_on: dependencies.iter().map(|value| (*value).into()).collect(),
            verifier_family: None,
        }
    }

    fn passed(id: &str, family: Option<&str>) -> CheckResult {
        CheckResult {
            check_id: id.into(),
            status: CheckStatus::Passed,
            observed_evidence_id: Some(format!("evidence-{id}")),
            verifier_family: family.map(str::to_owned),
        }
    }

    #[test]
    fn deterministic_checks_are_ordered_before_review() {
        let dag = VerificationDag::new([
            check("review", CheckKind::IndependentReview, &["tests"]),
            check("schema", CheckKind::Schema, &[]),
            check("tests", CheckKind::DeterministicAcceptance, &["schema"]),
        ])
        .unwrap();
        assert_eq!(dag.order(), &["schema", "tests", "review"]);
    }

    #[test]
    fn cycles_fail_closed() {
        let result = VerificationDag::new([
            check("a", CheckKind::Schema, &["b"]),
            check("b", CheckKind::Policy, &["a"]),
        ]);
        assert!(matches!(result, Err(VerificationError::Cycle)));
    }

    #[test]
    fn high_risk_rejects_same_family_review() {
        let dag = VerificationDag::new([
            check("schema", CheckKind::Schema, &[]),
            check("review", CheckKind::IndependentReview, &["schema"]),
        ])
        .unwrap();
        let result = dag.evaluate(
            RiskClass::High,
            "family-a",
            &[passed("schema", None), passed("review", Some("family-a"))],
        );
        assert_eq!(
            result,
            Err(VerificationError::NonIndependentVerifier("family-a".into()))
        );
    }

    #[test]
    fn claim_without_observed_evidence_cannot_accept() {
        let dag = VerificationDag::new([check("schema", CheckKind::Schema, &[])]).unwrap();
        let result = dag
            .evaluate(
                RiskClass::Low,
                "family-a",
                &[CheckResult {
                    check_id: "schema".into(),
                    status: CheckStatus::Claimed,
                    observed_evidence_id: None,
                    verifier_family: None,
                }],
            )
            .unwrap();
        assert_eq!(result, ReleaseOutcome::RepairRequired);
    }

    #[test]
    fn critical_requires_human_approval() {
        let dag = VerificationDag::new([
            check("schema", CheckKind::Schema, &[]),
            check("review", CheckKind::IndependentReview, &["schema"]),
            check("approval", CheckKind::HumanApproval, &["review"]),
        ])
        .unwrap();
        let result = dag
            .evaluate(
                RiskClass::Critical,
                "worker",
                &[
                    passed("schema", None),
                    passed("review", Some("independent")),
                ],
            )
            .unwrap();
        assert_eq!(result, ReleaseOutcome::HumanApprovalRequired);
    }
}
