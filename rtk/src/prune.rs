/// Pruning algorithms for message arrays.
///
/// Mirrors the Python `prune_compress` strategy:
/// 1. Truncate verbose system messages (keep first 500 chars + "...") except last.
/// 2. Truncate very long user messages (>2000 chars) with ellipsis marker.
/// 3. Remove empty/whitespace-only messages (except last system and last user).
/// 4. Remove duplicate consecutive tool-call results (same tool_call_id).

use std::collections::HashMap;

pub type Message = HashMap<String, String>;

const SYSTEM_MAX: usize = 500;
const USER_MAX: usize = 2000;
const USER_KEEP_EACH: usize = 800;

/// Prune verbose / low-value content from messages.
pub fn prune_compress(messages: &[Message]) -> Vec<Message> {
    if messages.is_empty() {
        return vec![];
    }

    // Identify protected indices: last system, last user
    let mut last_system_idx: Option<usize> = None;
    let mut last_user_idx: Option<usize> = None;
    for idx in (0..messages.len()).rev() {
        let role = messages[idx].get("role").map(|r| r.as_str()).unwrap_or("");
        if role == "system" && last_system_idx.is_none() {
            last_system_idx = Some(idx);
        }
        if role == "user" && last_user_idx.is_none() {
            last_user_idx = Some(idx);
        }
        if last_system_idx.is_some() && last_user_idx.is_some() {
            break;
        }
    }

    // Phase 1: truncate system messages, remove empty messages
    let mut pruned: Vec<Message> = Vec::new();
    for (idx, msg) in messages.iter().enumerate() {
        let role = msg.get("role").map(|r| r.as_str()).unwrap_or("");
        let content = msg.get("content").map(|c| c.as_str()).unwrap_or("");

        // Remove empty messages unless they are the last system/user
        if content.trim().is_empty() {
            if Some(idx) == last_system_idx || Some(idx) == last_user_idx {
                pruned.push(msg.clone());
            }
            continue;
        }

        // Truncate non-last system messages
        if role == "system" && Some(idx) != last_system_idx {
            if content.chars().count() > SYSTEM_MAX {
                let mut new_msg = msg.clone();
                let truncated: String = content.chars().take(SYSTEM_MAX).collect();
                new_msg.insert("content".to_string(), truncated + "...");
                pruned.push(new_msg);
                continue;
            }
        }

        // Truncate very long user messages
        if role == "user" && content.chars().count() > USER_MAX {
            let mut new_msg = msg.clone();
            let chars: Vec<char> = content.chars().collect();
            let prefix: String = chars[..USER_KEEP_EACH.min(chars.len())].iter().collect();
            let suffix_start = if chars.len() > USER_KEEP_EACH {
                chars.len() - USER_KEEP_EACH
            } else {
                0
            };
            let suffix: String = chars[suffix_start..].iter().collect();
            new_msg.insert(
                "content".to_string(),
                format!("{}\n...[content truncated]...\n{}", prefix, suffix),
            );
            pruned.push(new_msg);
            continue;
        }

        pruned.push(msg.clone());
    }

    // Phase 2: remove duplicate consecutive tool results
    let mut deduped: Vec<Message> = Vec::new();
    let mut seen_tool_ids: std::collections::HashSet<String> = std::collections::HashSet::new();
    for msg in &pruned {
        let role = msg.get("role").map(|r| r.as_str()).unwrap_or("");
        if role == "tool" {
            let tool_id = msg.get("tool_call_id").cloned().unwrap_or_default();
            if !tool_id.is_empty() {
                if seen_tool_ids.contains(&tool_id) {
                    continue;
                }
                seen_tool_ids.insert(tool_id);
            }
        }
        deduped.push(msg.clone());
    }

    if deduped.is_empty() {
        messages.to_vec()
    } else {
        deduped
    }
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

    fn msg_with_tool(content: &str, tool_call_id: &str) -> Message {
        let mut m = HashMap::new();
        m.insert("role".to_string(), "tool".to_string());
        m.insert("content".to_string(), content.to_string());
        m.insert("tool_call_id".to_string(), tool_call_id.to_string());
        m
    }

    #[test]
    fn test_empty_list() {
        assert!(prune_compress(&[]).is_empty());
    }

    #[test]
    fn test_empty_messages_removed() {
        let msgs = vec![
            msg("system", "You are helpful"),
            msg("user", "   "),
            msg("user", "Real question"),
        ];
        let result = prune_compress(&msgs);
        let contents: Vec<&str> = result
            .iter()
            .map(|m| m.get("content").unwrap().as_str())
            .collect();
        assert!(contents.contains(&"Real question"));
        assert!(!contents.contains(&"   "));
    }

    #[test]
    fn test_verbose_non_last_system_truncated() {
        let msgs = vec![
            msg("system", &"A".repeat(1000)),
            msg("system", "Final instruction"),
            msg("user", "Task"),
        ];
        let result = prune_compress(&msgs);
        let sys_contents: Vec<&str> = result
            .iter()
            .filter(|m| m.get("role").unwrap() == "system")
            .map(|m| m.get("content").unwrap().as_str())
            .collect();
        assert_eq!(sys_contents.len(), 2);
        assert!(sys_contents.contains(&"Final instruction"));
        assert!(sys_contents.iter().any(|c| c.contains("...")));
    }

    #[test]
    fn test_very_long_user_truncated() {
        let msgs = vec![msg("user", &"Hello ".repeat(1000))];
        let result = prune_compress(&msgs);
        let content = result[0].get("content").unwrap();
        assert!(content.contains("[content truncated]"));
    }

    #[test]
    fn test_tool_duplicate_ids_removed() {
        let msgs = vec![
            msg_with_tool("Result A", "call_1"),
            msg_with_tool("Result B", "call_1"),
            msg_with_tool("Result C", "call_2"),
        ];
        let result = prune_compress(&msgs);
        let tool_ids: Vec<&str> = result
            .iter()
            .filter(|m| m.get("role").unwrap() == "tool")
            .map(|m| m.get("tool_call_id").unwrap().as_str())
            .collect();
        assert_eq!(tool_ids, vec!["call_1", "call_2"]);
    }
}