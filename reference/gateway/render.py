"""OpenAI-compatible response and error envelope renderers.

Includes the Responses API adapter (EPR-API-001 "or documented adapter").

EPR-API-002: the ``model`` field on success responses echoes the advertised
virtual model id. The selected upstream route is ledger-bound, never
response-bound (``eprMetadata`` and the receipt schema are closed).
"""

from __future__ import annotations

import time
import uuid
from typing import Any

VIRTUAL_MODEL_ID = "noerelay/epr-1"


def error_envelope(
    message: str,
    type: str = "invalid_request_error",
    param: str | None = None,
    code: str | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"message": message, "type": type}
    if param is not None:
        error["param"] = param
    if code is not None:
        error["code"] = code
    return {"error": error}


def render_models_list() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": VIRTUAL_MODEL_ID,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "noerelay",
            }
        ],
    }


def render_epr_metadata(
    *,
    run_id: str,
    trace_id: str,
    status: str,
    route_decision_id: str,
    external_base_url: str,
    ledger_head_hash: str,
    total_cost_usd: float,
    actual_cost_usd: float = 0.0,
    total_tokens: int = 0,
    latency_ms: float = 0.0,
    provider_fallback_count: int = 0,
    semantic_fallback_count: int = 0,
    required_human_intervention: bool = False,
    required_rework: bool = False,
    cache_hit: bool = False,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "trace_id": trace_id,
        "status": status,
        "route_decision_id": route_decision_id,
        "evidence_receipt_url": f"{external_base_url}/v1/epr/runs/{run_id}",
        "ledger_head_hash": ledger_head_hash,
        "total_cost_usd": total_cost_usd,
        "actual_cost_usd": actual_cost_usd,
        "total_tokens": total_tokens,
        "latency_ms": latency_ms,
        "provider_fallback_count": provider_fallback_count,
        "semantic_fallback_count": semantic_fallback_count,
        "required_human_intervention": required_human_intervention,
        "required_rework": required_rework,
        "cache_hit": cache_hit,
    }


def render_chat_completion(
    *,
    run_id: str,
    content: str,
    epr: dict[str, Any],
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if usage is None:
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": VIRTUAL_MODEL_ID,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        },
        "epr": epr,
    }


def render_validation_error(
    run_id: str,
    trace_id: str,
    ledger_head_hash: str,
    errors: list[str],
) -> dict[str, Any]:
    return {
        "error": {
            "message": "; ".join(errors),
            "type": "governance_validation_error",
            "param": "governance",
            "code": "governance_invalid",
        },
        "epr": {
            "run_id": run_id,
            "trace_id": trace_id,
            "ledger_head_hash": ledger_head_hash,
        },
    }


def render_responses_object(
    *,
    run_id: str,
    content: str,
    epr: dict[str, Any],
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if usage is None:
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    return {
        "id": f"resp-{uuid.uuid4().hex}",
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": VIRTUAL_MODEL_ID,
        "output": [
            {
                "type": "message",
                "id": f"msg-{uuid.uuid4().hex}",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": content}],
            }
        ],
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        },
        "epr": epr,
    }


def render_clarification_error(
    run_id: str,
    trace_id: str,
    ledger_head_hash: str,
    contract: dict[str, Any],
) -> dict[str, Any]:
    """Render the fail-closed body for EPR-CON-003 clarification."""
    return {
        "error": {
            "message": (
                "High/critical-risk work requires usable acceptance criteria "
                "before autonomous execution."
            ),
            "type": "clarification_required_error",
            "param": "acceptance_criteria",
            "code": "clarification_required",
        },
        "epr": {
            "run_id": run_id,
            "trace_id": trace_id,
            "ledger_head_hash": ledger_head_hash,
            "task_id": contract.get("task_id"),
            "risk_class": contract.get("risk_class"),
        },
    }


def render_escalation_error(
    run_id: str,
    trace_id: str,
    ledger_head_hash: str,
    decision: dict[str, Any],
) -> dict[str, Any]:
    audit = decision.get("candidate_audit", [])
    return {
        "error": {
            "message": decision.get("explanation", "No admissible route exists."),
            "type": "no_admissible_route_error",
            "param": None,
            "code": "no_admissible_route",
        },
        "epr": {
            "run_id": run_id,
            "trace_id": trace_id,
            "ledger_head_hash": ledger_head_hash,
            "route_decision": {
                "decision_id": decision.get("decision_id"),
                "status": decision.get("status"),
                "explanation": decision.get("explanation"),
                "required_acceptance_lcb": decision.get("required_acceptance_lcb"),
                "candidates_evaluated": len(audit),
                "candidates_admissible": sum(
                    1 for entry in audit if entry.get("admissible")
                ),
            },
        },
    }


def render_stream_chunk(
    *,
    stream_id: str,
    created: int,
    delta: dict[str, Any] | None = None,
    finish_reason: str | None = None,
    choices: list[dict[str, Any]] | None = None,
    epr: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Render a single OpenAI-compatible chat.completion.chunk (EPR-API-004)."""
    if choices is None:
        choices = [
            {
                "index": 0,
                "delta": delta if delta is not None else {},
                "finish_reason": finish_reason,
            }
        ]
    chunk: dict[str, Any] = {
        "id": stream_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": VIRTUAL_MODEL_ID,
        "choices": choices,
    }
    if epr is not None:
        chunk["epr"] = epr
    return chunk


def render_stream_error_chunk(
    error: dict[str, Any], epr: dict[str, Any]
) -> dict[str, Any]:
    """Render an SSE error chunk carrying the ``epr`` run metadata."""
    return {"error": error, "epr": epr}
