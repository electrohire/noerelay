use axum::{
    body::Body,
    http::{Request, StatusCode},
};
use http_body_util::BodyExt;
use noerelay_core::{Candidate, CostBreakdown, DataClass, IdentityScope, ReceiptSigner};
use noerelay_gateway::{AppState, GatewayConfig, app};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{collections::BTreeSet, time::Duration};
use tower::ServiceExt;

const API_KEY: &str = "api-02-test-key-that-is-at-least-32-characters";

fn application() -> axum::Router {
    let candidate = Candidate {
        candidate_id: "stub-model".into(),
        openrouter_model_id: "test/stub-model".into(),
        provider: "noerelay-stub".into(),
        available: true,
        capabilities: BTreeSet::from(["text".into(), "vision".into()]),
        maximum_data_class: DataClass::Confidential,
        cost: CostBreakdown {
            inference_microusd: 1,
            ..Default::default()
        },
        latency_p95_ms: 1,
        acceptance_lcb_ppm: 999_000,
        supports_independent_verification: true,
    };
    let state = AppState::new(GatewayConfig {
        bearer_key_sha256: Sha256::digest(API_KEY.as_bytes()).into(),
        openrouter_api_key: String::new(),
        openrouter_base_url: "http://127.0.0.1:1".into(),
        stub_mode: true,
        default_scope: IdentityScope {
            organization_id: "local-test-org".into(),
            project_id: "local-test-project".into(),
            environment_id: "test".into(),
            user_id: "test-user".into(),
            session_id: "test-session".into(),
        },
        candidates: vec![candidate],
        request_timeout: Duration::from_secs(2),
        maximum_body_bytes: 1024 * 1024,
        budget_limit_microusd: 1_000_000,
        database_url: None,
        receipt_signer: ReceiptSigner::from_seed("api-02-test", [7; 32]).unwrap(),
        context_budget_tokens: 32_768,
        ranking_mode: noerelay_core::RankingMode::Disabled,
        ranker_sidecar_url: None,
    })
    .unwrap();
    app(state)
}

fn request(path: &str, value: Value) -> Request<Body> {
    Request::post(path)
        .header("authorization", format!("Bearer {API_KEY}"))
        .header("content-type", "application/json")
        .body(Body::from(serde_json::to_vec(&value).unwrap()))
        .unwrap()
}

async fn json_body(response: axum::response::Response) -> Value {
    serde_json::from_slice(&response.into_body().collect().await.unwrap().to_bytes()).unwrap()
}

fn basic_request() -> Value {
    json!({"model":"axiovex-agni","messages":[{"role":"user","content":"hello"}]})
}

#[tokio::test]
async fn basic_text_has_chat_completion_shape() {
    let response = application()
        .oneshot(request("/v1/chat/completions", basic_request()))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response.headers()["content-type"], "application/json");
    let body = json_body(response).await;
    assert_eq!(body["object"], "chat.completion");
    assert_eq!(body["choices"][0]["message"]["role"], "assistant");
    assert_eq!(
        body["choices"][0]["message"]["content"],
        "NoeRelay stub response"
    );
}

#[tokio::test]
async fn unknown_field_fails_closed() {
    let mut value = basic_request();
    value["future_field"] = json!(true);
    let response = application()
        .oneshot(request("/v1/chat/completions", value))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert_eq!(
        json_body(response).await["error"]["code"],
        "unsupported_field"
    );
}

#[tokio::test]
async fn unknown_model_returns_not_found() {
    let response = application()
        .oneshot(request(
            "/v1/chat/completions",
            json!({"model":"missing/model","messages":[{"role":"user","content":"hello"}]}),
        ))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    assert_eq!(
        json_body(response).await["error"]["code"],
        "model_not_found"
    );
}

#[tokio::test]
async fn tools_produce_function_call() {
    let response = application()
        .oneshot(request(
            "/v1/chat/completions",
            json!({
                "model":"axiovex-agni",
                "messages":[{"role":"user","content":"weather"}],
                "tools":[{"type":"function","function":{"name":"weather","description":"lookup","parameters":{"type":"object"}}}],
                "tool_choice": "required"
            }),
        ))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = json_body(response).await;
    assert_eq!(body["choices"][0]["finish_reason"], "tool_calls");
    assert_eq!(
        body["choices"][0]["message"]["tool_calls"][0]["function"]["name"],
        "weather"
    );
}

#[tokio::test]
async fn tools_with_auto_choice_returns_text() {
    let response = application()
        .oneshot(request(
            "/v1/chat/completions",
            json!({
                "model":"axiovex-agni",
                "messages":[{"role":"user","content":"hello"}],
                "tools":[{"type":"function","function":{"name":"weather","description":"lookup","parameters":{"type":"object"}}}]
            }),
        ))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = json_body(response).await;
    assert_eq!(body["choices"][0]["finish_reason"], "stop");
    assert!(body["choices"][0]["message"]["content"].as_str().is_some());
}

#[tokio::test]
async fn json_object_response_is_valid_json_text() {
    let response = application()
        .oneshot(request(
            "/v1/chat/completions",
            json!({
                "model":"axiovex-agni",
                "messages":[{"role":"user","content":"json"}],
                "response_format":{"type":"json_object"}
            }),
        ))
        .await
        .unwrap();
    let body = json_body(response).await;
    let text = body["choices"][0]["message"]["content"].as_str().unwrap();
    assert!(serde_json::from_str::<Value>(text).unwrap().is_object());
}

#[tokio::test]
async fn streaming_returns_basic_sse() {
    let mut value = basic_request();
    value["stream"] = json!(true);
    let response = application()
        .oneshot(request("/v1/chat/completions", value))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response.headers()["content-type"], "text/event-stream");
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    let body = String::from_utf8(bytes.to_vec()).unwrap();
    assert!(body.contains("chat.completion.chunk"));
    assert!(body.contains("data: [DONE]"));
}

#[tokio::test]
async fn missing_authentication_returns_unauthorized() {
    let request = Request::post("/v1/chat/completions")
        .header("content-type", "application/json")
        .body(Body::from(serde_json::to_vec(&basic_request()).unwrap()))
        .unwrap();
    let response = application().oneshot(request).await.unwrap();
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn multi_turn_conversation_is_accepted() {
    let response = application()
        .oneshot(request(
            "/v1/chat/completions",
            json!({
                "model":"axiovex-agni",
                "messages":[
                    {"role":"system","content":"be concise"},
                    {"role":"user","content":"first"},
                    {"role":"assistant","content":"answer"},
                    {"role":"user","content":"second"}
                ]
            }),
        ))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
}

#[tokio::test]
async fn vision_image_url_is_accepted() {
    let response = application()
        .oneshot(request(
            "/v1/chat/completions",
            json!({
                "model":"axiovex-agni",
                "messages":[{"role":"user","content":[
                    {"type":"text","text":"describe"},
                    {"type":"image_url","image_url":{"url":"data:image/png;base64,AA==","detail":"low"}}
                ]}]
            }),
        ))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
}

#[tokio::test]
async fn usage_is_nonzero_and_balanced() {
    let response = application()
        .oneshot(request("/v1/chat/completions", basic_request()))
        .await
        .unwrap();
    let body = json_body(response).await;
    let usage = &body["usage"];
    assert!(usage["prompt_tokens"].as_i64().unwrap() > 0);
    assert_eq!(
        usage["total_tokens"].as_i64().unwrap(),
        usage["prompt_tokens"].as_i64().unwrap() + usage["completion_tokens"].as_i64().unwrap()
    );
}

#[tokio::test]
async fn responses_endpoint_uses_responses_shape() {
    let response = application()
        .oneshot(request(
            "/v1/responses",
            json!({"model":"axiovex-agni","input":"hello"}),
        ))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = json_body(response).await;
    assert_eq!(body["object"], "response");
    assert_eq!(body["status"], "completed");
}

#[tokio::test]
async fn missing_messages_is_invalid_request() {
    let response = application()
        .oneshot(request(
            "/v1/chat/completions",
            json!({"model":"axiovex-agni"}),
        ))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert_eq!(
        json_body(response).await["error"]["code"],
        "invalid_request"
    );
}

#[tokio::test]
async fn max_tokens_limits_stub_output() {
    let response = application()
        .oneshot(request(
            "/v1/chat/completions",
            json!({
                "model":"axiovex-agni",
                "messages":[{"role":"user","content":"hello"}],
                "max_tokens": 1
            }),
        ))
        .await
        .unwrap();
    let body = json_body(response).await;
    assert!(
        body["choices"][0]["message"]["content"]
            .as_str()
            .unwrap()
            .chars()
            .count()
            <= 4
    );
}
