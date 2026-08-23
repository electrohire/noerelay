use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use thiserror::Error;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Requirement {
    pub requirement_id: String,
    pub architecture_refs: BTreeSet<String>,
    pub outcome: String,
    pub must: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct TestCase {
    pub test_id: String,
    pub requirement_ids: BTreeSet<String>,
    pub independent: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum EvidenceStatus {
    ObservedPass,
    ObservedFail,
    NotRun,
    Claimed,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Evidence {
    pub evidence_id: String,
    pub test_id: String,
    pub source_revision: String,
    pub artifact_hash: String,
    pub status: EvidenceStatus,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum TraceError {
    #[error("duplicate requirement {0}")]
    DuplicateRequirement(String),
    #[error("duplicate test {0}")]
    DuplicateTest(String),
    #[error("duplicate evidence {0}")]
    DuplicateEvidence(String),
    #[error("test {test_id} references unknown requirement {requirement_id}")]
    UnknownRequirement {
        test_id: String,
        requirement_id: String,
    },
    #[error("evidence {evidence_id} references unknown test {test_id}")]
    UnknownTest {
        evidence_id: String,
        test_id: String,
    },
    #[error("mandatory requirement {0} has no linked test")]
    UntestedRequirement(String),
    #[error("mandatory requirement {0} has no observed passing evidence")]
    UnverifiedRequirement(String),
}

#[derive(Debug, Clone, Default)]
pub struct TraceGraph {
    requirements: BTreeMap<String, Requirement>,
    tests: BTreeMap<String, TestCase>,
    evidence: BTreeMap<String, Evidence>,
}

impl TraceGraph {
    pub fn new(
        requirements: impl IntoIterator<Item = Requirement>,
        tests: impl IntoIterator<Item = TestCase>,
        evidence: impl IntoIterator<Item = Evidence>,
    ) -> Result<Self, TraceError> {
        let mut graph = Self::default();
        for requirement in requirements {
            let id = requirement.requirement_id.clone();
            if graph.requirements.insert(id.clone(), requirement).is_some() {
                return Err(TraceError::DuplicateRequirement(id));
            }
        }
        for test in tests {
            let id = test.test_id.clone();
            for requirement_id in &test.requirement_ids {
                if !graph.requirements.contains_key(requirement_id) {
                    return Err(TraceError::UnknownRequirement {
                        test_id: id,
                        requirement_id: requirement_id.clone(),
                    });
                }
            }
            if graph.tests.insert(id.clone(), test).is_some() {
                return Err(TraceError::DuplicateTest(id));
            }
        }
        for item in evidence {
            let id = item.evidence_id.clone();
            if !graph.tests.contains_key(&item.test_id) {
                return Err(TraceError::UnknownTest {
                    evidence_id: id,
                    test_id: item.test_id,
                });
            }
            if graph.evidence.insert(id.clone(), item).is_some() {
                return Err(TraceError::DuplicateEvidence(id));
            }
        }
        Ok(graph)
    }

    pub fn release_gate(&self) -> Result<(), TraceError> {
        for requirement in self.requirements.values().filter(|item| item.must) {
            let linked_tests: Vec<&TestCase> = self
                .tests
                .values()
                .filter(|test| test.requirement_ids.contains(&requirement.requirement_id))
                .collect();
            if linked_tests.is_empty() {
                return Err(TraceError::UntestedRequirement(
                    requirement.requirement_id.clone(),
                ));
            }
            let passed = linked_tests.iter().any(|test| {
                self.evidence.values().any(|evidence| {
                    evidence.test_id == test.test_id
                        && evidence.status == EvidenceStatus::ObservedPass
                        && !evidence.source_revision.is_empty()
                        && !evidence.artifact_hash.is_empty()
                })
            });
            if !passed {
                return Err(TraceError::UnverifiedRequirement(
                    requirement.requirement_id.clone(),
                ));
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn requirement() -> Requirement {
        Requirement {
            requirement_id: "NR-1".into(),
            architecture_refs: BTreeSet::from(["core".into()]),
            outcome: "It works".into(),
            must: true,
        }
    }

    fn test_case() -> TestCase {
        TestCase {
            test_id: "T-1".into(),
            requirement_ids: BTreeSet::from(["NR-1".into()]),
            independent: true,
        }
    }

    #[test]
    fn observed_evidence_releases() {
        let graph = TraceGraph::new(
            [requirement()],
            [test_case()],
            [Evidence {
                evidence_id: "E-1".into(),
                test_id: "T-1".into(),
                source_revision: "abc".into(),
                artifact_hash: "def".into(),
                status: EvidenceStatus::ObservedPass,
            }],
        )
        .unwrap();
        assert_eq!(graph.release_gate(), Ok(()));
    }

    #[test]
    fn model_claim_is_not_evidence() {
        let graph = TraceGraph::new(
            [requirement()],
            [test_case()],
            [Evidence {
                evidence_id: "E-1".into(),
                test_id: "T-1".into(),
                source_revision: "abc".into(),
                artifact_hash: "def".into(),
                status: EvidenceStatus::Claimed,
            }],
        )
        .unwrap();
        assert_eq!(
            graph.release_gate(),
            Err(TraceError::UnverifiedRequirement("NR-1".into()))
        );
    }

    #[test]
    fn orphan_test_is_rejected() {
        let mut test = test_case();
        test.requirement_ids = BTreeSet::from(["missing".into()]);
        assert!(matches!(
            TraceGraph::new([requirement()], [test], []),
            Err(TraceError::UnknownRequirement { .. })
        ));
    }
}
