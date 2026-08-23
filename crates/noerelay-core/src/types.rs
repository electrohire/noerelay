use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use thiserror::Error;

#[derive(
    Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize, JsonSchema,
)]
#[serde(rename_all = "snake_case")]
pub enum RiskClass {
    Low,
    Medium,
    High,
    Critical,
}

#[derive(
    Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize, JsonSchema,
)]
#[serde(rename_all = "snake_case")]
pub enum DataClass {
    Public,
    Internal,
    Confidential,
    Restricted,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct IdentityScope {
    pub organization_id: String,
    pub project_id: String,
    pub environment_id: String,
    pub user_id: String,
    pub session_id: String,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum ScopeError {
    #[error("{0} must be between 1 and 128 characters")]
    InvalidLength(&'static str),
    #[error("{0} contains a character outside [A-Za-z0-9._:@-]")]
    InvalidCharacter(&'static str),
}

impl IdentityScope {
    pub fn validate(&self) -> Result<(), ScopeError> {
        for (name, value) in [
            ("organization_id", self.organization_id.as_str()),
            ("project_id", self.project_id.as_str()),
            ("environment_id", self.environment_id.as_str()),
            ("user_id", self.user_id.as_str()),
            ("session_id", self.session_id.as_str()),
        ] {
            if value.is_empty() || value.len() > 128 {
                return Err(ScopeError::InvalidLength(name));
            }
            if !value
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || b"._:@-".contains(&byte))
            {
                return Err(ScopeError::InvalidCharacter(name));
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum MessageRole {
    System,
    Developer,
    User,
    Assistant,
    Tool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Message {
    pub role: MessageRole,
    pub content: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tool_call_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CanonicalRequest {
    pub request_id: String,
    pub scope: IdentityScope,
    pub messages: Vec<Message>,
    pub risk: RiskClass,
    pub data_class: DataClass,
    #[serde(default)]
    pub acceptance_criteria: Vec<String>,
    #[serde(default)]
    pub required_capabilities: Vec<String>,
    #[serde(default)]
    pub allowed_tools: Vec<String>,
    #[serde(default)]
    pub allowed_agents: Vec<String>,
    #[serde(default)]
    pub metadata: BTreeMap<String, String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_cost_microusd: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_latency_ms: Option<u64>,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scope() -> IdentityScope {
        IdentityScope {
            organization_id: "org:1".into(),
            project_id: "project_1".into(),
            environment_id: "prod".into(),
            user_id: "user@example.invalid".into(),
            session_id: "session-1".into(),
        }
    }

    #[test]
    fn valid_scope_passes() {
        assert_eq!(scope().validate(), Ok(()));
    }

    #[test]
    fn empty_scope_fails() {
        let mut value = scope();
        value.project_id.clear();
        assert_eq!(
            value.validate(),
            Err(ScopeError::InvalidLength("project_id"))
        );
    }

    #[test]
    fn path_character_fails() {
        let mut value = scope();
        value.project_id = "../foreign".into();
        assert_eq!(
            value.validate(),
            Err(ScopeError::InvalidCharacter("project_id"))
        );
    }
}
