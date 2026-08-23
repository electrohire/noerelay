use chrono::Utc;
use noerelay_core::registry::*;
use noerelay_core::{OrganizationId, PrincipalId};
use serde_json::json;
use uuid::Uuid;

fn data_policy() -> DataPolicy {
    DataPolicy {
        training_opt_out: true,
        data_residency: Some("us".into()),
        retention_days: Some(30),
        privacy_policy_url: Some("https://example.invalid/privacy".into()),
    }
}

fn provenance() -> ProvenanceInfo {
    ProvenanceInfo {
        source: "openrouter_api".into(),
        source_url: Some("https://openrouter.ai/api/v1/models".into()),
        source_hash: Some("sha256:source".into()),
    }
}

fn model() -> ModelRevision {
    let now = Utc::now();
    ModelRevision {
        id: Uuid::new_v4(),
        entity_id: "openai/gpt-4o".into(),
        revision: 1,
        revision_hash: "sha256:model".into(),
        lifecycle: RegistryLifecycle::Draft,
        display_name: "GPT-4o".into(),
        openrouter_id: "openai/gpt-4o".into(),
        provider: "openai".into(),
        modalities: vec![Modality::Text, Modality::Vision],
        supports_tools: true,
        supports_structured_output: true,
        supports_streaming: true,
        context_window: 128_000,
        max_output_tokens: 16_384,
        price: PriceSnapshot {
            input_price_per_million: 2.5,
            output_price_per_million: 10.0,
            currency: "USD".into(),
            price_source: "openrouter".into(),
            fetched_at: now,
        },
        data_policy: data_policy(),
        regions: vec!["us-east".into()],
        health_status: HealthStatus::Healthy,
        benchmark_version: Some("benchmark-v1".into()),
        harness_version: Some("harness-v1".into()),
        allowed_roles: vec!["operator".into()],
        provenance: provenance(),
        fetched_at: now,
        valid_at: now,
        expires_at: None,
        created_at: now,
        created_by: PrincipalId(Uuid::new_v4()),
        activated_at: None,
        activated_by: None,
        superseded_by: None,
        quarantine_reason: None,
        organization_id: OrganizationId(Uuid::new_v4()),
        notes: "fixture".into(),
    }
}

#[test]
fn draft_can_be_proposed() {
    assert!(RegistryLifecycle::Draft.can_transition(&RegistryLifecycle::Proposed));
}

#[test]
fn proposed_can_be_reviewed() {
    assert!(RegistryLifecycle::Proposed.can_transition(&RegistryLifecycle::Reviewed));
}

#[test]
fn reviewed_can_be_approved() {
    assert!(RegistryLifecycle::Reviewed.can_transition(&RegistryLifecycle::Approved));
}

#[test]
fn approved_can_be_active() {
    assert!(RegistryLifecycle::Approved.can_transition(&RegistryLifecycle::Active));
}

#[test]
fn active_can_be_superseded() {
    assert!(RegistryLifecycle::Active.can_transition(&RegistryLifecycle::Superseded));
}

#[test]
fn active_can_be_quarantined() {
    assert!(RegistryLifecycle::Active.can_transition(&RegistryLifecycle::Quarantined));
}

#[test]
fn draft_cannot_skip_to_active() {
    assert!(!RegistryLifecycle::Draft.can_transition(&RegistryLifecycle::Active));
}

#[test]
fn terminal_states_have_no_transitions() {
    for state in [
        RegistryLifecycle::Quarantined,
        RegistryLifecycle::Superseded,
        RegistryLifecycle::Rejected,
    ] {
        assert!(state.legal_transitions().is_empty());
    }
}

#[test]
fn model_revision_round_trips() {
    let expected = model();
    let actual: ModelRevision =
        serde_json::from_value(serde_json::to_value(&expected).unwrap()).unwrap();
    assert_eq!(expected, actual);
}

#[test]
fn provider_revision_round_trips() {
    let now = Utc::now();
    let expected = ProviderRevision {
        id: Uuid::new_v4(),
        entity_id: "openai".into(),
        revision: 1,
        revision_hash: "sha256:provider".into(),
        lifecycle: RegistryLifecycle::Approved,
        display_name: "OpenAI".into(),
        base_url: "https://api.openai.com".into(),
        supported_modalities: vec![Modality::Text, Modality::Vision],
        rate_limits: Some(RateLimitInfo {
            requests_per_minute: Some(100),
            tokens_per_minute: Some(1_000_000),
        }),
        data_policy: data_policy(),
        regions: vec!["us".into()],
        health_status: HealthStatus::Degraded,
        provenance: provenance(),
        organization_id: OrganizationId(Uuid::new_v4()),
        created_at: now,
        created_by: PrincipalId(Uuid::new_v4()),
        activated_at: None,
        superseded_by: None,
        quarantine_reason: None,
        notes: String::new(),
    };
    let actual = serde_json::from_value(serde_json::to_value(&expected).unwrap()).unwrap();
    assert_eq!(expected, actual);
}

#[test]
fn agent_revision_round_trips() {
    let now = Utc::now();
    let expected = AgentRevision {
        id: Uuid::new_v4(),
        entity_id: "research-agent".into(),
        revision: 2,
        revision_hash: "sha256:agent".into(),
        lifecycle: RegistryLifecycle::Reviewed,
        display_name: "Research Agent".into(),
        agent_type: "remote_a2a".into(),
        endpoint: Some("https://agent.example.invalid".into()),
        trust_root: Some("ed25519:root".into()),
        capabilities: vec!["research".into()],
        allowed_models: vec!["openai/gpt-4o".into()],
        data_policy: data_policy(),
        organization_id: OrganizationId(Uuid::new_v4()),
        created_at: now,
        created_by: PrincipalId(Uuid::new_v4()),
        activated_at: None,
        superseded_by: None,
        quarantine_reason: None,
        notes: String::new(),
    };
    let actual = serde_json::from_value(serde_json::to_value(&expected).unwrap()).unwrap();
    assert_eq!(expected, actual);
}

#[test]
fn tool_revision_round_trips() {
    let now = Utc::now();
    let expected = ToolRevision {
        id: Uuid::new_v4(),
        entity_id: "search".into(),
        revision: 1,
        revision_hash: "sha256:tool".into(),
        lifecycle: RegistryLifecycle::Draft,
        display_name: "Search".into(),
        description: "Search approved data".into(),
        input_schema: json!({"type": "object"}),
        output_schema: Some(json!({"type": "array"})),
        risk_class: "low".into(),
        side_effect_class: "read".into(),
        required_permissions: vec!["tool.search.execute".into()],
        timeout_seconds: 30,
        idempotency_supported: true,
        organization_id: OrganizationId(Uuid::new_v4()),
        created_at: now,
        created_by: PrincipalId(Uuid::new_v4()),
        activated_at: None,
        superseded_by: None,
        quarantine_reason: None,
        notes: String::new(),
    };
    let actual = serde_json::from_value(serde_json::to_value(&expected).unwrap()).unwrap();
    assert_eq!(expected, actual);
}

#[test]
fn price_snapshot_round_trips() {
    let expected = model().price;
    let actual = serde_json::from_value(serde_json::to_value(&expected).unwrap()).unwrap();
    assert_eq!(expected, actual);
}

#[test]
fn data_policy_round_trips() {
    let expected = data_policy();
    let actual = serde_json::from_value(serde_json::to_value(&expected).unwrap()).unwrap();
    assert_eq!(expected, actual);
}

#[test]
fn health_status_uses_snake_case_variants() {
    assert_eq!(
        serde_json::to_value(HealthStatus::Healthy).unwrap(),
        "healthy"
    );
    assert_eq!(
        serde_json::to_value(HealthStatus::Degraded).unwrap(),
        "degraded"
    );
    assert_eq!(
        serde_json::to_value(HealthStatus::Unhealthy).unwrap(),
        "unhealthy"
    );
    assert_eq!(
        serde_json::to_value(HealthStatus::Unknown).unwrap(),
        "unknown"
    );
}

#[test]
fn all_modalities_round_trip() {
    let expected = vec![
        Modality::Text,
        Modality::Vision,
        Modality::ImageGeneration,
        Modality::AudioTranscription,
        Modality::AudioGeneration,
        Modality::Embedding,
    ];
    let actual: Vec<Modality> =
        serde_json::from_value(serde_json::to_value(&expected).unwrap()).unwrap();
    assert_eq!(expected, actual);
}

#[test]
fn registry_entity_types_use_stable_wire_names() {
    assert_eq!(
        serde_json::to_value(RegistryEntityType::Model).unwrap(),
        "model"
    );
    assert_eq!(
        serde_json::to_value(RegistryEntityType::Provider).unwrap(),
        "provider"
    );
    assert_eq!(
        serde_json::to_value(RegistryEntityType::Agent).unwrap(),
        "agent"
    );
    assert_eq!(
        serde_json::to_value(RegistryEntityType::Tool).unwrap(),
        "tool"
    );
}
