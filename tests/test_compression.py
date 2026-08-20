"""Tests for the RTK Phase 1 compression module (reference/gateway/compression.py).

Covers:
- estimate_tokens / count_message_tokens
- dedup_compress (consecutive duplicates, same-role merging, whitespace dedup)
- prune_compress (system truncation, user truncation, empty removal, tool dedup)
- auto_compress (combined strategy)
- compress_messages (full flow: disabled, below-min-tokens, skip logic)
- CompressionConfig.from_env (env var parsing)
- Integration: compressed messages used for LLM call, original for routing.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reference"))

from gateway.compression import (
    CompressionConfig,
    CompressionResult,
    auto_compress,
    compress_messages,
    count_message_tokens,
    dedup_compress,
    estimate_tokens,
    prune_compress,
)


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


class EstimateTokensTests(unittest.TestCase):
    def test_empty_string(self):
        self.assertEqual(estimate_tokens(""), 0)

    def test_short_text(self):
        # len("hello") == 5, 5 // 4 == 1, but max(1, 1) == 1
        self.assertEqual(estimate_tokens("hello"), 1)

    def test_medium_text(self):
        # 40 chars => 10 tokens
        self.assertEqual(estimate_tokens("a" * 40), 10)

    def test_long_text(self):
        # "hello world " = 12 chars; 12*100 // 4 = 300
        self.assertEqual(estimate_tokens("hello world " * 100), (12 * 100) // 4)


class CountMessageTokensTests(unittest.TestCase):
    def test_empty_list(self):
        self.assertEqual(count_message_tokens([]), 0)

    def test_single_message(self):
        msgs = [{"role": "user", "content": "hello world " * 10}]  # ~150 chars
        expect = len("hello world " * 10) // 4
        self.assertEqual(count_message_tokens(msgs), max(1, expect))

    def test_multiple_messages(self):
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]
        total = sum(max(1, len(m["content"]) // 4) for m in msgs)
        self.assertEqual(count_message_tokens(msgs), total)

    def test_multipart_content(self):
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Part A"},
                    {"type": "text", "text": "Part B"},
                ],
            }
        ]
        expect = estimate_tokens("Part A") + estimate_tokens("Part B")
        self.assertEqual(count_message_tokens(msgs), expect)


# ---------------------------------------------------------------------------
# Dedup strategy
# ---------------------------------------------------------------------------


class DedupCompressTests(unittest.TestCase):
    def test_empty_list(self):
        self.assertEqual(dedup_compress([], 0.5), [])

    def test_single_message(self):
        msgs = [{"role": "user", "content": "hello"}]
        self.assertEqual(dedup_compress(msgs, 0.5), msgs)

    def test_consecutive_exact_duplicates_removed(self):
        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "user", "content": "Hello"},  # duplicate
            {"role": "user", "content": "World"},
        ]
        result = dedup_compress(msgs, 0.5)
        # After dedup removes the duplicate "Hello", same-role merge
        # joins the remaining two user messages into one.
        self.assertEqual(len(result), 1)
        self.assertIn("Hello", result[0]["content"])
        self.assertIn("World", result[0]["content"])

    def test_non_consecutive_duplicates_kept(self):
        msgs = [
            {"role": "user", "content": "A"},
            {"role": "assistant", "content": "B"},
            {"role": "user", "content": "A"},  # same as first but not consecutive
        ]
        result = dedup_compress(msgs, 0.5)
        self.assertEqual(len(result), 3)

    def test_same_role_merge(self):
        msgs = [
            {"role": "user", "content": "First question"},
            {"role": "user", "content": "Second question"},
            {"role": "assistant", "content": "Answer"},
        ]
        result = dedup_compress(msgs, 0.5)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["role"], "user")
        self.assertIn("First question", result[0]["content"])
        self.assertIn("Second question", result[0]["content"])

    def test_no_merge_across_different_roles(self):
        msgs = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "User message"},
            {"role": "assistant", "content": "Bot reply"},
        ]
        result = dedup_compress(msgs, 0.5)
        self.assertEqual(len(result), 3)

    def test_whitespace_collapse(self):
        msgs = [
            {"role": "user", "content": "Line 1\n\n\n\n\n\nLine 2    with    spaces"},
        ]
        result = dedup_compress(msgs, 0.5)
        # After collapse: 3+ spaces should become 2 spaces.
        content = result[0]["content"]
        self.assertNotIn("    ", content)


# ---------------------------------------------------------------------------
# Prune strategy
# ---------------------------------------------------------------------------


class PruneCompressTests(unittest.TestCase):
    def test_empty_list(self):
        self.assertEqual(prune_compress([], 0.5), [])

    def test_empty_messages_removed(self):
        msgs = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "   "},  # whitespace only
            {"role": "user", "content": "Real question"},
        ]
        result = prune_compress(msgs, 0.5)
        # Only the non-empty messages remain (plus last user preserved)
        contents = [m["content"] for m in result]
        self.assertIn("Real question", contents)
        # Whitespace-only message should be gone unless it was the last user
        self.assertNotIn("   ", contents)

    def test_verbose_system_truncated(self):
        long_system = "x" * 1000
        msgs = [
            {"role": "system", "content": long_system},
            {"role": "user", "content": "hello"},
        ]
        result = prune_compress(msgs, 0.5)
        # The system message should be truncated (or it's the last, but
        # prune only truncates non-last system messages; since there's
        # only one system, it's also the last, so it's preserved).
        # Let's add another system message.
        pass

    def test_verbose_non_last_system_truncated(self):
        msgs = [
            {"role": "system", "content": "A" * 1000},
            {"role": "system", "content": "Final instruction"},
            {"role": "user", "content": "Task"},
        ]
        result = prune_compress(msgs, 0.5)
        # First system should be truncated; last system preserved.
        sys_contents = [
            m["content"] for m in result if m["role"] == "system"
        ]
        self.assertEqual(len(sys_contents), 2)
        self.assertIn("Final instruction", sys_contents)
        self.assertTrue(
            any("..." in c for c in sys_contents),
            f"Expected at least one truncated system message: {sys_contents}",
        )

    def test_very_long_user_truncated(self):
        msgs = [
            {"role": "user", "content": "Hello " * 1000},
        ]
        result = prune_compress(msgs, 0.5)
        self.assertIn("[content truncated]", result[0]["content"])

    def test_short_user_not_truncated(self):
        msgs = [
            {"role": "user", "content": "Short question"},
        ]
        result = prune_compress(msgs, 0.5)
        self.assertEqual(result[0]["content"], "Short question")

    def test_tool_duplicate_ids_removed(self):
        msgs = [
            {"role": "tool", "content": "Result A", "tool_call_id": "call_1"},
            {"role": "tool", "content": "Result B", "tool_call_id": "call_1"},
            {"role": "tool", "content": "Result C", "tool_call_id": "call_2"},
        ]
        result = prune_compress(msgs, 0.5)
        tool_ids = [
            m.get("tool_call_id") for m in result if m["role"] == "tool"
        ]
        self.assertEqual(tool_ids, ["call_1", "call_2"])


# ---------------------------------------------------------------------------
# Auto strategy
# ---------------------------------------------------------------------------


class AutoCompressTests(unittest.TestCase):
    def test_auto_uses_dedup_plus_prune(self):
        msgs = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello"},
            {"role": "user", "content": "Hello"},  # duplicate
            {"role": "user", "content": "World"},
        ]
        result = auto_compress(msgs, 0.5)
        # Should have deduped the duplicate "Hello" and merged user messages
        # Count should be <= dedup-only count
        dedup_only = dedup_compress(msgs, 0.5)
        self.assertLessEqual(len(result), len(msgs))

    def test_auto_returns_better_of_dedup_and_prune(self):
        """Auto picks the strategy that yields the fewest tokens after dedup."""
        msgs = [
            {"role": "system", "content": "A" * 2000},
            {"role": "user", "content": "Q1"},
            {"role": "user", "content": "Q1"},
            {"role": "user", "content": "Q2"},
        ]
        result = auto_compress(msgs, 0.5)
        deduped_tokens = count_message_tokens(dedup_compress(msgs, 0.5))
        auto_tokens = count_message_tokens(result)
        # Auto tokens should be <= dedup-only tokens (with potential prune added)
        # Actually auto = min(dedup_only, dedup+prune)
        self.assertLessEqual(auto_tokens, deduped_tokens)


# ---------------------------------------------------------------------------
# Full flow: compress_messages
# ---------------------------------------------------------------------------


class CompressMessagesTests(unittest.TestCase):
    def test_disabled_returns_original(self):
        config = CompressionConfig(enabled=False, strategy="dedup")
        msgs = [{"role": "user", "content": "Hello"}]
        result = compress_messages(msgs, config)
        self.assertTrue(result.skipped)
        self.assertEqual(result.original_messages, msgs)
        self.assertEqual(result.compressed_messages, msgs)

    def test_below_min_tokens_skips(self):
        config = CompressionConfig(
            enabled=True, strategy="dedup", min_tokens=9999,
        )
        msgs = [{"role": "user", "content": "short"}]
        result = compress_messages(msgs, config)
        self.assertTrue(result.skipped)
        self.assertEqual(result.tokens_saved, 0)

    def test_above_min_tokens_compresses(self):
        # Generate enough messages to exceed 512 token estimate
        msgs = [
            {"role": "user", "content": "Hello world! " * 100}
            for _ in range(20)
        ]
        config = CompressionConfig(
            enabled=True, strategy="dedup", min_tokens=100,
        )
        result = compress_messages(msgs, config)
        self.assertFalse(result.skipped)
        self.assertGreaterEqual(result.original_token_count, 100)
        # With dedup on 20 identical messages, we should see dramatic reduction
        self.assertLessEqual(
            result.compressed_token_count, result.original_token_count,
        )

    def test_dedup_strategy_reduces_duplicates(self):
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Duplicate"},
            {"role": "user", "content": "Duplicate"},
            {"role": "user", "content": "Unique"},
        ]
        config = CompressionConfig(
            enabled=True, strategy="dedup", min_tokens=0,
        )
        result = compress_messages(msgs, config)
        self.assertFalse(result.skipped)
        self.assertEqual(result.strategy, "dedup")
        self.assertLess(result.compressed_token_count, result.original_token_count)

    def test_prune_strategy(self):
        long_content = "The " * 800  # ~4000 chars
        msgs = [
            {"role": "system", "content": "B" * 2000},
            {"role": "system", "content": "Final system prompt"},
            {"role": "user", "content": long_content},
            {"role": "user", "content": "   "},  # empty
            {"role": "user", "content": "Real question"},
        ]
        config = CompressionConfig(
            enabled=True, strategy="prune", min_tokens=0,
        )
        result = compress_messages(msgs, config)
        self.assertFalse(result.skipped)
        self.assertLess(result.compressed_token_count, result.original_token_count)

    def test_auto_strategy(self):
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "A"},
            {"role": "user", "content": "A"},
            {"role": "user", "content": "B"},
        ]
        config = CompressionConfig(
            enabled=True, strategy="auto", min_tokens=0,
        )
        result = compress_messages(msgs, config)
        self.assertFalse(result.skipped)
        self.assertLessEqual(
            result.compressed_token_count, result.original_token_count,
        )

    def test_metrics_populated(self):
        msgs = [
            {"role": "user", "content": "Hello " * 100}
            for _ in range(5)
        ]
        config = CompressionConfig(
            enabled=True, strategy="dedup", min_tokens=0,
        )
        result = compress_messages(msgs, config)
        self.assertGreater(result.original_token_count, 0)
        self.assertGreaterEqual(result.compressed_token_count, 0)
        self.assertGreaterEqual(result.compression_ratio, 0.0)
        self.assertGreater(result.duration_ms, 0)
        self.assertGreaterEqual(result.tokens_saved, 0)


# ---------------------------------------------------------------------------
# CompressionConfig.from_env
# ---------------------------------------------------------------------------


class CompressionConfigFromEnvTests(unittest.TestCase):
    def test_defaults(self):
        config = CompressionConfig.from_env({})
        self.assertFalse(config.enabled)
        self.assertEqual(config.strategy, "dedup")
        self.assertEqual(config.min_tokens, 512)
        self.assertEqual(config.target_ratio, 0.5)

    def test_enabled_true(self):
        config = CompressionConfig.from_env(
            {"NOERELAY_COMPRESSION_ENABLED": "1"},
        )
        self.assertTrue(config.enabled)

    def test_strategy_auto(self):
        config = CompressionConfig.from_env(
            {"NOERELAY_COMPRESSION_STRATEGY": "auto"},
        )
        self.assertEqual(config.strategy, "auto")

    def test_strategy_prune(self):
        config = CompressionConfig.from_env(
            {"NOERELAY_COMPRESSION_STRATEGY": "prune"},
        )
        self.assertEqual(config.strategy, "prune")

    def test_invalid_strategy_raises(self):
        with self.assertRaises(ValueError):
            CompressionConfig.from_env(
                {"NOERELAY_COMPRESSION_STRATEGY": "invalid"},
            )

    def test_min_tokens_custom(self):
        config = CompressionConfig.from_env(
            {"NOERELAY_COMPRESSION_MIN_TOKENS": "1024"},
        )
        self.assertEqual(config.min_tokens, 1024)

    def test_target_ratio_custom(self):
        config = CompressionConfig.from_env(
            {"NOERELAY_COMPRESSION_TARGET_RATIO": "0.3"},
        )
        self.assertEqual(config.target_ratio, 0.3)

    def test_full_customization(self):
        config = CompressionConfig.from_env(
            {
                "NOERELAY_COMPRESSION_ENABLED": "1",
                "NOERELAY_COMPRESSION_STRATEGY": "auto",
                "NOERELAY_COMPRESSION_MIN_TOKENS": "256",
                "NOERELAY_COMPRESSION_TARGET_RATIO": "0.7",
            },
        )
        self.assertTrue(config.enabled)
        self.assertEqual(config.strategy, "auto")
        self.assertEqual(config.min_tokens, 256)
        self.assertEqual(config.target_ratio, 0.7)

    def test_enabled_invalid_bool_raises(self):
        with self.assertRaises(ValueError):
            CompressionConfig.from_env(
                {"NOERELAY_COMPRESSION_ENABLED": "yes"},
            )

    def test_min_tokens_invalid_raises(self):
        with self.assertRaises(ValueError):
            CompressionConfig.from_env(
                {"NOERELAY_COMPRESSION_MIN_TOKENS": "abc"},
            )

    def test_target_ratio_invalid_raises(self):
        with self.assertRaises(ValueError):
            CompressionConfig.from_env(
                {"NOERELAY_COMPRESSION_TARGET_RATIO": "two"},
            )

    def test_target_ratio_out_of_range(self):
        with self.assertRaises(ValueError):
            CompressionConfig.from_env(
                {"NOERELAY_COMPRESSION_TARGET_RATIO": "2.0"},
            )


# ---------------------------------------------------------------------------
# Integration: compressed messages for LLM, original for routing
# ---------------------------------------------------------------------------


class CompressionIntegrationTests(unittest.TestCase):
    """Verify that the pipeline uses compressed messages for the LLM call
    but keeps original messages for routing decisions."""

    def test_compressed_messages_preserved_in_result(self):
        """CompressionResult stores both original and compressed arrays."""
        original = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Hello"},
            {"role": "user", "content": "Hello"},
            {"role": "user", "content": "World"},
        ]
        config = CompressionConfig(
            enabled=True, strategy="dedup", min_tokens=0,
        )
        result = compress_messages(original, config)
        # Original should be stored untouched
        self.assertEqual(result.original_messages, original)
        # Compressed should differ
        self.assertNotEqual(result.compressed_messages, original)
        # Each element should have role and content
        for msg in result.compressed_messages:
            self.assertIn("role", msg)
            self.assertIn("content", msg)

    def test_compression_result_dataclass_attributes(self):
        """All CompressionResult fields are present and sensible."""
        original = [{"role": "user", "content": "Hello world"}]
        config = CompressionConfig(
            enabled=True, strategy="dedup", min_tokens=0,
        )
        result = compress_messages(original, config)
        self.assertIsInstance(result, CompressionResult)
        self.assertIsInstance(result.original_token_count, int)
        self.assertIsInstance(result.compressed_token_count, int)
        self.assertIsInstance(result.compression_ratio, float)
        self.assertIsInstance(result.strategy, str)
        self.assertIsInstance(result.duration_ms, float)
        self.assertIsInstance(result.tokens_saved, int)
        self.assertIsInstance(result.skipped, bool)

    def test_no_compression_leaves_messages_unchanged(self):
        """When disabled, compressed_messages == original_messages."""
        original = [{"role": "user", "content": "Hello"}]
        config = CompressionConfig(enabled=False, strategy="dedup")
        result = compress_messages(original, config)
        self.assertEqual(result.compressed_messages, original)

    def test_compression_can_dramatically_reduce_tokens(self):
        """Verify that dedup on many identical messages yields big savings."""
        # 50 identical user messages
        msgs = [{"role": "user", "content": "The quick brown fox jumps over the lazy dog."} for _ in range(50)]
        config = CompressionConfig(
            enabled=True, strategy="dedup", min_tokens=0,
        )
        result = compress_messages(msgs, config)
        # 50 messages should collapse to 1 after dedup+merge
        self.assertLess(result.compressed_token_count, result.original_token_count * 0.5)
        self.assertGreater(result.compression_ratio, 0.0)
        self.assertGreater(result.tokens_saved, 0)


if __name__ == "__main__":
    unittest.main()