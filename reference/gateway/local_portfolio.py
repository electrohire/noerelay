"""Local model portfolio definitions.

Local models are free (no API cost) and have lower latency but may have lower
quality.  The flattened entries below are the canonical local model catalog;
``local_candidates()`` converts them to the candidate-action shape understood
by :func:`epr.kernel.select_route` so they can be merged with the cloud
portfolio.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

LOCAL_MODELS: list[dict[str, Any]] = [
    {
        "candidate_id": "qwen3-8b-local",
        "action_kind": "model",
        "model_id": "qwen3:8b",
        "provider_family": "qwen",
        "inference_gateway": "local",
        "base_url": "http://127.0.0.1:11434",
        "call_cost_usd": 0.0,
        "tool_cost_usd": 0.0,
        "verification_cost_usd": 0.0,
        "expected_retry_cost_usd": 0.0,
        "expected_fallback_cost_usd": 0.001,
        "infrastructure_cost_usd": 0.0,
        "expected_human_review_cost_usd": 0.0,
        "expected_total_cost_usd": 0.001,
        "latency_ms_p95": 3000,
        "acceptance_lcb": 0.80,
        "available": True,
        "required_capabilities": ["text", "tool_calling"],
        "data_policy": "local_only",
        "is_local": True,
        "model_size": "8B",
        "specialization": "general",
    },
    {
        "candidate_id": "qwen3-coder-30b-local",
        "action_kind": "model",
        "model_id": "qwen3-coder:30b",
        "provider_family": "qwen",
        "inference_gateway": "local",
        "base_url": "http://127.0.0.1:11434",
        "call_cost_usd": 0.0,
        "tool_cost_usd": 0.0,
        "verification_cost_usd": 0.0,
        "expected_retry_cost_usd": 0.0,
        "expected_fallback_cost_usd": 0.001,
        "infrastructure_cost_usd": 0.0,
        "expected_human_review_cost_usd": 0.0,
        "expected_total_cost_usd": 0.001,
        "latency_ms_p95": 5000,
        "acceptance_lcb": 0.85,
        "available": True,
        "required_capabilities": ["text", "tool_calling", "structured_output"],
        "data_policy": "local_only",
        "is_local": True,
        "model_size": "30B",
        "specialization": "coding",
    },
    {
        "candidate_id": "qwen3-vl-8b-local",
        "action_kind": "model",
        "model_id": "qwen3-vl:8b-thinking",
        "provider_family": "qwen",
        "inference_gateway": "local",
        "base_url": "http://127.0.0.1:11434",
        "call_cost_usd": 0.0,
        "tool_cost_usd": 0.0,
        "verification_cost_usd": 0.0,
        "expected_retry_cost_usd": 0.0,
        "expected_fallback_cost_usd": 0.001,
        "infrastructure_cost_usd": 0.0,
        "expected_human_review_cost_usd": 0.0,
        "expected_total_cost_usd": 0.001,
        "latency_ms_p95": 4000,
        "acceptance_lcb": 0.80,
        "available": True,
        "required_capabilities": ["text", "vision", "tool_calling"],
        "data_policy": "local_only",
        "is_local": True,
        "model_size": "8B",
        "specialization": "vision",
    },
    {
        "candidate_id": "qwen38-4b-distilled-local",
        "action_kind": "model",
        "model_id": "qwen38-4b-distilled:latest",
        "provider_family": "qwen",
        "inference_gateway": "local",
        "base_url": "http://127.0.0.1:11434",
        "call_cost_usd": 0.0,
        "tool_cost_usd": 0.0,
        "verification_cost_usd": 0.0,
        "expected_retry_cost_usd": 0.0,
        "expected_fallback_cost_usd": 0.0005,
        "infrastructure_cost_usd": 0.0,
        "expected_human_review_cost_usd": 0.0,
        "expected_total_cost_usd": 0.0005,
        "latency_ms_p95": 1000,
        "acceptance_lcb": 0.70,
        "available": True,
        "required_capabilities": ["text"],
        "data_policy": "local_only",
        "is_local": True,
        "model_size": "4B",
        "specialization": "fast_general",
    },
]


def get_local_models() -> list[dict[str, Any]]:
    """Return local model portfolio entries."""
    return [dict(m) for m in LOCAL_MODELS]


def get_local_model_by_id(model_id: str) -> dict[str, Any] | None:
    """Find a local model by its ``model_id``."""
    for m in LOCAL_MODELS:
        if m["model_id"] == model_id:
            return dict(m)
    return None


def discover_local_models(
    base_url: str = "http://127.0.0.1:11434", timeout: int = 5
) -> list[str]:
    """Discover available local model IDs from the Ollama server.

    Queries ``/v1/models`` and returns a list of model ``id`` strings.
    Returns an empty list on any error (server not running, timeout, etc.).
    """
    try:
        url = f"{base_url.rstrip('/')}/v1/models"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return [m.get("id", "") for m in data.get("data", []) if m.get("id")]
    except Exception:
        return []


# All data policies that local models satisfy (they never send data externally).
_ALL_LOCAL_DATA_POLICIES = ["local_only", "zdr", "no_training", "standard"]


def local_candidates(data_policy: str = "local_only") -> list[dict[str, Any]]:
    """Return local models in kernel-compatible candidate-action shape.

    The flattened catalog uses ``required_capabilities``, ``data_policy``, and
    ``acceptance_lcb``.  :func:`epr.kernel.select_route` expects
    ``capabilities``, ``data_policies``, a nested ``acceptance`` object, and a
    nested ``costs`` object, so the conversion here makes local models directly
    mergeable with the cloud portfolio.

    Local models satisfy ALL data policies (they never send data to an external
    service), so ``data_policies`` is set to the full set.  The *data_policy*
    parameter sets the singular ``data_policy`` field for documentation.
    """
    candidates: list[dict[str, Any]] = []
    for m in LOCAL_MODELS:
        acceptance_lcb = float(m.get("acceptance_lcb", 0.0))
        candidates.append(
            {
                "candidate_id": m["candidate_id"],
                "action_kind": "model",
                "inference_gateway": m.get("inference_gateway", "local"),
                "model_id": m.get("model_id"),
                "provider_family": m.get("provider_family"),
                "roles": ["execute"],
                "available": bool(m.get("available", False)),
                "capabilities": list(m.get("required_capabilities", [])),
                "data_policies": list(_ALL_LOCAL_DATA_POLICIES),
                "data_policy": data_policy,
                "acceptance": {
                    "estimate": acceptance_lcb,
                    "lower_bound": acceptance_lcb,
                    "confidence_level": 0.95,
                },
                "latency_ms_p95": int(m.get("latency_ms_p95", 0)),
                "costs": {
                    "call_cost_usd": float(m.get("call_cost_usd", 0.0)),
                    "tool_cost_usd": float(m.get("tool_cost_usd", 0.0)),
                    "verification_cost_usd": float(
                        m.get("verification_cost_usd", 0.0)
                    ),
                    "expected_retry_cost_usd": float(
                        m.get("expected_retry_cost_usd", 0.0)
                    ),
                    "expected_fallback_cost_usd": float(
                        m.get("expected_fallback_cost_usd", 0.0)
                    ),
                    "infrastructure_cost_usd": float(
                        m.get("infrastructure_cost_usd", 0.0)
                    ),
                    "expected_human_review_cost_usd": float(
                        m.get("expected_human_review_cost_usd", 0.0)
                    ),
                },
                "is_local": True,
            }
        )
    return candidates