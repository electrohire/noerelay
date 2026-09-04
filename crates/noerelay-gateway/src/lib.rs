#![allow(clippy::result_large_err)]
pub mod admin;
pub mod iam;
pub mod stub_provider;

use axum::{
    Json, Router,
    body::{Body, Bytes},
    extract::{DefaultBodyLimit, Extension, Path, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use noerelay_core::{
    AdmissibleCandidate, AdvisoryRanker, ApiError, AuthenticatedIdentity, Candidate,
    CanonicalRequest, ChatCompletionsConverter, CheckResult, CheckStatus, Constraints,
    ContextCompiler, ContextNode, DataClass, Evidence, GovernanceRuntime, IdentityScope, Message,
    MessageRole, NodeKind, RankingContext, RankingMode, ReceiptSignatureError, ReceiptSigner,
    ReleaseOutcome, Requirement, ResponsesConverter, RiskClass, RouteDecision, RuntimeError,
    SignedRunReceipt, StagedRouter, TestCase, TraceGraph, UsageMeasurement, WireCanonicalRequest,
};
use noerelay_store::{
    CostRollupRow, ExecutionRepository, IamRepository, PostgresAuthorityStore, StoreError,
};
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use sqlx::PgPool;
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::Arc,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use thiserror::Error;
use tokio::sync::Mutex;
use uuid::Uuid;

use crate::stub_provider::StubProvider;

/// Stable public aliases exposed by the AXIOVEX Sentinel API.
const PRIMARY_PUBLIC_MODEL_ID: &str = "axiovex-agni";

/// HTTP client for the LLMRouter sidecar, implementing [`AdvisoryRanker`].
///
/// Calls `POST /rank` on the sidecar with sanitized features and admissible
/// candidates. Handles timeouts, errors, and circuit breaking.
#[derive(Debug, Clone)]
pub struct RankerClient {
    sidecar_url: String,
    timeout: Duration,
}

impl RankerClient {
    pub fn new(sidecar_url: String, timeout: Duration) -> Self {
        Self {
            sidecar_url: sidecar_url.trim_end_matches('/').to_owned(),
            timeout,
        }
    }
}

impl AdvisoryRanker for RankerClient {
    fn rank(
        &self,
        context: &RankingContext,
        candidates: &[AdmissibleCandidate],
    ) -> Result<Option<noerelay_core::RankingAdvice>, noerelay_core::RankerError> {
        let url = format!("{}/rank", self.sidecar_url);
        let body = serde_json::json!({
            "cohort": context.cohort,
            "features": context.features,
            "candidates": candidates,
        });
        let body_bytes = serde_json::to_vec(&body)
            .map_err(|e| noerelay_core::RankerError::MalformedResponse(e.to_string()))?;

        let client = reqwest::blocking::Client::builder()
            .timeout(self.timeout)
            .build()
            .map_err(|_| noerelay_core::RankerError::Unavailable)?;

        let response = client
            .post(&url)
            .header("Content-Type", "application/json")
            .body(body_bytes)
            .send()
            .map_err(|e| {
                if e.is_timeout() {
                    noerelay_core::RankerError::Timeout(self.timeout.as_millis() as u64)
                } else if e.is_connect() {
                    noerelay_core::RankerError::Unavailable
                } else {
                    noerelay_core::RankerError::MalformedResponse(e.to_string())
                }
            })?;

        if !response.status().is_success() {
            return Err(noerelay_core::RankerError::MalformedResponse(format!(
                "sidecar returned status {}",
                response.status()
            )));
        }

        let advice: noerelay_core::RankingAdvice = response
            .json()
            .map_err(|e| noerelay_core::RankerError::MalformedResponse(e.to_string()))?;

        Ok(Some(advice))
    }
}

#[derive(Debug, Clone)]
pub struct GatewayConfig {
    pub bearer_key_sha256: [u8; 32],
    pub openrouter_api_key: String,
    pub openrouter_base_url: String,
    pub stub_mode: bool,
    pub default_scope: IdentityScope,
    pub candidates: Vec<Candidate>,
    pub request_timeout: Duration,
    pub maximum_body_bytes: usize,
    pub budget_limit_microusd: u64,
    pub database_url: Option<String>,
    pub receipt_signer: ReceiptSigner,
    pub context_budget_tokens: u32,
    pub ranking_mode: RankingMode,
    pub ranker_sidecar_url: Option<String>,
}

#[derive(Debug, Error)]
pub enum ConfigError {
    #[error("{0} is required")]
    Missing(&'static str),
    #[error("NOERELAY_API_KEY must be at least 32 characters")]
    WeakApiKey,
    #[error("NOERELAY_CANDIDATES_JSON is invalid: {0}")]
    Candidates(String),
    #[error("default identity scope is invalid: {0}")]
    Scope(String),
    #[error("stub mode is prohibited when NOERELAY_PRODUCTION_MODE is enabled")]
    ProductionStub,
    #[error("DATABASE_URL is required when NOERELAY_PRODUCTION_MODE is enabled")]
    ProductionDatabaseRequired,
    #[error("NOERELAY_BUDGET_LIMIT_MICROUSD must be a positive integer")]
    InvalidBudget,
    #[error("NOERELAY_RECEIPT_SIGNING_SEED_HEX must be exactly 64 hexadecimal characters")]
    InvalidReceiptSigningSeed,
    #[error("receipt signer configuration is invalid: {0}")]
    ReceiptSigner(#[from] ReceiptSignatureError),
    #[error("NOERELAY_CONTEXT_BUDGET_TOKENS must be a positive integer")]
    InvalidContextBudget,
    #[error("NOERELAY_PROVIDER_TIMEOUT_SECONDS must be a positive integer")]
    InvalidProviderTimeout,
}

impl GatewayConfig {
    pub fn from_env() -> Result<Self, ConfigError> {
        let api_key = required_env("NOERELAY_API_KEY")?;
        if api_key.len() < 32 {
            return Err(ConfigError::WeakApiKey);
        }
        let stub_mode = std::env::var("NOERELAY_OPENROUTER_MODE")
            .is_ok_and(|value| value.eq_ignore_ascii_case("stub"));
        let production_mode = std::env::var("NOERELAY_PRODUCTION_MODE")
            .is_ok_and(|value| matches!(value.to_ascii_lowercase().as_str(), "1" | "true" | "yes"));
        if production_mode && stub_mode {
            return Err(ConfigError::ProductionStub);
        }
        let database_url = std::env::var("DATABASE_URL")
            .ok()
            .filter(|value| !value.trim().is_empty());
        if production_mode && database_url.is_none() {
            return Err(ConfigError::ProductionDatabaseRequired);
        }
        let signing_seed_hex = std::env::var("NOERELAY_RECEIPT_SIGNING_SEED_HEX")
            .ok()
            .filter(|value| !value.trim().is_empty());
        if production_mode && signing_seed_hex.is_none() {
            return Err(ConfigError::InvalidReceiptSigningSeed);
        }
        let signing_seed_hex = signing_seed_hex.unwrap_or_else(|| "00".repeat(32));
        let mut signing_seed = [0_u8; 32];
        hex::decode_to_slice(signing_seed_hex, &mut signing_seed)
            .map_err(|_| ConfigError::InvalidReceiptSigningSeed)?;
        let receipt_signer = ReceiptSigner::from_seed(
            std::env::var("NOERELAY_RECEIPT_SIGNING_KEY_ID")
                .unwrap_or_else(|_| "development-key".into()),
            signing_seed,
        )?;
        let context_budget_tokens = std::env::var("NOERELAY_CONTEXT_BUDGET_TOKENS")
            .unwrap_or_else(|_| "32768".into())
            .parse::<u32>()
            .ok()
            .filter(|value| *value > 0)
            .ok_or(ConfigError::InvalidContextBudget)?;
        let openrouter_api_key = if stub_mode {
            String::new()
        } else {
            required_env("OPENROUTER_API_KEY")?
        };
        let candidates: Vec<Candidate> =
            serde_json::from_str(&required_env("NOERELAY_CANDIDATES_JSON")?)
                .map_err(|error| ConfigError::Candidates(error.to_string()))?;
        if candidates.is_empty() {
            return Err(ConfigError::Candidates(
                "at least one candidate is required".into(),
            ));
        }
        let default_scope = IdentityScope {
            organization_id: required_env("NOERELAY_ORGANIZATION_ID")?,
            project_id: required_env("NOERELAY_PROJECT_ID")?,
            environment_id: std::env::var("NOERELAY_ENVIRONMENT_ID")
                .unwrap_or_else(|_| "production".into()),
            user_id: "api-key-principal".into(),
            session_id: "stateless".into(),
        };
        default_scope
            .validate()
            .map_err(|error| ConfigError::Scope(error.to_string()))?;
        let budget_limit_microusd = std::env::var("NOERELAY_BUDGET_LIMIT_MICROUSD")
            .unwrap_or_else(|_| "10000000000".into())
            .parse::<u64>()
            .ok()
            .filter(|value| *value > 0)
            .ok_or(ConfigError::InvalidBudget)?;
        let ranking_mode = match std::env::var("NOERELAY_RANKING_MODE")
            .unwrap_or_else(|_| "disabled".into())
            .to_ascii_lowercase()
            .as_str()
        {
            "shadow" => RankingMode::Shadow,
            "advisory" => RankingMode::Advisory,
            _ => RankingMode::Disabled,
        };
        let ranker_sidecar_url = std::env::var("NOERELAY_RANKER_SIDECAR_URL")
            .ok()
            .filter(|v| !v.trim().is_empty());
        let request_timeout = std::env::var("NOERELAY_PROVIDER_TIMEOUT_SECONDS")
            .unwrap_or_else(|_| "600".into())
            .parse::<u64>()
            .ok()
            .filter(|value| *value > 0)
            .map(Duration::from_secs)
            .ok_or(ConfigError::InvalidProviderTimeout)?;
        Ok(Self {
            bearer_key_sha256: Sha256::digest(api_key.as_bytes()).into(),
            openrouter_api_key,
            openrouter_base_url: std::env::var("OPENROUTER_BASE_URL")
                .unwrap_or_else(|_| "https://openrouter.ai/api/v1".into())
                .trim_end_matches('/')
                .to_owned(),
            stub_mode,
            default_scope,
            candidates,
            request_timeout,
            maximum_body_bytes: 2 * 1024 * 1024,
            budget_limit_microusd,
            database_url,
            receipt_signer,
            context_budget_tokens,
            ranking_mode,
            ranker_sidecar_url,
        })
    }
}

fn required_env(name: &'static str) -> Result<String, ConfigError> {
    std::env::var(name)
        .ok()
        .filter(|value| !value.trim().is_empty())
        .ok_or(ConfigError::Missing(name))
}

#[derive(Clone)]
pub struct AppState {
    config: Arc<GatewayConfig>,
    client: Client,
    runtime: Arc<Mutex<GovernanceRuntime>>,
    receipts: Arc<Mutex<BTreeMap<String, SignedRunReceipt>>>,
    store: Option<PostgresAuthorityStore>,
    iam_repo: Option<IamRepository>,
    api_key_repo: Option<noerelay_store::ApiKeyRepository>,
    oidc_providers: Vec<Arc<dyn noerelay_core::iam::IdentityProvider>>,
    execution_repo: Option<ExecutionRepository>,
    storage_version: Arc<Mutex<i64>>,
    ranker_client: Option<RankerClient>,
}

#[derive(Debug, Error)]
pub enum StateError {
    #[error("HTTP client initialization failed: {0}")]
    Http(#[from] reqwest::Error),
    #[error("authority storage initialization failed: {0}")]
    Store(#[from] StoreError),
    #[error("IAM storage initialization failed: {0}")]
    IamStore(#[from] noerelay_store::IamStoreError),
    #[error("OIDC provider initialization failed: {0}")]
    Oidc(#[from] noerelay_core::iam::IamError),
}

#[derive(Debug, Error)]
enum AuthorityError {
    #[error(transparent)]
    Runtime(#[from] RuntimeError),
    #[error(transparent)]
    Store(#[from] StoreError),
    #[error(transparent)]
    Signature(#[from] ReceiptSignatureError),
}

impl AppState {
    pub fn new(config: GatewayConfig) -> Result<Self, reqwest::Error> {
        let client = Client::builder()
            .timeout(config.request_timeout)
            .redirect(reqwest::redirect::Policy::none())
            .build()?;
        let budget_limit_microusd = config.budget_limit_microusd;
        let ranker_client = config
            .ranker_sidecar_url
            .as_ref()
            .map(|url| RankerClient::new(url.clone(), Duration::from_secs(5)));
        Ok(Self {
            config: Arc::new(config),
            client,
            runtime: Arc::new(Mutex::new(GovernanceRuntime::new(budget_limit_microusd))),
            receipts: Arc::new(Mutex::new(BTreeMap::new())),
            store: None,
            iam_repo: None,
            api_key_repo: None,
            oidc_providers: Vec::new(),
            execution_repo: None,
            storage_version: Arc::new(Mutex::new(0)),
            ranker_client,
        })
    }

    pub async fn from_config(config: GatewayConfig) -> Result<Self, StateError> {
        let Some(database_url) = config.database_url.clone() else {
            return Ok(Self::new(config)?);
        };
        let store = PostgresAuthorityStore::connect(&database_url, 10).await?;
        let loaded = store
            .load(
                &config.default_scope.organization_id,
                &config.default_scope.project_id,
            )
            .await?;
        let (runtime, version) = match loaded {
            Some(value) => (
                GovernanceRuntime::from_snapshot(value.snapshot)
                    .map_err(|error| StoreError::InvalidSnapshot(error.to_string()))?,
                value.storage_version,
            ),
            None => (GovernanceRuntime::new(config.budget_limit_microusd), 0),
        };
        let client = Client::builder()
            .timeout(config.request_timeout)
            .redirect(reqwest::redirect::Policy::none())
            .build()?;
        let pool = PgPool::connect(&database_url)
            .await
            .map_err(|e| StateError::Store(StoreError::Database(e)))?;
        let iam_repo = Some(IamRepository::new(pool.clone()));
        let api_key_repo = Some(noerelay_store::ApiKeyRepository::new(pool.clone()));
        let mut oidc_providers: Vec<Arc<dyn noerelay_core::iam::IdentityProvider>> = Vec::new();
        if let (Some(repo), Ok(organization_id)) = (
            iam_repo.as_ref(),
            Uuid::parse_str(&config.default_scope.organization_id),
        ) {
            let organization_id = noerelay_core::iam::OrganizationId(organization_id);
            for oidc_config in repo.list_oidc_configs(organization_id).await? {
                let provider =
                    iam::OidcIdentityProvider::fetch(oidc_config, organization_id, None).await?;
                oidc_providers.push(Arc::new(provider));
            }
        }
        let ranker_client = config
            .ranker_sidecar_url
            .as_ref()
            .map(|url| RankerClient::new(url.clone(), Duration::from_secs(5)));
        Ok(Self {
            config: Arc::new(config),
            client,
            runtime: Arc::new(Mutex::new(runtime)),
            receipts: Arc::new(Mutex::new(BTreeMap::new())),
            store: Some(store),
            iam_repo,
            api_key_repo,
            oidc_providers,
            execution_repo: Some(ExecutionRepository::new(pool)),
            storage_version: Arc::new(Mutex::new(version)),
            ranker_client,
        })
    }

    async fn prepare_run(
        &self,
        request: &CanonicalRequest,
        constraints: &Constraints,
    ) -> Result<noerelay_core::PreparedRun, AuthorityError> {
        let mut authority = self.runtime.lock().await;
        let mut staged = authority.clone();

        // Use StagedRouter with optional advisory ranking
        let router = StagedRouter::new();
        let ranking_context = if self.config.ranking_mode != RankingMode::Disabled {
            Some(RankingContext {
                cohort: request
                    .metadata
                    .get("cohort")
                    .cloned()
                    .unwrap_or_else(|| "default".into())
                    .to_string(),
                features: serde_json::json!({
                    "risk": format!("{:?}", request.risk),
                    "data_class": format!("{:?}", request.data_class),
                    "capabilities": request.required_capabilities,
                }),
                features_hash: noerelay_core::features_hash(&serde_json::json!({
                    "risk": format!("{:?}", request.risk),
                    "data_class": format!("{:?}", request.data_class),
                    "capabilities": request.required_capabilities,
                })),
            })
        } else {
            None
        };

        let ranker: Option<&dyn AdvisoryRanker> = self
            .ranker_client
            .as_ref()
            .map(|c| c as &dyn AdvisoryRanker);
        let staged_decision = router.select_with_ranking(
            &self.config.candidates,
            constraints,
            ranker,
            self.config.ranking_mode,
            ranking_context.as_ref(),
        );

        // Convert StagedRouteDecision to RouteDecision for GovernanceRuntime
        let route = RouteDecision {
            selected_candidate_id: staged_decision.selected_candidate_id,
            selected_openrouter_model_id: staged_decision.selected_openrouter_model_id,
            expected_total_cost_microusd: staged_decision.expected_total_cost_microusd,
            rejections: staged_decision.rejections,
        };

        let prepared = staged.prepare_with_decision(request, route, now_unix_ms())?;
        self.persist_staged(&staged, None).await?;
        *authority = staged;
        Ok(prepared)
    }

    async fn complete_run(
        &self,
        prepared: &noerelay_core::PreparedRun,
        bytes: &[u8],
        input_tokens: u64,
        output_tokens: u64,
        results: &[CheckResult],
    ) -> Result<(noerelay_core::Completion, Option<SignedRunReceipt>), AuthorityError> {
        let mut authority = self.runtime.lock().await;
        let mut staged = authority.clone();
        let completion = staged.complete(
            &prepared.run_id,
            bytes,
            &UsageMeasurement {
                cost_microusd: prepared.reserved_cost_microusd,
                cost_source: "estimated".into(),
                input_tokens,
                output_tokens,
            },
            prepared
                .route
                .selected_candidate_id
                .as_deref()
                .unwrap_or("unknown-worker"),
            results,
            now_unix_ms(),
        )?;
        let signed_receipt = completion
            .receipt
            .clone()
            .map(|receipt| self.config.receipt_signer.sign(receipt))
            .transpose()?;
        self.persist_staged(&staged, signed_receipt.as_ref())
            .await?;
        *authority = staged;
        Ok((completion, signed_receipt))
    }

    async fn abort_run(&self, run_id: &str, reason_code: &str) -> Result<(), AuthorityError> {
        let mut authority = self.runtime.lock().await;
        let mut staged = authority.clone();
        staged.abort(run_id, reason_code, now_unix_ms())?;
        self.persist_staged(&staged, None).await?;
        *authority = staged;
        Ok(())
    }

    async fn persist_staged(
        &self,
        staged: &GovernanceRuntime,
        receipt: Option<&SignedRunReceipt>,
    ) -> Result<(), StoreError> {
        let Some(store) = &self.store else {
            return Ok(());
        };
        let mut version = self.storage_version.lock().await;
        *version = store
            .save(
                &self.config.default_scope.organization_id,
                &self.config.default_scope.project_id,
                *version,
                &staged.snapshot(),
                receipt,
            )
            .await?;
        Ok(())
    }
}

pub fn app(state: AppState) -> Router {
    let body_limit = state.config.maximum_body_bytes;
    let admin_router = if let Some(ref repo) = state.iam_repo {
        let api_key_repo = state.api_key_repo.clone();
        let admin_state = admin::AdminState {
            repo: repo.clone(),
            api_key_repo: api_key_repo.clone(),
        };
        Some(admin::admin_routes(admin_state))
    } else {
        None
    };
    let default_org_id = Uuid::parse_str(&state.config.default_scope.organization_id)
        .ok()
        .map(noerelay_core::iam::OrganizationId);
    let mut middleware_state = iam::IamMiddlewareState::new(
        state.iam_repo.clone(),
        state.api_key_repo.clone(),
        state.config.bearer_key_sha256,
        default_org_id,
    );
    for provider in &state.oidc_providers {
        middleware_state = middleware_state.with_oidc_provider(provider.clone());
    }

    let mut protected = Router::new()
        .route("/v1/models", get(models))
        .route("/v1/chat/completions", post(chat_completions))
        .route("/v1/responses", post(responses))
        .route("/v1/noerelay/runs/{run_id}/receipt", get(receipt))
        .route("/v1/noerelay/reports/costs", get(cost_report))
        .route(
            "/v1/noerelay/governance/release-gate",
            post(governance_release_gate),
        )
        .layer(DefaultBodyLimit::max(body_limit))
        .with_state(state.clone());
    if let Some(admin) = admin_router {
        protected = protected.merge(admin);
    }
    protected = protected.layer(axum::middleware::from_fn_with_state(
        Arc::new(middleware_state),
        iam::iam_middleware,
    ));

    Router::new()
        .route("/health", get(health))
        .route("/ready", get(ready))
        .with_state(state)
        .merge(protected)
}

async fn health() -> Json<Value> {
    Json(json!({"status":"live"}))
}

async fn ready(State(state): State<AppState>) -> Response {
    if state.config.candidates.is_empty()
        || (!state.config.stub_mode && state.config.openrouter_api_key.is_empty())
    {
        return error(
            StatusCode::SERVICE_UNAVAILABLE,
            "not_ready",
            "No admissible model plane is configured",
        );
    }
    if let Some(store) = &state.store
        && store.health().await.is_err()
    {
        return error(
            StatusCode::SERVICE_UNAVAILABLE,
            "not_ready",
            "Authority storage is unavailable",
        );
    }
    Json(json!({"status":"ready","authority":"rust","model_plane":"openrouter"})).into_response()
}

async fn models(
    State(_state): State<AppState>,
    _identity: Option<Extension<AuthenticatedIdentity>>,
) -> Response {
    Json(json!({
        "object": "list",
        "data": [
            {
                "id": PRIMARY_PUBLIC_MODEL_ID,
                "object": "model",
                "owned_by": "axiovex"
            }
        ]
    }))
    .into_response()
}

async fn receipt(
    State(state): State<AppState>,
    Path(run_id): Path<String>,
    headers: HeaderMap,
) -> Response {
    if !authorized(&headers, &state.config.bearer_key_sha256) {
        return error(
            StatusCode::UNAUTHORIZED,
            "invalid_api_key",
            "Invalid API key",
        );
    }
    if let Some(value) = state.receipts.lock().await.get(&run_id).cloned() {
        return Json(value).into_response();
    }
    if let Some(store) = &state.store {
        match store
            .receipt(&state.config.default_scope.organization_id, &run_id)
            .await
        {
            Ok(Some(value)) => return Json(value).into_response(),
            Err(_) => {
                return error(
                    StatusCode::SERVICE_UNAVAILABLE,
                    "storage_error",
                    "Authority storage is unavailable",
                );
            }
            Ok(None) => {}
        }
    }
    error(
        StatusCode::NOT_FOUND,
        "run_not_found",
        "Run receipt not found",
    )
}

async fn cost_report(State(state): State<AppState>, headers: HeaderMap) -> Response {
    if !authorized(&headers, &state.config.bearer_key_sha256) {
        return error(
            StatusCode::UNAUTHORIZED,
            "invalid_api_key",
            "Invalid API key",
        );
    }
    let rows = if let Some(store) = &state.store {
        match store
            .cost_rollups(
                &state.config.default_scope.organization_id,
                Some(&state.config.default_scope.project_id),
            )
            .await
        {
            Ok(value) => value,
            Err(_) => {
                return error(
                    StatusCode::SERVICE_UNAVAILABLE,
                    "storage_error",
                    "Cost reporting storage is unavailable",
                );
            }
        }
    } else {
        ephemeral_cost_rollups(&state).await
    };
    let totals = rows.iter().fold(
        (0_u64, 0_u64, 0_u64, 0_u64),
        |(requests, input, output, cost), row| {
            (
                requests.saturating_add(row.requests),
                input.saturating_add(row.input_tokens),
                output.saturating_add(row.output_tokens),
                cost.saturating_add(row.cost_microusd),
            )
        },
    );
    Json(json!({
        "object": "noerelay.cost_report",
        "scope": {
            "organization_id": state.config.default_scope.organization_id,
            "project_id": state.config.default_scope.project_id,
        },
        "data": rows,
        "totals": {
            "requests": totals.0,
            "input_tokens": totals.1,
            "output_tokens": totals.2,
            "cost_microusd": totals.3,
        }
    }))
    .into_response()
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct GovernanceGateRequest {
    requirements: Vec<Requirement>,
    tests: Vec<TestCase>,
    evidence: Vec<Evidence>,
}

async fn governance_release_gate(
    State(state): State<AppState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if !authorized(&headers, &state.config.bearer_key_sha256) {
        return error(
            StatusCode::UNAUTHORIZED,
            "invalid_api_key",
            "Invalid API key",
        );
    }
    let value: GovernanceGateRequest = match serde_json::from_slice(&body) {
        Ok(value) => value,
        Err(_) => {
            return error(
                StatusCode::BAD_REQUEST,
                "invalid_governance_bundle",
                "The governance bundle does not match the required schema",
            );
        }
    };
    let counts = (
        value.requirements.len(),
        value.tests.len(),
        value.evidence.len(),
    );
    let graph = match TraceGraph::new(value.requirements, value.tests, value.evidence) {
        Ok(value) => value,
        Err(error_value) => {
            return error(
                StatusCode::UNPROCESSABLE_ENTITY,
                "traceability_rejected",
                &error_value.to_string(),
            );
        }
    };
    if let Err(error_value) = graph.release_gate() {
        return error(
            StatusCode::FAILED_DEPENDENCY,
            "release_gate_rejected",
            &error_value.to_string(),
        );
    }
    Json(json!({
        "object": "noerelay.governance_release_gate",
        "release": "accepted",
        "requirements": counts.0,
        "tests": counts.1,
        "observed_evidence": counts.2,
    }))
    .into_response()
}

async fn ephemeral_cost_rollups(state: &AppState) -> Vec<CostRollupRow> {
    let mut grouped: BTreeMap<(String, String), CostRollupRow> = BTreeMap::new();
    for signed in state.receipts.lock().await.values() {
        let receipt = &signed.receipt;
        let row = grouped
            .entry((receipt.project_id.clone(), receipt.user_id.clone()))
            .or_insert_with(|| CostRollupRow {
                organization_id: receipt.organization_id.clone(),
                project_id: receipt.project_id.clone(),
                user_id: receipt.user_id.clone(),
                requests: 0,
                input_tokens: 0,
                output_tokens: 0,
                cost_microusd: 0,
            });
        row.requests = row.requests.saturating_add(1);
        row.input_tokens = row.input_tokens.saturating_add(receipt.input_tokens);
        row.output_tokens = row.output_tokens.saturating_add(receipt.output_tokens);
        row.cost_microusd = row
            .cost_microusd
            .saturating_add(receipt.actual_cost_microusd);
    }
    grouped.into_values().collect()
}

async fn chat_completions(
    State(state): State<AppState>,
    identity: Option<Extension<AuthenticatedIdentity>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    proxy_openai_request(state, identity, headers, body, ApiProfile::ChatCompletions).await
}

async fn responses(
    State(state): State<AppState>,
    identity: Option<Extension<AuthenticatedIdentity>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    proxy_openai_request(state, identity, headers, body, ApiProfile::Responses).await
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum ApiProfile {
    ChatCompletions,
    Responses,
}

async fn proxy_openai_request(
    state: AppState,
    identity: Option<Extension<AuthenticatedIdentity>>,
    headers: HeaderMap,
    body: Bytes,
    profile: ApiProfile,
) -> Response {
    let request_value = match serde_json::from_slice::<Value>(&body) {
        Ok(value @ Value::Object(_)) => value,
        _ => {
            return api_error_response(
                StatusCode::BAD_REQUEST,
                ApiError::invalid_request("Body must be a JSON object.", None),
            );
        }
    };
    let wire_request = match profile {
        ApiProfile::ChatCompletions => ChatCompletionsConverter::parse_request(&request_value),
        ApiProfile::Responses => ResponsesConverter::parse_request(&request_value),
    };
    let mut wire_request = match wire_request {
        Ok(value) => value,
        Err(errors) => {
            return api_error_response(
                StatusCode::BAD_REQUEST,
                errors
                    .into_iter()
                    .next()
                    .unwrap_or_else(|| ApiError::invalid_request("The request is invalid.", None)),
            );
        }
    };
    // Open WebUI exposes `ask_user` as a presentation helper. Smaller local
    // models tend to over-select it for greetings and may serialize the call
    // as visible JSON. Agni can ask a clarification in ordinary text, so this
    // one UI-only helper never crosses the provider boundary.
    if let Some(tools) = wire_request.tools.as_mut() {
        tools.retain(|tool| !is_ask_user_tool(&tool.function.name));
    }
    let self_capability_inquiry = is_self_capability_inquiry(&wire_request);
    let repeated_tool_loop = has_repeated_tool_loop(&wire_request);
    if self_capability_inquiry || repeated_tool_loop {
        wire_request.tools = None;
    }
    if let Err(response) =
        validate_requested_model(&state, identity.as_deref(), &wire_request).await
    {
        return response;
    }
    let stream = wire_request.stream;
    let allowed_tool_names = wire_request
        .tools
        .as_deref()
        .unwrap_or_default()
        .iter()
        .map(|tool| tool.function.name.clone())
        .collect::<BTreeSet<_>>();
    let messages = governance_messages(&wire_request);
    let mut request = request_value
        .as_object()
        .cloned()
        .expect("request object checked above");
    remove_ask_user_tool(&mut request);
    if self_capability_inquiry || repeated_tool_loop {
        disable_tool_use(&mut request);
    }
    let upstream_path = match profile {
        ApiProfile::ChatCompletions => "chat/completions",
        ApiProfile::Responses => "responses",
    };
    let (messages, context_manifest_hash, omitted_context_nodes) =
        match compile_wire_context(&messages, state.config.context_budget_tokens) {
            Ok(value) => value,
            Err(message) => {
                return error(
                    StatusCode::UNPROCESSABLE_ENTITY,
                    "context_rejected",
                    &message,
                );
            }
        };
    let risk = parse_risk(
        headers
            .get("x-noerelay-risk")
            .and_then(|value| value.to_str().ok()),
    );
    let acceptance_criteria = headers
        .get("x-noerelay-acceptance")
        .and_then(|value| value.to_str().ok())
        .map(|value| value.split('|').map(str::to_owned).collect())
        .unwrap_or_default();
    let mut scope = state.config.default_scope.clone();
    if let Some(identity) = identity
        .as_deref()
        .filter(|identity| identity.principal_id.0 != Uuid::nil())
    {
        scope.organization_id = identity.organization_id.0.to_string();
        scope.user_id = identity.principal_id.0.to_string();
    } else {
        scope.user_id = header_or(&headers, "x-noerelay-user", &scope.user_id);
    }
    scope.session_id = header_or(&headers, "x-noerelay-session", &scope.session_id);
    let required_capabilities = vec!["text".into()];
    let requested_public_model = wire_request.model.clone();
    let canonical = CanonicalRequest {
        request_id: header_or(&headers, "x-request-id", &Uuid::new_v4().to_string()),
        scope,
        messages,
        risk,
        data_class: DataClass::Internal,
        acceptance_criteria,
        required_capabilities,
        allowed_tools: wire_request
            .tools
            .as_deref()
            .unwrap_or_default()
            .iter()
            .map(|tool| tool.function.name.clone())
            .collect(),
        allowed_agents: vec![],
        metadata: BTreeMap::from([
            ("context_manifest_hash".into(), context_manifest_hash),
            (
                "omitted_context_nodes".into(),
                omitted_context_nodes.to_string(),
            ),
            (
                "tool_policy_outcome".into(),
                if repeated_tool_loop {
                    "repetition_circuit_breaker"
                } else if self_capability_inquiry {
                    "self_description_tools_not_required"
                } else {
                    "tools_available"
                }
                .into(),
            ),
        ]),
        max_cost_microusd: None,
        max_latency_ms: None,
    };
    let constraints = Constraints {
        required_capabilities: canonical.required_capabilities.iter().cloned().collect(),
        data_class: canonical.data_class,
        allowed_providers: BTreeSet::new(),
        max_total_cost_microusd: canonical.max_cost_microusd,
        max_latency_ms: canonical.max_latency_ms,
        min_acceptance_lcb_ppm: match risk {
            RiskClass::Low => 850_000,
            RiskClass::Medium => 950_000,
            RiskClass::High => 990_000,
            RiskClass::Critical => 995_000,
        },
        require_independent_verification: matches!(risk, RiskClass::High | RiskClass::Critical),
    };
    let prepared = match state.prepare_run(&canonical, &constraints).await {
        Ok(value) => value,
        Err(error_value) => return authority_error_response(error_value),
    };
    let durable = match create_durable_run(&state, identity.as_deref(), &prepared).await {
        Ok(value) => value,
        Err(response) => return response,
    };
    let model = prepared
        .route
        .selected_openrouter_model_id
        .clone()
        .expect("prepared run always has a selected route");
    request.insert("model".into(), Value::String(model));
    apply_agent_instructions(
        &mut request,
        profile,
        &requested_public_model,
        self_capability_inquiry,
        repeated_tool_loop,
    );
    if state.config.stub_mode {
        let canonical_response = StubProvider.complete(&wire_request);
        let formatted = match profile {
            ApiProfile::ChatCompletions => {
                ChatCompletionsConverter::format_response(&canonical_response)
            }
            ApiProfile::Responses => ResponsesConverter::format_response(&canonical_response),
        };
        let (bytes, content_type) = if stream {
            (
                Bytes::from(format_sse(profile, &canonical_response)),
                "text/event-stream",
            )
        } else {
            (
                Bytes::from(serde_json::to_vec(&formatted).expect("formatted response serializes")),
                "application/json",
            )
        };
        if let Err(response) = record_stub_attempt(
            &state,
            durable.as_ref(),
            &wire_request,
            &canonical_response,
            &body,
            &bytes,
        )
        .await
        {
            return response;
        }
        return release_response(
            &state,
            &prepared,
            StatusCode::OK,
            content_type,
            stream,
            bytes,
        )
        .await;
    }
    let response = state
        .client
        .post(format!(
            "{}/{upstream_path}",
            state.config.openrouter_base_url
        ))
        .bearer_auth(&state.config.openrouter_api_key)
        .header("content-type", "application/json")
        .json(&request)
        .send()
        .await;
    let response = match response {
        Ok(value) => value,
        Err(_) => {
            abort_run(&state, &prepared.run_id, "provider_transport_failed").await;
            return error(
                StatusCode::BAD_GATEWAY,
                "provider_error",
                "OpenRouter request failed",
            );
        }
    };
    let status =
        StatusCode::from_u16(response.status().as_u16()).unwrap_or(StatusCode::BAD_GATEWAY);
    if !status.is_success() {
        abort_run(&state, &prepared.run_id, "provider_rejected").await;
        return error(status, "provider_error", "OpenRouter rejected the request");
    }
    let bytes = match response.bytes().await {
        Ok(value) => value,
        Err(_) => {
            abort_run(&state, &prepared.run_id, "provider_body_failed").await;
            return error(
                StatusCode::BAD_GATEWAY,
                "provider_error",
                "OpenRouter response could not be read",
            );
        }
    };
    let bytes = normalize_legacy_tool_calls(bytes, stream, profile, &allowed_tool_names);
    let bytes = rewrite_public_model(bytes, stream, &requested_public_model);
    release_response(
        &state,
        &prepared,
        status,
        if stream {
            "text/event-stream"
        } else {
            "application/json"
        },
        stream,
        bytes,
    )
    .await
}

fn parse_legacy_tool_call(
    content: &str,
    allowed_tool_names: &BTreeSet<String>,
) -> Option<(String, String)> {
    let mut candidate = content.trim();
    if candidate.starts_with("```json") && candidate.ends_with("```") {
        candidate = candidate
            .strip_prefix("```json")?
            .strip_suffix("```")?
            .trim();
    } else if candidate.starts_with("```") && candidate.ends_with("```") {
        candidate = candidate.strip_prefix("```")?.strip_suffix("```")?.trim();
    }
    let value = serde_json::from_str::<Value>(candidate).ok()?;
    let object = value.as_object()?;
    if object.keys().any(|key| key != "name" && key != "arguments") {
        return None;
    }
    let name = object.get("name")?.as_str()?.replace("\\_", "_");
    if !allowed_tool_names.contains(&name) {
        return None;
    }
    let arguments = match object.get("arguments")? {
        Value::String(arguments) => arguments.clone(),
        arguments @ Value::Object(_) => serde_json::to_string(arguments).ok()?,
        _ => return None,
    };
    serde_json::from_str::<Value>(&arguments).ok()?;
    Some((name, arguments))
}

fn normalize_legacy_tool_calls(
    bytes: Bytes,
    stream: bool,
    profile: ApiProfile,
    allowed_tool_names: &BTreeSet<String>,
) -> Bytes {
    if allowed_tool_names.is_empty() {
        return bytes;
    }
    if stream && profile == ApiProfile::ChatCompletions {
        return normalize_streamed_legacy_tool_call(bytes, allowed_tool_names);
    }
    if stream {
        return bytes;
    }
    let Ok(mut value) = serde_json::from_slice::<Value>(&bytes) else {
        return bytes;
    };
    match profile {
        ApiProfile::ChatCompletions => {
            let Some(choice) = value
                .get_mut("choices")
                .and_then(Value::as_array_mut)
                .and_then(|choices| choices.first_mut())
            else {
                return bytes;
            };
            let Some(message) = choice.get_mut("message").and_then(Value::as_object_mut) else {
                return bytes;
            };
            let Some(content) = message.get("content").and_then(Value::as_str) else {
                return bytes;
            };
            let Some((name, arguments)) = parse_legacy_tool_call(content, allowed_tool_names)
            else {
                return bytes;
            };
            message.insert("content".into(), Value::Null);
            message.insert(
                "tool_calls".into(),
                json!([{
                    "id": format!("call_{}", Uuid::new_v4()),
                    "type": "function",
                    "function": {"name": name, "arguments": arguments}
                }]),
            );
            if let Some(choice) = choice.as_object_mut() {
                choice.insert("finish_reason".into(), Value::String("tool_calls".into()));
            }
        }
        ApiProfile::Responses => {
            let Some(output) = value.get_mut("output").and_then(Value::as_array_mut) else {
                return bytes;
            };
            let call = output.iter().find_map(|item| {
                item.get("content")
                    .and_then(Value::as_array)
                    .and_then(|content| content.first())
                    .and_then(|content| content.get("text"))
                    .and_then(Value::as_str)
                    .and_then(|text| parse_legacy_tool_call(text, allowed_tool_names))
            });
            let Some((name, arguments)) = call else {
                return bytes;
            };
            let call_id = format!("call_{}", Uuid::new_v4());
            *output = vec![json!({
                "id": call_id,
                "call_id": call_id,
                "type": "function_call",
                "status": "completed",
                "name": name,
                "arguments": arguments
            })];
        }
    }
    serde_json::to_vec(&value).map(Bytes::from).unwrap_or(bytes)
}

fn normalize_streamed_legacy_tool_call(
    bytes: Bytes,
    allowed_tool_names: &BTreeSet<String>,
) -> Bytes {
    let body = String::from_utf8_lossy(&bytes);
    let mut content = String::new();
    let mut template = None;
    for line in body.lines() {
        let Some(data) = line.strip_prefix("data: ") else {
            continue;
        };
        if data == "[DONE]" {
            continue;
        }
        let Ok(value) = serde_json::from_str::<Value>(data) else {
            return bytes;
        };
        if template.is_none() {
            template = Some(value.clone());
        }
        if let Some(fragment) = value
            .pointer("/choices/0/delta/content")
            .and_then(Value::as_str)
        {
            content.push_str(fragment);
        }
    }
    let Some((name, arguments)) = parse_legacy_tool_call(&content, allowed_tool_names) else {
        return bytes;
    };
    let mut chunk = template.unwrap_or_else(|| json!({}));
    let Some(object) = chunk.as_object_mut() else {
        return bytes;
    };
    object.insert(
        "object".into(),
        Value::String("chat.completion.chunk".into()),
    );
    object.insert(
        "choices".into(),
        json!([{
            "index": 0,
            "delta": {
                "role": "assistant",
                "content": null,
                "tool_calls": [{
                    "index": 0,
                    "id": format!("call_{}", Uuid::new_v4()),
                    "type": "function",
                    "function": {"name": name, "arguments": arguments}
                }]
            },
            "finish_reason": "tool_calls"
        }]),
    );
    Bytes::from(format!("data: {chunk}\n\ndata: [DONE]\n\n"))
}

fn is_ask_user_tool(name: &str) -> bool {
    name.replace("\\_", "_").eq_ignore_ascii_case("ask_user")
}

fn is_self_capability_inquiry(request: &WireCanonicalRequest) -> bool {
    let Some(content) = request.messages.iter().rev().find_map(|message| {
        matches!(message.role, noerelay_core::wire::CanonicalRole::User)
            .then(|| message.content.as_ref().map(canonical_content_text))
            .flatten()
    }) else {
        return false;
    };
    let normalized = content.to_ascii_lowercase();
    normalized.contains("your capabilities")
        || normalized.contains("what can you do")
        || normalized.contains("how do you work")
        || normalized.contains("how you work")
        || normalized.contains("your models capable")
        || normalized.contains("your model capable")
        || normalized.contains("how do you route")
        || normalized.contains("how you route")
        || normalized.contains("route to different models")
        || normalized.contains("model routing")
        || normalized.contains("routing and stack")
        || normalized.contains("your stack")
        || normalized.contains("noerelay control plane")
        || normalized.contains("models integrated into")
        || normalized.contains("axiovex sentinel system")
}

/// Detect a provider repeatedly requesting an identical tool invocation during
/// one user turn. Open WebUI resubmits prior assistant tool calls and results on
/// each continuation, so this works without process-local conversational state.
/// Two identical calls or eight total calls are enough to force a text answer.
fn has_repeated_tool_loop(request: &WireCanonicalRequest) -> bool {
    let start = request
        .messages
        .iter()
        .rposition(|message| matches!(message.role, noerelay_core::wire::CanonicalRole::User))
        .map_or(0, |index| index + 1);
    let mut signatures = BTreeMap::<String, usize>::new();
    let mut total_calls = 0_usize;

    for call in request.messages[start..]
        .iter()
        .filter_map(|message| message.tool_calls.as_deref())
        .flatten()
    {
        total_calls += 1;
        let normalized_arguments = serde_json::from_str::<Value>(&call.function.arguments)
            .and_then(|value| serde_json::to_string(&value))
            .unwrap_or_else(|_| call.function.arguments.trim().to_owned());
        let signature = format!("{}\n{normalized_arguments}", call.function.name);
        let count = signatures.entry(signature).or_default();
        *count += 1;
        if *count >= 2 || total_calls >= 8 {
            return true;
        }
    }
    false
}

fn disable_tool_use(request: &mut serde_json::Map<String, Value>) {
    request.remove("tools");
    request.insert("tool_choice".into(), Value::String("none".into()));
}

fn remove_ask_user_tool(request: &mut serde_json::Map<String, Value>) {
    let Some(tools) = request.get_mut("tools").and_then(Value::as_array_mut) else {
        return;
    };
    tools.retain(|tool| {
        let name = tool
            .get("function")
            .and_then(|function| function.get("name"))
            .or_else(|| tool.get("name"))
            .and_then(Value::as_str);
        name.is_none_or(|name| !is_ask_user_tool(name))
    });
    if tools.is_empty() {
        disable_tool_use(request);
    }
}

fn rewrite_public_model(bytes: Bytes, stream: bool, public_model_id: &str) -> Bytes {
    if !stream {
        let Ok(mut value) = serde_json::from_slice::<Value>(&bytes) else {
            return bytes;
        };
        if let Some(object) = value.as_object_mut() {
            object.insert("model".into(), Value::String(public_model_id.into()));
        }
        return serde_json::to_vec(&value).map(Bytes::from).unwrap_or(bytes);
    }

    let body = String::from_utf8_lossy(&bytes);
    let mut rewritten = String::with_capacity(body.len());
    for segment in body.split_inclusive('\n') {
        let (line, newline) = segment
            .strip_suffix('\n')
            .map_or((segment, ""), |line| (line, "\n"));
        if let Some(data) = line.strip_prefix("data: ")
            && data != "[DONE]"
            && let Ok(mut value) = serde_json::from_str::<Value>(data)
        {
            if let Some(object) = value.as_object_mut() {
                object.insert("model".into(), Value::String(public_model_id.into()));
            }
            rewritten.push_str("data: ");
            rewritten.push_str(&serde_json::to_string(&value).unwrap_or_else(|_| data.into()));
            rewritten.push_str(newline);
            continue;
        }
        rewritten.push_str(line);
        rewritten.push_str(newline);
    }
    Bytes::from(rewritten)
}

fn apply_agent_instructions(
    request: &mut serde_json::Map<String, Value>,
    profile: ApiProfile,
    _model: &str,
    self_capability_inquiry: bool,
    repeated_tool_loop: bool,
) {
    let role = "You are AXIOVEX Agni, the governed assistant presented by AXIOVEX Sentinel and powered by the NoeRelay Intelligent AI Control Plane.";
    let loop_instruction = if repeated_tool_loop {
        " A repeated-tool circuit breaker is active for this response. Do not call any tool. Use the results already present in the conversation, answer directly, and clearly identify anything still unknown."
    } else {
        ""
    };
    let product_manifest = if self_capability_inquiry {
        "\n\nYour factual product identity: AXIOVEX Sentinel is the Open WebUI-based enterprise interface. NoeRelay is the Rust-based Intelligent AI Control Plane behind its OpenAI-compatible API. NoeRelay compiles and cleans context, enforces policy and risk constraints, selects an admissible internal route using capability, cost, latency, and acceptance evidence, records governed runs and signed hash-linked receipts in PostgreSQL, and reports usage and cost. The axiovex-agni route uses a LiteLLM model plane spanning host-GPU Ollama and the remote GPU, with OpenRouter failover. AXIOVEX Sentinel separately exposes axiovex-agni-recovery through direct host-GPU Ollama inference, deliberately bypassing NoeRelay so maintainers can repair the control plane when it is unavailable. Open Terminal supplies permission-controlled workspace execution; Docling supplies document extraction, OCR, and PDF processing; optional WebUI tools supply web search and other configured actions. Tool availability always depends on deployment configuration and the signed-in user's permissions; never claim access you do not have. Public AXIOVEX model names intentionally hide changeable provider model identifiers."
    } else {
        ""
    };
    let self_description_instruction = if self_capability_inquiry {
        " Answer self-description, routing, and architecture questions directly from the factual product identity above. Never search knowledge bases merely to explain your own built-in architecture."
    } else {
        ""
    };
    let instructions = format!(
        "{role}{product_manifest}\n\nAnswer greetings, capability questions, explanations, and ordinary conversation directly in natural language.{self_description_instruction} Use a tool only when current external data or an action is actually required. Never call ask_user merely to present generic choices or ask what the user wants after they already made a clear request. Never emit a serialized tool-call object as assistant text; when a tool is necessary, use the provider's native function-calling protocol. Do not identify yourself as the underlying provider or base model.{loop_instruction}"
    );
    match profile {
        ApiProfile::ChatCompletions => {
            if let Some(messages) = request.get_mut("messages").and_then(Value::as_array_mut) {
                messages.insert(0, json!({"role": "system", "content": instructions}));
            }
        }
        ApiProfile::Responses => {
            let existing = request
                .get("instructions")
                .and_then(Value::as_str)
                .unwrap_or_default();
            request.insert(
                "instructions".into(),
                Value::String(if existing.is_empty() {
                    instructions
                } else {
                    format!("{instructions}\n\n{existing}")
                }),
            );
        }
    }
}

fn format_sse(profile: ApiProfile, response: &noerelay_core::CanonicalResponse) -> String {
    match profile {
        ApiProfile::ChatCompletions => {
            let message = response
                .choices
                .first()
                .and_then(|choice| choice.message.as_ref());
            let content = message.and_then(|message| match message.content.as_ref() {
                Some(noerelay_core::wire::CanonicalContent::Text(text)) => Some(text.clone()),
                _ => None,
            });
            let tool_calls = message.and_then(|message| {
                message.tool_calls.as_ref().map(|calls| {
                    calls
                        .iter()
                        .map(|call| {
                            json!({
                                "index": 0,
                                "id": call.id,
                                "type": "function",
                                "function": {
                                    "name": call.function.name,
                                    "arguments": call.function.arguments,
                                }
                            })
                        })
                        .collect::<Vec<_>>()
                })
            });
            let finish_reason = response
                .choices
                .first()
                .and_then(|choice| choice.finish_reason.as_deref());
            let chunk = json!({
                "id": response.id,
                "object": "chat.completion.chunk",
                "model": response.model,
                "choices": [{
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "content": content,
                        "tool_calls": tool_calls,
                    },
                    "finish_reason": finish_reason,
                }],
            });
            format!("data: {chunk}\n\ndata: [DONE]\n\n")
        }
        ApiProfile::Responses => {
            let formatted = ResponsesConverter::format_response(response);
            format!(
                "event: response.completed\ndata: {}\n\n",
                json!({"type": "response.completed", "response": formatted})
            )
        }
    }
}

fn governance_messages(request: &WireCanonicalRequest) -> Vec<Message> {
    request
        .messages
        .iter()
        .map(|message| Message {
            role: match message.role {
                noerelay_core::wire::CanonicalRole::System => MessageRole::System,
                noerelay_core::wire::CanonicalRole::User => MessageRole::User,
                noerelay_core::wire::CanonicalRole::Assistant => MessageRole::Assistant,
                noerelay_core::wire::CanonicalRole::Tool => MessageRole::Tool,
            },
            content: message
                .content
                .as_ref()
                .map(canonical_content_text)
                .unwrap_or_default(),
            name: message.name.clone(),
            tool_call_id: message.tool_call_id.clone(),
        })
        .collect()
}

fn canonical_content_text(content: &noerelay_core::wire::CanonicalContent) -> String {
    match content {
        noerelay_core::wire::CanonicalContent::Text(text) => text.clone(),
        noerelay_core::wire::CanonicalContent::Parts(parts) => parts
            .iter()
            .map(|part| match part {
                noerelay_core::wire::CanonicalContentPart::Text { text } => text.clone(),
                noerelay_core::wire::CanonicalContentPart::ImageUrl { image_url } => {
                    format!("[image_url:{}]", image_url.url)
                }
            })
            .collect::<Vec<_>>()
            .join("\n"),
    }
}

async fn validate_requested_model(
    _state: &AppState,
    _identity: Option<&AuthenticatedIdentity>,
    request: &WireCanonicalRequest,
) -> Result<(), Response> {
    if request.model == PRIMARY_PUBLIC_MODEL_ID {
        return Ok(());
    }
    Err(api_error_response(
        StatusCode::NOT_FOUND,
        ApiError::model_not_found(&request.model),
    ))
}

struct DurableExecution {
    organization_id: noerelay_core::iam::OrganizationId,
    run_id: noerelay_core::RunId,
    step_id: noerelay_core::StepId,
    attempt_id: noerelay_core::AttemptId,
}

async fn create_durable_run(
    state: &AppState,
    identity: Option<&AuthenticatedIdentity>,
    prepared: &noerelay_core::PreparedRun,
) -> Result<Option<DurableExecution>, Response> {
    let (Some(repository), Some(identity)) = (&state.execution_repo, identity) else {
        return Ok(None);
    };
    // A fresh deployment authenticated with its bootstrap key has a stateless
    // identity until an organization principal is provisioned. The core
    // authority snapshot and signed receipt are still persisted, but the
    // relational execution projection cannot satisfy its tenant foreign keys.
    if identity.organization_id.0 == Uuid::nil() || identity.principal_id.0 == Uuid::nil() {
        return Ok(None);
    }
    let run = repository
        .create_run(
            identity.organization_id,
            None,
            None,
            identity.principal_id,
            &prepared.contract.contract_hash,
            prepared.contract.context_manifest_hash.as_deref(),
            "single-region-org-v1-local-test",
            None,
        )
        .await
        .map_err(|_| internal_api_error())?;
    repository
        .update_run_status(
            identity.organization_id,
            run.id,
            noerelay_core::RunStatus::Running,
        )
        .await
        .map_err(|_| internal_api_error())?;
    let step = repository
        .create_step(
            identity.organization_id,
            run.id,
            None,
            noerelay_core::StepType::ProviderCall,
            "stub provider completion",
            0,
            None,
        )
        .await
        .map_err(|_| internal_api_error())?;
    repository
        .update_step_status(
            identity.organization_id,
            step.id,
            noerelay_core::StepStatus::Ready,
        )
        .await
        .map_err(|_| internal_api_error())?;
    repository
        .update_step_status(
            identity.organization_id,
            step.id,
            noerelay_core::StepStatus::Running,
        )
        .await
        .map_err(|_| internal_api_error())?;
    let attempt = repository
        .create_attempt(identity.organization_id, step.id, 1, None)
        .await
        .map_err(|_| internal_api_error())?;
    repository
        .update_attempt_status(
            identity.organization_id,
            attempt.id,
            noerelay_core::AttemptStatus::Running,
            None,
            None,
        )
        .await
        .map_err(|_| internal_api_error())?;
    Ok(Some(DurableExecution {
        organization_id: identity.organization_id,
        run_id: run.id,
        step_id: step.id,
        attempt_id: attempt.id,
    }))
}

async fn record_stub_attempt(
    state: &AppState,
    durable: Option<&DurableExecution>,
    request: &WireCanonicalRequest,
    response: &noerelay_core::CanonicalResponse,
    request_bytes: &[u8],
    response_bytes: &[u8],
) -> Result<(), Response> {
    let (Some(repository), Some(durable)) = (&state.execution_repo, durable) else {
        return Ok(());
    };
    let request_hash = format!("sha256:{}", hex::encode(Sha256::digest(request_bytes)));
    let response_hash = format!("sha256:{}", hex::encode(Sha256::digest(response_bytes)));
    let usage = response.usage.as_ref();
    repository
        .record_provider_call(
            durable.organization_id,
            durable.attempt_id,
            "noerelay-stub",
            &request.model,
            &request_hash,
            Some(&response_hash),
            usage.map(|value| value.prompt_tokens),
            usage.map(|value| value.completion_tokens),
            "completed",
        )
        .await
        .map_err(|_| internal_api_error())?;
    repository
        .update_attempt_status(
            durable.organization_id,
            durable.attempt_id,
            noerelay_core::AttemptStatus::Succeeded,
            None,
            Some(0),
        )
        .await
        .map_err(|_| internal_api_error())?;
    repository
        .update_step_status(
            durable.organization_id,
            durable.step_id,
            noerelay_core::StepStatus::Completed,
        )
        .await
        .map_err(|_| internal_api_error())?;
    repository
        .update_run_status(
            durable.organization_id,
            durable.run_id,
            noerelay_core::RunStatus::Completed,
        )
        .await
        .map_err(|_| internal_api_error())?;
    Ok(())
}

async fn release_response(
    state: &AppState,
    prepared: &noerelay_core::PreparedRun,
    status: StatusCode,
    content_type: &'static str,
    stream: bool,
    bytes: Bytes,
) -> Response {
    // A governed stream is buffered until its terminal event and verification
    // are observed. This preserves the release gate at the cost of first-token
    // latency; incremental verified streaming remains an explicit release gap.
    let schema_valid = if stream {
        let body = String::from_utf8_lossy(&bytes);
        body.contains("data:") && (body.contains("[DONE]") || body.contains("response.completed"))
    } else {
        serde_json::from_slice::<Value>(&bytes).is_ok_and(|value| value.is_object())
    };
    let output_hash = hex::encode(Sha256::digest(&bytes));
    let results = [CheckResult {
        check_id: "response_schema".into(),
        status: if schema_valid {
            CheckStatus::Passed
        } else {
            CheckStatus::Failed
        },
        observed_evidence_id: schema_valid.then(|| format!("sha256:{output_hash}")),
        verifier_family: None,
        evidence_kind: Some("observed".into()),
        uncertainty: Some("none".into()),
        recommended_action: if schema_valid {
            Some("none".into())
        } else {
            Some("revise".into())
        },
        finding_severity: if schema_valid {
            Some("info".into())
        } else {
            Some("high".into())
        },
        finding_kind: if schema_valid {
            Some("other".into())
        } else {
            Some("schema_violation".into())
        },
        description: Some(if schema_valid {
            "Response schema validation passed.".into()
        } else {
            "Response schema validation failed — output is not valid JSON.".into()
        }),
        rationale: None,
    }];
    let (input_tokens, output_tokens) = response_usage(&bytes, stream);
    let (completion, signed_receipt) = match state
        .complete_run(prepared, &bytes, input_tokens, output_tokens, &results)
        .await
    {
        Ok(value) => value,
        Err(error_value) => return authority_error_response(error_value),
    };
    if completion.outcome != ReleaseOutcome::Accepted {
        return error(
            StatusCode::FAILED_DEPENDENCY,
            "verification_required",
            "The result was withheld because required verification or approval is incomplete",
        );
    }
    let receipt = signed_receipt.expect("accepted completion always produces a signed receipt");
    let receipt_hash = receipt.receipt.receipt_hash.clone();
    state
        .receipts
        .lock()
        .await
        .insert(prepared.run_id.clone(), receipt);
    // Build evaluator-contract result from verification checks
    let evaluator_result = build_evaluator_result(&prepared, &results);
    let evaluator_result_json =
        serde_json::to_string(&evaluator_result).unwrap_or_else(|_| "{}".into());

    let mut builder = Response::builder()
        .status(status)
        .header("content-type", content_type)
        .header("x-noerelay-run-id", &prepared.run_id)
        .header("x-noerelay-contract-hash", &prepared.contract.contract_hash)
        .header(
            "x-noerelay-context-manifest-hash",
            prepared
                .contract
                .context_manifest_hash
                .as_deref()
                .unwrap_or("unavailable"),
        )
        .header("x-noerelay-receipt-hash", receipt_hash)
        .header("x-noerelay-evaluator-result", &evaluator_result_json);
    if stream {
        builder = builder.header("cache-control", "no-cache");
    }
    builder.body(Body::from(bytes)).unwrap_or_else(|_| {
        error(
            StatusCode::INTERNAL_SERVER_ERROR,
            "response_error",
            "Could not construct the response",
        )
    })
}

/// Build an evaluator-contract result from the prepared run and check results.
fn build_evaluator_result(
    prepared: &noerelay_core::PreparedRun,
    results: &[CheckResult],
) -> noerelay_core::EvaluatorResult {
    let findings: Vec<noerelay_core::Finding> = prepared
        .verification_checks
        .iter()
        .map(|check| {
            let result = results
                .iter()
                .find(|r| r.check_id == check.check_id)
                .cloned()
                .unwrap_or_else(|| CheckResult {
                    check_id: check.check_id.clone(),
                    status: CheckStatus::NotRun,
                    observed_evidence_id: None,
                    verifier_family: None,
                    evidence_kind: Some("unsupported".into()),
                    uncertainty: Some("insufficient_evidence".into()),
                    recommended_action: Some("gather_evidence".into()),
                    finding_severity: Some("medium".into()),
                    finding_kind: Some("missing_evidence".into()),
                    description: Some(format!("Check '{}' was not executed.", check.check_id)),
                    rationale: None,
                });
            noerelay_core::Finding::from_check_result(&result, &check.kind, &prepared.run_id)
        })
        .collect();

    noerelay_core::EvaluatorResult::from_findings(
        "noerelay-gateway",
        env!("CARGO_PKG_VERSION"),
        noerelay_core::EvaluatorPhase::AfterImplement,
        findings,
    )
}

async fn abort_run(state: &AppState, run_id: &str, reason_code: &str) {
    let _ = state.abort_run(run_id, reason_code).await;
}

fn authority_error_response(value: AuthorityError) -> Response {
    match value {
        AuthorityError::Runtime(error_value) => runtime_error_response(error_value),
        AuthorityError::Store(_) => error(
            StatusCode::SERVICE_UNAVAILABLE,
            "storage_error",
            "The durable authority transition could not be committed",
        ),
        AuthorityError::Signature(_) => error(
            StatusCode::INTERNAL_SERVER_ERROR,
            "receipt_signing_error",
            "The result could not be signed and was not released",
        ),
    }
}

fn runtime_error_response(value: RuntimeError) -> Response {
    match value {
        RuntimeError::Contract(error_value) => error(
            StatusCode::UNPROCESSABLE_ENTITY,
            "contract_rejected",
            &error_value.to_string(),
        ),
        RuntimeError::NoAdmissibleRoute => error(
            StatusCode::FAILED_DEPENDENCY,
            "no_admissible_route",
            "No model satisfies the request constraints",
        ),
        RuntimeError::DuplicateRun => error(
            StatusCode::CONFLICT,
            "duplicate_run",
            "The request ID is already active",
        ),
        RuntimeError::Budget(_) => error(
            StatusCode::PAYMENT_REQUIRED,
            "budget_rejected",
            "The governed budget could not authorize this request",
        ),
        RuntimeError::Verification(_) => error(
            StatusCode::FAILED_DEPENDENCY,
            "verification_rejected",
            "Verification failed closed",
        ),
        _ => error(
            StatusCode::INTERNAL_SERVER_ERROR,
            "authority_error",
            "The Rust authority could not complete the transition",
        ),
    }
}

fn now_unix_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

fn response_usage(bytes: &[u8], stream: bool) -> (u64, u64) {
    if stream {
        return (0, 0);
    }
    let Ok(value) = serde_json::from_slice::<Value>(bytes) else {
        return (0, 0);
    };
    let usage = &value["usage"];
    let input = usage["input_tokens"]
        .as_u64()
        .or_else(|| usage["prompt_tokens"].as_u64())
        .unwrap_or(0);
    let output = usage["output_tokens"]
        .as_u64()
        .or_else(|| usage["completion_tokens"].as_u64())
        .unwrap_or(0);
    (input, output)
}

fn compile_wire_context(
    messages: &[Message],
    budget_tokens: u32,
) -> Result<(Vec<Message>, String, usize), String> {
    let latest_user = messages
        .iter()
        .rposition(|message| message.role == MessageRole::User);
    let nodes: Vec<ContextNode> = messages
        .iter()
        .enumerate()
        .map(|(index, message)| {
            let kind = match message.role {
                MessageRole::System | MessageRole::Developer => NodeKind::Decision,
                MessageRole::Tool => NodeKind::ToolState,
                MessageRole::User if Some(index) == latest_user => NodeKind::Requirement,
                _ => NodeKind::Conversation,
            };
            let character_count = message.content.chars().count();
            let estimated_tokens = u32::try_from(character_count.div_ceil(4))
                .unwrap_or(u32::MAX)
                .max(1);
            let sequence = u64::try_from(index).unwrap_or(u64::MAX);
            ContextNode {
                node_id: format!("message-{index}"),
                kind,
                content: message.content.clone(),
                source_handle: format!("wire:message:{index}"),
                estimated_tokens,
                salience_ppm: if Some(index) == latest_user {
                    1_000_000
                } else {
                    500_000_u32.saturating_add(u32::try_from(index).unwrap_or(u32::MAX))
                },
                sequence,
                explicitly_protected: false,
            }
        })
        .collect();
    let manifest = ContextCompiler
        .compile(&nodes, budget_tokens)
        .map_err(|error| error.to_string())?;
    let included_ids: BTreeSet<&str> = manifest
        .included
        .iter()
        .map(|node| node.node_id.as_str())
        .collect();
    let included = messages
        .iter()
        .enumerate()
        .filter(|(index, _)| included_ids.contains(format!("message-{index}").as_str()))
        .map(|(_, message)| message.clone())
        .collect();
    Ok((
        included,
        manifest.manifest_hash,
        manifest.omitted_node_ids.len(),
    ))
}

fn parse_risk(value: Option<&str>) -> RiskClass {
    match value {
        Some("medium") => RiskClass::Medium,
        Some("high") => RiskClass::High,
        Some("critical") => RiskClass::Critical,
        _ => RiskClass::Low,
    }
}

fn header_or(headers: &HeaderMap, name: &str, fallback: &str) -> String {
    headers
        .get(name)
        .and_then(|value| value.to_str().ok())
        .filter(|value| !value.is_empty())
        .unwrap_or(fallback)
        .to_owned()
}

fn authorized(headers: &HeaderMap, expected: &[u8; 32]) -> bool {
    let Some(value) = headers
        .get("authorization")
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.strip_prefix("Bearer "))
    else {
        return false;
    };
    let actual: [u8; 32] = Sha256::digest(value.as_bytes()).into();
    constant_time_equal(&actual, expected)
}

fn constant_time_equal(left: &[u8], right: &[u8]) -> bool {
    if left.len() != right.len() {
        return false;
    }
    left.iter()
        .zip(right)
        .fold(0_u8, |difference, (a, b)| difference | (a ^ b))
        == 0
}

#[derive(Debug, Serialize, Deserialize)]
struct ErrorEnvelope {
    error: ErrorBody,
}

#[derive(Debug, Serialize, Deserialize)]
struct ErrorBody {
    message: String,
    r#type: String,
    code: String,
}

fn error(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(ErrorEnvelope {
            error: ErrorBody {
                message: message.into(),
                r#type: "noerelay_error".into(),
                code: code.into(),
            },
        }),
    )
        .into_response()
}

fn api_error_response(status: StatusCode, value: ApiError) -> Response {
    (status, Json(value)).into_response()
}

fn internal_api_error() -> Response {
    api_error_response(
        StatusCode::INTERNAL_SERVER_ERROR,
        ApiError::invalid_request("An internal error occurred.", None),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::Request;
    use http_body_util::BodyExt;
    use noerelay_core::CostBreakdown;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tower::ServiceExt;

    const CLIENT_KEY: &str = "a-client-key-that-is-at-least-32-chars";

    fn candidate() -> Candidate {
        Candidate {
            candidate_id: "model-a".into(),
            openrouter_model_id: "anthropic/claude-test".into(),
            provider: "anthropic".into(),
            available: true,
            capabilities: BTreeSet::from(["text".into()]),
            maximum_data_class: DataClass::Confidential,
            cost: CostBreakdown {
                inference_microusd: 100,
                ..Default::default()
            },
            latency_p95_ms: 100,
            acceptance_lcb_ppm: 999_000,
            supports_independent_verification: true,
        }
    }

    fn state(base_url: String) -> AppState {
        AppState::new(GatewayConfig {
            bearer_key_sha256: Sha256::digest(CLIENT_KEY.as_bytes()).into(),
            openrouter_api_key: "upstream-test-key".into(),
            openrouter_base_url: base_url,
            stub_mode: false,
            default_scope: IdentityScope {
                organization_id: "org".into(),
                project_id: "project".into(),
                environment_id: "test".into(),
                user_id: "user".into(),
                session_id: "session".into(),
            },
            candidates: vec![candidate()],
            request_timeout: Duration::from_secs(2),
            maximum_body_bytes: 1024 * 1024,
            budget_limit_microusd: 1_000_000,
            database_url: None,
            receipt_signer: ReceiptSigner::from_seed("test-key", [3; 32]).unwrap(),
            context_budget_tokens: 32_768,
            ranking_mode: RankingMode::Disabled,
            ranker_sidecar_url: None,
        })
        .expect("test state")
    }

    fn authenticated_request(method: &str, uri: &str, body: Body) -> Request<Body> {
        Request::builder()
            .method(method)
            .uri(uri)
            .header("authorization", format!("Bearer {CLIENT_KEY}"))
            .header("content-type", "application/json")
            .body(body)
            .unwrap()
    }

    #[test]
    fn constant_time_equality_is_correct() {
        assert!(constant_time_equal(b"same", b"same"));
        assert!(!constant_time_equal(b"same", b"diff"));
        assert!(!constant_time_equal(b"short", b"longer"));
    }

    #[test]
    fn error_shape_matches_openai_convention() {
        let value = serde_json::to_value(ErrorEnvelope {
            error: ErrorBody {
                message: "bad".into(),
                r#type: "noerelay_error".into(),
                code: "invalid".into(),
            },
        })
        .unwrap();
        assert_eq!(value["error"]["code"], "invalid");
    }

    #[test]
    fn wire_context_drops_optional_history_but_preserves_authority_nodes() {
        let messages = vec![
            Message {
                role: MessageRole::System,
                content: "system decision".into(),
                name: None,
                tool_call_id: None,
            },
            Message {
                role: MessageRole::Assistant,
                content: "x".repeat(400),
                name: None,
                tool_call_id: None,
            },
            Message {
                role: MessageRole::User,
                content: "current requirement".into(),
                name: None,
                tool_call_id: None,
            },
        ];
        let (compiled, manifest_hash, omitted) = compile_wire_context(&messages, 16).unwrap();
        assert_eq!(compiled.len(), 2);
        assert_eq!(compiled[0].role, MessageRole::System);
        assert_eq!(compiled[1].role, MessageRole::User);
        assert_eq!(manifest_hash.len(), 64);
        assert_eq!(omitted, 1);
    }

    #[test]
    fn wire_context_fails_closed_when_protected_content_exceeds_budget() {
        let messages = vec![Message {
            role: MessageRole::User,
            content: "x".repeat(100),
            name: None,
            tool_call_id: None,
        }];
        assert!(compile_wire_context(&messages, 1).is_err());
    }

    #[tokio::test]
    async fn health_is_public_but_models_require_authentication() {
        let application = app(state("http://127.0.0.1:1".into()));
        let health_response = application
            .clone()
            .oneshot(Request::get("/health").body(Body::empty()).unwrap())
            .await
            .unwrap();
        assert_eq!(health_response.status(), StatusCode::OK);

        let models_response = application
            .oneshot(Request::get("/v1/models").body(Body::empty()).unwrap())
            .await
            .unwrap();
        assert_eq!(models_response.status(), StatusCode::UNAUTHORIZED);
    }

    #[tokio::test]
    async fn model_list_exposes_only_governed_primary_model() {
        let response = app(state("http://127.0.0.1:1".into()))
            .oneshot(authenticated_request("GET", "/v1/models", Body::empty()))
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body: Value =
            serde_json::from_slice(&response.into_body().collect().await.unwrap().to_bytes())
                .unwrap();
        assert_eq!(body["data"].as_array().unwrap().len(), 1);
        assert_eq!(body["data"][0]["id"], PRIMARY_PUBLIC_MODEL_ID);
    }

    #[tokio::test]
    async fn high_risk_without_acceptance_never_reaches_provider() {
        let request = authenticated_request(
            "POST",
            "/v1/chat/completions",
            Body::from(
                r#"{"model":"axiovex-agni","messages":[{"role":"user","content":"deploy"}]}"#,
            ),
        );
        let mut request = request;
        request
            .headers_mut()
            .insert("x-noerelay-risk", "high".parse().unwrap());
        let response = app(state("http://127.0.0.1:1".into()))
            .oneshot(request)
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::UNPROCESSABLE_ENTITY);
    }

    #[tokio::test]
    async fn selected_model_is_explicitly_forwarded_to_openrouter() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let provider = tokio::spawn(async move {
            let (mut socket, _) = listener.accept().await.unwrap();
            let mut received = vec![0_u8; 16 * 1024];
            let size = socket.read(&mut received).await.unwrap();
            let request_text = String::from_utf8_lossy(&received[..size]);
            assert!(request_text.contains("anthropic/claude-test"));
            assert!(!request_text.contains("\"model\":\"axiovex-agni\""));
            assert!(request_text.contains("Answer greetings"));
            let body = r#"{"id":"chatcmpl-test","object":"chat.completion","choices":[]}"#;
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            );
            socket.write_all(response.as_bytes()).await.unwrap();
        });
        let application = app(state(format!("http://{address}")));
        let response = application
            .clone()
            .oneshot(authenticated_request(
                "POST",
                "/v1/chat/completions",
                Body::from(
                    r#"{"model":"axiovex-agni","messages":[{"role":"user","content":"hello"}]}"#,
                ),
            ))
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert!(response.headers().contains_key("x-noerelay-contract-hash"));
        assert!(
            response
                .headers()
                .contains_key("x-noerelay-context-manifest-hash")
        );
        assert!(response.headers().contains_key("x-noerelay-receipt-hash"));
        let run_id = response
            .headers()
            .get("x-noerelay-run-id")
            .unwrap()
            .to_str()
            .unwrap()
            .to_owned();
        let public_response: Value =
            serde_json::from_slice(&response.into_body().collect().await.unwrap().to_bytes())
                .unwrap();
        assert_eq!(public_response["model"], PRIMARY_PUBLIC_MODEL_ID);
        let receipt_response = application
            .oneshot(authenticated_request(
                "GET",
                &format!("/v1/noerelay/runs/{run_id}/receipt"),
                Body::empty(),
            ))
            .await
            .unwrap();
        assert_eq!(receipt_response.status(), StatusCode::OK);
        let receipt: Value = serde_json::from_slice(
            &receipt_response
                .into_body()
                .collect()
                .await
                .unwrap()
                .to_bytes(),
        )
        .unwrap();
        assert_eq!(receipt["receipt"]["run_id"], run_id);
        assert_eq!(receipt["receipt"]["release_outcome"], "accepted");
        assert_eq!(receipt["algorithm"], "Ed25519");
        provider.await.unwrap();
    }

    #[test]
    fn public_model_is_rewritten_in_stream_events() {
        let upstream = Bytes::from_static(
            b"data: {\"id\":\"chatcmpl-test\",\"model\":\"gpu-pool\",\"choices\":[]}\n\ndata: [DONE]\n\n",
        );
        let rewritten = String::from_utf8(
            rewrite_public_model(upstream, true, PRIMARY_PUBLIC_MODEL_ID).to_vec(),
        )
        .unwrap();
        assert!(rewritten.contains("\"model\":\"axiovex-agni\""));
        assert!(!rewritten.contains("gpu-pool"));
        assert!(rewritten.contains("data: [DONE]"));
    }

    #[test]
    fn ask_user_is_removed_without_disabling_execution_tools() {
        let mut request = json!({
            "tools": [
                {"type":"function","function":{"name":"ask_user"}},
                {"type":"function","function":{"name":"run_terminal"}}
            ],
            "tool_choice": "auto"
        })
        .as_object()
        .unwrap()
        .clone();
        remove_ask_user_tool(&mut request);
        assert_eq!(request["tools"].as_array().unwrap().len(), 1);
        assert_eq!(request["tools"][0]["function"]["name"], "run_terminal");
        assert_eq!(request["tool_choice"], "auto");
    }

    #[test]
    fn escaped_ask_user_is_removed_and_tool_choice_is_disabled() {
        let mut request = json!({
            "tools": [{"type":"function","name":"ask\\_user"}],
            "tool_choice": "auto"
        })
        .as_object()
        .unwrap()
        .clone();
        remove_ask_user_tool(&mut request);
        assert!(request.get("tools").is_none());
        assert_eq!(request["tool_choice"], "none");
    }

    #[test]
    fn legacy_json_tool_envelope_is_promoted_to_native_chat_tool_call() {
        let allowed = BTreeSet::from(["list_knowledge_bases".to_owned()]);
        let upstream = Bytes::from_static(
            br#"{"choices":[{"index":0,"finish_reason":"stop","message":{"role":"assistant","content":"{\"name\":\"list_knowledge_bases\",\"arguments\":{\"count\":5}}"}}],"model":"gpu-pool"}"#,
        );
        let normalized =
            normalize_legacy_tool_calls(upstream, false, ApiProfile::ChatCompletions, &allowed);
        let value: Value = serde_json::from_slice(&normalized).unwrap();
        assert_eq!(value["choices"][0]["finish_reason"], "tool_calls");
        assert_eq!(
            value["choices"][0]["message"]["tool_calls"][0]["function"]["name"],
            "list_knowledge_bases"
        );
        assert!(value["choices"][0]["message"]["content"].is_null());
    }

    #[test]
    fn streamed_legacy_json_tool_envelope_is_promoted_to_native_tool_call() {
        let allowed = BTreeSet::from(["list_knowledge_bases".to_owned()]);
        let upstream = Bytes::from_static(
            b"data: {\"id\":\"chatcmpl-test\",\"model\":\"gpu-pool\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"{\\\"name\\\":\\\"list_knowledge_bases\\\",\"},\"finish_reason\":null}]}\n\ndata: {\"id\":\"chatcmpl-test\",\"model\":\"gpu-pool\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"\\\"arguments\\\":{\\\"count\\\":5}}\"},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n",
        );
        let normalized =
            normalize_legacy_tool_calls(upstream, true, ApiProfile::ChatCompletions, &allowed);
        let text = String::from_utf8(normalized.to_vec()).unwrap();
        assert!(text.contains("list_knowledge_bases"));
        assert!(text.contains("tool_calls"));
        assert!(text.contains("data: [DONE]"));
        assert!(!text.contains("delta\":{\"content\":\"{"));
    }

    #[test]
    fn arbitrary_json_content_is_not_promoted_without_an_allowed_tool() {
        let allowed = BTreeSet::from(["run_terminal".to_owned()]);
        let content = r#"{"name":"list_knowledge_bases","arguments":{"count":5}}"#;
        assert!(parse_legacy_tool_call(content, &allowed).is_none());
    }

    #[test]
    fn architecture_question_is_answered_without_tools() {
        let request = ChatCompletionsConverter::parse_request(&json!({
            "model": PRIMARY_PUBLIC_MODEL_ID,
            "messages": [{
                "role": "user",
                "content": "Be specific: what are your models capable of, how do you route to different models, and what does your stack look like?"
            }],
            "tools": [{
                "type": "function",
                "function": {"name": "list_knowledge_bases", "parameters": {"type": "object"}}
            }]
        }))
        .unwrap();
        assert!(is_self_capability_inquiry(&request));

        let concise = ChatCompletionsConverter::parse_request(&json!({
            "model": PRIMARY_PUBLIC_MODEL_ID,
            "messages": [{"role": "user", "content": "Explain your model routing and stack."}]
        }))
        .unwrap();
        assert!(is_self_capability_inquiry(&concise));
    }

    #[test]
    fn repeated_identical_tool_calls_activate_circuit_breaker() {
        let request = ChatCompletionsConverter::parse_request(&json!({
            "model": PRIMARY_PUBLIC_MODEL_ID,
            "messages": [
                {"role": "user", "content": "Find the relevant internal material."},
                {"role": "assistant", "content": null, "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "list_knowledge_bases", "arguments": "{\"count\":5}"}
                }]},
                {"role": "tool", "tool_call_id": "call_1", "content": "[]"},
                {"role": "assistant", "content": null, "tool_calls": [{
                    "id": "call_2", "type": "function",
                    "function": {"name": "list_knowledge_bases", "arguments": "{ \"count\" : 5 }"}
                }]},
                {"role": "tool", "tool_call_id": "call_2", "content": "[]"}
            ]
        }))
        .unwrap();
        assert!(has_repeated_tool_loop(&request));
    }

    #[test]
    fn distinct_tool_progress_does_not_activate_circuit_breaker() {
        let request = ChatCompletionsConverter::parse_request(&json!({
            "model": PRIMARY_PUBLIC_MODEL_ID,
            "messages": [
                {"role": "user", "content": "Compare these sources."},
                {"role": "assistant", "content": null, "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "search", "arguments": "{\"q\":\"one\"}"}
                }]},
                {"role": "tool", "tool_call_id": "call_1", "content": "first"},
                {"role": "assistant", "content": null, "tool_calls": [{
                    "id": "call_2", "type": "function",
                    "function": {"name": "search", "arguments": "{\"q\":\"two\"}"}
                }]},
                {"role": "tool", "tool_call_id": "call_2", "content": "second"}
            ]
        }))
        .unwrap();
        assert!(!has_repeated_tool_loop(&request));
    }

    #[tokio::test]
    async fn high_risk_output_is_withheld_without_independent_evidence() {
        let mut configured = state("http://127.0.0.1:1".into());
        Arc::get_mut(&mut configured.config).unwrap().stub_mode = true;
        let mut request = authenticated_request(
            "POST",
            "/v1/responses",
            Body::from(r#"{"model":"axiovex-agni","input":"deploy this"}"#),
        );
        request
            .headers_mut()
            .insert("x-noerelay-risk", "high".parse().unwrap());
        request.headers_mut().insert(
            "x-noerelay-acceptance",
            "independent checks pass".parse().unwrap(),
        );
        let response = app(configured).oneshot(request).await.unwrap();
        assert_eq!(response.status(), StatusCode::FAILED_DEPENDENCY);
        let body: Value =
            serde_json::from_slice(&response.into_body().collect().await.unwrap().to_bytes())
                .unwrap();
        assert_eq!(body["error"]["code"], "verification_required");
        assert!(!body.to_string().contains("NoeRelay stub response"));
    }

    #[tokio::test]
    async fn cost_report_rolls_up_requests_tokens_and_integer_cost_by_user() {
        let mut configured = state("http://127.0.0.1:1".into());
        Arc::get_mut(&mut configured.config).unwrap().stub_mode = true;
        let application = app(configured);
        let mut request = authenticated_request(
            "POST",
            "/v1/responses",
            Body::from(r#"{"model":"axiovex-agni","input":"report this"}"#),
        );
        request
            .headers_mut()
            .insert("x-noerelay-user", "alice".parse().unwrap());
        let response = application.clone().oneshot(request).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);

        let report = application
            .oneshot(authenticated_request(
                "GET",
                "/v1/noerelay/reports/costs",
                Body::empty(),
            ))
            .await
            .unwrap();
        assert_eq!(report.status(), StatusCode::OK);
        let body: Value =
            serde_json::from_slice(&report.into_body().collect().await.unwrap().to_bytes())
                .unwrap();
        assert_eq!(body["data"][0]["user_id"], "alice");
        assert_eq!(body["totals"]["requests"], 1);
        assert!(body["totals"]["input_tokens"].as_u64().unwrap() > 0);
        assert!(body["totals"]["output_tokens"].as_u64().unwrap() > 0);
        assert_eq!(body["totals"]["cost_microusd"], 100);
    }

    #[tokio::test]
    async fn governance_gate_requires_observed_requirement_linked_evidence() {
        let application = app(state("http://127.0.0.1:1".into()));
        let bundle = json!({
            "requirements": [{
                "requirement_id": "REQ-1",
                "architecture_refs": ["architecture:gateway"],
                "outcome": "The endpoint is governed",
                "must": true
            }],
            "tests": [{
                "test_id": "TEST-1",
                "requirement_ids": ["REQ-1"],
                "independent": true
            }],
            "evidence": [{
                "evidence_id": "EVIDENCE-1",
                "test_id": "TEST-1",
                "source_revision": "abc123",
                "artifact_hash": "def456",
                "status": "claimed"
            }]
        });
        let rejected = application
            .clone()
            .oneshot(authenticated_request(
                "POST",
                "/v1/noerelay/governance/release-gate",
                Body::from(serde_json::to_vec(&bundle).unwrap()),
            ))
            .await
            .unwrap();
        assert_eq!(rejected.status(), StatusCode::FAILED_DEPENDENCY);

        let mut observed = bundle;
        observed["evidence"][0]["status"] = Value::String("observed_pass".into());
        let accepted = application
            .oneshot(authenticated_request(
                "POST",
                "/v1/noerelay/governance/release-gate",
                Body::from(serde_json::to_vec(&observed).unwrap()),
            ))
            .await
            .unwrap();
        assert_eq!(accepted.status(), StatusCode::OK);
    }
}
