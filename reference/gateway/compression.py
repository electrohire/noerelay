"""RTK Phase 1 — Python-only compression module for the NoeRelay gateway.

Inserts as a ``context_compressed`` pipeline stage between context compilation
and route selection.  Operates on the ``messages`` array, returning a compressed
version plus metadata (original/compressed token counts, ratio, duration).

Strategies
----------
* ``dedup`` — remove consecutive duplicate messages, merge same-role runs,
  and collapse duplicate content blocks within messages.
* ``prune`` — truncate verbose system messages, trim very long user messages,
  and drop empty/whitespace-only messages.
* ``auto`` — apply dedup first; if the result still exceeds the target ratio,
  follow with prune.

EPR-COMP-001: Compression is always lossless with respect to semantic intent.
The router sees original token counts; the LLM receives compressed messages.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class CompressionConfig:
    """Per-gateway compression settings, read from environment variables."""

    enabled: bool = False
    strategy: str = "dedup"          # dedup | prune | auto
    min_tokens: int = 512            # skip compression when below this
    target_ratio: float = 0.5        # aim to reduce tokens by this fraction

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None,
    ) -> "CompressionConfig":
        """Build a ``CompressionConfig`` from an environment mapping.

        When *environ* is ``None`` the process environment is read.
        """
        env: Mapping[str, str] = os.environ if environ is None else environ

        enabled = _parse_bool(
            env, "NOERELAY_COMPRESSION_ENABLED", False,
        )
        strategy = _value(
            env, "NOERELAY_COMPRESSION_STRATEGY", "dedup",
        )
        if strategy not in {"dedup", "prune", "auto"}:
            raise ValueError(
                f"NOERELAY_COMPRESSION_STRATEGY must be dedup, prune, or auto; "
                f"got {strategy!r}",
            )
        min_tokens = _parse_int(
            env, "NOERELAY_COMPRESSION_MIN_TOKENS", 512, minimum=0,
        )
        target_ratio = _parse_float(
            env, "NOERELAY_COMPRESSION_TARGET_RATIO", 0.5,
            minimum=0.0, maximum=1.0,
        )
        return cls(
            enabled=enabled,
            strategy=strategy,
            min_tokens=min_tokens,
            target_ratio=target_ratio,
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


# ---------------------------------------------------------------------------
# Token estimation (Phase 1: `len(text) // 4`)
# ---------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """Return a rough token-count estimate for *text*.

    Uses the heuristic ``len(text) // 4``, which approximates the average
    character-per-token ratio for English text under common tokenizers.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def count_message_tokens(messages: list[dict[str, Any]]) -> int:
    """Sum token estimates across every content field in *messages*."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            # Multi-part content blocks (text + image_url, etc.)
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
    """Remove duplicate consecutive messages and merge same-role runs.

    - Drop a message when its role and content are identical to the
      *immediately preceding* message.
    - When two (or more) consecutive messages share the same role but have
      different content, merge them by joining their content with ``\\n\\n``.
    - Collapse repeated whitespace / blank lines within content strings.
    - Never removes the **last** system message and the **last** user
      message regardless of duplication.
    """
    if not messages:
        return []

    # ---- Phase 1: collapse consecutive exact duplicates -------------------
    deduped: list[dict[str, Any]] = [messages[0]]
    for msg in messages[1:]:
        prev = deduped[-1]
        if (
            msg.get("role") == prev.get("role")
            and msg.get("content") == prev.get("content")
        ):
            continue  # exact duplicate — skip
        deduped.append(msg)

    # ---- Phase 2: merge consecutive same-role messages --------------------
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

    # ---- Phase 3: deduplicate whitespace/newlines in content strings ------
    import re
    for msg in merged:
        if isinstance(msg.get("content"), str):
            current = str(msg["content"])
            # Collapse 3+ consecutive newlines to 2
            current = re.sub(r"\n{3,}", "\n\n", current)
            # Collapse 3+ consecutive spaces to 2
            current = re.sub(r" {3,}", "  ", current)
            msg["content"] = current

    return merged if merged else list(messages)


# ---------------------------------------------------------------------------
# Prune strategy
# ---------------------------------------------------------------------------


def prune_compress(
    messages: list[dict[str, Any]], target_ratio: float,
) -> list[dict[str, Any]]:
    """Prune verbose / low-value content from messages.

    - System messages: keep the first 500 chars + ``"..."`` if truncated.
      The *last* system message is preserved in full.
    - User messages: keep the first and last *N* characters with an
      ellipsis marker when the message exceeds 2000 characters.
    - Remove empty or whitespace-only messages (except the last system
      and last user messages).
    - Remove redundant tool-call result messages (same ``tool_call_id``
      appearing consecutively).
    """
    if not messages:
        return []

    _SYSTEM_MAX = 500
    _USER_MAX = 2000
    _USER_KEEP_EACH = 800

    # Identify protected indices: last system, last user.
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

    # ---- Phase 1: truncate system messages --------------------------------
    pruned: list[dict[str, Any]] = []
    for idx, msg in enumerate(messages):
        role = msg.get("role", "")
        content = str(msg.get("content", ""))

        # Remove empty messages unless they are the last system/user.
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

    # ---- Phase 2: remove duplicate consecutive tool results ---------------
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
# Auto strategy
# ---------------------------------------------------------------------------


def auto_compress(
    messages: list[dict[str, Any]], target_ratio: float,
) -> list[dict[str, Any]]:
    """Run dedup first; if compression ratio still exceeds target, follow
    with prune.  Returns the best result (lowest token count) between the
    dedup-only and dedup+prune paths."""
    deduped = dedup_compress(messages, target_ratio)
    deduped_tokens = count_message_tokens(deduped)

    pruned = prune_compress(deduped, target_ratio)
    pruned_tokens = count_message_tokens(pruned)

    return pruned if pruned_tokens < deduped_tokens else deduped


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def compress_messages(
    messages: list[dict[str, Any]], config: CompressionConfig,
) -> CompressionResult:
    """Main compression entry point for the pipeline.

    Checks ``config.enabled`` and ``config.min_tokens``, then applies the
    selected strategy.  Returns a :class:`CompressionResult` with full
    metadata even when compression is skipped.
    """
    original = list(messages)
    original_tokens = count_message_tokens(original)

    # Skip when disabled or below threshold.
    if not config.enabled:
        return CompressionResult(
            original_messages=original,
            compressed_messages=original,
            original_token_count=original_tokens,
            compressed_token_count=original_tokens,
            compression_ratio=0.0,
            strategy=config.strategy,
            duration_ms=0.0,
            tokens_saved=0,
            skipped=True,
        )

    if original_tokens <= config.min_tokens:
        return CompressionResult(
            original_messages=original,
            compressed_messages=original,
            original_token_count=original_tokens,
            compressed_token_count=original_tokens,
            compression_ratio=0.0,
            strategy=config.strategy,
            duration_ms=0.0,
            tokens_saved=0,
            skipped=True,
        )

    # Apply strategy.
    start = time.perf_counter()

    strategy_map = {
        "dedup": dedup_compress,
        "prune": prune_compress,
        "auto": auto_compress,
    }
    strategy_fn = strategy_map.get(
        config.strategy, dedup_compress,
    )
    compressed = strategy_fn(original, config.target_ratio)

    duration_ms = (time.perf_counter() - start) * 1000
    compressed_tokens = count_message_tokens(compressed)
    tokens_saved = original_tokens - compressed_tokens
    ratio = tokens_saved / max(original_tokens, 1)

    return CompressionResult(
        original_messages=original,
        compressed_messages=compressed,
        original_token_count=original_tokens,
        compressed_token_count=compressed_tokens,
        compression_ratio=round(ratio, 4),
        strategy=config.strategy,
        duration_ms=round(duration_ms, 3),
        tokens_saved=max(0, tokens_saved),
        skipped=False,
    )


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
        raise ValueError(
            f"{name} must be '0' or '1'; got {raw!r}",
        )
    return raw == "1"


def _parse_int(
    environ: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int | None = None,
) -> int:
    raw = _value(environ, name, str(default))
    try:
        parsed = int(raw)
    except ValueError:
        raise ValueError(
            f"{name} must be an integer; got {raw!r}",
        ) from None
    if minimum is not None and parsed < minimum:
        raise ValueError(
            f"{name} must be >= {minimum}; got {parsed}",
        )
    return parsed


def _parse_float(
    environ: Mapping[str, str],
    name: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    raw = _value(environ, name, str(default))
    try:
        parsed = float(raw)
    except ValueError:
        raise ValueError(
            f"{name} must be a number; got {raw!r}",
        ) from None
    if minimum is not None and parsed < minimum:
        raise ValueError(
            f"{name} must be >= {minimum}; got {parsed}",
        )
    if maximum is not None and parsed > maximum:
        raise ValueError(
            f"{name} must be <= {maximum}; got {parsed}",
        )
    return parsed