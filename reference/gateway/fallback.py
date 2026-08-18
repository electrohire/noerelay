"""EPR-ROUTE-005: separate fallback-event recording.

Provider-availability fallbacks (transport-level) and semantic-quality
fallbacks (model-quality-level) are recorded in distinct lists so the ``epr``
metadata block can report accurate, separate counts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class FallbackRecorder:
    """EPR-ROUTE-005: records provider and semantic fallbacks separately."""

    def __init__(self) -> None:
        self._provider_fallbacks: list[dict[str, Any]] = []
        self._semantic_fallbacks: list[dict[str, Any]] = []
        self._capability_fallbacks: list[dict[str, Any]] = []
        self._other_fallbacks: list[dict[str, Any]] = []

    def record(
        self, fallback_class: str, from_id: str, to_id: str, reason: str
    ) -> dict[str, Any]:
        """Record a fallback event, routing it to the correct per-class list.

        ``provider_fallback`` events carry ``from_provider``/``to_provider``;
        all other classes carry ``from_model``/``to_model``.  Every event also
        keeps the generic ``from_id``/``to_id`` pair for ledger payloads.
        """
        event: dict[str, Any] = {
            "fallback_class": fallback_class,
            "from_id": from_id,
            "to_id": to_id,
            "reason": reason,
            "timestamp": _now(),
        }
        if fallback_class == "provider_fallback":
            event["from_provider"] = from_id
            event["to_provider"] = to_id
            self._provider_fallbacks.append(event)
        elif fallback_class == "semantic_fallback":
            event["from_model"] = from_id
            event["to_model"] = to_id
            self._semantic_fallbacks.append(event)
        elif fallback_class == "capability_fallback":
            event["from_model"] = from_id
            event["to_model"] = to_id
            self._capability_fallbacks.append(event)
        else:
            event["from_model"] = from_id
            event["to_model"] = to_id
            self._other_fallbacks.append(event)
        return event

    def get_provider_fallbacks(self) -> list[dict[str, Any]]:
        """Return provider-availability fallbacks only."""
        return list(self._provider_fallbacks)

    def get_semantic_fallbacks(self) -> list[dict[str, Any]]:
        """Return semantic-quality fallbacks only."""
        return list(self._semantic_fallbacks)

    def get_capability_fallbacks(self) -> list[dict[str, Any]]:
        """Return capability fallbacks only."""
        return list(self._capability_fallbacks)

    def get_other_fallbacks(self) -> list[dict[str, Any]]:
        """Return epistemic/policy/specification fallbacks."""
        return list(self._other_fallbacks)

    def get_fallback_summary(self) -> dict[str, Any]:
        """Return the fallback summary for the ``epr`` metadata block."""
        return {
            "provider_fallback_count": len(self._provider_fallbacks),
            "semantic_fallback_count": len(self._semantic_fallbacks),
        }

    def get_summary(self) -> dict[str, Any]:
        """Alias for :meth:`get_fallback_summary`."""
        return self.get_fallback_summary()
