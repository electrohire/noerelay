//! Deterministic local provider used by the local/test compatibility profile.

use noerelay_core::wire::{
    CanonicalChoice, CanonicalContent, CanonicalFunctionCall, CanonicalMessage, CanonicalRequest,
    CanonicalResponse, CanonicalResponseFormat, CanonicalRole, CanonicalToolCall,
    CanonicalToolChoice, CanonicalUsage,
};
use serde_json::json;

#[derive(Debug, Clone, Default)]
pub struct StubProvider;

impl StubProvider {
    pub fn complete(&self, request: &CanonicalRequest) -> CanonicalResponse {
        let prompt_tokens = estimate_request_tokens(request);
        let tool = selected_tool(request);
        let (content, tool_calls, finish_reason) = if let Some(tool) = tool {
            (
                None,
                Some(vec![CanonicalToolCall {
                    id: "call_stub_0001".to_owned(),
                    function: CanonicalFunctionCall {
                        name: tool.function.name.clone(),
                        arguments: "{}".to_owned(),
                    },
                }]),
                "tool_calls".to_owned(),
            )
        } else {
            let text = match request.response_format {
                Some(CanonicalResponseFormat::JsonObject)
                | Some(CanonicalResponseFormat::JsonSchema { .. }) => {
                    json!({"message": "NoeRelay stub response"}).to_string()
                }
                _ => "NoeRelay stub response".to_owned(),
            };
            (
                Some(CanonicalContent::Text(limit_output(
                    &text,
                    request.max_completion_tokens.or(request.max_tokens),
                ))),
                None,
                "stop".to_owned(),
            )
        };
        let completion_tokens = content.as_ref().map(estimate_content_tokens).unwrap_or(8);

        CanonicalResponse {
            id: "chatcmpl-noerelay-stub".to_owned(),
            model: request.model.clone(),
            choices: vec![CanonicalChoice {
                index: 0,
                message: Some(CanonicalMessage {
                    role: CanonicalRole::Assistant,
                    content,
                    tool_calls,
                    tool_call_id: None,
                    name: None,
                    reasoning_content: None,
                }),
                delta: None,
                finish_reason: Some(finish_reason),
                logprobs: None,
            }],
            usage: Some(CanonicalUsage {
                prompt_tokens,
                completion_tokens,
                total_tokens: prompt_tokens.saturating_add(completion_tokens),
            }),
            system_fingerprint: Some("noerelay-stub-v1".to_owned()),
        }
    }
}

fn selected_tool(request: &CanonicalRequest) -> Option<&noerelay_core::wire::CanonicalTool> {
    let tools = request.tools.as_deref()?;
    match request.tool_choice.as_ref() {
        Some(CanonicalToolChoice::None) | None => None,
        Some(CanonicalToolChoice::Auto) => None,
        Some(CanonicalToolChoice::Specific { function }) => tools
            .iter()
            .find(|tool| tool.function.name == function.name),
        Some(CanonicalToolChoice::Required) => tools.first(),
    }
}

fn limit_output(text: &str, maximum: Option<i32>) -> String {
    let Some(maximum) = maximum.and_then(|value| usize::try_from(value).ok()) else {
        return text.to_owned();
    };
    if maximum == 0 {
        return String::new();
    }
    let character_limit = maximum.saturating_mul(4);
    text.chars().take(character_limit).collect()
}

fn estimate_request_tokens(request: &CanonicalRequest) -> i32 {
    request
        .messages
        .iter()
        .filter_map(|message| message.content.as_ref())
        .map(estimate_content_tokens)
        .fold(0_i32, i32::saturating_add)
}

fn estimate_content_tokens(content: &CanonicalContent) -> i32 {
    let characters = match content {
        CanonicalContent::Text(text) => text.chars().count(),
        CanonicalContent::Parts(parts) => parts
            .iter()
            .map(|part| match part {
                noerelay_core::wire::CanonicalContentPart::Text { text } => text.chars().count(),
                noerelay_core::wire::CanonicalContentPart::ImageUrl { .. } => 85,
            })
            .sum(),
    };
    i32::try_from(characters.div_ceil(4)).unwrap_or(i32::MAX)
}
