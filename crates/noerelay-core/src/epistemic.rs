use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum EpistemicState {
    Neither,
    Supported,
    Refuted,
    Both,
}

impl EpistemicState {
    pub fn merge(self, other: Self) -> Self {
        Self::from_bits(self.bits() | other.bits())
    }

    pub fn apply(self, polarity: EvidencePolarity) -> Self {
        self.merge(match polarity {
            EvidencePolarity::Supports => Self::Supported,
            EvidencePolarity::Refutes => Self::Refuted,
        })
    }

    pub fn is_release_blocking(self) -> bool {
        matches!(self, Self::Refuted | Self::Both | Self::Neither)
    }

    fn bits(self) -> u8 {
        match self {
            Self::Neither => 0b00,
            Self::Supported => 0b01,
            Self::Refuted => 0b10,
            Self::Both => 0b11,
        }
    }

    fn from_bits(bits: u8) -> Self {
        match bits & 0b11 {
            0b00 => Self::Neither,
            0b01 => Self::Supported,
            0b10 => Self::Refuted,
            _ => Self::Both,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum EvidencePolarity {
    Supports,
    Refutes,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ClaimKind {
    Fact,
    Requirement,
    Decision,
    Assumption,
    Observation,
    Prediction,
    Preference,
    Artifact,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Claim {
    pub claim_id: String,
    pub kind: ClaimKind,
    pub statement: String,
    pub state: EpistemicState,
    pub supporting_evidence: Vec<String>,
    pub refuting_evidence: Vec<String>,
}

impl Claim {
    pub fn observe(&mut self, evidence_id: String, polarity: EvidencePolarity) {
        let collection = match polarity {
            EvidencePolarity::Supports => &mut self.supporting_evidence,
            EvidencePolarity::Refutes => &mut self.refuting_evidence,
        };
        if !collection.contains(&evidence_id) {
            collection.push(evidence_id);
            collection.sort();
        }
        self.state = self.state.apply(polarity);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn four_valued_merge_preserves_contradiction() {
        assert_eq!(
            EpistemicState::Supported.merge(EpistemicState::Refuted),
            EpistemicState::Both
        );
        assert_eq!(
            EpistemicState::Neither.merge(EpistemicState::Supported),
            EpistemicState::Supported
        );
        assert_eq!(
            EpistemicState::Both.merge(EpistemicState::Supported),
            EpistemicState::Both
        );
    }

    #[test]
    fn evidence_is_deduplicated_without_erasing_conflict() {
        let mut claim = Claim {
            claim_id: "claim".into(),
            kind: ClaimKind::Fact,
            statement: "The tests pass".into(),
            state: EpistemicState::Neither,
            supporting_evidence: vec![],
            refuting_evidence: vec![],
        };
        claim.observe("test-pass".into(), EvidencePolarity::Supports);
        claim.observe("test-pass".into(), EvidencePolarity::Supports);
        claim.observe("hidden-failure".into(), EvidencePolarity::Refutes);
        assert_eq!(claim.state, EpistemicState::Both);
        assert_eq!(claim.supporting_evidence, vec!["test-pass"]);
        assert_eq!(claim.refuting_evidence, vec!["hidden-failure"]);
        assert!(claim.state.is_release_blocking());
    }
}
