//! Frozen OpenAI-compatible wire profiles and their canonical transport IR.
//!
//! This module is intentionally fail-closed: only fields listed by the selected
//! compatibility profile are accepted. Provider-specific or future fields must
//! be added to a versioned profile before they can cross the wire boundary.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use std::collections::{HashMap, HashSet};

pub const PROFILE_VERSION: &str = "2024-02-15";

const CHAT_SUPPORTED_FIELDS: &[&str] = &[
    "model",
    "messages",
    "temperature",
    "max_tokens",
    "max_completion_tokens",
    "top_p",
    "frequency_penalty",
    "presence_penalty",
    "stop",
    "stream",
    "tools",
    "tool_choice",
    "response_format",
    "seed",
    "user",
    "n",
    "logprobs",
    "top_logprobs",
    "stream_options",
    "service_tier",
    "parallel_tool_calls",
    "logit_bias",
    "metadata",
];

const CHAT_UNSUPPORTED_FIELDS: &[&str] = &[
    "audio",
    "modalities",
    "prediction",
    "reasoning_effort",
    "store",
    "web_search_options",
];

const RESPONSES_SUPPORTED_FIELDS: &[&str] = &[
    "model",
    "input",
    "instructions",
    "temperature",
    "max_output_tokens",
    "top_p",
    "tools",
    "tool_choice",
    "text",
    "metadata",
    "stream",
    "user",
    "parallel_tool_calls",
];

const RESPONSES_UNSUPPORTED_FIELDS: &[&str] = &[
    "background",
    "conversation",
    "include",
    "max_tool_calls",
    "previous_response_id",
    "prompt",
    "reasoning",
    "safety_identifier",
    "service_tier",
    "store",
    "truncation",
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum CompatibilityProfile {
    ChatCompletionsV1,
    ResponsesV1,
}

impl CompatibilityProfile {
    pub fn support(self) -> ProfileSupport {
        let (supported, unsupported) = match self {
            Self::ChatCompletionsV1 => (CHAT_SUPPORTED_FIELDS, CHAT_UNSUPPORTED_FIELDS),
            Self::ResponsesV1 => (RESPONSES_SUPPORTED_FIELDS, RESPONSES_UNSUPPORTED_FIELDS),
        };
        ProfileSupport {
            profile: self,
            supported_fields: supported.iter().map(|field| (*field).to_owned()).collect(),
            unsupported_fields: unsupported
                .iter()
                .map(|field| (*field).to_owned())
                .collect(),
            version: PROFILE_VERSION.to_owned(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ProfileSupport {
    pub profile: CompatibilityProfile,
    pub supported_fields: Vec<String>,
    pub unsupported_fields: Vec<String>,
    pub version: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CanonicalRequest {
    pub messages: Vec<CanonicalMessage>,
    pub model: String,
    pub temperature: Option<f32>,
    pub max_tokens: Option<i32>,
    pub max_completion_tokens: Option<i32>,
    pub top_p: Option<f32>,
    pub frequency_penalty: Option<f32>,
    pub presence_penalty: Option<f32>,
    pub stop: Option<Vec<String>>,
    pub stream: bool,
    pub tools: Option<Vec<CanonicalTool>>,
    pub tool_choice: Option<CanonicalToolChoice>,
    pub response_format: Option<CanonicalResponseFormat>,
    pub seed: Option<i64>,
    pub user: Option<String>,
    pub n: Option<i32>,
    pub logprobs: Option<bool>,
    pub top_logprobs: Option<i32>,
    pub stream_options: Option<CanonicalStreamOptions>,
    pub service_tier: Option<String>,
    pub parallel_tool_calls: Option<bool>,
    pub logit_bias: Option<HashMap<String, f32>>,
    pub metadata: Option<HashMap<String, String>>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CanonicalMessage {
    pub role: CanonicalRole,
    pub content: Option<CanonicalContent>,
    pub tool_calls: Option<Vec<CanonicalToolCall>>,
    pub tool_call_id: Option<String>,
    pub name: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "lowercase")]
pub enum CanonicalRole {
    System,
    User,
    Assistant,
    Tool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(untagged)]
pub enum CanonicalContent {
    Text(String),
    Parts(Vec<CanonicalContentPart>),
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "type")]
pub enum CanonicalContentPart {
    #[serde(rename = "text")]
    Text { text: String },
    #[serde(rename = "image_url")]
    ImageUrl { image_url: CanonicalImageUrl },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CanonicalImageUrl {
    pub url: String,
    pub detail: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CanonicalTool {
    pub id: String,
    pub function: CanonicalFunction,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CanonicalFunction {
    pub name: String,
    pub description: Option<String>,
    pub parameters: Option<Value>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(untagged)]
pub enum CanonicalToolChoice {
    Auto,
    None,
    Required,
    Specific { function: CanonicalFunctionName },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CanonicalFunctionName {
    pub name: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "type")]
pub enum CanonicalResponseFormat {
    #[serde(rename = "text")]
    Text,
    #[serde(rename = "json_object")]
    JsonObject,
    #[serde(rename = "json_schema")]
    JsonSchema { json_schema: CanonicalJsonSchema },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CanonicalJsonSchema {
    pub name: String,
    pub schema: Option<Value>,
    pub strict: Option<bool>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CanonicalStreamOptions {
    pub include_usage: Option<bool>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CanonicalToolCall {
    pub id: String,
    pub function: CanonicalFunctionCall,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CanonicalFunctionCall {
    pub name: String,
    pub arguments: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CanonicalResponse {
    pub id: String,
    pub model: String,
    pub choices: Vec<CanonicalChoice>,
    pub usage: Option<CanonicalUsage>,
    pub system_fingerprint: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CanonicalChoice {
    pub index: i32,
    pub message: Option<CanonicalMessage>,
    pub delta: Option<CanonicalDelta>,
    pub finish_reason: Option<String>,
    pub logprobs: Option<Value>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CanonicalDelta {
    pub role: Option<CanonicalRole>,
    pub content: Option<String>,
    pub tool_calls: Option<Vec<CanonicalToolCallDelta>>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CanonicalToolCallDelta {
    pub index: i32,
    pub id: Option<String>,
    pub function: Option<CanonicalFunctionDelta>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CanonicalFunctionDelta {
    pub name: Option<String>,
    pub arguments: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CanonicalUsage {
    pub prompt_tokens: i32,
    pub completion_tokens: i32,
    pub total_tokens: i32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ApiError {
    pub error: ApiErrorBody,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ApiErrorBody {
    pub message: String,
    pub r#type: String,
    pub param: Option<String>,
    pub code: Option<String>,
}

impl ApiError {
    pub fn unsupported_field(field: &str, profile: CompatibilityProfile) -> Self {
        Self {
            error: ApiErrorBody {
                message: format!(
                    "Unsupported field '{field}' for compatibility profile '{}'.",
                    profile_name(profile)
                ),
                r#type: "unsupported_field_error".to_owned(),
                param: Some(field.to_owned()),
                code: Some("unsupported_field".to_owned()),
            },
        }
    }

    pub fn invalid_request(message: &str, param: Option<&str>) -> Self {
        Self {
            error: ApiErrorBody {
                message: message.to_owned(),
                r#type: "invalid_request_error".to_owned(),
                param: param.map(str::to_owned),
                code: Some("invalid_request".to_owned()),
            },
        }
    }

    pub fn model_not_found(model: &str) -> Self {
        Self {
            error: ApiErrorBody {
                message: format!("The model '{model}' does not exist or is not available."),
                r#type: "invalid_request_error".to_owned(),
                param: Some("model".to_owned()),
                code: Some("model_not_found".to_owned()),
            },
        }
    }

    pub fn rate_limit_exceeded() -> Self {
        Self {
            error: ApiErrorBody {
                message: "Rate limit exceeded. Please retry later.".to_owned(),
                r#type: "rate_limit_error".to_owned(),
                param: None,
                code: Some("rate_limit_exceeded".to_owned()),
            },
        }
    }
}

fn profile_name(profile: CompatibilityProfile) -> &'static str {
    match profile {
        CompatibilityProfile::ChatCompletionsV1 => "chat_completions_v1",
        CompatibilityProfile::ResponsesV1 => "responses_v1",
    }
}

#[derive(Debug, Clone)]
pub struct FieldValidator {
    profile: CompatibilityProfile,
    supported_fields: HashSet<String>,
}

impl FieldValidator {
    pub fn new(profile: CompatibilityProfile) -> Self {
        Self {
            profile,
            supported_fields: profile.support().supported_fields.into_iter().collect(),
        }
    }

    pub fn validate(&self, request_json: &Value) -> Result<(), Vec<ApiError>> {
        let object = request_json.as_object().ok_or_else(|| {
            vec![ApiError::invalid_request(
                "Request body must be a JSON object.",
                None,
            )]
        })?;
        let mut fields: Vec<&String> = object.keys().collect();
        fields.sort_unstable();
        let errors: Vec<ApiError> = fields
            .into_iter()
            .filter(|field| !self.is_supported(field))
            .map(|field| ApiError::unsupported_field(field, self.profile))
            .collect();
        if errors.is_empty() {
            Ok(())
        } else {
            Err(errors)
        }
    }

    pub fn is_supported(&self, field: &str) -> bool {
        self.supported_fields.contains(field)
    }
}

pub struct ChatCompletionsConverter;

impl ChatCompletionsConverter {
    pub fn parse_request(json: &Value) -> Result<CanonicalRequest, Vec<ApiError>> {
        FieldValidator::new(CompatibilityProfile::ChatCompletionsV1).validate(json)?;
        parse_chat_request(json)
    }

    pub fn format_response(response: &CanonicalResponse) -> Value {
        let choices: Vec<Value> = response
            .choices
            .iter()
            .map(|choice| {
                json!({
                    "index": choice.index,
                    "message": choice.message.as_ref().map(format_chat_message),
                    "finish_reason": choice.finish_reason,
                    "logprobs": choice.logprobs,
                })
            })
            .collect();
        json!({
            "id": response.id,
            "object": "chat.completion",
            "model": response.model,
            "choices": choices,
            "usage": response.usage,
            "system_fingerprint": response.system_fingerprint,
        })
    }

    pub fn format_stream_chunk(delta: &CanonicalDelta, usage: Option<&CanonicalUsage>) -> Value {
        json!({
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": delta, "finish_reason": Value::Null}],
            "usage": usage,
        })
    }
}

pub struct ResponsesConverter;

impl ResponsesConverter {
    pub fn parse_request(json: &Value) -> Result<CanonicalRequest, Vec<ApiError>> {
        FieldValidator::new(CompatibilityProfile::ResponsesV1).validate(json)?;
        parse_responses_request(json)
    }

    pub fn format_response(response: &CanonicalResponse) -> Value {
        let output: Vec<Value> = response
            .choices
            .iter()
            .filter_map(|choice| choice.message.as_ref())
            .enumerate()
            .map(|(index, message)| {
                json!({
                    "id": format!("msg_{index}"),
                    "type": "message",
                    "role": message.role,
                    "content": response_content_items(message.content.as_ref()),
                    "status": "completed",
                })
            })
            .collect();
        let usage = response.usage.as_ref().map(|usage| {
            json!({
                "input_tokens": usage.prompt_tokens,
                "output_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            })
        });
        json!({
            "id": response.id,
            "object": "response",
            "status": "completed",
            "model": response.model,
            "output": output,
            "usage": usage,
        })
    }
}

fn parse_chat_request(value: &Value) -> Result<CanonicalRequest, Vec<ApiError>> {
    let object = value.as_object().expect("field validator requires object");
    let model = required_non_empty_string(object, "model")?;
    let messages_value = object.get("messages").ok_or_else(|| {
        vec![ApiError::invalid_request(
            "Missing required field: messages.",
            Some("messages"),
        )]
    })?;
    let messages = parse_messages(messages_value)?;
    if messages.is_empty() {
        return Err(vec![ApiError::invalid_request(
            "messages must contain at least one item.",
            Some("messages"),
        )]);
    }
    build_request(object, model, messages, "max_tokens", "response_format")
}

fn parse_responses_request(value: &Value) -> Result<CanonicalRequest, Vec<ApiError>> {
    let object = value.as_object().expect("field validator requires object");
    let model = required_non_empty_string(object, "model")?;
    let input = object.get("input").ok_or_else(|| {
        vec![ApiError::invalid_request(
            "Missing required field: input.",
            Some("input"),
        )]
    })?;
    let mut messages = match input {
        Value::String(text) => vec![message(
            CanonicalRole::User,
            CanonicalContent::Text(text.clone()),
        )],
        Value::Array(values) => values
            .iter()
            .map(parse_response_message)
            .collect::<Result<_, _>>()?,
        _ => {
            return Err(vec![ApiError::invalid_request(
                "input must be a string or an array of messages.",
                Some("input"),
            )]);
        }
    };
    if let Some(instructions) = optional_string(object, "instructions")? {
        messages.insert(
            0,
            message(CanonicalRole::System, CanonicalContent::Text(instructions)),
        );
    }
    build_request(object, model, messages, "max_output_tokens", "text")
}

fn parse_response_message(value: &Value) -> Result<CanonicalMessage, Vec<ApiError>> {
    let object = value.as_object().ok_or_else(|| {
        vec![ApiError::invalid_request(
            "Each input item must be a message object.",
            Some("input"),
        )]
    })?;
    let role: CanonicalRole = object
        .get("role")
        .ok_or_else(|| {
            vec![ApiError::invalid_request(
                "Input role is required.",
                Some("input.role"),
            )]
        })
        .and_then(|value| decode(value, "input.role"))?;
    let content_value = object.get("content").ok_or_else(|| {
        vec![ApiError::invalid_request(
            "Input message content is required.",
            Some("input.content"),
        )]
    })?;
    let content = match content_value {
        Value::String(text) => CanonicalContent::Text(text.clone()),
        Value::Array(parts) => CanonicalContent::Parts(
            parts
                .iter()
                .map(|part| {
                    let part = part.as_object().ok_or_else(|| {
                        vec![ApiError::invalid_request(
                            "Input content part must be an object.",
                            Some("input.content"),
                        )]
                    })?;
                    match part.get("type").and_then(Value::as_str) {
                        Some("input_text") => Ok(CanonicalContentPart::Text {
                            text: part
                                .get("text")
                                .and_then(Value::as_str)
                                .ok_or_else(|| {
                                    vec![ApiError::invalid_request(
                                        "input_text requires text.",
                                        Some("input.content.text"),
                                    )]
                                })?
                                .to_owned(),
                        }),
                        Some("input_image") => Ok(CanonicalContentPart::ImageUrl {
                            image_url: CanonicalImageUrl {
                                url: part
                                    .get("image_url")
                                    .and_then(Value::as_str)
                                    .ok_or_else(|| {
                                        vec![ApiError::invalid_request(
                                            "input_image requires image_url.",
                                            Some("input.content.image_url"),
                                        )]
                                    })?
                                    .to_owned(),
                                detail: optional_string(part, "detail")?,
                            },
                        }),
                        _ => Err(vec![ApiError::invalid_request(
                            "Only input_text and input_image content parts are supported.",
                            Some("input.content.type"),
                        )]),
                    }
                })
                .collect::<Result<_, _>>()?,
        ),
        _ => {
            return Err(vec![ApiError::invalid_request(
                "Input content must be a string or array.",
                Some("input.content"),
            )]);
        }
    };
    Ok(message(role, content))
}

fn build_request(
    object: &Map<String, Value>,
    model: String,
    messages: Vec<CanonicalMessage>,
    max_tokens_field: &str,
    response_format_field: &str,
) -> Result<CanonicalRequest, Vec<ApiError>> {
    let stop = match object.get("stop") {
        None | Some(Value::Null) => None,
        Some(Value::String(value)) => Some(vec![value.clone()]),
        Some(value) => Some(decode(value, "stop")?),
    };
    let response_format = match object.get(response_format_field) {
        None | Some(Value::Null) => None,
        Some(value) if response_format_field == "text" => {
            let format = value.get("format").unwrap_or(value);
            Some(decode(format, response_format_field)?)
        }
        Some(value) => Some(decode(value, response_format_field)?),
    };
    Ok(CanonicalRequest {
        messages,
        model,
        temperature: optional(object, "temperature")?,
        max_tokens: optional(object, max_tokens_field)?,
        max_completion_tokens: optional(object, "max_completion_tokens")?,
        top_p: optional(object, "top_p")?,
        frequency_penalty: optional(object, "frequency_penalty")?,
        presence_penalty: optional(object, "presence_penalty")?,
        stop,
        stream: optional(object, "stream")?.unwrap_or(false),
        tools: parse_tools(object.get("tools"))?,
        tool_choice: parse_tool_choice(object.get("tool_choice"))?,
        response_format,
        seed: optional(object, "seed")?,
        user: optional_string(object, "user")?,
        n: optional(object, "n")?,
        logprobs: optional(object, "logprobs")?,
        top_logprobs: optional(object, "top_logprobs")?,
        stream_options: optional(object, "stream_options")?,
        service_tier: optional_string(object, "service_tier")?,
        parallel_tool_calls: optional(object, "parallel_tool_calls")?,
        logit_bias: optional(object, "logit_bias")?,
        metadata: optional(object, "metadata")?,
    })
}

fn required_non_empty_string(
    object: &Map<String, Value>,
    field: &str,
) -> Result<String, Vec<ApiError>> {
    match object.get(field).and_then(Value::as_str) {
        Some(value) if !value.trim().is_empty() => Ok(value.to_owned()),
        Some(_) => Err(vec![ApiError::invalid_request(
            "model must not be empty.",
            Some(field),
        )]),
        None => Err(vec![ApiError::invalid_request(
            &format!("Missing or invalid required field: {field}."),
            Some(field),
        )]),
    }
}

fn optional<T: serde::de::DeserializeOwned>(
    object: &Map<String, Value>,
    field: &str,
) -> Result<Option<T>, Vec<ApiError>> {
    match object.get(field) {
        None | Some(Value::Null) => Ok(None),
        Some(value) => decode(value, field).map(Some),
    }
}

fn optional_string(
    object: &Map<String, Value>,
    field: &str,
) -> Result<Option<String>, Vec<ApiError>> {
    optional(object, field)
}

fn decode<T: serde::de::DeserializeOwned>(value: &Value, field: &str) -> Result<T, Vec<ApiError>> {
    serde_json::from_value(value.clone()).map_err(|error| {
        vec![ApiError::invalid_request(
            &format!("Invalid value for '{field}': {error}"),
            Some(field),
        )]
    })
}

fn parse_messages(value: &Value) -> Result<Vec<CanonicalMessage>, Vec<ApiError>> {
    let values = value.as_array().ok_or_else(|| {
        vec![ApiError::invalid_request(
            "messages must be an array.",
            Some("messages"),
        )]
    })?;
    values.iter().map(parse_message).collect()
}

fn parse_message(value: &Value) -> Result<CanonicalMessage, Vec<ApiError>> {
    let object = value.as_object().ok_or_else(|| {
        vec![ApiError::invalid_request(
            "Each message must be an object.",
            Some("messages"),
        )]
    })?;
    let allowed: HashSet<&str> = ["role", "content", "tool_calls", "tool_call_id", "name"]
        .into_iter()
        .collect();
    if let Some(field) = object
        .keys()
        .find(|field| !allowed.contains(field.as_str()))
    {
        return Err(vec![ApiError::unsupported_field(
            &format!("messages.{field}"),
            CompatibilityProfile::ChatCompletionsV1,
        )]);
    }
    let role: CanonicalRole = object
        .get("role")
        .ok_or_else(|| {
            vec![ApiError::invalid_request(
                "Message role is required.",
                Some("messages.role"),
            )]
        })
        .and_then(|value| decode(value, "messages.role"))?;
    let content = match object.get("content") {
        None | Some(Value::Null) => None,
        Some(value) => Some(decode(value, "messages.content")?),
    };
    let tool_calls = parse_tool_calls(object.get("tool_calls"))?;
    if content.is_none() && tool_calls.is_none() {
        return Err(vec![ApiError::invalid_request(
            "A message requires content or tool_calls.",
            Some("messages.content"),
        )]);
    }
    Ok(CanonicalMessage {
        role,
        content,
        tool_calls,
        tool_call_id: optional_string(object, "tool_call_id")?,
        name: optional_string(object, "name")?,
    })
}

fn parse_tools(value: Option<&Value>) -> Result<Option<Vec<CanonicalTool>>, Vec<ApiError>> {
    let Some(value) = value.filter(|value| !value.is_null()) else {
        return Ok(None);
    };
    let tools = value.as_array().ok_or_else(|| {
        vec![ApiError::invalid_request(
            "tools must be an array.",
            Some("tools"),
        )]
    })?;
    tools
        .iter()
        .enumerate()
        .map(|(index, value)| {
            let object = value.as_object().ok_or_else(|| {
                vec![ApiError::invalid_request(
                    "Each tool must be an object.",
                    Some("tools"),
                )]
            })?;
            if object.get("type").and_then(Value::as_str) != Some("function") {
                return Err(vec![ApiError::invalid_request(
                    "Only tools of type 'function' are supported.",
                    Some("tools.type"),
                )]);
            }
            let function: CanonicalFunction = if let Some(value) = object.get("function") {
                decode(value, "tools.function")?
            } else {
                CanonicalFunction {
                    name: object
                        .get("name")
                        .and_then(Value::as_str)
                        .ok_or_else(|| {
                            vec![ApiError::invalid_request(
                                "Tool function name is required.",
                                Some("tools.name"),
                            )]
                        })?
                        .to_owned(),
                    description: optional_string(object, "description")?,
                    parameters: object.get("parameters").cloned(),
                }
            };
            Ok(CanonicalTool {
                id: format!("function:{index}:{}", function.name),
                function,
            })
        })
        .collect::<Result<Vec<_>, _>>()
        .map(Some)
}

fn parse_tool_choice(value: Option<&Value>) -> Result<Option<CanonicalToolChoice>, Vec<ApiError>> {
    let Some(value) = value.filter(|value| !value.is_null()) else {
        return Ok(None);
    };
    if let Some(choice) = value.as_str() {
        return match choice {
            "auto" => Ok(Some(CanonicalToolChoice::Auto)),
            "none" => Ok(Some(CanonicalToolChoice::None)),
            "required" => Ok(Some(CanonicalToolChoice::Required)),
            _ => Err(vec![ApiError::invalid_request(
                "tool_choice must be 'auto', 'none', 'required', or a function selector.",
                Some("tool_choice"),
            )]),
        };
    }
    let object = value.as_object().ok_or_else(|| {
        vec![ApiError::invalid_request(
            "Invalid tool_choice.",
            Some("tool_choice"),
        )]
    })?;
    if object.get("type").and_then(Value::as_str) != Some("function") {
        return Err(vec![ApiError::invalid_request(
            "Specific tool_choice must select a function.",
            Some("tool_choice.type"),
        )]);
    }
    let function = object
        .get("function")
        .ok_or_else(|| {
            vec![ApiError::invalid_request(
                "Function selector is required.",
                Some("tool_choice.function"),
            )]
        })
        .and_then(|value| decode(value, "tool_choice.function"))?;
    Ok(Some(CanonicalToolChoice::Specific { function }))
}

fn parse_tool_calls(
    value: Option<&Value>,
) -> Result<Option<Vec<CanonicalToolCall>>, Vec<ApiError>> {
    let Some(value) = value.filter(|value| !value.is_null()) else {
        return Ok(None);
    };
    let calls = value.as_array().ok_or_else(|| {
        vec![ApiError::invalid_request(
            "tool_calls must be an array.",
            Some("messages.tool_calls"),
        )]
    })?;
    calls
        .iter()
        .map(|value| {
            let object = value.as_object().ok_or_else(|| {
                vec![ApiError::invalid_request(
                    "Tool call must be an object.",
                    Some("messages.tool_calls"),
                )]
            })?;
            if object.get("type").and_then(Value::as_str) != Some("function") {
                return Err(vec![ApiError::invalid_request(
                    "Only function tool calls are supported.",
                    Some("messages.tool_calls.type"),
                )]);
            }
            Ok(CanonicalToolCall {
                id: object
                    .get("id")
                    .and_then(Value::as_str)
                    .ok_or_else(|| {
                        vec![ApiError::invalid_request(
                            "Tool call id is required.",
                            Some("messages.tool_calls.id"),
                        )]
                    })?
                    .to_owned(),
                function: object
                    .get("function")
                    .ok_or_else(|| {
                        vec![ApiError::invalid_request(
                            "Tool call function is required.",
                            Some("messages.tool_calls.function"),
                        )]
                    })
                    .and_then(|value| decode(value, "messages.tool_calls.function"))?,
            })
        })
        .collect::<Result<Vec<_>, _>>()
        .map(Some)
}

fn message(role: CanonicalRole, content: CanonicalContent) -> CanonicalMessage {
    CanonicalMessage {
        role,
        content: Some(content),
        tool_calls: None,
        tool_call_id: None,
        name: None,
    }
}

fn format_chat_message(message: &CanonicalMessage) -> Value {
    let tool_calls = message.tool_calls.as_ref().map(|calls| {
        calls
            .iter()
            .map(|call| json!({"id": call.id, "type": "function", "function": call.function}))
            .collect::<Vec<_>>()
    });
    json!({
        "role": message.role,
        "content": message.content,
        "tool_calls": tool_calls,
        "tool_call_id": message.tool_call_id,
        "name": message.name,
    })
}

fn response_content_items(content: Option<&CanonicalContent>) -> Vec<Value> {
    match content {
        Some(CanonicalContent::Text(text)) => vec![json!({"type": "output_text", "text": text})],
        Some(CanonicalContent::Parts(parts)) => parts
            .iter()
            .filter_map(|part| match part {
                CanonicalContentPart::Text { text } => {
                    Some(json!({"type": "output_text", "text": text}))
                }
                CanonicalContentPart::ImageUrl { .. } => None,
            })
            .collect(),
        None => Vec::new(),
    }
}
