//! Immutable model, provider, agent, and tool registry domain types.

use chrono::{DateTime, Utc};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::iam::{OrganizationId, PrincipalId};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum RegistryEntityType {
    Model,
    Provider,
    Agent,
    Tool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum Modality {
    Text,
    Vision,
    ImageGeneration,
    AudioTranscription,
    AudioGeneration,
    Embedding,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum RegistryLifecycle {
    Draft,
    Proposed,
    Reviewed,
    Approved,
    Active,
    Quarantined,
    Superseded,
    Rejected,
}

impl RegistryLifecycle {
    pub fn legal_transitions(&self) -> &'static [RegistryLifecycle] {
        use RegistryLifecycle::{
            Active, Approved, Proposed, Quarantined, Rejected, Reviewed, Superseded,
        };

        match self {
            Self::Draft => &[Proposed, Quarantined, Rejected],
            Self::Proposed => &[Reviewed, Quarantined, Rejected],
            Self::Reviewed => &[Approved, Quarantined, Rejected],
            Self::Approved => &[Active, Quarantined, Rejected],
            Self::Active => &[Quarantined, Superseded],
            Self::Quarantined | Self::Superseded | Self::Rejected => &[],
        }
    }

    pub fn can_transition(&self, to: &RegistryLifecycle) -> bool {
        self.legal_transitions().contains(to)
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct ModelRevision {
    pub id: Uuid,
    pub entity_id: String,
    pub revision: i32,
    pub revision_hash: String,
    pub lifecycle: RegistryLifecycle,
    pub display_name: String,
    pub openrouter_id: String,
    pub provider: String,
    pub modalities: Vec<Modality>,
    pub supports_tools: bool,
    pub supports_structured_output: bool,
    pub supports_streaming: bool,
    pub context_window: i32,
    pub max_output_tokens: i32,
    pub price: PriceSnapshot,
    pub data_policy: DataPolicy,
    pub regions: Vec<String>,
    pub health_status: HealthStatus,
    pub benchmark_version: Option<String>,
    pub harness_version: Option<String>,
    pub allowed_roles: Vec<String>,
    pub provenance: ProvenanceInfo,
    pub fetched_at: DateTime<Utc>,
    pub valid_at: DateTime<Utc>,
    pub expires_at: Option<DateTime<Utc>>,
    pub created_at: DateTime<Utc>,
    pub created_by: PrincipalId,
    pub activated_at: Option<DateTime<Utc>>,
    pub activated_by: Option<PrincipalId>,
    pub superseded_by: Option<Uuid>,
    pub quarantine_reason: Option<String>,
    pub organization_id: OrganizationId,
    pub notes: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct PriceSnapshot {
    pub input_price_per_million: f64,
    pub output_price_per_million: f64,
    pub currency: String,
    pub price_source: String,
    pub fetched_at: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct DataPolicy {
    pub training_opt_out: bool,
    pub data_residency: Option<String>,
    pub retention_days: Option<i32>,
    pub privacy_policy_url: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum HealthStatus {
    Healthy,
    Degraded,
    Unhealthy,
    Unknown,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ProvenanceInfo {
    pub source: String,
    pub source_url: Option<String>,
    pub source_hash: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct ProviderRevision {
    pub id: Uuid,
    pub entity_id: String,
    pub revision: i32,
    pub revision_hash: String,
    pub lifecycle: RegistryLifecycle,
    pub display_name: String,
    pub base_url: String,
    pub supported_modalities: Vec<Modality>,
    pub rate_limits: Option<RateLimitInfo>,
    pub data_policy: DataPolicy,
    pub regions: Vec<String>,
    pub health_status: HealthStatus,
    pub provenance: ProvenanceInfo,
    pub organization_id: OrganizationId,
    pub created_at: DateTime<Utc>,
    pub created_by: PrincipalId,
    pub activated_at: Option<DateTime<Utc>>,
    pub superseded_by: Option<Uuid>,
    pub quarantine_reason: Option<String>,
    pub notes: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct RateLimitInfo {
    pub requests_per_minute: Option<i32>,
    pub tokens_per_minute: Option<i32>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct AgentRevision {
    pub id: Uuid,
    pub entity_id: String,
    pub revision: i32,
    pub revision_hash: String,
    pub lifecycle: RegistryLifecycle,
    pub display_name: String,
    pub agent_type: String,
    pub endpoint: Option<String>,
    pub trust_root: Option<String>,
    pub capabilities: Vec<String>,
    pub allowed_models: Vec<String>,
    pub data_policy: DataPolicy,
    pub organization_id: OrganizationId,
    pub created_at: DateTime<Utc>,
    pub created_by: PrincipalId,
    pub activated_at: Option<DateTime<Utc>>,
    pub superseded_by: Option<Uuid>,
    pub quarantine_reason: Option<String>,
    pub notes: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct ToolRevision {
    pub id: Uuid,
    pub entity_id: String,
    pub revision: i32,
    pub revision_hash: String,
    pub lifecycle: RegistryLifecycle,
    pub display_name: String,
    pub description: String,
    pub input_schema: serde_json::Value,
    pub output_schema: Option<serde_json::Value>,
    pub risk_class: String,
    pub side_effect_class: String,
    pub required_permissions: Vec<String>,
    pub timeout_seconds: i64,
    pub idempotency_supported: bool,
    pub organization_id: OrganizationId,
    pub created_at: DateTime<Utc>,
    pub created_by: PrincipalId,
    pub activated_at: Option<DateTime<Utc>>,
    pub superseded_by: Option<Uuid>,
    pub quarantine_reason: Option<String>,
    pub notes: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum RegistryError {
    NotFound,
    IllegalTransition {
        from: RegistryLifecycle,
        to: RegistryLifecycle,
    },
    AlreadyActive,
    AlreadySuperseded,
    Quarantined {
        reason: String,
    },
    UnauthorizedActivation,
    UnauthorizedSupersession,
    DuplicateEntity,
    InvalidRevision,
}
