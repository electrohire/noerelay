"""JSON-structured logger for production use.

Outputs JSON logs with:
- timestamp (ISO 8601)
- level (INFO, WARNING, ERROR, etc.)
- message
- correlation_id (run_id or trace_id)
- component (gateway, pipeline, router, etc.)
- extra fields (model_id, cost, latency, etc.)
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class JsonFormatter(logging.Formatter):
    """JSON log formatter."""

    def format(self, record: logging.LogRecord) -> str:
        if isinstance(record.msg, str) and record.msg.startswith("{"):
            return record.msg  # Already JSON
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        # Include any extra fields attached to the record
        for key in ("correlation_id", "component", "model_id", "cost", "latency_ms"):
            if hasattr(record, key):
                log_entry[key] = getattr(record, key)
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
            "message": message,
        }
        record.update(kwargs)
        self._logger.log(getattr(logging, level), json.dumps(record, default=str))