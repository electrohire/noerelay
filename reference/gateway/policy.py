"""Routing-policy loading and OpenAI-exclusion enforcement for the gateway.

EPR-API-007: the ``openai`` model family, ``openai/`` namespace, OpenAI
upstream endpoints, and automatic model selection MUST be denied by
deterministic policy. This module provides three independent layers:

- ``load_policy`` — load the routing-policy JSON
- ``check_requested_model`` — per-request boundary check on the wire-level
  ``model`` string
- ``validate_portfolio_against_policy`` — startup fail-closed validation of
  every candidate in the portfolio

The kernel's ``_inference_policy_reasons`` (in ``epr.kernel``) provides the
third, unmodified enforcement layer at route-selection time.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_policy(path: Path) -> dict[str, Any]:
    """Load the routing-policy JSON document."""
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def check_requested_model(model: str, policy: dict[str, Any]) -> list[str]:
    """Check a client-requested model string against the routing policy.

    Returns an empty list when the model is the advertised virtual model
    (``noerelay/epr-1``). Returns a list of reason strings when the model
    matches a forbidden family, prefix, id, or host reference.

    The caller is responsible for the 404 case: a model that is neither the
    virtual model nor policy-denied is simply unknown.
    """
    model_lower = model.lower()
    if model_lower == "noerelay/epr-1":
        return []

    inference = policy.get("inference", {})
    reasons: list[str] = []

    forbidden_ids = {
        str(item).lower() for item in inference.get("forbidden_model_ids", [])
    }
    if model_lower in forbidden_ids:
        reasons.append("model_id_denied")

    forbidden_prefixes = tuple(
        str(item).lower() for item in inference.get("forbidden_model_prefixes", [])
    )
    if model_lower.startswith(forbidden_prefixes):
        reasons.append("model_id_denied")

    forbidden_families = {
        str(item).lower() for item in inference.get("forbidden_model_families", [])
    }
    first_segment = model_lower.split("/")[0]
    if first_segment in forbidden_families:
        reasons.append("model_family_denied")

    if "api.openai.com" in model_lower:
        reasons.append("model_id_denied")

    return reasons


def validate_portfolio_against_policy(
    portfolio: list[dict[str, Any]],
    policy: dict[str, Any],
) -> list[str]:
    """Validate every model candidate against the inference policy.

    Returns a list of human-readable error strings (empty when valid). This
    is a startup check: any violation prevents the server from binding.
    """
    errors: list[str] = []
    inference = policy.get("inference", {})

    allowed_gateways = {
        str(item).lower() for item in inference.get("allowed_gateways", [])
    }
    explicit_required = inference.get("explicit_model_id_required", False)
    forbidden_families = {
        str(item).lower() for item in inference.get("forbidden_model_families", [])
    }
    forbidden_ids = {
        str(item).lower() for item in inference.get("forbidden_model_ids", [])
    }
    forbidden_prefixes = tuple(
        str(item).lower() for item in inference.get("forbidden_model_prefixes", [])
    )

    for candidate in portfolio:
        if candidate.get("action_kind") != "model":
            continue

        cid = candidate.get("candidate_id", "?")
        gateway = str(candidate.get("inference_gateway", "")).lower()
        model_id = str(candidate.get("model_id", "")).lower()
        family = str(candidate.get("provider_family", "")).lower()

        if allowed_gateways and gateway not in allowed_gateways:
            errors.append(f"{cid}: inference_gateway_not_allowed ({gateway})")
        if explicit_required and not model_id:
            errors.append(f"{cid}: model_id_required")
        if family in forbidden_families:
            errors.append(f"{cid}: model_family_denied ({family})")
        if model_id in forbidden_ids or (
            model_id and model_id.startswith(forbidden_prefixes)
        ):
            errors.append(f"{cid}: model_id_denied ({model_id})")

    return errors