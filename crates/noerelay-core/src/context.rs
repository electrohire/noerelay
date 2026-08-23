use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::cmp::Reverse;
use thiserror::Error;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum NodeKind {
    Requirement,
    Decision,
    Contradiction,
    Approval,
    EvidenceHandle,
    ToolState,
    Observation,
    Conversation,
    Summary,
}

impl NodeKind {
    pub fn protected_by_default(self) -> bool {
        matches!(
            self,
            Self::Requirement
                | Self::Decision
                | Self::Contradiction
                | Self::Approval
                | Self::EvidenceHandle
                | Self::ToolState
        )
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ContextNode {
    pub node_id: String,
    pub kind: NodeKind,
    pub content: String,
    pub source_handle: String,
    pub estimated_tokens: u32,
    pub salience_ppm: u32,
    pub sequence: u64,
    #[serde(default)]
    pub explicitly_protected: bool,
}

impl ContextNode {
    pub fn is_protected(&self) -> bool {
        self.explicitly_protected || self.kind.protected_by_default()
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ContextManifest {
    pub budget_tokens: u32,
    pub used_tokens: u32,
    pub included: Vec<ContextNode>,
    pub omitted_node_ids: Vec<String>,
    pub manifest_hash: String,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum ContextError {
    #[error("protected context requires {required} tokens but budget is {budget}")]
    ProtectedContextExceedsBudget { required: u32, budget: u32 },
    #[error("context token arithmetic overflowed")]
    ArithmeticOverflow,
}

#[derive(Debug, Default, Clone, Copy)]
pub struct ContextCompiler;

impl ContextCompiler {
    pub fn compile(
        &self,
        nodes: &[ContextNode],
        budget_tokens: u32,
    ) -> Result<ContextManifest, ContextError> {
        let mut protected: Vec<ContextNode> = nodes
            .iter()
            .filter(|node| node.is_protected())
            .cloned()
            .collect();
        protected.sort_by_key(|node| (node.sequence, node.node_id.clone()));
        let required = protected
            .iter()
            .try_fold(0_u32, |sum, node| sum.checked_add(node.estimated_tokens));
        let Some(mut used_tokens) = required else {
            return Err(ContextError::ArithmeticOverflow);
        };
        if used_tokens > budget_tokens {
            return Err(ContextError::ProtectedContextExceedsBudget {
                required: used_tokens,
                budget: budget_tokens,
            });
        }

        let mut optional: Vec<ContextNode> = nodes
            .iter()
            .filter(|node| !node.is_protected())
            .cloned()
            .collect();
        optional.sort_by_key(|node| {
            (
                Reverse(node.salience_ppm),
                Reverse(node.sequence),
                node.node_id.clone(),
            )
        });

        let mut included = protected;
        let mut omitted_node_ids = Vec::new();
        for node in optional {
            let Some(next) = used_tokens.checked_add(node.estimated_tokens) else {
                return Err(ContextError::ArithmeticOverflow);
            };
            if next <= budget_tokens {
                used_tokens = next;
                included.push(node);
            } else {
                omitted_node_ids.push(node.node_id);
            }
        }
        included.sort_by_key(|node| (node.sequence, node.node_id.clone()));
        omitted_node_ids.sort();

        #[derive(Serialize)]
        struct Material<'a> {
            budget_tokens: u32,
            used_tokens: u32,
            included: &'a [ContextNode],
            omitted_node_ids: &'a [String],
        }
        let material = serde_json::to_vec(&Material {
            budget_tokens,
            used_tokens,
            included: &included,
            omitted_node_ids: &omitted_node_ids,
        })
        .expect("known context material is serializable");
        let manifest_hash = hex::encode(Sha256::digest(material));
        Ok(ContextManifest {
            budget_tokens,
            used_tokens,
            included,
            omitted_node_ids,
            manifest_hash,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn node(id: &str, kind: NodeKind, tokens: u32, salience: u32, sequence: u64) -> ContextNode {
        ContextNode {
            node_id: id.into(),
            kind,
            content: format!("content-{id}"),
            source_handle: format!("ledger:{id}"),
            estimated_tokens: tokens,
            salience_ppm: salience,
            sequence,
            explicitly_protected: false,
        }
    }

    #[test]
    fn protected_nodes_survive_pressure() {
        let nodes = vec![
            node("chat", NodeKind::Conversation, 50, 999_999, 2),
            node("requirement", NodeKind::Requirement, 10, 1, 1),
        ];
        let manifest = ContextCompiler.compile(&nodes, 10).unwrap();
        assert_eq!(manifest.included[0].node_id, "requirement");
        assert_eq!(manifest.omitted_node_ids, vec!["chat"]);
    }

    #[test]
    fn impossible_protected_budget_fails_closed() {
        let error = ContextCompiler
            .compile(&[node("decision", NodeKind::Decision, 11, 1, 1)], 10)
            .unwrap_err();
        assert_eq!(
            error,
            ContextError::ProtectedContextExceedsBudget {
                required: 11,
                budget: 10
            }
        );
    }

    #[test]
    fn optional_selection_prefers_salience_then_recency() {
        let nodes = vec![
            node("old-low", NodeKind::Conversation, 5, 10, 1),
            node("new-high", NodeKind::Observation, 5, 20, 3),
            node("old-high", NodeKind::Observation, 5, 20, 2),
        ];
        let manifest = ContextCompiler.compile(&nodes, 10).unwrap();
        let ids: Vec<&str> = manifest
            .included
            .iter()
            .map(|node| node.node_id.as_str())
            .collect();
        assert_eq!(ids, vec!["old-high", "new-high"]);
    }

    #[test]
    fn manifest_hash_is_deterministic() {
        let nodes = vec![node("r", NodeKind::Requirement, 5, 1, 1)];
        assert_eq!(
            ContextCompiler.compile(&nodes, 10).unwrap().manifest_hash,
            ContextCompiler.compile(&nodes, 10).unwrap().manifest_hash
        );
    }
}
