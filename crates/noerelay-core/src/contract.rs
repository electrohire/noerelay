use crate::types::{CanonicalRequest, RiskClass};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use thiserror::Error;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct TaskContract {
    pub contract_version: String,
    pub contract_hash: String,
    pub request_id: String,
    pub outcome: String,
    pub risk: RiskClass,
    pub acceptance_criteria: Vec<String>,
    pub required_capabilities: BTreeSet<String>,
    pub allowed_tools: BTreeSet<String>,
    pub allowed_agents: BTreeSet<String>,
    pub max_cost_microusd: Option<u64>,
    pub max_latency_ms: Option<u64>,
    pub requires_independent_verifier: bool,
    pub requires_human_approval: bool,
    pub context_manifest_hash: Option<String>,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum ContractError {
    #[error("identity scope is invalid: {0}")]
    InvalidScope(String),
    #[error("request must contain at least one non-empty user message")]
    MissingOutcome,
    #[error("high and critical risk requests require explicit acceptance criteria")]
    MissingAcceptanceCriteria,
    #[error("acceptance criteria must be unique and non-empty")]
    InvalidAcceptanceCriteria,
}

#[derive(Debug, Default, Clone, Copy)]
pub struct ContractCompiler;

impl ContractCompiler {
    pub fn compile(&self, request: &CanonicalRequest) -> Result<TaskContract, ContractError> {
        request
            .scope
            .validate()
            .map_err(|error| ContractError::InvalidScope(error.to_string()))?;

        let outcome = request
            .messages
            .iter()
            .rev()
            .find(|message| {
                matches!(message.role, crate::types::MessageRole::User)
                    && !message.content.trim().is_empty()
            })
            .map(|message| message.content.trim().to_owned())
            .ok_or(ContractError::MissingOutcome)?;

        if matches!(request.risk, RiskClass::High | RiskClass::Critical)
            && request.acceptance_criteria.is_empty()
        {
            return Err(ContractError::MissingAcceptanceCriteria);
        }

        let acceptance_criteria = normalized_unique(&request.acceptance_criteria)
            .ok_or(ContractError::InvalidAcceptanceCriteria)?;

        #[derive(Serialize)]
        struct HashMaterial<'a> {
            version: &'a str,
            request: &'a CanonicalRequest,
            outcome: &'a str,
            acceptance_criteria: &'a [String],
        }

        let material = serde_json::to_vec(&HashMaterial {
            version: "1.0.0",
            request,
            outcome: &outcome,
            acceptance_criteria: &acceptance_criteria,
        })
        .expect("serializing known contract material cannot fail");
        let contract_hash = hex::encode(Sha256::digest(material));

        Ok(TaskContract {
            contract_version: "1.0.0".into(),
            contract_hash,
            request_id: request.request_id.clone(),
            outcome,
            risk: request.risk,
            acceptance_criteria,
            required_capabilities: normalized_set(&request.required_capabilities),
            allowed_tools: normalized_set(&request.allowed_tools),
            allowed_agents: normalized_set(&request.allowed_agents),
            max_cost_microusd: request.max_cost_microusd,
            max_latency_ms: request.max_latency_ms,
            requires_independent_verifier: matches!(
                request.risk,
                RiskClass::High | RiskClass::Critical
            ),
            requires_human_approval: request.risk == RiskClass::Critical,
            context_manifest_hash: request.metadata.get("context_manifest_hash").cloned(),
        })
    }
}

fn normalized_set(values: &[String]) -> BTreeSet<String> {
    values
        .iter()
        .map(|value| value.trim())
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .collect()
}

fn normalized_unique(values: &[String]) -> Option<Vec<String>> {
    let normalized: Vec<String> = values.iter().map(|value| value.trim().to_owned()).collect();
    if normalized.iter().any(String::is_empty) {
        return None;
    }
    let unique: BTreeSet<&str> = normalized.iter().map(String::as_str).collect();
    (unique.len() == normalized.len()).then_some(normalized)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{DataClass, IdentityScope, Message, MessageRole};
    use std::collections::BTreeMap;

    fn request(risk: RiskClass) -> CanonicalRequest {
        CanonicalRequest {
            request_id: "req-1".into(),
            scope: IdentityScope {
                organization_id: "org".into(),
                project_id: "project".into(),
                environment_id: "test".into(),
                user_id: "user".into(),
                session_id: "session".into(),
            },
            messages: vec![Message {
                role: MessageRole::User,
                content: " Build the endpoint. ".into(),
                name: None,
                tool_call_id: None,
            }],
            risk,
            data_class: DataClass::Internal,
            acceptance_criteria: vec!["Tests pass".into()],
            required_capabilities: vec!["coding".into(), "coding".into()],
            allowed_tools: vec![],
            allowed_agents: vec![],
            metadata: BTreeMap::new(),
            max_cost_microusd: Some(50_000),
            max_latency_ms: Some(30_000),
        }
    }

    #[test]
    fn compiles_deterministically() {
        let compiler = ContractCompiler;
        let left = compiler.compile(&request(RiskClass::High)).unwrap();
        let right = compiler.compile(&request(RiskClass::High)).unwrap();
        assert_eq!(left, right);
        assert_eq!(left.outcome, "Build the endpoint.");
        assert!(left.requires_independent_verifier);
        assert_eq!(left.required_capabilities.len(), 1);
    }

    #[test]
    fn high_risk_requires_acceptance() {
        let mut value = request(RiskClass::High);
        value.acceptance_criteria.clear();
        assert_eq!(
            ContractCompiler.compile(&value),
            Err(ContractError::MissingAcceptanceCriteria)
        );
    }

    #[test]
    fn low_risk_can_use_empty_acceptance() {
        let mut value = request(RiskClass::Low);
        value.acceptance_criteria.clear();
        assert!(ContractCompiler.compile(&value).is_ok());
    }

    #[test]
    fn duplicate_acceptance_is_rejected() {
        let mut value = request(RiskClass::Medium);
        value.acceptance_criteria = vec!["same".into(), "same".into()];
        assert_eq!(
            ContractCompiler.compile(&value),
            Err(ContractError::InvalidAcceptanceCriteria)
        );
    }
}
