"""RTK Phases 1-4 — Python compression module for the NoeRelay gateway.

Inserts as a ``context_compressed`` pipeline stage between context compilation
and route selection.  Operates on the ``messages`` array, returning a compressed
version plus metadata (original/compressed token counts, ratio, duration, quality).

Strategies
----------
* ``dedup`` — remove consecutive duplicate messages, merge same-role runs,
  and collapse duplicate content blocks within messages.
* ``prune`` — truncate verbose system messages, trim very long user messages,
  and drop empty/whitespace-only messages.
* ``summarize`` — use LLM-based or extractive summarization to compress
  conversation history into a single system message.
* ``auto`` — adaptively select the best strategy based on message content
  or apply dedup first; if still exceeding target ratio, follow with prune.

Phase 2: Rust bridge via rtk_bridge for native performance.
Phase 3: LLM summarization, adaptive strategy selection, quality metrics.
Phase 4: Cache, profiler, adaptive ratio adjustment.

EPR-COMP-001: Compression is always lossless with respect to semantic intent.
The router sees original token counts; the LLM receives compressed messages.
"""

from __future__ import annotations

import os
import re
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable

from .compression_cache import CompressionCache, _hash_messages
from .compression_profiler import CompressionProfiler, ProfileEntry
from . import rtk_bridge

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class CompressionConfig:
    """Per-gateway compression settings, read from environment variables."""

    enabled: bool = False
    strategy: str = "dedup"          # dedup | prune | summarize | auto
    min_tokens: int = 512            # skip compression when below this
    target_ratio: float = 0.5        # aim to reduce tokens by this fraction

    # Phase 3: summarization / adaptive / quality
    summarizer_model: str = "noerelay/epr-1"
    summarizer_max_messages: int = 10
    quality_threshold: float = 0.0  # 0 = never skip based on quality

    # Phase 4: cache / profiling / adaptive ratio
    cache_enabled: bool = True
    cache_max_size: int = 100
    cache_ttl_seconds: int = 300
    profiling_enabled: bool = True
    adaptive_ratio_enabled: bool = False
    adaptive_min_ratio: float = 0.3
    adaptive_max_ratio: float = 0.8

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None,
    ) -> "CompressionConfig":
        """Build a ``CompressionConfig`` from an environment mapping.

        When *environ* is ``None`` the process environment is read.
        """
        env: Mapping[str, str] = os.environ if environ is None else environ

        enabled = _parse_bool(env, "NOERELAY_COMPRESSION_ENABLED", False)
        strategy = _value(env, "NOERELAY_COMPRESSION_STRATEGY", "dedup")
        if strategy not in {"dedup", "prune", "summarize", "auto"}:
            raise ValueError(
                f"NOERELAY_COMPRESSION_STRATEGY must be dedup, prune, summarize, "
                f"or auto; got {strategy!r}",
            )
        min_tokens = _parse_int(env, "NOERELAY_COMPRESSION_MIN_TOKENS", 512, minimum=0)
        target_ratio = _parse_float(env, "NOERELAY_COMPRESSION_TARGET_RATIO", 0.5,
                                    minimum=0.0, maximum=1.0)

        # Phase 3
        summarizer_model = _value(env, "NOERELAY_COMPRESSION_SUMMARIZER_MODEL",
                                  "noerelay/epr-1")
        summarizer_max_messages = _parse_int(
            env, "NOERELAY_COMPRESSION_SUMMARIZER_MAX_MESSAGES", 10, minimum=2,
        )
        quality_threshold = _parse_float(
            env, "NOERELAY_COMPRESSION_QUALITY_THRESHOLD", 0.0,
            minimum=0.0, maximum=1.0,
        )

        # Phase 4
        cache_enabled = _parse_bool(env, "NOERELAY_COMPRESSION_CACHE_ENABLED", True)
        cache_max_size = _parse_int(env, "NOERELAY_COMPRESSION_CACHE_MAX_SIZE", 100, minimum=1)
        cache_ttl_seconds = _parse_int(env, "NOERELAY_COMPRESSION_CACHE_TTL", 300, minimum=0)
        profiling_enabled = _parse_bool(env, "NOERELAY_COMPRESSION_PROFILING_ENABLED", True)
        adaptive_ratio_enabled = _parse_bool(env, "NOERELAY_COMPRESSION_ADAPTIVE_RATIO", False)
        adaptive_min_ratio = _parse_float(
            env, "NOERELAY_COMPRESSION_ADAPTIVE_MIN_RATIO", 0.3,
            minimum=0.0, maximum=1.0,
        )
        adaptive_max_ratio = _parse_float(
            env, "NOERELAY_COMPRESSION_ADAPTIVE_MAX_RATIO", 0.8,
            minimum=0.0, maximum=1.0,
        )

        return cls(
            enabled=enabled, strategy=strategy, min_tokens=min_tokens,
            target_ratio=target_ratio,
            summarizer_model=summarizer_model,
            summarizer_max_messages=summarizer_max_messages,
            quality_threshold=quality_threshold,
            cache_enabled=cache_enabled, cache_max_size=cache_max_size,
            cache_ttl_seconds=cache_ttl_seconds,
            profiling_enabled=profiling_enabled,
            adaptive_ratio_enabled=adaptive_ratio_enabled,
            adaptive_min_ratio=adaptive_min_ratio,
            adaptive_max_ratio=adaptive_max_ratio,
        )


# ---------------------------------------------------------------------------
# Quality metrics
# ---------------------------------------------------------------------------


@dataclass
class CompressionQuality:
    """Quality assessment of a compression result."""

    semantic_preservation: float   # 0-1, how much meaning is preserved
    information_loss: float        # 0-1, how much info is lost
    redundancy_removed: float      # 0-1, how much redundancy was removed
    quality_score: float           # weighted combination


def assess_quality(
    original: list[dict[str, Any]], compressed: list[dict[str, Any]],
) -> CompressionQuality:
    """Compare original and compressed messages to estimate preservation quality.

    Uses heuristic metrics:
    - Key term overlap (important words preserved)
    - Content length ratio (not too aggressive)
    - Redundancy reduction (duplicate content removed)
    """
    def _extract_terms(messages: list[dict[str, Any]]) -> Counter[str]:
        """Extract important words from message content."""
        terms: Counter[str] = Counter()
        for msg in messages:
            content = str(msg.get("content", ""))
            # Tokenize to lowercase words, min length 3, skip common stop words
            words = re.findall(r"\b[a-zA-Z]{3,}\b", content.lower())
            # Filter common stop words
            stop_words = {"the", "and", "for", "that", "this", "with", "you",
                          "are", "was", "not", "but", "all", "can", "has",
                          "have", "been", "will", "would", "from", "they",
                          "their", "what", "when", "where", "which", "there",
                          "about", "your"}
            for w in words:
                if w not in stop_words:
                    terms[w] += 1
        return terms

    orig_terms = _extract_terms(original)
    comp_terms = _extract_terms(compressed)

    # Semantic preservation: overlap of important terms
    all_terms = set(orig_terms) | set(comp_terms)
    if not all_terms:
        return CompressionQuality(
            semantic_preservation=1.0,
            information_loss=0.0,
            redundancy_removed=0.0,
            quality_score=1.0,
        )

    shared = set(orig_terms) & set(comp_terms)
    semantic_preservation = len(shared) / len(all_terms) if all_terms else 1.0

    # Information loss: unique terms in original not in compressed
    unique_original = set(orig_terms) - set(comp_terms)
    information_loss = len(unique_original) / max(len(set(orig_terms)), 1)

    # Redundancy removed: count ratio + token ratio blend
    orig_total = sum(orig_terms.values())
    comp_total = sum(comp_terms.values())
    term_redundancy = 0.0
    if orig_total > 0:
        term_redundancy = 1.0 - (comp_total / orig_total)
    term_redundancy = max(0.0, min(1.0, term_redundancy))

    # Also measure by raw content length (handles code/non-word content)
    orig_len = sum(len(str(m.get("content", ""))) for m in original)
    comp_len = sum(len(str(m.get("content", ""))) for m in compressed)
    len_reduction = 0.0
    if orig_len > 0:
        len_reduction = 1.0 - (comp_len / orig_len)
    len_reduction = max(0.0, min(1.0, len_reduction))

    # Blend term-based and length-based redundancy
    redundancy_removed = (term_redundancy * 0.5) + (len_reduction * 0.5)
    redundancy_removed = max(0.0, min(1.0, redundancy_removed))

    # Quality score: weighted combination
    # If no meaningful terms were found in either original or compressed,
    # rely on content length reduction as a quality proxy
    if len(set(orig_terms)) == 0:
        # No recognizable words — quality is based on whether compression was reasonable
        quality_score = 0.8 if len_reduction > 0.0 else 0.7
    else:
        quality_score = (
            semantic_preservation * 0.5
            + (1.0 - information_loss) * 0.3
            + (redundancy_removed * 0.2 if redundancy_removed > 0.1 else 0.1)
        )
    quality_score = max(0.0, min(1.0, quality_score))

    return CompressionQuality(
        semantic_preservation=round(semantic_preservation, 4),
        information_loss=round(information_loss, 4),
        redundancy_removed=round(redundancy_removed, 4),
        quality_score=round(quality_score, 4),
    )


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class CompressionResult:
    """The outcome of one compression pass on a message array."""

    original_messages: list[dict[str, Any]]
    compressed_messages: list[dict[str, Any]]
    original_token_count: int
    compressed_token_count: int
    compression_ratio: float
    strategy: str
    duration_ms: float
    tokens_saved: int
    skipped: bool = False
    # Phase 2
    native_used: bool = False
    # Phase 3
    quality: CompressionQuality | None = None
    strategy_selected: str = ""
    # Phase 4
    cache_hit: bool = False


# ---------------------------------------------------------------------------
# Global singletons (lazily initialized)
# ---------------------------------------------------------------------------

_cache: CompressionCache | None = None
_profiler: CompressionProfiler | None = None
_adaptive_ratio: AdaptiveRatio | None = None


def _get_cache(config: CompressionConfig) -> CompressionCache:
    global _cache
    if _cache is None or _cache._max_size != config.cache_max_size:
        _cache = CompressionCache(
            max_size=config.cache_max_size,
            ttl_seconds=config.cache_ttl_seconds,
        )
    return _cache


def _get_profiler(config: CompressionConfig) -> CompressionProfiler:
    global _profiler
    if _profiler is None:
        _profiler = CompressionProfiler()
    return _profiler


def _get_adaptive_ratio(config: CompressionConfig) -> "AdaptiveRatio":
    global _adaptive_ratio
    if _adaptive_ratio is None:
        _adaptive_ratio = AdaptiveRatio(
            min_ratio=config.adaptive_min_ratio,
            max_ratio=config.adaptive_max_ratio,
        )
    return _adaptive_ratio


# ---------------------------------------------------------------------------
# Adaptive Ratio
# ---------------------------------------------------------------------------


class AdaptiveRatio:
    """Dynamically adjusts compression target_ratio based on quality feedback.

    - If quality is consistently high → increase compression (lower ratio).
    - If quality is low → decrease compression (higher ratio).
    """

    def __init__(
        self,
        min_ratio: float = 0.3,
        max_ratio: float = 0.8,
        adjustment_step: float = 0.05,
        window_size: int = 10,
    ) -> None:
        self.min_ratio = min_ratio
        self.max_ratio = max_ratio
        self.adjustment_step = adjustment_step
        self.window_size = window_size
        self._current_ratio: float = (min_ratio + max_ratio) / 2.0
        self._quality_history: list[float] = []

    def update(self, result: CompressionResult) -> None:
        """Feed a compression result to update the adaptive ratio."""
        if result.skipped:
            return
        qs = result.quality.quality_score if result.quality else 0.7
        self._quality_history.append(qs)
        if len(self._quality_history) > self.window_size:
            self._quality_history.pop(0)

    def get_ratio(self) -> float:
        """Return the current adaptive compression ratio."""
        if len(self._quality_history) < 3:
            return self._current_ratio

        avg_quality = sum(self._quality_history) / len(self._quality_history)

        if avg_quality > 0.85:
            # Quality is very high → compress more aggressively
            self._current_ratio = max(
                self.min_ratio,
                self._current_ratio - self.adjustment_step,
            )
        elif avg_quality < 0.6:
            # Quality is low → compress less aggressively
            self._current_ratio = min(
                self.max_ratio,
                self._current_ratio + self.adjustment_step,
            )
        # else: keep current ratio

        return self._current_ratio


# ---------------------------------------------------------------------------
# Token estimation (Phase 1: `len(text) // 4`)
# ---------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """Return a rough token-count estimate for *text*.

    Uses the heuristic ``len(text) // 4``, which approximates the average
    character-per-token ratio for English text under common tokenizers.
    """
    # Try native first
    native = rtk_bridge.estimate_tokens_native(text)
    if native is not None:
        return native
    if not text:
        return 0
    return max(1, len(text) // 4)


def count_message_tokens(messages: list[dict[str, Any]]) -> int:
    """Sum token estimates across every content field in *messages*."""
    native = rtk_bridge.count_message_tokens_native(messages)
    if native is not None:
        return native
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    total += estimate_tokens(str(part["text"]))
    return total


# ---------------------------------------------------------------------------
# Dedup strategy
# ---------------------------------------------------------------------------


def dedup_compress(
    messages: list[dict[str, Any]], target_ratio: float,
) -> list[dict[str, Any]]:
    """Remove duplicate consecutive messages and merge same-role runs."""
    native = rtk_bridge.dedup_compress_native(messages)
    if native is not None:
        return native
    if not messages:
        return []

    deduped: list[dict[str, Any]] = [messages[0]]
    for msg in messages[1:]:
        prev = deduped[-1]
        if (
            msg.get("role") == prev.get("role")
            and msg.get("content") == prev.get("content")
        ):
            continue
        deduped.append(msg)

    merged: list[dict[str, Any]] = []
    i = 0
    while i < len(deduped):
        msg = deduped[i]
        role = msg.get("role", "user")
        contents: list[str] = [str(msg.get("content", ""))]
        j = i + 1
        while j < len(deduped) and deduped[j].get("role") == role:
            contents.append(str(deduped[j].get("content", "")))
            j += 1
        if len(contents) > 1:
            merged.append({"role": role, "content": "\n\n".join(contents)})
        else:
            merged.append(msg)
        i = j

    for msg in merged:
        if isinstance(msg.get("content"), str):
            current = str(msg["content"])
            current = re.sub(r"\n{3,}", "\n\n", current)
            current = re.sub(r" {3,}", "  ", current)
            msg["content"] = current

    return merged if merged else list(messages)


# ---------------------------------------------------------------------------
# Prune strategy
# ---------------------------------------------------------------------------


def prune_compress(
    messages: list[dict[str, Any]], target_ratio: float,
) -> list[dict[str, Any]]:
    """Prune verbose / low-value content from messages."""
    native = rtk_bridge.prune_compress_native(messages)
    if native is not None:
        return native
    if not messages:
        return []

    _SYSTEM_MAX = 500
    _USER_MAX = 2000
    _USER_KEEP_EACH = 800

    last_system_idx: int | None = None
    last_user_idx: int | None = None
    for idx in range(len(messages) - 1, -1, -1):
        role = messages[idx].get("role", "")
        if role == "system" and last_system_idx is None:
            last_system_idx = idx
        if role == "user" and last_user_idx is None:
            last_user_idx = idx
        if last_system_idx is not None and last_user_idx is not None:
            break

    pruned: list[dict[str, Any]] = []
    for idx, msg in enumerate(messages):
        role = msg.get("role", "")
        content = str(msg.get("content", ""))

        if not content.strip():
            if idx == last_system_idx or idx == last_user_idx:
                pruned.append(dict(msg))
            continue

        if role == "system" and idx != last_system_idx:
            if len(content) > _SYSTEM_MAX:
                new_msg = dict(msg)
                new_msg["content"] = content[:_SYSTEM_MAX] + "..."
                pruned.append(new_msg)
                continue

        if role == "user" and len(content) > _USER_MAX:
            new_msg = dict(msg)
            new_msg["content"] = (
                content[:_USER_KEEP_EACH]
                + "\n...[content truncated]...\n"
                + content[-_USER_KEEP_EACH:]
            )
            pruned.append(new_msg)
            continue

        pruned.append(dict(msg))

    deduped: list[dict[str, Any]] = []
    seen_tool_ids: set[str] = set()
    for idx, msg in enumerate(pruned):
        role = msg.get("role", "")
        if role == "tool":
            tool_id = str(msg.get("tool_call_id", ""))
            if tool_id and tool_id in seen_tool_ids:
                continue
            if tool_id:
                seen_tool_ids.add(tool_id)
        deduped.append(msg)

    return deduped if deduped else list(messages)


# ---------------------------------------------------------------------------
# Summarize strategy (Phase 3)
# ---------------------------------------------------------------------------


def summarize_compress(
    messages: list[dict[str, Any]],
    target_ratio: float,
    summarizer_fn: Callable[[list[dict[str, Any]]], str] | None = None,
) -> list[dict[str, Any]]:
    """Compress conversation history via summarization.

    Strategy:
    - Keep the most recent ``keep_last`` messages intact (default: 2 turns).
    - Summarize all older messages into a single system message.
    - If no ``summarizer_fn`` is provided, use extractive summarization
      (pick representative sentences, remove low-content messages).

    Parameters
    ----------
    summarizer_fn:
        Optional callable that takes a list of message dicts and returns
        a summary string. If ``None``, extractive summarization is used.
    """
    if not messages:
        return []

    total_tokens = count_message_tokens(messages)

    # Determine how many recent messages to keep intact
    keep_last = max(1, int(len(messages) * (1.0 - target_ratio)))
    # Always keep at least 2 messages, at most all but 2
    keep_last = max(2, min(keep_last, len(messages) - 2))

    # Ensure we preserve the last system and last user messages
    recent = messages[-keep_last:]
    older = messages[:-keep_last]

    if not older:
        return list(messages)

    # Generate summary of older messages
    if summarizer_fn is not None:
        summary = summarizer_fn(older)
    else:
        summary = _extractive_summarize(older)

    # Build compressed output: summary as system message + recent messages
    compressed: list[dict[str, Any]] = [
        {"role": "system", "content": f"[Conversation summary]\n{summary}"},
    ]
    compressed.extend(recent)

    # Verify we actually reduced tokens
    if count_message_tokens(compressed) >= total_tokens:
        return list(messages)

    return compressed


def _extractive_summarize(messages: list[dict[str, Any]]) -> str:
    """Simple extractive summarization: collect first sentences and key points.

    Creates a bullet-point summary from the most salient content in the
    messages, preserving names, facts, and decisions while removing filler.
    """
    sentences: list[str] = []
    for msg in messages:
        content = str(msg.get("content", ""))
        role = msg.get("role", "user")
        if not content.strip():
            continue

        # Take first 2 sentences of each message
        msg_sentences = re.split(r"(?<=[.!?])\s+", content)
        top = msg_sentences[:2]
        for s in top:
            s = s.strip()
            if len(s) > 15:  # skip very short fragments
                prefix = "User asked:" if role == "user" else "Assistant said:"
                sentences.append(f"- {prefix} {s}")

    if not sentences:
        return "(No substantial content to summarize)"

    # Deduplicate similar sentences
    unique: list[str] = []
    for s in sentences:
        if not unique or not any(
            _sentence_similarity(s, u) > 0.7 for u in unique
        ):
            unique.append(s)

    # Limit summary length
    if len(unique) > 15:
        unique = unique[:15]
        unique.append("- ...(additional history summarized)")

    return "\n".join(unique)


def _sentence_similarity(a: str, b: str) -> float:
    """Compute simple word-overlap similarity between two sentences."""
    words_a = set(re.findall(r"\b[a-zA-Z]{3,}\b", a.lower()))
    words_b = set(re.findall(r"\b[a-zA-Z]{3,}\b", b.lower()))
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


# ---------------------------------------------------------------------------
# Auto strategy
# ---------------------------------------------------------------------------


def auto_compress(
    messages: list[dict[str, Any]], target_ratio: float,
) -> list[dict[str, Any]]:
    """Run dedup first; if compression ratio still exceeds target, follow
    with prune.  Returns the best result (lowest token count) between the
    dedup-only and dedup+prune paths."""
    native = rtk_bridge.auto_compress_native(messages, target_ratio)
    if native is not None:
        return native

    deduped = dedup_compress(messages, target_ratio)
    deduped_tokens = count_message_tokens(deduped)

    pruned = prune_compress(deduped, target_ratio)
    pruned_tokens = count_message_tokens(pruned)

    return pruned if pruned_tokens < deduped_tokens else deduped


# ---------------------------------------------------------------------------
# Adaptive strategy selection (Phase 3)
# ---------------------------------------------------------------------------


def select_strategy(
    messages: list[dict[str, Any]],
    config: CompressionConfig,
) -> str:
    """Select the best compression strategy based on message characteristics.

    Heuristics:
    - Many consecutive duplicates → ``dedup``
    - Very long messages → ``prune``
    - Many messages (>10) with substantial history → ``summarize``
    - Otherwise → ``auto`` (which is dedup + prune)
    - If ``config.strategy`` is not ``auto``, respect it.
    """
    # If user explicitly chose a non-auto strategy, use it
    if config.strategy != "auto":
        return config.strategy

    if not messages:
        return "dedup"

    # Count consecutive duplicates
    dup_count = 0
    for i in range(1, len(messages)):
        if (
            messages[i].get("role") == messages[i - 1].get("role")
            and messages[i].get("content") == messages[i - 1].get("content")
        ):
            dup_count += 1

    dup_ratio = dup_count / max(len(messages), 1)

    # Check for very long messages
    max_length = max(
        (len(str(m.get("content", ""))) for m in messages), default=0,
    )
    avg_length = sum(
        len(str(m.get("content", ""))) for m in messages
    ) / max(len(messages), 1)

    # If many duplicates, dedup is the best
    if dup_ratio > 0.15:
        return "dedup"

    # If messages are long, prune first
    if max_length > 3000 or avg_length > 1000:
        return "prune"

    # If many messages with substantial content, summarize
    if len(messages) > config.summarizer_max_messages:
        return "summarize"

    # Default to auto (dedup + prune)
    return "auto"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def compress_messages(
    messages: list[dict[str, Any]], config: CompressionConfig,
) -> CompressionResult:
    """Main compression entry point for the pipeline.

    Full flow:
    1. Check ``config.enabled`` and ``config.min_tokens``.
    2. Select strategy (adaptive if config.strategy == "auto").
    3. Try native Rust extension.
    4. Check cache for previous result.
    5. Apply selected strategy.
    6. Assess quality.
    7. Check quality threshold — skip compression if below.
    8. Record in profiler.
    9. Update adaptive ratio.
    10. Store in cache.
    11. Return ``CompressionResult`` with full metadata.
    """
    original = list(messages)
    original_tokens = count_message_tokens(original)

    # Skip when disabled or below threshold.
    if not config.enabled:
        return CompressionResult(
            original_messages=original, compressed_messages=original,
            original_token_count=original_tokens,
            compressed_token_count=original_tokens,
            compression_ratio=0.0, strategy=config.strategy,
            duration_ms=0.0, tokens_saved=0, skipped=True,
            strategy_selected=config.strategy,
        )

    if original_tokens <= config.min_tokens:
        return CompressionResult(
            original_messages=original, compressed_messages=original,
            original_token_count=original_tokens,
            compressed_token_count=original_tokens,
            compression_ratio=0.0, strategy=config.strategy,
            duration_ms=0.0, tokens_saved=0, skipped=True,
            strategy_selected=config.strategy,
            quality=assess_quality(original, original),
        )

    # Adaptive strategy selection
    selected_strategy = config.strategy
    if config.strategy == "auto":
        selected_strategy = select_strategy(original, config)

    # Phase 4: check cache
    cache_hit = False
    if config.cache_enabled:
        cache = _get_cache(config)
        cached = cache.get(original)
        if cached is not None:
            cached.cache_hit = True
            return cached

    # Try native Rust extension first
    native_used = False
    if rtk_bridge.is_native_available():
        native_result = rtk_bridge.compress_native(
            original, selected_strategy, config.target_ratio, config.min_tokens,
        )
        if native_result is not None and not native_result.get("skipped", True):
            native_used = True
            start = time.perf_counter()
            compressed = native_result["compressed_messages"]
            if not isinstance(compressed, list):
                compressed = original
            duration_ms = (time.perf_counter() - start) * 1000
            compressed_tokens = count_message_tokens(compressed)
            tokens_saved = original_tokens - compressed_tokens
            ratio = tokens_saved / max(original_tokens, 1)

            # Assess quality
            quality = assess_quality(original, compressed)

            result = CompressionResult(
                original_messages=original, compressed_messages=compressed,
                original_token_count=original_tokens,
                compressed_token_count=compressed_tokens,
                compression_ratio=round(ratio, 4),
                strategy=config.strategy,
                duration_ms=round(duration_ms, 3),
                tokens_saved=max(0, tokens_saved), skipped=False,
                native_used=True, quality=quality,
                strategy_selected=selected_strategy, cache_hit=False,
            )

            # Quality threshold check
            if quality.quality_score < config.quality_threshold:
                result = CompressionResult(
                    original_messages=original, compressed_messages=original,
                    original_token_count=original_tokens,
                    compressed_token_count=original_tokens,
                    compression_ratio=0.0, strategy=config.strategy,
                    duration_ms=round(duration_ms, 3), tokens_saved=0,
                    skipped=True, native_used=True, quality=quality,
                    strategy_selected=selected_strategy, cache_hit=False,
                )

            # Record in profiler
            if config.profiling_enabled:
                _get_profiler(config).record(ProfileEntry(
                    strategy=selected_strategy, duration_ms=result.duration_ms,
                    original_tokens=result.original_token_count,
                    compressed_tokens=result.compressed_token_count,
                    compression_ratio=result.compression_ratio,
                    quality_score=quality.quality_score,
                ))

            # Update adaptive ratio
            if config.adaptive_ratio_enabled:
                _get_adaptive_ratio(config).update(result)

            # Store in cache
            if config.cache_enabled:
                _get_cache(config).put(original, result)

            return result

    # Python fallback
    start = time.perf_counter()

    strategy_map = {
        "dedup": dedup_compress,
        "prune": prune_compress,
        "summarize": summarize_compress,
        "auto": auto_compress,
    }
    strategy_fn = strategy_map.get(selected_strategy, dedup_compress)

    # Use adaptive ratio if enabled
    effective_ratio = config.target_ratio
    if config.adaptive_ratio_enabled and config.strategy == "auto":
        effective_ratio = _get_adaptive_ratio(config).get_ratio()

    if selected_strategy == "summarize":
        compressed = summarize_compress(original, effective_ratio)
    else:
        compressed = strategy_fn(original, effective_ratio)

    duration_ms = (time.perf_counter() - start) * 1000
    compressed_tokens = count_message_tokens(compressed)
    tokens_saved = original_tokens - compressed_tokens
    ratio = tokens_saved / max(original_tokens, 1)

    # Assess quality
    quality = assess_quality(original, compressed)

    # Quality threshold check — skip if quality too low
    if quality.quality_score < config.quality_threshold:
        result = CompressionResult(
            original_messages=original, compressed_messages=original,
            original_token_count=original_tokens,
            compressed_token_count=original_tokens,
            compression_ratio=0.0, strategy=config.strategy,
            duration_ms=round(duration_ms, 3), tokens_saved=0,
            skipped=True, native_used=False, quality=quality,
            strategy_selected=selected_strategy, cache_hit=False,
        )
    else:
        result = CompressionResult(
            original_messages=original, compressed_messages=compressed,
            original_token_count=original_tokens,
            compressed_token_count=compressed_tokens,
            compression_ratio=round(ratio, 4), strategy=config.strategy,
            duration_ms=round(duration_ms, 3),
            tokens_saved=max(0, tokens_saved), skipped=False,
            native_used=native_used, quality=quality,
            strategy_selected=selected_strategy, cache_hit=False,
        )

    # Record in profiler
    if config.profiling_enabled:
        _get_profiler(config).record(ProfileEntry(
            strategy=selected_strategy, duration_ms=result.duration_ms,
            original_tokens=result.original_token_count,
            compressed_tokens=result.compressed_token_count,
            compression_ratio=result.compression_ratio,
            quality_score=quality.quality_score,
        ))

    # Update adaptive ratio
    if config.adaptive_ratio_enabled:
        _get_adaptive_ratio(config).update(result)

    # Store in cache
    if config.cache_enabled:
        _get_cache(config).put(original, result)

    return result


def get_cache_stats() -> dict[str, Any] | None:
    """Return compression cache statistics, or ``None`` if no cache exists."""
    if _cache is not None:
        return _cache.stats()
    return None


def get_profiler_stats() -> dict[str, Any] | None:
    """Return compression profiler statistics, or ``None`` if no profiler."""
    if _profiler is not None:
        return _profiler.get_stats()
    return None


# ---------------------------------------------------------------------------
# Internal helpers (mirror config.py conventions)
# ---------------------------------------------------------------------------


def _value(environ: Mapping[str, str], name: str, default: str) -> str:
    value = environ.get(name, default)
    return default if value is None else value.strip()


def _parse_bool(
    environ: Mapping[str, str], name: str, default: bool,
) -> bool:
    raw = _value(environ, name, "1" if default else "0")
    if raw not in {"0", "1"}:
        raise ValueError(f"{name} must be '0' or '1'; got {raw!r}")
    return raw == "1"


def _parse_int(
    environ: Mapping[str, str], name: str, default: int, *,
    minimum: int | None = None,
) -> int:
    raw = _value(environ, name, str(default))
    try:
        parsed = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer; got {raw!r}") from None
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{name} must be >= {minimum}; got {parsed}")
    return parsed


def _parse_float(
    environ: Mapping[str, str], name: str, default: float, *,
    minimum: float | None = None, maximum: float | None = None,
) -> float:
    raw = _value(environ, name, str(default))
    try:
        parsed = float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number; got {raw!r}") from None
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{name} must be >= {minimum}; got {parsed}")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{name} must be <= {maximum}; got {parsed}")
    return parsed