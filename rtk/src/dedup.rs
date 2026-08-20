/// Deduplication algorithms for message arrays.
///
/// Mirrors the Python `dedup_compress` strategy:
/// 1. Remove consecutive exact duplicates (same role + content).
/// 2. Merge consecutive same-role messages by joining content with "\n\n".
/// 3. Collapse repeated whitespace/newlines within content strings.

use std::collections::HashMap;

/// A chat message with role and content.
pub type Message = HashMap<String, String>;

/// Remove consecutive duplicate messages and merge same-role runs.
pub fn dedup_compress(messages: &[Message]) -> Vec<Message> {
    if messages.is_empty() {
        return vec![];
    }

    // Phase 1: collapse consecutive exact duplicates
    let mut deduped: Vec<Message> = vec![messages[0].clone()];
    for msg in &messages[1..] {
        let prev = deduped.last().unwrap();
        let same_role = msg.get("role") == prev.get("role");
        let same_content = msg.get("content") == prev.get("content");
        if same_role && same_content {
            continue; // exact duplicate
        }
        deduped.push(msg.clone());
    }

    // Phase 2: merge consecutive same-role messages
    let mut merged: Vec<Message> = Vec::new();
    let mut i = 0;
    while i < deduped.len() {
        let msg = &deduped[i];
        let role = msg.get("role").cloned().unwrap_or_else(|| "user".to_string());
        let mut contents: Vec<String> = vec![msg.get("content").cloned().unwrap_or_default()];
        let mut j = i + 1;
        while j < deduped.len() && deduped[j].get("role").map(|r| r == &role).unwrap_or(false) {
            contents.push(deduped[j].get("content").cloned().unwrap_or_default());
            j += 1;
        }
        if contents.len() > 1 {
            let mut new_msg = HashMap::new();
            new_msg.insert("role".to_string(), role);
            new_msg.insert("content".to_string(), contents.join("\n\n"));
            merged.push(new_msg);
        } else {
            merged.push(msg.clone());
        }
        i = j;
    }

    // Phase 3: collapse whitespace in content strings
    for msg in &mut merged {
        if let Some(content) = msg.get_mut("content") {
            *content = collapse_whitespace(content);
        }
    }

    if merged.is_empty() {
        messages.to_vec()
    } else {
        merged
    }
}

/// Collapse 3+ consecutive newlines to 2, and 3+ consecutive spaces to 2.
fn collapse_whitespace(text: &str) -> String {
    // Collapse 3+ newlines to 2
    let mut result = String::new();
    let mut newline_count = 0;
    for ch in text.chars() {
        if ch == '\n' {
            newline_count += 1;
            if newline_count <= 2 {
                result.push(ch);
            }
        } else {
            if newline_count > 2 {
                // already skipped, nothing to flush
            }
            newline_count = 0;
            result.push(ch);
        }
    }

    // Collapse 3+ spaces to 2
    let mut final_result = String::new();
    let mut space_count = 0;
    for ch in result.chars() {
        if ch == ' ' {
            space_count += 1;
            if space_count <= 2 {
                final_result.push(ch);
            }
        } else {
            space_count = 0;
            final_result.push(ch);
        }
    }

    final_result
}

#[cfg(test)]
mod tests {
    use super::*;

    fn msg(role: &str, content: &str) -> Message {
        let mut m = HashMap::new();
        m.insert("role".to_string(), role.to_string());
        m.insert("content".to_string(), content.to_string());
        m
    }

    #[test]
    fn test_empty_list() {
        assert!(dedup_compress(&[]).is_empty());
    }

    #[test]
    fn test_single_message() {
        let msgs = vec![msg("user", "hello")];
        let result = dedup_compress(&msgs);
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].get("content").unwrap(), "hello");
    }

    #[test]
    fn test_consecutive_exact_duplicates_removed() {
        let msgs = vec![
            msg("user", "Hello"),
            msg("user", "Hello"),
            msg("user", "World"),
        ];
        let result = dedup_compress(&msgs);
        // After dedup removes duplicate "Hello", same-role merge joins remaining
        assert_eq!(result.len(), 1);
        let content = result[0].get("content").unwrap();
        assert!(content.contains("Hello"));
        assert!(content.contains("World"));
    }

    #[test]
    fn test_same_role_merge() {
        let msgs = vec![
            msg("user", "First question"),
            msg("user", "Second question"),
            msg("assistant", "Answer"),
        ];
        let result = dedup_compress(&msgs);
        assert_eq!(result.len(), 2);
        assert_eq!(result[0].get("role").unwrap(), "user");
        let content = result[0].get("content").unwrap();
        assert!(content.contains("First question"));
        assert!(content.contains("Second question"));
    }

    #[test]
    fn test_whitespace_collapse() {
        let msgs = vec![msg("user", "Line 1\n\n\n\n\n\nLine 2    with    spaces")];
        let result = dedup_compress(&msgs);
        let content = result[0].get("content").unwrap();
        assert!(!content.contains("    "));
    }
}