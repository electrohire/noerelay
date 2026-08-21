"""JSON-structured logger for production use.

Outputs JSON logs with:
- timestamp (ISO 8601)
- level (INFO, WARNING, ERROR, etc.)
- message
- correlation_id (run_id or trace_id)
- component (gateway, pipeline, router, etc.)
- extra fields (model_id, cost, latency, etc.)

Secret redaction: known secret patterns (API keys, tokens) are
automatically redacted from log messages and extra fields.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any

# Patterns for content-aware secret redaction
# Order matters: more specific patterns must come before generic ones.
_SECRET_PATTERNS: list[tuple[str, str]] = [
    # OpenRouter API keys (most specific first)
    (r'sk-or-v1-[a-zA-Z0-9]{32,}', '[REDACTED:OPENROUTER_KEY]'),
    # Hugging Face tokens (before generic sk- to avoid partial match)
    (r'hf_[a-zA-Z0-9]{25,}', '[REDACTED:HF_TOKEN]'),
    # OpenAI-style API keys (dash/underscore allowed, 20+ chars after sk-)
    # Negative lookahead excludes sk-or- which is handled above
    (r'sk-(?!or-)[a-zA-Z0-9\-_]{20,}', '[REDACTED:API_KEY]'),
    # Bearer tokens in Authorization headers
    (r'Bearer\s+[a-zA-Z0-9\-_\.]{20,}', 'Bearer [REDACTED]'),
    # JSON-style "key": "value" for secret fields
    (r'"(api_key|apikey|secret|token|password|passwd)"\s*:\s*"[^"]*"',
     r'"\1": "[REDACTED]"'),
    # Plain key=value for secret fields (no spaces in value)
    (r'\b(api_key|apikey|secret|token|password|passwd)\s*=\s*[^\s,}]+',
     r'\1=[REDACTED]'),
]

_REDACTED_MARKER = "[REDACTED]"


def _redact_secrets(text: str) -> str:
    """Apply all secret patterns to redact sensitive values from a string."""
    for pattern, replacement in _SECRET_PATTERNS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


# Key names that indicate secret values
_SECRET_KEY_PATTERNS = re.compile(
    r'(api[_-]?key|apikey|secret|token|password|passwd|credential)',
    re.IGNORECASE,
)


def _redact_dict(obj: dict[str, Any]) -> dict[str, Any]:
    """Recursively redact secrets from dictionary values.

    Redacts values whose keys match secret patterns, and also
    applies content-based redaction to all string values.
    """
    result: dict[str, Any] = {}
    for key, value in obj.items():
        if isinstance(value, str):
            if _SECRET_KEY_PATTERNS.search(key):
                result[key] = _REDACTED_MARKER
            else:
                result[key] = _redact_secrets(value)
        elif isinstance(value, dict):
            result[key] = _redact_dict(value)
        elif isinstance(value, list):
            result[key] = [
                _redact_secrets(v) if isinstance(v, str) else v for v in value
            ]
        else:
            result[key] = value
    return result


class JsonFormatter(logging.Formatter):
    """JSON log formatter with secret redaction."""

    def format(self, record: logging.LogRecord) -> str:
        if isinstance(record.msg, str) and record.msg.startswith("{"):
            return _redact_secrets(record.msg)  # Already JSON
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "message": _redact_secrets(record.getMessage()),
        }
        # Include any extra fields attached to the record
        for key in ("correlation_id", "component", "model_id", "cost", "latency_ms"):
            if hasattr(record, key):
                val = getattr(record, key)
                if isinstance(val, str):
                    log_entry[key] = _redact_secrets(val)
                else:
                    log_entry[key] = val
        return json.dumps(log_entry, default=str)


class StructuredLogger:
    """JSON-structured logger for production use.

    Outputs JSON logs with timestamp, level, message, correlation_id,
    component, and extra fields.
    """

    def __init__(
        self,
        name: str = "noerelay",
        level: str = "INFO",
        output: str = "stdout",
        file_path: str | None = None,
    ) -> None:
        self._logger = logging.getLogger(name)
        self._logger.setLevel(getattr(logging, level.upper()))
        self._output = output
        self._file_path = file_path

        # Remove existing handlers
        self._logger.handlers.clear()

        if output == "file" and file_path:
            handler: logging.Handler = logging.FileHandler(file_path)
        else:
            handler = logging.StreamHandler(sys.stdout)

        handler.setFormatter(JsonFormatter())
        self._logger.addHandler(handler)

    def info(self, message: str, **kwargs: Any) -> None:
        self._log("INFO", message, **kwargs)

    def warning(self, message: str, **kwargs: Any) -> None:
        self._log("WARNING", message, **kwargs)

    def error(self, message: str, **kwargs: Any) -> None:
        self._log("ERROR", message, **kwargs)

    def debug(self, message: str, **kwargs: Any) -> None:
        self._log("DEBUG", message, **kwargs)

    def _log(self, level: str, message: str, **kwargs: Any) -> None:
        record: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": level,
            "message": _redact_secrets(message),
        }
        record.update(_redact_dict(kwargs))
        self._logger.log(getattr(logging, level), json.dumps(record, default=str))