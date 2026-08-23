use noerelay_core::wire::{
    ChatCompletionsConverter, CompatibilityProfile, PROFILE_VERSION, ResponsesConverter,
};
use serde_json::{Value, json};
use std::{fs, path::PathBuf};

fn fixture(path: &str) -> Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .join("tests/compat/fixtures")
        .join(path);
    serde_json::from_str(&fs::read_to_string(path).unwrap()).unwrap()
}

macro_rules! positive_chat {
    ($name:ident, $path:literal) => {
        #[test]
        fn $name() {
            ChatCompletionsConverter::parse_request(&fixture($path)).unwrap();
        }
    };
}

positive_chat!(chat_basic_text, "chat-completions/positive/basic-text.json");
positive_chat!(chat_with_tools, "chat-completions/positive/with-tools.json");
positive_chat!(
    chat_with_streaming,
    "chat-completions/positive/with-streaming.json"
);
positive_chat!(
    chat_with_response_format,
    "chat-completions/positive/with-response-format.json"
);
positive_chat!(
    chat_with_vision,
    "chat-completions/positive/with-vision.json"
);
positive_chat!(
    chat_with_multiple_messages,
    "chat-completions/positive/with-multiple-messages.json"
);

#[test]
fn chat_unsupported_field_is_negative() {
    assert!(
        ChatCompletionsConverter::parse_request(&fixture(
            "chat-completions/negative/unsupported-field.json"
        ))
        .is_err()
    );
}

#[test]
fn chat_invalid_model_is_negative() {
    assert!(
        ChatCompletionsConverter::parse_request(&fixture(
            "chat-completions/negative/invalid-model.json"
        ))
        .is_err()
    );
}

#[test]
fn chat_missing_messages_is_negative() {
    assert!(
        ChatCompletionsConverter::parse_request(&fixture(
            "chat-completions/negative/missing-messages.json"
        ))
        .is_err()
    );
}

#[test]
fn responses_basic_text_is_positive() {
    ResponsesConverter::parse_request(&fixture("responses/positive/basic-text.json")).unwrap();
}

#[test]
fn responses_with_tools_is_positive() {
    ResponsesConverter::parse_request(&fixture("responses/positive/with-tools.json")).unwrap();
}

#[test]
fn responses_unsupported_field_is_negative() {
    assert!(
        ResponsesConverter::parse_request(&fixture("responses/negative/unsupported-field.json"))
            .is_err()
    );
}

#[test]
fn support_matrix_version_matches_code() {
    assert_eq!(fixture("support-matrix.json")["version"], PROFILE_VERSION);
}

#[test]
fn support_matrix_chat_profile_is_generated_from_code() {
    let matrix = fixture("support-matrix.json");
    let actual = &matrix["profiles"][0];
    let expected = CompatibilityProfile::ChatCompletionsV1.support();
    assert_eq!(actual["supported_fields"], json!(expected.supported_fields));
    assert_eq!(
        actual["unsupported_fields"],
        json!(expected.unsupported_fields)
    );
}

#[test]
fn support_matrix_responses_profile_is_generated_from_code() {
    let matrix = fixture("support-matrix.json");
    let actual = &matrix["profiles"][1];
    let expected = CompatibilityProfile::ResponsesV1.support();
    assert_eq!(actual["supported_fields"], json!(expected.supported_fields));
    assert_eq!(
        actual["unsupported_fields"],
        json!(expected.unsupported_fields)
    );
}
