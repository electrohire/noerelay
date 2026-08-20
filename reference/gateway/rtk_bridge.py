"""RTK Bridge — Python↔Rust bridging with automatic fallback.

Tries to import the native Rust extension (``noerelay_rtk``). If available, uses
native functions for maximum performance. If not installed (e.g., during development
without a Rust toolchain), falls back to the existing Python implementations in
``compression.py``.

Provides a unified interface:
- ``is_native_available()`` — check if the Rust extension is loaded
- ``compress_native(messages, strategy, target_ratio, min_tokens)`` — unified entry
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Try to import the native Rust extension
# ---------------------------------------------------------------------------

_NATIVE_AVAILABLE = False
_native_module: Any = None

try:
    import noerelay_rtk as _native_module  # type: ignore[import-untyped]
    _NATIVE_AVAILABLE = True
    logger.debug("RTK: native Rust extension loaded (version=%s)", getattr(_native_module, "__version__", "unknown"))
except ImportError:
    logger.debug("RTK: Rust extension not available, using Python fallback")


def is_native_available() -> bool:
    """Return ``True`` if the native Rust extension is loaded and usable."""
    return _NATIVE_AVAILABLE


def get_native_version() -> str | None:
    """Return the native extension version string, or ``None``."""
    if _native_module is not None:
        return getattr(_native_module, "__version__", None)
    return None


# ---------------------------------------------------------------------------
# Native function wrappers
# ---------------------------------------------------------------------------


def estimate_tokens_native(text: str) -> int | None:
    """Estimate tokens using the Rust implementation.

    Returns ``None`` if the native extension is not available.
    """
    if not _NATIVE_AVAILABLE or _native_module is None:
        return None
    try:
        return int(_native_module.estimate_tokens_rust(text))
    except Exception as exc:
        logger.warning("RTK native estimate_tokens failed: %s", exc)
        return None


def count_message_tokens_native(messages: list[dict[str, Any]]) -> int | None:
    """Count tokens across messages using the Rust implementation.

    Returns ``None`` if the native extension is not available.
    """
    if not _NATIVE_AVAILABLE or _native_module is None:
        return None
    try:
        # Convert messages to the format the Rust extension expects
        import json
        messages_json = json.loads(json.dumps({"messages": messages}))
        result = _native_module.count_message_tokens_rust(messages_json)
        return int(result)
    except Exception as exc:
        logger.warning("RTK native count_message_tokens failed: %s", exc)
        return None


def dedup_compress_native(messages: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Deduplicate messages using the Rust implementation.

    Returns ``None`` if the native extension is not available.
    """
    if not _NATIVE_AVAILABLE or _native_module is None:
        return None
    try:
        import json
        messages_json = json.loads(json.dumps({"messages": messages}))
        result = _native_module.dedup_compress_rust(messages_json)
        return result.get("messages", messages) if isinstance(result, dict) else messages
    except Exception as exc:
        logger.warning("RTK native dedup_compress failed: %s", exc)
        return None


def prune_compress_native(messages: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Prune messages using the Rust implementation.

    Returns ``None`` if the native extension is not available.
    """
    if not _NATIVE_AVAILABLE or _native_module is None:
        return None
    try:
        import json
        messages_json = json.loads(json.dumps({"messages": messages}))
        result = _native_module.prune_compress_rust(messages_json)
        return result.get("messages", messages) if isinstance(result, dict) else messages
    except Exception as exc:
        logger.warning("RTK native prune_compress failed: %s", exc)
        return None


def auto_compress_native(messages: list[dict[str, Any]], target_ratio: float) -> list[dict[str, Any]] | None:
    """Auto compress (dedup+prune) using the Rust implementation.

    Returns ``None`` if the native extension is not available.
    """
    if not _NATIVE_AVAILABLE or _native_module is None:
        return None
    try:
        import json
        messages_json = json.loads(json.dumps({"messages": messages}))
        result = _native_module.auto_compress_rust(messages_json, float(target_ratio))
        return result.get("messages", messages) if isinstance(result, dict) else messages
    except Exception as exc:
        logger.warning("RTK native auto_compress failed: %s", exc)
        return None


def compress_native(
    messages: list[dict[str, Any]],
    strategy: str,
    target_ratio: float,
    min_tokens: int,
) -> dict[str, Any] | None:
    """Main compression entry point using the Rust native extension.

    Returns a dict with the same keys as ``CompressionResult``, or ``None``
    if the native extension is not available.

    Result keys: original_messages, compressed_messages, original_token_count,
    compressed_token_count, compression_ratio, strategy, duration_ms,
    tokens_saved, skipped.
    """
    if not _NATIVE_AVAILABLE or _native_module is None:
        return None
    try:
        import json
        messages_json = json.loads(json.dumps({"messages": messages}))
        result = _native_module.compress_messages_rust(
            messages_json, strategy, float(target_ratio), int(min_tokens),
        )
        if isinstance(result, dict):
            # Unwrap the "messages" key back into a list
            if "original_messages" in result and isinstance(result["original_messages"], dict):
                result["original_messages"] = result["original_messages"].get("messages", messages)
            if "compressed_messages" in result and isinstance(result["compressed_messages"], dict):
                result["compressed_messages"] = result["compressed_messages"].get("messages", messages)
            # Convert numpy-style ints to plain Python ints
            for key in ("original_token_count", "compressed_token_count", "tokens_saved"):
                if key in result:
                    result[key] = int(result[key])
            for key in ("compression_ratio", "duration_ms"):
                if key in result:
                    result[key] = float(result[key])
            if "skipped" in result:
                result["skipped"] = bool(result["skipped"])
            return result
        return None
    except Exception as exc:
        logger.warning("RTK native compress_messages failed: %s", exc)
        return None