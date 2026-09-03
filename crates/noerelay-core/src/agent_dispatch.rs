//! Governed local-agent dispatch per mission §9.
//!
//! Each local agent is registered by immutable revision with endpoint, trust root,
//! capabilities, permitted model revisions, provider family, maximum data classification,
//! filesystem/network/tool permissions, context and budget ceilings, runtime,
//! delegation/fan-out/cycle limits, and verification restrictions.

use crate::types::DataClass;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use thiserror::Error;

/// Agent dispatch configuration bound to a specific agent revision.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct AgentDispatchConfig {
    /// Agent revision identifier.
    pub agent_id: String,
    /// Immutable revision number.
    pub revision: i32,
    /// Cryptographic hash of the revision.
    pub revision_hash: String,
    /// Network endpoint of the agent runtime.
    pub endpoint: String,
    /// Cryptographic trust anchor for agent authentication.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub trust_root: Option<String>,
    /// Declared capabilities.
    pub capabilities: Vec<String>,
    /// Permitted model revisions this agent may use.
    pub allowed_models: Vec<String>,
    /// Provider family for verification independence checks.
    pub provider_family: String,
    /// Maximum data classification this agent may handle.
    pub maximum_data_class: String,
    /// Filesystem permissions.
    #[serde(default)]
    pub filesystem_permissions: Vec<String>,
    /// Network permissions.
    #[serde(default)]
    pub network_permissions: Vec<String>,
    /// Tool permissions.
    #[serde(default)]
    pub tool_permissions: Vec<String>,
    /// Maximum context window in tokens.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub context_budget_tokens: Option<u64>,
    /// Per-run budget ceiling in micro-USD.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub budget_ceiling_microusd: Option<u64>,
    /// Runtime identifier (vLLM, Ollama, SGLang).
    pub runtime: String,
    /// Maximum delegation/fan-out depth.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub delegation_limit: Option<u32>,
    /// Maximum repair/retry cycles.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cycle_limit: Option<u32>,
    /// Required verifier families for this agent's output.
    #[serde(default)]
    pub verification_restrictions: Vec<String>,
}

/// Status of an agent dispatch.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum DispatchStatus {
    /// Dispatch is pending authentication.
    PendingAuth,
    /// Dispatch is authorized and in progress.
    InProgress,
    /// Dispatch completed successfully.
    Completed,
    /// Dispatch failed.
    Failed,
    /// Dispatch was cancelled.
    Cancelled,
    /// Dispatch was rejected (policy violation).
    Rejected,
}

/// Result of an agent dispatch.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct DispatchResult {
    /// Unique dispatch identifier.
    pub dispatch_id: String,
    /// Idempotency key for duplicate detection.
    pub idempotency_key: String,
    /// Agent and revision dispatched.
    pub agent_id: String,
    pub revision: i32,
    /// Current status.
    pub status: DispatchStatus,
    /// Raw stdout/stderr artifact reference.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub raw_output_artifact_id: Option<String>,
    /// Raw output SHA-256.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub raw_output_sha256: Option<String>,
    /// Compact (RTK-derived) artifact reference.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub compact_output_artifact_id: Option<String>,
    /// Compact output SHA-256.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub compact_output_sha256: Option<String>,
    /// Exit code of the dispatched command.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub exit_code: Option<i32>,
    /// Error message if dispatch failed.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum DispatchError {
    #[error("agent {0} revision {1} is not active")]
    AgentNotActive(String, i32),
    #[error("agent {0} is quarantined: {1}")]
    AgentQuarantined(String, String),
    #[error("agent {0} is superseded by {1}")]
    AgentSuperseded(String, String),
    #[error("capability {0} is not in agent's declared capabilities")]
    MissingCapability(String),
    #[error("model {0} is not in agent's allowed models")]
    ModelNotAllowed(String),
    #[error("data class {0} exceeds agent's maximum {1}")]
    DataClassExceeded(String, String),
    #[error("budget ceiling {0} µUSD exceeded by reservation {1} µUSD")]
    BudgetExceeded(u64, u64),
    #[error("delegation depth {0} exceeds limit {1}")]
    DelegationDepthExceeded(u32, u32),
    #[error("cycle count {0} exceeds limit {1}")]
    CycleLimitExceeded(u32, u32),
    #[error("duplicate idempotency key: {0}")]
    DuplicateIdempotencyKey(String),
    #[error("agent endpoint is not configured")]
    NoEndpoint,
}

/// Validate that an agent dispatch config is valid for a given task.
pub fn validate_dispatch(
    config: &AgentDispatchConfig,
    required_capabilities: &[String],
    requested_model: Option<&str>,
    data_class: DataClass,
    budget_microusd: u64,
    delegation_depth: u32,
    cycle_count: u32,
) -> Result<(), DispatchError> {
    // Check capabilities
    for cap in required_capabilities {
        if !config.capabilities.iter().any(|c| c == cap) {
            return Err(DispatchError::MissingCapability(cap.clone()));
        }
    }

    // Check model allowlist
    if let Some(model) = requested_model {
        if !config.allowed_models.iter().any(|m| m == model) {
            return Err(DispatchError::ModelNotAllowed(model.into()));
        }
    }

    // Check data classification using the DataClass ordering
    let agent_max_class = parse_data_class(&config.maximum_data_class);
    if data_class > agent_max_class {
        return Err(DispatchError::DataClassExceeded(
            format!("{:?}", data_class),
            config.maximum_data_class.clone(),
        ));
    }

    // Check budget
    if let Some(ceiling) = config.budget_ceiling_microusd {
        if budget_microusd > ceiling {
            return Err(DispatchError::BudgetExceeded(ceiling, budget_microusd));
        }
    }

    // Check delegation depth
    if let Some(limit) = config.delegation_limit {
        if delegation_depth > limit {
            return Err(DispatchError::DelegationDepthExceeded(
                delegation_depth,
                limit,
            ));
        }
    }

    // Check cycle limit
    if let Some(limit) = config.cycle_limit {
        if cycle_count > limit {
            return Err(DispatchError::CycleLimitExceeded(cycle_count, limit));
        }
    }

    // Check endpoint
    if config.endpoint.is_empty() {
        return Err(DispatchError::NoEndpoint);
    }

    Ok(())
}

fn parse_data_class(s: &str) -> DataClass {
    match s {
        "public" => DataClass::Public,
        "internal" => DataClass::Internal,
        "confidential" => DataClass::Confidential,
        "restricted" => DataClass::Restricted,
        _ => DataClass::Public,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::DataClass;

    fn test_config() -> AgentDispatchConfig {
        AgentDispatchConfig {
            agent_id: "agent-1".into(),
            revision: 1,
            revision_hash: "abc123".into(),
            endpoint: "http://127.0.0.1:9000".into(),
            trust_root: Some("sha256:def456".into()),
            capabilities: vec!["coding".into(), "reasoning".into()],
            allowed_models: vec!["qwen3-coder:30b".into()],
            provider_family: "ollama".into(),
            maximum_data_class: "confidential".into(),
            filesystem_permissions: vec!["/workspace".into()],
            network_permissions: vec!["github.com".into()],
            tool_permissions: vec!["cargo".into(), "git".into()],
            context_budget_tokens: Some(131_072),
            budget_ceiling_microusd: Some(10_000),
            runtime: "ollama".into(),
            delegation_limit: Some(3),
            cycle_limit: Some(5),
            verification_restrictions: vec!["independent-review".into()],
        }
    }

    #[test]
    fn valid_dispatch_passes() {
        assert_eq!(
            validate_dispatch(
                &test_config(),
                &["coding".into()],
                Some("qwen3-coder:30b"),
                DataClass::Internal,
                1_000,
                1,
                1,
            ),
            Ok(())
        );
    }

    #[test]
    fn missing_capability_is_rejected() {
        assert_eq!(
            validate_dispatch(
                &test_config(),
                &["vision".into()],
                None,
                DataClass::Internal,
                1_000,
                0,
                0,
            ),
            Err(DispatchError::MissingCapability("vision".into()))
        );
    }

    #[test]
    fn disallowed_model_is_rejected() {
        assert_eq!(
            validate_dispatch(
                &test_config(),
                &["coding".into()],
                Some("gpt-5"),
                DataClass::Internal,
                1_000,
                0,
                0,
            ),
            Err(DispatchError::ModelNotAllowed("gpt-5".into()))
        );
    }

    #[test]
    fn budget_exceeded_is_rejected() {
        assert_eq!(
            validate_dispatch(
                &test_config(),
                &["coding".into()],
                None,
                DataClass::Internal,
                20_000,
                0,
                0,
            ),
            Err(DispatchError::BudgetExceeded(10_000, 20_000))
        );
    }

    #[test]
    fn delegation_depth_exceeded_is_rejected() {
        assert_eq!(
            validate_dispatch(
                &test_config(),
                &["coding".into()],
                None,
                DataClass::Internal,
                1_000,
                5,
                0,
            ),
            Err(DispatchError::DelegationDepthExceeded(5, 3))
        );
    }

    #[test]
    fn cycle_limit_exceeded_is_rejected() {
        assert_eq!(
            validate_dispatch(
                &test_config(),
                &["coding".into()],
                None,
                DataClass::Internal,
                1_000,
                0,
                10,
            ),
            Err(DispatchError::CycleLimitExceeded(10, 5))
        );
    }

    #[test]
    fn no_endpoint_is_rejected() {
        let mut config = test_config();
        config.endpoint = String::new();
        assert_eq!(
            validate_dispatch(
                &config,
                &["coding".into()],
                None,
                DataClass::Internal,
                1_000,
                0,
                0
            ),
            Err(DispatchError::NoEndpoint)
        );
    }
}
