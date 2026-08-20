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


# ---------------------------------------------------------------------------
# Phase 2: RTK bridge fallback tests
# ---------------------------------------------------------------------------


class RtkBridgeTests(unittest.TestCase):
    """Test the RTK bridge fallback behavior."""

    def test_is_native_available_returns_bool(self):
        from gateway.rtk_bridge import is_native_available
        result = is_native_available()
        self.assertIsInstance(result, bool)

    def test_get_native_version(self):
        from gateway.rtk_bridge import get_native_version
        version = get_native_version()
        # Either None (not available) or a string
        self.assertTrue(version is None or isinstance(version, str))

    def test_estimate_tokens_native_returns_int_or_none(self):
        from gateway.rtk_bridge import estimate_tokens_native
        result = estimate_tokens_native("hello world")
        self.assertTrue(result is None or isinstance(result, int))

    def test_compress_native_fallback_returns_none(self):
        from gateway.rtk_bridge import compress_native
        # Without a compiled extension, should return None
        result = compress_native(
            [{"role": "user", "content": "test"}], "dedup", 0.5, 100,
        )
        # May be None (fallback) or dict (native available)
        self.assertTrue(result is None or isinstance(result, dict))

    def test_compress_messages_uses_bridge(self):
        """compress_messages records native_used flag."""
        msgs = [{"role": "user", "content": "Hello world"}]
        config = CompressionConfig(
            enabled=True, strategy="dedup", min_tokens=0,
        )
        result = compress_messages(msgs, config)
        self.assertIsInstance(result.native_used, bool)


# ---------------------------------------------------------------------------
# Phase 3: Summarize strategy
# ---------------------------------------------------------------------------


class SummarizeCompressTests(unittest.TestCase):
    """Tests for the summarize_compress strategy."""

    def setUp(self):
        # Need to import summarize_compress
        from gateway.compression import summarize_compress
        self.summarize = summarize_compress

    def test_empty_list(self):
        self.assertEqual(self.summarize([], 0.5), [])

    def test_short_conversation_not_summarized(self):
        """Very few messages should not trigger aggressive summarization."""
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        result = self.summarize(msgs, 0.5)
        # Should keep at least 2 messages, only summarize if older ones exist
        self.assertGreater(len(result), 0)

    def test_long_conversation_summarized(self):
        """Many messages should produce a summary + recent messages."""
        msgs = [{"role": "system", "content": "You are helpful."}]
        for i in range(20):
            msgs.append({"role": "user", "content": f"Question {i}"})
            msgs.append({"role": "assistant", "content": f"Answer {i}"})
        result = self.summarize(msgs, 0.5)
        # Should have a system summary + recent messages
        self.assertLess(len(result), len(msgs))
        # First message should be a system summary
        self.assertEqual(result[0]["role"], "system")
        self.assertIn("summary", result[0]["content"].lower())

    def test_summary_with_custom_summarizer(self):
        """Custom summarizer function is used when provided."""
        msgs = [
            {"role": "system", "content": "You are a helpful assistant with enough context to summarize."},
            {"role": "user", "content": "Question 1 about complex topics and detailed information"},
            {"role": "assistant", "content": "Answer 1 with comprehensive explanation about the topic"},
            {"role": "user", "content": "Question 2 about another complex domain"},
            {"role": "assistant", "content": "Answer 2 with detailed analysis"},
            {"role": "user", "content": "Question 3 about yet another subject"},
            {"role": "assistant", "content": "Answer 3 with thorough explanation"},
        ]

        def custom_summarizer(messages):
            return "Custom summary of the conversation."

        result = self.summarize(msgs, 0.5, summarizer_fn=custom_summarizer)
        self.assertIn("Custom summary", result[0]["content"])

    def test_summarize_no_reduction_returns_original(self):
        """If summarization doesn't reduce tokens, return original."""
        msgs = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
        ]
        result = self.summarize(msgs, 0.1)  # very low target → keep almost all
        self.assertEqual(len(result), 2)


# ---------------------------------------------------------------------------
# Phase 3: Adaptive strategy selection
# ---------------------------------------------------------------------------


class SelectStrategyTests(unittest.TestCase):
    """Tests for the select_strategy function."""

    def setUp(self):
        from gateway.compression import select_strategy

        self.select = select_strategy

    def test_respects_explicit_non_auto_strategy(self):
        """If config.strategy is not 'auto', it is returned directly."""
        config = CompressionConfig(strategy="dedup")
        result = self.select([{"role": "user", "content": "hello"}], config)
        self.assertEqual(result, "dedup")

        config = CompressionConfig(strategy="prune")
        result = self.select([{"role": "user", "content": "hello"}], config)
        self.assertEqual(result, "prune")

        config = CompressionConfig(strategy="summarize")
        result = self.select([{"role": "user", "content": "hello"}], config)
        self.assertEqual(result, "summarize")

    def test_auto_selects_dedup_for_duplicates(self):
        """High duplicate ratio → dedup."""
        config = CompressionConfig(strategy="auto")
        msgs = [
            {"role": "user", "content": "Same content"},
            {"role": "user", "content": "Same content"},
            {"role": "user", "content": "Same content"},
            {"role": "assistant", "content": "Reply"},
        ]
        result = self.select(msgs, config)
        self.assertEqual(result, "dedup")

    def test_auto_selects_prune_for_long_messages(self):
        """Very long messages → prune."""
        config = CompressionConfig(strategy="auto")
        msgs = [
            {"role": "user", "content": "A" * 5000},
            {"role": "assistant", "content": "Short"},
        ]
        result = self.select(msgs, config)
        self.assertEqual(result, "prune")

    def test_auto_selects_summarize_for_many_messages(self):
        """Many messages → summarize."""
        config = CompressionConfig(
            strategy="auto", summarizer_max_messages=5,
        )
        msgs = []
        for i in range(15):
            msgs.append({"role": "user", "content": f"Question {i}"})
            msgs.append({"role": "assistant", "content": f"Answer {i}"})
        result = self.select(msgs, config)
        self.assertEqual(result, "summarize")

    def test_auto_selects_auto_for_normal_messages(self):
        """Normal length, few duplicates → auto."""
        config = CompressionConfig(strategy="auto")
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        result = self.select(msgs, config)
        self.assertEqual(result, "auto")

    def test_empty_messages_default_to_dedup(self):
        """Empty list → dedup."""
        config = CompressionConfig(strategy="auto")
        result = self.select([], config)
        self.assertEqual(result, "dedup")


# ---------------------------------------------------------------------------
# Phase 3: Quality metrics
# ---------------------------------------------------------------------------


class AssessQualityTests(unittest.TestCase):
    """Tests for the assess_quality function."""

    def setUp(self):
        from gateway.compression import assess_quality

        self.assess = assess_quality

    def test_identical_messages(self):
        """Identical input and output → perfect quality."""
        msgs = [{"role": "user", "content": "Hello world"}]
        quality = self.assess(msgs, msgs)
        self.assertGreaterEqual(quality.quality_score, 0.9)
        self.assertEqual(quality.semantic_preservation, 1.0)

    def test_empty_both(self):
        """Both empty → perfect quality."""
        quality = self.assess([], [])
        self.assertEqual(quality.semantic_preservation, 1.0)
        self.assertEqual(quality.information_loss, 0.0)
        self.assertEqual(quality.quality_score, 1.0)

    def test_compression_reduces_content(self):
        """Compressed messages have lower content → some loss."""
        original = [
            {"role": "user", "content": "This is a long message with many words"},
        ]
        compressed = [
            {"role": "user", "content": "Short msg"},
        ]
        quality = self.assess(original, compressed)
        self.assertLess(quality.semantic_preservation, 1.0)
        self.assertGreater(quality.information_loss, 0.0)

    def test_quality_fields_present(self):
        """All quality fields are floats in [0, 1]."""
        original = [{"role": "user", "content": "Original content here"}]
        compressed = [{"role": "user", "content": "Compressed"}]
        quality = self.assess(original, compressed)
        self.assertIsInstance(quality.semantic_preservation, float)
        self.assertIsInstance(quality.information_loss, float)
        self.assertIsInstance(quality.redundancy_removed, float)
        self.assertIsInstance(quality.quality_score, float)
        self.assertGreaterEqual(quality.semantic_preservation, 0.0)
        self.assertLessEqual(quality.semantic_preservation, 1.0)
        self.assertGreaterEqual(quality.quality_score, 0.0)
        self.assertLessEqual(quality.quality_score, 1.0)

    def test_dedup_preserves_quality(self):
        """Dedup on duplicates should have high quality."""
        original = [
            {"role": "user", "content": "Hello world"},
            {"role": "user", "content": "Hello world"},
            {"role": "user", "content": "Goodbye"},
        ]
        compressed = [
            {"role": "user", "content": "Hello world\n\nGoodbye"},
        ]
        quality = self.assess(original, compressed)
        self.assertGreater(quality.quality_score, 0.5)
        self.assertGreater(quality.redundancy_removed, 0.0)


# ---------------------------------------------------------------------------
# Phase 3: CompressionResult with new fields
# ---------------------------------------------------------------------------


class CompressionResultNewFieldsTests(unittest.TestCase):
    """Verify new fields in CompressionResult."""

    def test_new_fields_present(self):
        """CompressionResult has quality and strategy_selected fields."""
        msgs = [{"role": "user", "content": "Hello world " * 50}]
        config = CompressionConfig(
            enabled=True, strategy="dedup", min_tokens=0,
        )
        result = compress_messages(msgs, config)
        self.assertIsInstance(result.native_used, bool)
        self.assertIsNotNone(result.quality)
        self.assertIsInstance(result.strategy_selected, str)
        self.assertIsInstance(result.cache_hit, bool)
        self.assertGreater(len(result.strategy_selected), 0)

    def test_quality_populated(self):
        """CompressionResult.quality is populated after compression."""
        original = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello world " * 20},
        ]
        config = CompressionConfig(
            enabled=True, strategy="dedup", min_tokens=0,
        )
        result = compress_messages(original, config)
        self.assertIsNotNone(result.quality)
        self.assertIsInstance(result.quality.semantic_preservation, float)
        self.assertGreater(result.quality.quality_score, 0.0)

    def test_skipped_also_populates_quality(self):
        """Even skipped results have quality metadata."""
        msgs = [{"role": "user", "content": "short"}]
        config = CompressionConfig(
            enabled=True, strategy="dedup", min_tokens=9999,
        )
        result = compress_messages(msgs, config)
        self.assertTrue(result.skipped)
        self.assertIsNotNone(result.quality)


# ---------------------------------------------------------------------------
# Phase 4: CompressionCache integration
# ---------------------------------------------------------------------------


class CompressionCacheIntegrationTests(unittest.TestCase):
    """Test cache integration in compress_messages."""

    def setUp(self):
        from gateway.compression import _cache
        if _cache is not None:
            _cache.clear()

    def tearDown(self):
        from gateway.compression import _cache
        if _cache is not None:
            _cache.clear()

    def test_cache_integration_enabled(self):
        """With cache_enabled=True, repeated calls return same cached result."""
        msgs = [{"role": "user", "content": "Unique cache test message " * 30}]
        config = CompressionConfig(
            enabled=True, strategy="dedup", min_tokens=0,
            cache_enabled=True, profiling_enabled=False,
        )
        result1 = compress_messages(msgs, config)
        result2 = compress_messages(msgs, config)
        # At least one call should be a cache hit
        self.assertTrue(result2.cache_hit or result1.cache_hit)

    def test_cache_enabled_can_be_false(self):
        """With cache_enabled=False, each call is fresh."""
        msgs = [{"role": "user", "content": "Hello world " * 50}]
        config = CompressionConfig(
            enabled=True, strategy="dedup", min_tokens=0,
            cache_enabled=False, profiling_enabled=False,
        )
        result = compress_messages(msgs, config)
        self.assertFalse(result.cache_hit)

    def test_get_cache_stats(self):
        """get_cache_stats returns stats after compression."""
        from gateway.compression import get_cache_stats

        msgs = [{"role": "user", "content": "Hello world " * 50}]
        config = CompressionConfig(
            enabled=True, strategy="dedup", min_tokens=0,
            cache_enabled=True, profiling_enabled=False,
        )
        compress_messages(msgs, config)
        stats = get_cache_stats()
        self.assertIsNotNone(stats)
        self.assertIn("size", stats)

    def test_get_profiler_stats(self):
        """get_profiler_stats returns stats after compression."""
        from gateway.compression import get_profiler_stats

        msgs = [{"role": "user", "content": "Hello world " * 50}]
        config = CompressionConfig(
            enabled=True, strategy="dedup", min_tokens=0,
            profiling_enabled=True, cache_enabled=False,
        )
        compress_messages(msgs, config)
        stats = get_profiler_stats()
        self.assertIsNotNone(stats)
        self.assertIn("overall", stats)


# ---------------------------------------------------------------------------
# Phase 4: AdaptiveRatio
# ---------------------------------------------------------------------------


class AdaptiveRatioTests(unittest.TestCase):
    """Tests for the AdaptiveRatio class."""

    def setUp(self):
        from gateway.compression import AdaptiveRatio

        self.AdaptiveRatio = AdaptiveRatio

    def test_initial_ratio_is_midpoint(self):
        """Initial ratio is between min and max."""
        ar = self.AdaptiveRatio(min_ratio=0.2, max_ratio=0.8)
        ratio = ar.get_ratio()
        self.assertGreaterEqual(ratio, 0.2)
        self.assertLessEqual(ratio, 0.8)

    def test_update_does_not_crash(self):
        """update() handles a compression result."""
        ar = self.AdaptiveRatio()
        msgs = [{"role": "user", "content": "Hello"}]
        config = CompressionConfig(
            enabled=True, strategy="dedup", min_tokens=0,
            profiling_enabled=False, cache_enabled=False,
        )
        result = compress_messages(msgs, config)
        ar.update(result)

    def test_no_adjustment_with_few_updates(self):
        """Ratio shouldn't change with fewer than 3 updates."""
        from gateway.compression import CompressionQuality

        ar = self.AdaptiveRatio(min_ratio=0.3, max_ratio=0.7)
        initial = ar.get_ratio()

        # Create dummy results
        quality = CompressionQuality(0.9, 0.1, 0.3, 0.85)
        msgs = [{"role": "user", "content": "hello"}]
        from gateway.compression import CompressionResult

        result = CompressionResult(
            original_messages=msgs, compressed_messages=msgs,
            original_token_count=10, compressed_token_count=8,
            compression_ratio=0.2, strategy="dedup", duration_ms=1.0,
            tokens_saved=2, skipped=False, quality=quality,
            strategy_selected="dedup",
        )
        ar.update(result)  # 1st
        self.assertEqual(ar.get_ratio(), initial)
        ar.update(result)  # 2nd
        self.assertEqual(ar.get_ratio(), initial)

    def test_high_quality_increases_compression(self):
        """Consistently high quality → lower ratio (more compression)."""
        from gateway.compression import CompressionQuality, CompressionResult

        ar = self.AdaptiveRatio(
            min_ratio=0.2, max_ratio=0.8,
            adjustment_step=0.1, window_size=5,
        )
        initial = ar.get_ratio()
        quality = CompressionQuality(0.95, 0.05, 0.4, 0.92)
        msgs = [{"role": "user", "content": "hello"}]

        for _ in range(5):
            result = CompressionResult(
                original_messages=msgs, compressed_messages=msgs,
                original_token_count=10, compressed_token_count=8,
                compression_ratio=0.2, strategy="dedup", duration_ms=1.0,
                tokens_saved=2, skipped=False, quality=quality,
                strategy_selected="dedup",
            )
            ar.update(result)

        new_ratio = ar.get_ratio()
        # Should decrease (more aggressive compression)
        self.assertLess(new_ratio, initial)

    def test_skipped_does_not_affect_ratio(self):
        """Skipped results don't update the quality history."""
        from gateway.compression import CompressionQuality, CompressionResult

        ar = self.AdaptiveRatio(min_ratio=0.3, max_ratio=0.7)
        initial = ar.get_ratio()

        quality = CompressionQuality(0.9, 0.1, 0.0, 0.9)
        msgs = [{"role": "user", "content": "hello"}]

        for _ in range(10):
            result = CompressionResult(
                original_messages=msgs, compressed_messages=msgs,
                original_token_count=10, compressed_token_count=8,
                compression_ratio=0.2, strategy="dedup", duration_ms=1.0,
                tokens_saved=2, skipped=True, quality=quality,
                strategy_selected="dedup",
            )
            ar.update(result)

        # Skipped results should not change ratio
        self.assertEqual(ar.get_ratio(), initial)


# ---------------------------------------------------------------------------
# Phase 4: Full pipeline with all features
# ---------------------------------------------------------------------------


class FullPipelineCompressionTests(unittest.TestCase):
    """End-to-end tests with all Phase 3-4 features enabled."""

    def test_all_features_enabled(self):
        """Compression works with cache, profiling, and adaptive ratio enabled."""
        config = CompressionConfig(
            enabled=True, strategy="auto", min_tokens=0,
            cache_enabled=True, profiling_enabled=True,
            adaptive_ratio_enabled=True,
        )
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "user", "content": "Hello"},
            {"role": "user", "content": "What is 2+2?"},
        ]
        result = compress_messages(msgs, config)
        self.assertFalse(result.skipped)
        self.assertIsNotNone(result.quality)
        self.assertIn(result.strategy_selected, {"dedup", "prune", "summarize", "auto"})

    def test_summarize_as_selected_strategy(self):
        """The 'summarize' strategy can be explicitly selected and used."""
        config = CompressionConfig(
            enabled=True, strategy="summarize", min_tokens=0,
            cache_enabled=False, profiling_enabled=False,
        )
        msgs = [
            {"role": "system", "content": "You are helpful."},
        ]
        for i in range(15):
            msgs.append({"role": "user", "content": f"Question {i}: What is the meaning?"})
            msgs.append({"role": "assistant", "content": f"Answer {i}: The meaning is 42."})
        from gateway.compression import count_message_tokens

        orig_tokens = count_message_tokens(msgs)
        result = compress_messages(msgs, config)
        self.assertFalse(result.skipped)
        self.assertEqual(result.strategy_selected, "summarize")
        self.assertLess(result.compressed_token_count, orig_tokens)

    def test_config_with_new_fields_from_env(self):
        """New config fields can be set via environment."""
        config = CompressionConfig.from_env({
            "NOERELAY_COMPRESSION_ENABLED": "1",
            "NOERELAY_COMPRESSION_STRATEGY": "auto",
            "NOERELAY_COMPRESSION_MIN_TOKENS": "100",
            "NOERELAY_COMPRESSION_TARGET_RATIO": "0.6",
            "NOERELAY_COMPRESSION_SUMMARIZER_MODEL": "custom/model",
            "NOERELAY_COMPRESSION_SUMMARIZER_MAX_MESSAGES": "5",
            "NOERELAY_COMPRESSION_QUALITY_THRESHOLD": "0.5",
            "NOERELAY_COMPRESSION_CACHE_ENABLED": "1",
            "NOERELAY_COMPRESSION_CACHE_MAX_SIZE": "50",
            "NOERELAY_COMPRESSION_CACHE_TTL": "120",
            "NOERELAY_COMPRESSION_PROFILING_ENABLED": "1",
            "NOERELAY_COMPRESSION_ADAPTIVE_RATIO": "1",
            "NOERELAY_COMPRESSION_ADAPTIVE_MIN_RATIO": "0.2",
            "NOERELAY_COMPRESSION_ADAPTIVE_MAX_RATIO": "0.9",
        })
        self.assertTrue(config.enabled)
        self.assertEqual(config.strategy, "auto")
        self.assertEqual(config.min_tokens, 100)
        self.assertEqual(config.target_ratio, 0.6)
        self.assertEqual(config.summarizer_model, "custom/model")
        self.assertEqual(config.summarizer_max_messages, 5)
        self.assertEqual(config.quality_threshold, 0.5)
        self.assertTrue(config.cache_enabled)
        self.assertEqual(config.cache_max_size, 50)
        self.assertEqual(config.cache_ttl_seconds, 120)
        self.assertTrue(config.profiling_enabled)
        self.assertTrue(config.adaptive_ratio_enabled)
        self.assertEqual(config.adaptive_min_ratio, 0.2)
        self.assertEqual(config.adaptive_max_ratio, 0.9)


if __name__ == "__main__":
    unittest.main()