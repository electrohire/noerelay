use noerelay_core::wire::*;
use serde_json::{Value, json};

fn chat_request() -> Value {
    json!({"model":"test/model","messages":[{"role":"user","content":"hello"}]})
}

fn response() -> CanonicalResponse {
    CanonicalResponse {
        id: "resp_1".into(),
        model: "test/model".into(),
        choices: vec![CanonicalChoice {
            index: 0,
            message: Some(CanonicalMessage {
                role: CanonicalRole::Assistant,
                content: Some(CanonicalContent::Text("hello".into())),
                tool_calls: None,
                tool_call_id: None,
                name: None,
                reasoning_content: None,
            }),
            delta: None,
            finish_reason: Some("stop".into()),
            logprobs: None,
        }],
        usage: Some(CanonicalUsage {
            prompt_tokens: 1,
            completion_tokens: 1,
            total_tokens: 2,
        }),
        system_fingerprint: Some("fp_1".into()),
    }
}

#[test]
fn canonical_request_serialization_round_trip() {
    let request = ChatCompletionsConverter::parse_request(&chat_request()).unwrap();
    let encoded = serde_json::to_value(&request).unwrap();
    assert_eq!(
        serde_json::from_value::<CanonicalRequest>(encoded).unwrap(),
        request
    );
}

#[test]
fn canonical_response_serialization_round_trip() {
    let encoded = serde_json::to_value(response()).unwrap();
    assert_eq!(
        serde_json::from_value::<CanonicalResponse>(encoded).unwrap(),
        response()
    );
}

#[test]
fn unsupported_error_is_openai_shaped() {
    let value = serde_json::to_value(ApiError::unsupported_field(
        "future_field",
        CompatibilityProfile::ChatCompletionsV1,
    ))
    .unwrap();
    assert_eq!(value["error"]["type"], "unsupported_field_error");
    assert_eq!(value["error"]["param"], "future_field");
}

#[test]
fn invalid_request_error_is_openai_shaped() {
    let error = ApiError::invalid_request("bad", Some("model"));
    assert_eq!(error.error.r#type, "invalid_request_error");
    assert_eq!(error.error.param.as_deref(), Some("model"));
}

#[test]
fn model_not_found_has_stable_code() {
    assert_eq!(
        ApiError::model_not_found("x").error.code.as_deref(),
        Some("model_not_found")
    );
}

#[test]
fn rate_limit_has_stable_shape() {
    let error = ApiError::rate_limit_exceeded();
    assert_eq!(error.error.r#type, "rate_limit_error");
    assert_eq!(error.error.code.as_deref(), Some("rate_limit_exceeded"));
}

#[test]
fn chat_validator_accepts_supported_fields() {
    assert!(
        FieldValidator::new(CompatibilityProfile::ChatCompletionsV1)
            .validate(&chat_request())
            .is_ok()
    );
}

#[test]
fn chat_validator_rejects_unknown_field() {
    let mut value = chat_request();
    value["future"] = json!(true);
    let errors = FieldValidator::new(CompatibilityProfile::ChatCompletionsV1)
        .validate(&value)
        .unwrap_err();
    assert_eq!(errors[0].error.param.as_deref(), Some("future"));
}

#[test]
fn validator_rejects_non_object() {
    assert!(
        FieldValidator::new(CompatibilityProfile::ResponsesV1)
            .validate(&json!([]))
            .is_err()
    );
}

#[test]
fn profile_support_version_is_frozen() {
    assert_eq!(
        CompatibilityProfile::ChatCompletionsV1.support().version,
        PROFILE_VERSION
    );
}

#[test]
fn chat_parser_accepts_basic_request() {
    let request = ChatCompletionsConverter::parse_request(&chat_request()).unwrap();
    assert_eq!(request.model, "test/model");
    assert_eq!(request.messages.len(), 1);
}

#[test]
fn chat_parser_rejects_unsupported_field() {
    let value = json!({"model":"x","messages":[],"store":true});
    assert!(ChatCompletionsConverter::parse_request(&value).is_err());
}

#[test]
fn chat_parser_rejects_empty_model() {
    let value = json!({"model":"","messages":[{"role":"user","content":"x"}]});
    assert_eq!(
        ChatCompletionsConverter::parse_request(&value).unwrap_err()[0]
            .error
            .param
            .as_deref(),
        Some("model")
    );
}

#[test]
fn chat_parser_rejects_missing_messages() {
    assert!(ChatCompletionsConverter::parse_request(&json!({"model":"x"})).is_err());
}

#[test]
fn chat_parser_normalizes_string_stop() {
    let mut value = chat_request();
    value["stop"] = json!("END");
    assert_eq!(
        ChatCompletionsConverter::parse_request(&value)
            .unwrap()
            .stop,
        Some(vec!["END".into()])
    );
}

#[test]
fn chat_parser_accepts_vision_parts() {
    let value = json!({"model":"x","messages":[{"role":"user","content":[{"type":"image_url","image_url":{"url":"https://example.invalid/a.png","detail":"low"}}]}]});
    assert!(ChatCompletionsConverter::parse_request(&value).is_ok());
}

#[test]
fn chat_parser_accepts_function_tool() {
    let value = json!({"model":"x","messages":[{"role":"user","content":"x"}],"tools":[{"type":"function","function":{"name":"f","parameters":{"type":"object"}}}]});
    assert_eq!(
        ChatCompletionsConverter::parse_request(&value)
            .unwrap()
            .tools
            .unwrap()[0]
            .function
            .name,
        "f"
    );
}

#[test]
fn responses_parser_accepts_string_input() {
    let value = json!({"model":"x","input":"hello"});
    assert_eq!(
        ResponsesConverter::parse_request(&value)
            .unwrap()
            .messages
            .len(),
        1
    );
}

#[test]
fn responses_parser_prepends_instructions() {
    let value = json!({"model":"x","input":"hello","instructions":"system"});
    let request = ResponsesConverter::parse_request(&value).unwrap();
    assert_eq!(request.messages.len(), 2);
    assert_eq!(request.messages[0].role, CanonicalRole::System);
}

#[test]
fn responses_parser_accepts_input_text_parts() {
    let value = json!({"model":"x","input":[{"role":"user","content":[{"type":"input_text","text":"hello"}]}]});
    assert!(ResponsesConverter::parse_request(&value).is_ok());
}

#[test]
fn chat_response_projection_has_expected_object() {
    let value = ChatCompletionsConverter::format_response(&response());
    assert_eq!(value["object"], "chat.completion");
    assert_eq!(value["choices"][0]["message"]["content"], "hello");
}

#[test]
fn responses_projection_maps_usage_names() {
    let value = ResponsesConverter::format_response(&response());
    assert_eq!(value["object"], "response");
    assert_eq!(value["usage"]["input_tokens"], 1);
}

#[test]
fn stream_chunk_projection_includes_usage() {
    let delta = CanonicalDelta {
        role: Some(CanonicalRole::Assistant),
        content: Some("a".into()),
        tool_calls: None,
    };
    let usage = CanonicalUsage {
        prompt_tokens: 1,
        completion_tokens: 1,
        total_tokens: 2,
    };
    let value = ChatCompletionsConverter::format_stream_chunk(&delta, Some(&usage));
    assert_eq!(value["object"], "chat.completion.chunk");
    assert_eq!(value["usage"]["total_tokens"], 2);
}

#[test]
fn parse_and_project_round_trip_preserves_text() {
    let request = ChatCompletionsConverter::parse_request(&chat_request()).unwrap();
    let CanonicalContent::Text(text) = request.messages[0].content.as_ref().unwrap() else {
        panic!("expected text")
    };
    assert_eq!(text, "hello");
    assert_eq!(
        ChatCompletionsConverter::format_response(&response())["choices"][0]["message"]["content"],
        "hello"
    );
}

#[test]
fn wire_types_have_json_schema() {
    let schema = schemars::schema_for!(CanonicalRequest);
    assert!(schema.schema.object.is_some());
}
