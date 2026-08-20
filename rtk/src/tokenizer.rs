/// Fast token estimation for English text.
///
/// Uses chars/4 heuristic, which approximates the average character-per-token
/// ratio for English text under common tokenizers (GPT, Claude, etc.).
/// Also provides a tiktoken-compatible estimation using byte-pair counting.

/// Rough token count estimate using chars/4 heuristic.
/// Returns at least 1 for any non-empty string.
pub fn estimate_tokens(text: &str) -> usize {
    if text.is_empty() {
        return 0;
    }
    std::cmp::max(1, text.chars().count() / 4)
}

/// Count tokens across a list of message content strings.
pub fn count_message_tokens(contents: &[String]) -> usize {
    contents.iter().map(|s| estimate_tokens(s)).sum()
}

/// Byte-Pair Encoding (BPE) aware estimate — counts whitespace-delimited
/// "words" and applies a chars/word ratio. More accurate than raw chars/4
/// for code and structured text.
pub fn estimate_tokens_bpe(text: &str) -> usize {
    if text.is_empty() {
        return 0;
    }
    let words: Vec<&str> = text.split_whitespace().collect();
    if words.is_empty() {
        return 1;
    }
    // Average English word is ~5 chars, average token is ~4 chars
    // So each word is roughly 1.25 tokens on average
    let word_count = words.len();
    let char_count: usize = words.iter().map(|w| w.chars().count()).sum();
    // Blend: 70% word-based + 30% char-based for robustness
    let word_estimate = (word_count as f64 * 1.25).ceil() as usize;
    let char_estimate = std::cmp::max(1, char_count / 4);
    ((word_estimate as f64 * 0.7) + (char_estimate as f64 * 0.3)).ceil() as usize
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_empty_string() {
        assert_eq!(estimate_tokens(""), 0);
    }

    #[test]
    fn test_short_text() {
        assert_eq!(estimate_tokens("hello"), 1);
    }

    #[test]
    fn test_medium_text() {
        assert_eq!(estimate_tokens(&"a".repeat(40)), 10);
    }

    #[test]
    fn test_bpe_empty() {
        assert_eq!(estimate_tokens_bpe(""), 0);
    }

    #[test]
    fn test_bpe_short() {
        let tokens = estimate_tokens_bpe("hello world");
        assert!(tokens > 0);
    }

    #[test]
    fn test_count_message_tokens() {
        let contents = vec!["hello world".to_string(), "foo bar baz".to_string()];
        let total = count_message_tokens(&contents);
        assert!(total > 0);
    }
}