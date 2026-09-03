#[cfg(test)]
mod tests {
    use noerelay_core::wire::*;
    use serde_json::{Value, json};

    fn response(tool_calls: Option<Vec<CanonicalToolCall>>) -> CanonicalResponse {
        CanonicalResponse {
            id: "test-id".into(),
            model: "stub".into(),
            choices: vec![CanonicalChoice {
                index: 0,
                message: Some(CanonicalMessage {
                    role: CanonicalRole::Assistant,
                    content: Some(CanonicalContent::Text("Hello".into())),
                    tool_calls,
                    tool_call_id: None,
                    name: None,
                    reasoning_content: None,
                }),
                delta: None,
                finish_reason: Some("stop".into()),
                logprobs: None,
            }],
            usage: None,
            system_fingerprint: None,
        }
    }

    #[test]
    fn parse_string_input() {
        let json = json!({"model": "stub", "input": "Hello"});
        let req = ResponsesConverter::parse_request(&json).unwrap();
        assert_eq!(req.messages.len(), 1);
        assert_eq!(req.messages[0].role, CanonicalRole::User);
        assert_eq!(
            req.messages[0].content,
            Some(CanonicalContent::Text("Hello".into()))
        );
    }

    #[test]
    fn parse_instructions_prepended() {
        let json = json!({"model": "stub", "input": "Hello", "instructions": "Be brief"});
        let req = ResponsesConverter::parse_request(&json).unwrap();
        assert_eq!(req.messages.len(), 2);
        assert_eq!(req.messages[0].role, CanonicalRole::System);
        assert_eq!(req.messages[1].role, CanonicalRole::User);
    }

    #[test]
    fn parse_array_input() {
        let json = json!({
            "model": "stub",
            "input": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": [{"type": "input_text", "text": "Hi"}]}
            ]
        });
        let req = ResponsesConverter::parse_request(&json).unwrap();
        assert_eq!(req.messages.len(), 2);
        assert_eq!(req.messages[1].role, CanonicalRole::Assistant);
    }

    #[test]
    fn parse_max_output_tokens() {
        let json = json!({"model": "stub", "input": "Hello", "max_output_tokens": 128});
        let req = ResponsesConverter::parse_request(&json).unwrap();
        assert_eq!(req.max_completion_tokens, Some(128));
        assert_eq!(req.max_tokens, None);
    }

    #[test]
    fn parse_optional_response_fields() {
        let json = json!({
            "model": "stub",
            "input": "Hello",
            "temperature": 0.5,
            "top_p": 0.9,
            "stream": true,
            "user": "user-1",
            "metadata": {"key": "value"},
            "parallel_tool_calls": false,
            "text": {"format": {"type": "json_object"}}
        });
        let req = ResponsesConverter::parse_request(&json).unwrap();
        assert_eq!(req.temperature, Some(0.5));
        assert_eq!(req.top_p, Some(0.9));
        assert!(req.stream);
        assert_eq!(req.user.as_deref(), Some("user-1"));
        assert_eq!(
            req.metadata.unwrap().get("key").map(String::as_str),
            Some("value")
        );
        assert_eq!(req.parallel_tool_calls, Some(false));
        assert_eq!(
            req.response_format,
            Some(CanonicalResponseFormat::JsonObject)
        );
    }

    #[test]
    fn reject_unsupported_field() {
        let json = json!({
            "model": "stub",
            "input": "Hello",
            "background": true,
            "store": false
        });
        let errors = ResponsesConverter::parse_request(&json).unwrap_err();
        let params: Vec<_> = errors
            .iter()
            .filter_map(|error| error.error.param.as_deref())
            .collect();
        assert_eq!(params, ["background", "store"]);
        assert!(
            errors
                .iter()
                .all(|error| { error.error.code.as_deref() == Some("unsupported_field") })
        );
    }

    #[test]
    fn reject_missing_input() {
        let errors = ResponsesConverter::parse_request(&json!({"model": "stub"})).unwrap_err();
        assert_eq!(errors[0].error.param.as_deref(), Some("input"));
    }

    #[test]
    fn format_basic_response() {
        let value = ResponsesConverter::format_response(&response(None));
        assert_eq!(value["id"], "resp_test-id");
        assert_eq!(value["object"], "response");
        assert_eq!(value["model"], "stub");
        assert_eq!(value["status"], "completed");
        assert!(value["created_at"].as_i64().is_some());
        assert_eq!(value["output"][0]["type"], "message");
        assert_eq!(value["output"][0]["id"], "msg_test-id");
        assert_eq!(value["output"][0]["role"], "assistant");
        assert_eq!(value["output"][0]["content"][0]["text"], "Hello");
    }

    #[test]
    fn format_response_with_usage() {
        let mut canonical = response(None);
        canonical.usage = Some(CanonicalUsage {
            prompt_tokens: 4,
            completion_tokens: 3,
            total_tokens: 7,
        });
        let value = ResponsesConverter::format_response(&canonical);
        assert_eq!(value["usage"]["input_tokens"], 4);
        assert_eq!(value["usage"]["output_tokens"], 3);
        assert_eq!(value["usage"]["total_tokens"], 7);
    }

    #[test]
    fn format_response_with_tool_calls() {
        let call = CanonicalToolCall {
            id: "call-1".into(),
            function: CanonicalFunctionCall {
                name: "weather".into(),
                arguments: r#"{"city":"Boston"}"#.into(),
            },
        };
        let value = ResponsesConverter::format_response(&response(Some(vec![call])));
        assert_eq!(value["output"][1]["type"], "function_call");
        assert_eq!(value["output"][1]["id"], "fc_call-1");
        assert_eq!(value["output"][1]["call_id"], "call-1");
        assert_eq!(value["output"][1]["name"], "weather");
        assert_eq!(value["output"][1]["arguments"], r#"{"city":"Boston"}"#);
    }

    #[test]
    fn round_trip_parse_and_format() {
        let request = ResponsesConverter::parse_request(&json!({
            "model": "stub",
            "input": "Hello"
        }))
        .unwrap();
        let content = request.messages[0].content.clone();
        let mut canonical = response(None);
        canonical.choices[0].message.as_mut().unwrap().content = content;
        let value: Value = ResponsesConverter::format_response(&canonical);
        assert_eq!(value["model"], request.model);
        assert_eq!(value["output"][0]["content"][0]["text"], "Hello");
    }
}
