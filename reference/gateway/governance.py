"""Deterministic governance metadata handling for the NoeRelay gateway.

EPR-API-003: governance metadata is optional; a deterministic default policy
applies when it is absent. This module provides:

- ``default_governance`` — the documented default profile
- ``merge_governance`` — per-field override over the defaults
- ``validate_governance`` — dependency-free validation of the merged result

The default profile and validation mirror the ``governance`` component schema in
``spec/openapi.json``. See ``docs/gateway.md`` (implementation phase 10) for the
normative table of defaults.
"""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

# Fixed defaults (documented in docs/gateway.md).
DEFAULT_RISK_CLASS = "low"
DEFAULT_DATA_POLICY = "zdr"
DEFAULT_RETENTION_CLASS = "ephemeral"
DEFAULT_RETURN_EVIDENCE_RECEIPT = True

RISK_CLASSES = {"low", "medium", "high", "critical"}
DATA_POLICIES = {"standard", "no_training", "zdr", "local_only"}
RETENTION_CLASSES = {"ephemeral", "project", "regulated"}

GOVERNANCE_FIELDS = frozenset(
    {
        "project_id",
        "risk_class",
        "max_cost_usd",
        "max_latency_ms",
        "required_acceptance_probability",
        "data_policy",
        "retention_class",
        "return_evidence_receipt",
        "canary",
    }
)


def default_governance(*, max_cost_usd: float, max_latency_ms: int) -> dict[str, Any]:
    """Return the deterministic default governance profile.

    ``max_cost_usd`` and ``max_latency_ms`` are the configurable ceilings from
    ``GatewayConfig`` (``NOERELAY_DEFAULT_MAX_COST_USD`` /
    ``NOERELAY_DEFAULT_MAX_LATENCY_MS``). All other defaults are fixed.
    """
    return {
        "risk_class": DEFAULT_RISK_CLASS,
        "data_policy": DEFAULT_DATA_POLICY,
        "max_cost_usd": max_cost_usd,
        "max_latency_ms": max_latency_ms,
        "retention_class": DEFAULT_RETENTION_CLASS,
        "return_evidence_receipt": DEFAULT_RETURN_EVIDENCE_RECEIPT,
        "canary": False,
    }


def merge_governance(
    request_governance: Mapping[str, Any] | None,
    defaults: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge request-supplied governance over the defaults, field by field.

    Absent request fields keep their defaults; the merge is pure and
    deterministic (identical inputs produce an identical merged dict). The
    ``defaults`` mapping is not mutated.
    """
    merged = dict(defaults)
    if request_governance:
        merged.update(request_governance)
    return merged


def validate_governance(governance: Mapping[str, Any]) -> list[str]:
    """Return a list of human-readable validation errors (empty when valid)."""
    errors: list[str] = []

    if not isinstance(governance, Mapping):
        return ["governance_must_be_an_object"]

    unknown = sorted(set(governance) - GOVERNANCE_FIELDS)
    if unknown:
        errors.append("unknown_governance_fields:" + ",".join(unknown))

    if "project_id" in governance and not isinstance(governance["project_id"], str):
        errors.append("project_id_must_be_string")

    risk_class = governance.get("risk_class")
    if risk_class is None:
        errors.append("risk_class_required")
    elif risk_class not in RISK_CLASSES:
        errors.append(f"risk_class_invalid:{risk_class}")

    data_policy = governance.get("data_policy")
    if data_policy is None:
        errors.append("data_policy_required")
    elif data_policy not in DATA_POLICIES:
        errors.append(f"data_policy_invalid:{data_policy}")

    retention_class = governance.get("retention_class")
    if retention_class is not None and retention_class not in RETENTION_CLASSES:
        errors.append(f"retention_class_invalid:{retention_class}")

    if "max_cost_usd" not in governance:
        errors.append("max_cost_usd_required")
    else:
        cost = governance["max_cost_usd"]
        if isinstance(cost, bool) or not isinstance(cost, (int, float)):
            errors.append("max_cost_usd_must_be_number")
        elif not math.isfinite(cost):
            errors.append("max_cost_usd_must_be_finite")
        elif cost <= 0:
            errors.append("max_cost_usd_must_be_positive")

    if "max_latency_ms" not in governance:
        errors.append("max_latency_ms_required")
    else:
        latency = governance["max_latency_ms"]
        if isinstance(latency, bool) or not isinstance(latency, int):
            errors.append("max_latency_ms_must_be_integer")
        elif latency <= 0:
            errors.append("max_latency_ms_must_be_positive")

    if "required_acceptance_probability" in governance:
        probability = governance["required_acceptance_probability"]
        if isinstance(probability, bool) or not isinstance(probability, (int, float)):
            errors.append("required_acceptance_probability_must_be_number")
        elif not math.isfinite(probability):
            errors.append("required_acceptance_probability_must_be_finite")
        elif not (0 <= probability <= 1):
            errors.append("required_acceptance_probability_out_of_range")

    if "return_evidence_receipt" in governance:
        flag = governance["return_evidence_receipt"]
        if not isinstance(flag, bool):
            errors.append("return_evidence_receipt_must_be_boolean")

    if "canary" in governance:
        flag = governance["canary"]
        if not isinstance(flag, bool):
            errors.append("canary_must_be_boolean")

    return errors
