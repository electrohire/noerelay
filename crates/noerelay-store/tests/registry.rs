use chrono::Utc;
use noerelay_core::registry::*;
use noerelay_core::{OrganizationId, PrincipalId};
use noerelay_store::{RegistryRepository, RegistryStoreError};
use serde_json::{Value, json};
use sqlx::PgPool;
use uuid::Uuid;

struct TestContext {
    repo: RegistryRepository,
    org: OrganizationId,
    creator: PrincipalId,
    actor: PrincipalId,
}

async fn context() -> TestContext {
    let url = std::env::var("DATABASE_URL").expect("DATABASE_URL required for ignored test");
    let pool = PgPool::connect(&url).await.unwrap();
    let org = OrganizationId(Uuid::new_v4());
    TestContext {
        repo: RegistryRepository::new(pool, org),
        org,
        creator: PrincipalId(Uuid::new_v4()),
        actor: PrincipalId(Uuid::new_v4()),
    }
}

fn data_policy() -> DataPolicy {
    DataPolicy {
        training_opt_out: true,
        data_residency: Some("us".into()),
        retention_days: Some(7),
        privacy_policy_url: None,
    }
}

fn provenance() -> ProvenanceInfo {
    ProvenanceInfo {
        source: "manual".into(),
        source_url: None,
        source_hash: Some("sha256:source".into()),
    }
}

fn model_content(org: OrganizationId, creator: PrincipalId) -> Value {
    let now = Utc::now();
    serde_json::to_value(ModelRevision {
        id: Uuid::nil(),
        entity_id: String::new(),
        revision: 0,
        revision_hash: String::new(),
        lifecycle: RegistryLifecycle::Draft,
        display_name: String::new(),
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
            price_source: "manual".into(),
            fetched_at: now,
        },
        data_policy: data_policy(),
        regions: vec!["us".into()],
        health_status: HealthStatus::Healthy,
        benchmark_version: Some("v1".into()),
        harness_version: Some("v1".into()),
        allowed_roles: vec!["operator".into()],
        provenance: provenance(),
        fetched_at: now,
        valid_at: now,
        expires_at: None,
        created_at: now,
        created_by: creator,
        activated_at: None,
        activated_by: None,
        superseded_by: None,
        quarantine_reason: None,
        organization_id: org,
        notes: "test".into(),
    })
    .unwrap()
}

fn provider_content(org: OrganizationId, creator: PrincipalId) -> Value {
    serde_json::to_value(ProviderRevision {
        id: Uuid::nil(),
        entity_id: String::new(),
        revision: 0,
        revision_hash: String::new(),
        lifecycle: RegistryLifecycle::Draft,
        display_name: String::new(),
        base_url: "https://api.example.invalid".into(),
        supported_modalities: vec![Modality::Text],
        rate_limits: Some(RateLimitInfo {
            requests_per_minute: Some(60),
            tokens_per_minute: None,
        }),
        data_policy: data_policy(),
        regions: vec!["us".into()],
        health_status: HealthStatus::Healthy,
        provenance: provenance(),
        organization_id: org,
        created_at: Utc::now(),
        created_by: creator,
        activated_at: None,
        superseded_by: None,
        quarantine_reason: None,
        notes: String::new(),
    })
    .unwrap()
}

fn agent_content(org: OrganizationId, creator: PrincipalId) -> Value {
    serde_json::to_value(AgentRevision {
        id: Uuid::nil(),
        entity_id: String::new(),
        revision: 0,
        revision_hash: String::new(),
        lifecycle: RegistryLifecycle::Draft,
        display_name: String::new(),
        agent_type: "local".into(),
        endpoint: None,
        trust_root: None,
        capabilities: vec!["summarize".into()],
        allowed_models: vec!["openai/gpt-4o".into()],
        data_policy: data_policy(),
        organization_id: org,
        created_at: Utc::now(),
        created_by: creator,
        activated_at: None,
        superseded_by: None,
        quarantine_reason: None,
        notes: String::new(),
    })
    .unwrap()
}

fn tool_content(org: OrganizationId, creator: PrincipalId) -> Value {
    serde_json::to_value(ToolRevision {
        id: Uuid::nil(),
        entity_id: String::new(),
        revision: 0,
        revision_hash: String::new(),
        lifecycle: RegistryLifecycle::Draft,
        display_name: String::new(),
        description: "Search".into(),
        input_schema: json!({"type":"object"}),
        output_schema: Some(json!({"type":"array"})),
        risk_class: "low".into(),
        side_effect_class: "read".into(),
        required_permissions: vec!["tool.search".into()],
        timeout_seconds: 10,
        idempotency_supported: true,
        organization_id: org,
        created_at: Utc::now(),
        created_by: creator,
        activated_at: None,
        superseded_by: None,
        quarantine_reason: None,
        notes: String::new(),
    })
    .unwrap()
}

async fn create(ctx: &TestContext, kind: RegistryEntityType, entity: &str, content: Value) -> Uuid {
    ctx.repo
        .create_revision(kind, entity, content, entity, ctx.org, ctx.creator)
        .await
        .unwrap()
}

async fn advance(ctx: &TestContext, id: Uuid, states: &[RegistryLifecycle]) {
    for state in states {
        ctx.repo
            .transition_lifecycle(id, *state, ctx.actor)
            .await
            .unwrap();
    }
}

async fn activate(ctx: &TestContext, id: Uuid) {
    advance(
        ctx,
        id,
        &[
            RegistryLifecycle::Proposed,
            RegistryLifecycle::Reviewed,
            RegistryLifecycle::Approved,
            RegistryLifecycle::Active,
        ],
    )
    .await;
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn creates_model_and_gets_by_entity_revision() {
    let ctx = context().await;
    let entity = format!("model-{}", Uuid::new_v4());
    create(
        &ctx,
        RegistryEntityType::Model,
        &entity,
        model_content(ctx.org, ctx.creator),
    )
    .await;
    let value = ctx
        .repo
        .get_revision_by_entity(RegistryEntityType::Model, &entity, 1)
        .await
        .unwrap();
    assert_eq!(value["entity_id"], entity);
    assert_eq!(value["revision"], 1);
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn get_by_uuid_returns_full_revision() {
    let ctx = context().await;
    let entity = format!("model-{}", Uuid::new_v4());
    let id = create(
        &ctx,
        RegistryEntityType::Model,
        &entity,
        model_content(ctx.org, ctx.creator),
    )
    .await;
    assert_eq!(
        ctx.repo.get_revision(id).await.unwrap()["openrouter_id"],
        "openai/gpt-4o"
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn revision_numbers_increment() {
    let ctx = context().await;
    let entity = format!("model-{}", Uuid::new_v4());
    create(
        &ctx,
        RegistryEntityType::Model,
        &entity,
        model_content(ctx.org, ctx.creator),
    )
    .await;
    create(
        &ctx,
        RegistryEntityType::Model,
        &entity,
        model_content(ctx.org, ctx.creator),
    )
    .await;
    let history = ctx
        .repo
        .get_revision_history(RegistryEntityType::Model, &entity)
        .await
        .unwrap();
    assert_eq!(
        history
            .iter()
            .map(|v| v["revision"].as_i64().unwrap())
            .collect::<Vec<_>>(),
        vec![1, 2]
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn active_lookup_does_not_return_latest_draft() {
    let ctx = context().await;
    let entity = format!("model-{}", Uuid::new_v4());
    let first = create(
        &ctx,
        RegistryEntityType::Model,
        &entity,
        model_content(ctx.org, ctx.creator),
    )
    .await;
    activate(&ctx, first).await;
    create(
        &ctx,
        RegistryEntityType::Model,
        &entity,
        model_content(ctx.org, ctx.creator),
    )
    .await;
    assert_eq!(
        ctx.repo
            .get_active_revision(RegistryEntityType::Model, &entity)
            .await
            .unwrap()
            .unwrap()["revision"],
        1
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn transitions_through_full_lifecycle() {
    let ctx = context().await;
    let entity = format!("model-{}", Uuid::new_v4());
    let id = create(
        &ctx,
        RegistryEntityType::Model,
        &entity,
        model_content(ctx.org, ctx.creator),
    )
    .await;
    activate(&ctx, id).await;
    assert_eq!(
        ctx.repo.get_revision(id).await.unwrap()["lifecycle"],
        "active"
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn rejects_illegal_transition() {
    let ctx = context().await;
    let entity = format!("model-{}", Uuid::new_v4());
    let id = create(
        &ctx,
        RegistryEntityType::Model,
        &entity,
        model_content(ctx.org, ctx.creator),
    )
    .await;
    assert!(matches!(
        ctx.repo
            .transition_lifecycle(id, RegistryLifecycle::Active, ctx.actor)
            .await,
        Err(RegistryStoreError::Registry(
            RegistryError::IllegalTransition { .. }
        ))
    ));
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn quarantines_with_reason() {
    let ctx = context().await;
    let entity = format!("model-{}", Uuid::new_v4());
    let id = create(
        &ctx,
        RegistryEntityType::Model,
        &entity,
        model_content(ctx.org, ctx.creator),
    )
    .await;
    ctx.repo.quarantine(id, "stale metadata").await.unwrap();
    let value = ctx.repo.get_revision(id).await.unwrap();
    assert_eq!(value["lifecycle"], "quarantined");
    assert_eq!(value["quarantine_reason"], "stale metadata");
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn quarantine_check_returns_reason() {
    let ctx = context().await;
    let entity = format!("model-{}", Uuid::new_v4());
    let id = create(
        &ctx,
        RegistryEntityType::Model,
        &entity,
        model_content(ctx.org, ctx.creator),
    )
    .await;
    ctx.repo
        .quarantine(id, "contradictory sources")
        .await
        .unwrap();
    assert_eq!(
        ctx.repo
            .check_quarantine(RegistryEntityType::Model, &entity)
            .await
            .unwrap()
            .as_deref(),
        Some("contradictory sources")
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn supersede_moves_old_and_new_atomically() {
    let ctx = context().await;
    let entity = format!("model-{}", Uuid::new_v4());
    let old = create(
        &ctx,
        RegistryEntityType::Model,
        &entity,
        model_content(ctx.org, ctx.creator),
    )
    .await;
    activate(&ctx, old).await;
    let new = create(
        &ctx,
        RegistryEntityType::Model,
        &entity,
        model_content(ctx.org, ctx.creator),
    )
    .await;
    advance(
        &ctx,
        new,
        &[
            RegistryLifecycle::Proposed,
            RegistryLifecycle::Reviewed,
            RegistryLifecycle::Approved,
        ],
    )
    .await;
    ctx.repo.supersede(old, new, ctx.actor).await.unwrap();
    assert_eq!(
        ctx.repo.get_revision(old).await.unwrap()["lifecycle"],
        "superseded"
    );
    assert_eq!(
        ctx.repo
            .get_active_revision(RegistryEntityType::Model, &entity)
            .await
            .unwrap()
            .unwrap()["id"],
        new.to_string()
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn lists_active_models() {
    let ctx = context().await;
    let entity = format!("model-{}", Uuid::new_v4());
    let id = create(
        &ctx,
        RegistryEntityType::Model,
        &entity,
        model_content(ctx.org, ctx.creator),
    )
    .await;
    activate(&ctx, id).await;
    assert!(
        ctx.repo
            .list_active_models(ctx.org)
            .await
            .unwrap()
            .iter()
            .any(|m| m.entity_id == entity)
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn lists_active_tools() {
    let ctx = context().await;
    let entity = format!("tool-{}", Uuid::new_v4());
    let id = create(
        &ctx,
        RegistryEntityType::Tool,
        &entity,
        tool_content(ctx.org, ctx.creator),
    )
    .await;
    activate(&ctx, id).await;
    assert!(
        ctx.repo
            .list_active_tools(ctx.org)
            .await
            .unwrap()
            .iter()
            .any(|t| t.entity_id == entity)
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn typed_model_accessor_returns_model() {
    let ctx = context().await;
    let entity = format!("model-{}", Uuid::new_v4());
    let id = create(
        &ctx,
        RegistryEntityType::Model,
        &entity,
        model_content(ctx.org, ctx.creator),
    )
    .await;
    activate(&ctx, id).await;
    assert_eq!(
        ctx.repo
            .get_active_model(&entity)
            .await
            .unwrap()
            .unwrap()
            .openrouter_id,
        "openai/gpt-4o"
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn typed_provider_accessor_returns_provider() {
    let ctx = context().await;
    let entity = format!("provider-{}", Uuid::new_v4());
    let id = create(
        &ctx,
        RegistryEntityType::Provider,
        &entity,
        provider_content(ctx.org, ctx.creator),
    )
    .await;
    activate(&ctx, id).await;
    assert_eq!(
        ctx.repo
            .get_active_provider(&entity)
            .await
            .unwrap()
            .unwrap()
            .base_url,
        "https://api.example.invalid"
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn typed_agent_accessor_returns_agent() {
    let ctx = context().await;
    let entity = format!("agent-{}", Uuid::new_v4());
    let id = create(
        &ctx,
        RegistryEntityType::Agent,
        &entity,
        agent_content(ctx.org, ctx.creator),
    )
    .await;
    activate(&ctx, id).await;
    assert_eq!(
        ctx.repo
            .get_active_agent(&entity)
            .await
            .unwrap()
            .unwrap()
            .agent_type,
        "local"
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn typed_tool_accessor_returns_tool() {
    let ctx = context().await;
    let entity = format!("tool-{}", Uuid::new_v4());
    let id = create(
        &ctx,
        RegistryEntityType::Tool,
        &entity,
        tool_content(ctx.org, ctx.creator),
    )
    .await;
    activate(&ctx, id).await;
    assert_eq!(
        ctx.repo
            .get_active_tool(&entity)
            .await
            .unwrap()
            .unwrap()
            .risk_class,
        "low"
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn lists_quarantined_revisions() {
    let ctx = context().await;
    let entity = format!("model-{}", Uuid::new_v4());
    let id = create(
        &ctx,
        RegistryEntityType::Model,
        &entity,
        model_content(ctx.org, ctx.creator),
    )
    .await;
    ctx.repo.quarantine(id, "unevaluated").await.unwrap();
    assert!(
        ctx.repo
            .list_quarantined(ctx.org)
            .await
            .unwrap()
            .iter()
            .any(|v| v["entity_id"] == entity)
    );
}

#[tokio::test]
#[ignore = "requires PostgreSQL"]
async fn creator_cannot_activate_own_revision() {
    let ctx = context().await;
    let entity = format!("model-{}", Uuid::new_v4());
    let id = create(
        &ctx,
        RegistryEntityType::Model,
        &entity,
        model_content(ctx.org, ctx.creator),
    )
    .await;
    advance(
        &ctx,
        id,
        &[
            RegistryLifecycle::Proposed,
            RegistryLifecycle::Reviewed,
            RegistryLifecycle::Approved,
        ],
    )
    .await;
    assert!(matches!(
        ctx.repo
            .transition_lifecycle(id, RegistryLifecycle::Active, ctx.creator)
            .await,
        Err(RegistryStoreError::Registry(
            RegistryError::UnauthorizedActivation
        ))
    ));
}
