"""Separate decoding phases for tool calls and schema-constrained reporting.

EPR-VER-006: tool-call generation and schema-constrained reporting SHOULD occur
in separate decoding phases unless the exact model-serving combination has
passed conformance testing.

Phase 1 produces tool calls with native tool calling and no output grammar
constraint.  Phase 2 produces the final report with a ``response_format``
schema constraint and no ``tool_choice`` (so the model cannot issue new tool
calls during reporting).  A model-serving combination present in the
``conformance_tested_combinations`` set is allowed to merge both phases into a
single call.
"""

from __future__ import annotations

from typing import Any

from .openrouter import build_chat_payload


class DecodingPhaseManager:
    """EPR-VER-006: separate decoding phases for tool calls and reporting."""

    def __init__(
        self, conformance_tested: set[tuple[str, str]] | None = None
    ) -> None:
        self._conformance_tested = {
            (str(model), str(serving)) for model, serving in (conformance_tested or set())
        }

    def is_conformance_tested(self, model_id: str, serving_config: str) -> bool:
        """Check if a model-serving combination is conformance-tested."""
        return (str(model_id), str(serving_config)) in self._conformance_tested

    def needs_separate_phases(
        self,
        request: dict[str, Any],
        model_id: str,
        serving_config: str | None = None,
    ) -> bool:
        """Return True when tools are present and the combination is not conformance-tested."""
        passthrough = request.get("passthrough") or {}
        if not passthrough.get("tools"):
            return False
        if serving_config is not None and self.is_conformance_tested(
            model_id, serving_config
        ):
            return False
        return True

    def build_phase1_payload(
        self,
        request: dict[str, Any],
        selected_plan: dict[str, Any],
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        """Phase 1: tool-call generation (tools, no response_format)."""
        payload = build_chat_payload(selected_plan, request, policy)
        payload.pop("response_format", None)
        return payload

    def build_phase2_payload(
        self,
        request: dict[str, Any],
        selected_plan: dict[str, Any],
        policy: dict[str, Any],
        phase1_result: dict[str, Any],
    ) -> dict[str, Any]:
        """Phase 2: schema-constrained reporting (response_format, no tool_choice)."""
        del phase1_result  # reserved for tool-result injection in future phases
        payload = build_chat_payload(selected_plan, request, policy)
        payload.pop("tool_choice", None)
        return payload
