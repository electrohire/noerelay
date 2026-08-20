"""Pure, socket-free HTTP handlers for the gateway.

Each handler accepts already-decoded inputs and returns ``(status, body_dict)``.
They contain the protocol-plane validation; the pipeline performs the
contract/routing/execution work.
"""

from __future__ import annotations

import os
from typing import Any

from .model_lifecycle import (
    HuggingFaceModelDiscovery,
    ModelPerformanceAnalyzer,
    OllamaModelManager,
    OpenRouterModelDiscovery,
)
from .pipeline import PipelineContext, PipelineError, run_inference_pipeline
from .policy import check_requested_model
from .render import VIRTUAL_MODEL_ID, error_envelope, render_models_list
from .streaming import SSEStreamer, StreamResponse

_CHAT_HANDLED_KEYS = {"model", "messages", "governance", "stream"}
_RESPONSES_HANDLED_KEYS = {"model", "input", "instructions", "governance", "stream"}


def _normalize_responses_input(
    input_value: Any, instructions: str | None
) -> list[dict[str, Any]] | None:
    messages: list[dict[str, Any]] = []
    if instructions:
        messages.append({"role": "system", "content": instructions})
    if isinstance(input_value, str):
        messages.append({"role": "user", "content": input_value})
    elif isinstance(input_value, list):
        for item in input_value:
            if isinstance(item, dict) and item.get("type") == "message":
                role = item.get("role", "user")
                content = item.get("content", "")
                messages.append({"role": role, "content": content})
            else:
                return None
    else:
        return None
    return messages


def _extract_stream_content(result: dict[str, Any], response_format: str) -> str:
    if response_format == "responses":
        for item in result.get("output", []):
            if item.get("type") == "message":
                for block in item.get("content", []):
                    if block.get("type") == "output_text":
                        return block.get("text", "")
        return ""
    try:
        return result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""


def _stream_success_response(
    result: dict[str, Any], response_format: str
) -> StreamResponse:
    epr = result.get("epr", {})
    content = _extract_stream_content(result, response_format)
    chunks = SSEStreamer.build_stream_chunks(epr.get("run_id", ""), content, epr)
    return StreamResponse(chunks=chunks)


def _stream_pipeline_error(exc: PipelineError) -> StreamResponse:
    chunks = [
        SSEStreamer.build_error_stream_chunk(
            exc.body.get("error", {}), exc.body.get("epr", {})
        )
    ]
    return StreamResponse(chunks=chunks)


def _finish_inference(
    request: dict[str, Any],
    ctx: PipelineContext,
    response_format: str,
    stream: bool,
) -> tuple[int, dict[str, Any]] | StreamResponse:
    try:
        result = run_inference_pipeline(request, ctx, response_format=response_format)
    except PipelineError as exc:
        if stream:
            return _stream_pipeline_error(exc)
        return exc.status, exc.body
    if stream:
        return _stream_success_response(result, response_format)
    return 200, result


def handle_list_models(ctx: PipelineContext) -> tuple[int, dict[str, Any]]:
    return 200, render_models_list()


def handle_chat_completions(
    body: dict[str, Any], ctx: PipelineContext
) -> tuple[int, dict[str, Any]] | StreamResponse:
    model = body.get("model")
    if not isinstance(model, str) or not model:
        return 400, error_envelope(
            "model is required", "invalid_request_error", "model", "missing_field"
        )

    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return 400, error_envelope(
            "messages is required and must be a non-empty array",
            "invalid_request_error",
            "messages",
            "missing_field",
        )

    governance = body.get("governance")
    if governance is not None and not isinstance(governance, dict):
        return 422, error_envelope(
            "governance must be an object",
            "governance_validation_error",
            "governance",
            "governance_invalid",
        )

    reasons = check_requested_model(model, ctx.policy)
    if reasons:
        return 403, error_envelope(
            f"model denied by policy: {', '.join(reasons)}",
            "policy_denied_error",
            "model",
            "model_denied_by_policy",
        )

    if model != VIRTUAL_MODEL_ID:
        return 404, error_envelope(
            f"model {model!r} not found",
            "invalid_request_error",
            "model",
            "model_not_found",
        )

    passthrough = {k: v for k, v in body.items() if k not in _CHAT_HANDLED_KEYS}
    request = {
        "model": model,
        "messages": messages,
        "governance": governance,
        "passthrough": passthrough,
    }

    return _finish_inference(request, ctx, "chat", bool(body.get("stream")))


def handle_responses(
    body: dict[str, Any], ctx: PipelineContext
) -> tuple[int, dict[str, Any]] | StreamResponse:
    model = body.get("model")
    if not isinstance(model, str) or not model:
        return 400, error_envelope(
            "model is required", "invalid_request_error", "model", "missing_field"
        )

    input_value = body.get("input")
    if input_value is None:
        return 400, error_envelope(
            "input is required", "invalid_request_error", "input", "missing_field"
        )

    governance = body.get("governance")
    if governance is not None and not isinstance(governance, dict):
        return 422, error_envelope(
            "governance must be an object",
            "governance_validation_error",
            "governance",
            "governance_invalid",
        )

    reasons = check_requested_model(model, ctx.policy)
    if reasons:
        return 403, error_envelope(
            f"model denied by policy: {', '.join(reasons)}",
            "policy_denied_error",
            "model",
            "model_denied_by_policy",
        )

    if model != VIRTUAL_MODEL_ID:
        return 404, error_envelope(
            f"model {model!r} not found",
            "invalid_request_error",
            "model",
            "model_not_found",
        )

    messages = _normalize_responses_input(input_value, body.get("instructions"))
    if messages is None:
        return 400, error_envelope(
            "unsupported input item type",
            "invalid_request_error",
            "input",
            "unsupported_input_item",
        )

    passthrough = {k: v for k, v in body.items() if k not in _RESPONSES_HANDLED_KEYS}
    request = {
        "model": model,
        "messages": messages,
        "governance": governance,
        "passthrough": passthrough,
    }

    return _finish_inference(request, ctx, "responses", bool(body.get("stream")))


def handle_get_run(
    run_id: str, ctx: PipelineContext
) -> tuple[int, dict[str, Any]]:
    receipt = ctx.registry.get_receipt(run_id)
    if receipt is None:
        return 404, error_envelope(
            f"run {run_id!r} not found",
            "invalid_request_error",
            "run_id",
            "run_not_found",
        )
    return 200, receipt


def handle_health(ctx: PipelineContext) -> tuple[int, dict[str, Any]]:
    """Return a lightweight liveness/readiness payload."""
    return 200, {"status": "healthy", "version": "0.1.0"}


def handle_cache_stats(ctx: PipelineContext) -> tuple[int, dict[str, Any]]:
    """Return response cache statistics."""
    if ctx.response_cache is None:
        return 200, {"cache_enabled": False, "size": 0}
    return 200, ctx.response_cache.stats()


def handle_list_local_models(ctx: PipelineContext) -> tuple[int, dict[str, Any]]:
    """Return the list of discovered local models."""
    from .local_portfolio import discover_local_models

    available = discover_local_models(ctx.config.local_model_url)
    return 200, {
        "object": "list",
        "data": [
            {"id": m, "object": "model", "owned_by": "local"} for m in available
        ],
    }


def handle_metrics(
    ctx: PipelineContext,
    accept_header: str = "",
) -> tuple[int, Any, str]:
    """Return aggregated run metrics.

    Content negotiation:
    - If ``Accept: application/json``, return JSON format.
    - Otherwise (no Accept header, or ``Accept: text/plain``), return
      Prometheus text format.

    Returns ``(status, body, content_type)`` where *body* is either a
    ``dict`` (JSON) or ``str`` (Prometheus text).
    """
    # Check if JSON is explicitly requested
    want_json = "application/json" in accept_header

    if want_json:
        runs = getattr(ctx.registry, "_runs", {})
        runs_total = len(runs)
        runs_accepted = sum(
            1
            for r in runs.values()
            if r.receipt and r.receipt.get("status") == "accepted"
        )
        runs_escalated = sum(
            1
            for r in runs.values()
            if r.receipt and r.receipt.get("status") == "escalated"
        )
        return 200, {
            "runs_total": runs_total,
            "runs_accepted": runs_accepted,
            "runs_escalated": runs_escalated,
        }, "application/json"

    # Prometheus text format (default)
    prom = getattr(ctx, "prometheus_metrics", None)
    if prom is not None:
        # Sync gauges from current state
        cache_size = 0
        if ctx.response_cache is not None:
            try:
                stats = ctx.response_cache.stats()
                cache_size = stats.get("size", 0)
            except Exception:
                pass
        prom.set_gauge("noerelay_cache_size", float(cache_size))

        local_count = 0
        try:
            from .local_portfolio import discover_local_models
            local_models = discover_local_models(ctx.config.local_model_url)
            local_count = len(local_models)
        except Exception:
            pass
        prom.set_gauge("noerelay_local_models_count", float(local_count))

        # Sync RTK compression metrics (Phase 4)
        try:
            from .compression import get_cache_stats, get_profiler_stats
            cache_stats = get_cache_stats()
            if cache_stats:
                prom.set_gauge("noerelay_compression_cache_hits_total", float(cache_stats.get("hits", 0)))
                prom.set_gauge("noerelay_compression_cache_misses_total", float(cache_stats.get("misses", 0)))
            profiler_stats = get_profiler_stats()
            if profiler_stats and profiler_stats.get("overall", {}).get("count", 0) > 0:
                overall = profiler_stats["overall"]
                prom.set_gauge("noerelay_compression_total", float(overall.get("count", 0)))
                prom.set_gauge("noerelay_compression_tokens_saved_total", float(overall.get("total_tokens_saved", 0)))
                prom.set_gauge("noerelay_compression_avg_ratio", float(overall.get("avg_ratio", 0)))
                # Compute avg quality across all strategies
                total_quality = 0.0
                quality_count = 0
                for key, s in profiler_stats.items():
                    if key != "overall" and s.get("avg_quality") is not None:
                        total_quality += s["avg_quality"] * s.get("count", 0)
                        quality_count += s.get("count", 0)
                if quality_count > 0:
                    prom.set_gauge("noerelay_compression_avg_quality", total_quality / quality_count)
        except Exception:
            pass

        # Also sync runs totals from registry
        runs = getattr(ctx.registry, "_runs", {})
        active = sum(
            1 for r in runs.values()
            if r.receipt and r.receipt.get("status") not in ("accepted", "escalated", "rejected")
        )
        prom.set_gauge("noerelay_active_runs", float(active))

        return 200, prom.get_metrics_text(), "text/plain"

    # Fallback: no PrometheusMetrics instance, return minimal text
    runs = getattr(ctx.registry, "_runs", {})
    runs_total = len(runs)
    runs_accepted = sum(
        1
        for r in runs.values()
        if r.receipt and r.receipt.get("status") == "accepted"
    )
    runs_escalated = sum(
        1
        for r in runs.values()
        if r.receipt and r.receipt.get("status") == "escalated"
    )
    lines = [
        "# HELP noerelay_runs_total Total number of runs processed.",
        "# TYPE noerelay_runs_total counter",
        f"noerelay_runs_total {runs_total}",
        "",
    ]
    return 200, "\n".join(lines), "text/plain"


def _get_hf_token() -> str | None:
    """Get HuggingFace token from environment."""
    token = os.environ.get("HF_TOKEN", "")
    return token.strip() if token else None


def _get_openrouter_api_key() -> str | None:
    """Get OpenRouter API key from environment."""
    key = os.environ.get("OPENROUTER_API_KEY", "")
    return key.strip() if key else None


def handle_model_recommendations(ctx: PipelineContext) -> tuple[int, dict[str, Any]]:
    """Return download + removal recommendations for local models."""
    from .local_portfolio import discover_local_models

    ollama = OllamaModelManager(base_url=ctx.config.local_model_url)
    hf = HuggingFaceModelDiscovery(hf_token=_get_hf_token())
    analyzer = ModelPerformanceAnalyzer()

    local_model_names = discover_local_models(ctx.config.local_model_url)

    download_recs = analyzer.recommend_downloads(
        available_local=local_model_names,
        hf_discovery=hf,
    )

    removal_recs = analyzer.recommend_removals(local_model_names)

    return 200, {
        "object": "model_recommendations",
        "local_models": local_model_names,
        "download_recommendations": download_recs,
        "removal_recommendations": removal_recs,
    }


def handle_model_cloud(ctx: PipelineContext) -> tuple[int, dict[str, Any]]:
    """Return available OpenRouter models and recommendations."""
    api_key = _get_openrouter_api_key()
    if not api_key:
        return 200, {
            "object": "cloud_models",
            "error": "OPENROUTER_API_KEY not configured",
            "models": [],
        }

    discovery = OpenRouterModelDiscovery(api_key=api_key)
    try:
        models = discovery.list_models()
    except RuntimeError as exc:
        return 502, error_envelope(
            f"Failed to fetch OpenRouter models: {exc}",
            "upstream_error",
            None,
            "openrouter_unavailable",
        )

    return 200, {
        "object": "list",
        "data": models,
    }


def handle_model_ranking(ctx: PipelineContext) -> tuple[int, dict[str, Any]]:
    """Return model performance ranking with true cost breakdown."""
    from .cost_model import TrueCostModel
    from .local_portfolio import discover_local_models

    cost_model = TrueCostModel()
    analyzer = ModelPerformanceAnalyzer(cost_model=cost_model)
    local_model_names = discover_local_models(ctx.config.local_model_url)

    # If no benchmark history exists, return available models with empty ranking
    ranked = analyzer.rank_models(local_model_names)

    # Build enriched response with cost breakdowns
    ranked_models = []
    for r in ranked:
        entry: dict[str, Any] = {
            "model_id": r["model_id"],
            "true_cost_per_correct": r.get("true_cost_per_correct"),
            "cost_breakdown": r.get("cost_breakdown", {}),
            "accuracy": r.get("mean_accuracy", r.get("accuracy", 0)),
            "rework_rate": r.get("rework_rate", 0),
            "human_intervention_rate": r.get("human_intervention_rate", 0),
            "escalation_rate": r.get("escalation_rate", 0),
            "legacy_score": r.get("score", 0),
            "runs": r.get("runs", 0),
            "mean_latency_ms": r.get("mean_latency_ms", 0),
        }
        ranked_models.append(entry)

    return 200, {
        "object": "model_ranking",
        "ranked_models": ranked_models,
        "total_models_tracked": len(analyzer.history()),
        "cost_model_defaults": dict(cost_model._params),
    }


def handle_get_trace(
    run_id: str, ctx: PipelineContext
) -> tuple[int, dict[str, Any]]:
    """Return the full epistemic decision trace for a run."""
    record = ctx.registry.get(run_id)
    if record is None:
        return 404, error_envelope(
            f"run {run_id!r} not found",
            "invalid_request_error",
            "run_id",
            "run_not_found",
        )
    trace = record.decision_trace if record.decision_trace else []
    return 200, {
        "run_id": run_id,
        "trace_id": record.trace_id,
        "decision_trace": trace,
    }


def handle_ledger_events(
    ctx: PipelineContext,
    run_id: str | None = None,
    event_type: str | None = None,
    actor: str | None = None,
    from_ts: str | None = None,
    to_ts: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Query ledger events with optional filtering."""
    events: list[dict[str, Any]] = []

    if run_id:
        record = ctx.registry.get(run_id)
        if record is None:
            return 404, error_envelope(
                f"run {run_id!r} not found",
                "invalid_request_error",
                "run_id",
                "run_not_found",
            )
        events = list(record.events)
    else:
        # Collect events from all runs
        runs = getattr(ctx.registry, "_runs", {})
        for record in runs.values():
            events.extend(record.events)

    # Apply filters
    if event_type:
        events = [e for e in events if e.get("event_type") == event_type]
    if actor:
        events = [
            e for e in events
            if str(e.get("actor", {}).get("id", "")) == actor
        ]
    if from_ts:
        events = [e for e in events if e.get("timestamp", "") >= from_ts]
    if to_ts:
        events = [e for e in events if e.get("timestamp", "") <= to_ts]

    return 200, {
        "object": "list",
        "data": events,
        "count": len(events),
    }


def handle_ledger_chain(
    run_id: str, ctx: PipelineContext
) -> tuple[int, dict[str, Any]]:
    """Get the full hash-linked chain for a run."""
    record = ctx.registry.get(run_id)
    if record is None:
        return 404, error_envelope(
            f"run {run_id!r} not found",
            "invalid_request_error",
            "run_id",
            "run_not_found",
        )
    return 200, {
        "run_id": run_id,
        "chain": record.events,
        "head_hash": ctx.registry.head_hash(run_id),
        "event_count": len(record.events),
    }


def handle_ledger_verify(
    run_id: str, ctx: PipelineContext
) -> tuple[int, dict[str, Any]]:
    """Verify chain integrity for a run."""
    from epr.ledger import verify_chain

    record = ctx.registry.get(run_id)
    if record is None:
        return 404, error_envelope(
            f"run {run_id!r} not found",
            "invalid_request_error",
            "run_id",
            "run_not_found",
        )
    valid, message = verify_chain(record.events)
    return 200, {
        "run_id": run_id,
        "valid": valid,
        "message": message,
        "event_count": len(record.events),
        "head_hash": ctx.registry.head_hash(run_id),
    }


def handle_ledger_export(
    run_id: str, ctx: PipelineContext
) -> tuple[int, dict[str, Any]]:
    """Export the full chain as JSON."""
    record = ctx.registry.get(run_id)
    if record is None:
        return 404, error_envelope(
            f"run {run_id!r} not found",
            "invalid_request_error",
            "run_id",
            "run_not_found",
        )
    return 200, {
        "run_id": run_id,
        "trace_id": record.trace_id,
        "exported_at": record.events[-1]["timestamp"] if record.events else "",
        "events": record.events,
        "head_hash": ctx.registry.head_hash(run_id),
        "event_count": len(record.events),
    }


# ---------------------------------------------------------------------------
# Pagination helper
# ---------------------------------------------------------------------------


def paginate(items: list[Any], limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """Paginate a list of items."""
    total = len(items)
    page_items = items[offset : offset + limit]
    return {
        "data": page_items,
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
        },
    }


# ---------------------------------------------------------------------------
# Model Management API
# ---------------------------------------------------------------------------


def handle_pull_model(
    body: dict[str, Any], ctx: PipelineContext
) -> tuple[int, dict[str, Any]]:
    """POST /v1/models/pull — Download a model via Ollama."""
    model_name = body.get("model_name")
    if not model_name:
        return 400, error_envelope(
            "model_name is required",
            "invalid_request_error",
            "model_name",
            "missing_field",
        )
    manager = OllamaModelManager(ctx.config.local_model_url)
    result = manager.pull_model(model_name)
    return 200, {"status": "pulling", "model": model_name, "result": result}


def handle_remove_model(
    body: dict[str, Any], ctx: PipelineContext
) -> tuple[int, dict[str, Any]]:
    """DELETE /v1/models/{model_name} — Remove a model."""
    model_name = body.get("model_name")
    if not model_name:
        return 400, error_envelope(
            "model_name is required",
            "invalid_request_error",
            "model_name",
            "missing_field",
        )
    manager = OllamaModelManager(ctx.config.local_model_url)
    result = manager.delete_model(model_name)
    return 200, {"status": "deleted", "model": model_name, "result": result}


def handle_register_model(
    body: dict[str, Any], ctx: PipelineContext
) -> tuple[int, dict[str, Any]]:
    """POST /v1/models/register — Register a custom model in the portfolio."""
    required_fields = ["model_id", "provider_family", "inference_gateway"]
    for field in required_fields:
        if field not in body:
            return 400, error_envelope(
                f"{field} is required",
                "invalid_request_error",
                field,
                "missing_field",
            )
    return 200, {"status": "registered", "model": body}


# ---------------------------------------------------------------------------
# Benchmark API
# ---------------------------------------------------------------------------


def handle_run_benchmark(
    body: dict[str, Any], ctx: PipelineContext
) -> tuple[int, dict[str, Any]]:
    """POST /v1/benchmarks/run — Run a benchmark."""
    cohort_name = body.get("cohort_name")
    if not cohort_name:
        return 400, error_envelope(
            "cohort_name is required",
            "invalid_request_error",
            "cohort_name",
            "missing_field",
        )
    model_id = body.get("model_id")
    if not model_id:
        return 400, error_envelope(
            "model_id is required",
            "invalid_request_error",
            "model_id",
            "missing_field",
        )

    # If database is available, save a placeholder result
    if hasattr(ctx, "registry") and hasattr(ctx.registry, "_db"):
        try:
            db = ctx.registry._db  # type: ignore[attr-defined]
            result_id = db.save_benchmark_result(
                {
                    "cohort_name": cohort_name,
                    "model_id": model_id,
                    "accuracy": body.get("accuracy"),
                    "total_tokens": body.get("total_tokens"),
                    "total_cost_usd": body.get("total_cost_usd"),
                    "mean_latency_ms": body.get("mean_latency_ms"),
                    "p95_latency_ms": body.get("p95_latency_ms"),
                    "hir": body.get("hir"),
                    "rr": body.get("rr"),
                    "escalation_rate": body.get("escalation_rate"),
                    "results": body.get("results"),
                }
            )
            return 200, {
                "status": "completed",
                "result_id": result_id,
                "cohort_name": cohort_name,
                "model_id": model_id,
            }
        except Exception as exc:
            return 500, error_envelope(
                f"Benchmark failed: {exc}",
                "server_error",
                None,
                "benchmark_failed",
            )

    return 200, {
        "status": "completed",
        "cohort_name": cohort_name,
        "model_id": model_id,
    }


def handle_list_benchmark_results(
    ctx: PipelineContext,
    cohort: str | None = None,
    model_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[int, dict[str, Any]]:
    """GET /v1/benchmarks/results — List benchmark results with pagination."""
    if hasattr(ctx, "registry") and hasattr(ctx.registry, "_db"):
        try:
            db = ctx.registry._db  # type: ignore[attr-defined]
            results = db.get_benchmark_results(
                cohort_name=cohort, model_id=model_id, limit=limit, offset=offset
            )
            return 200, paginate(results, limit, offset)
        except Exception as exc:
            return 500, error_envelope(
                f"Failed to list benchmark results: {exc}",
                "server_error",
                None,
                "benchmark_list_failed",
            )
    return 200, paginate([], limit, offset)


def handle_compare_models(
    query_params: dict[str, Any], ctx: PipelineContext
) -> tuple[int, dict[str, Any]]:
    """GET /v1/benchmarks/compare — Compare models on benchmarks."""
    model_ids = query_params.get("model_ids", [])
    if isinstance(model_ids, str):
        model_ids = [m.strip() for m in model_ids.split(",") if m.strip()]

    comparison: list[dict[str, Any]] = []
    if hasattr(ctx, "registry") and hasattr(ctx.registry, "_db"):
        try:
            db = ctx.registry._db  # type: ignore[attr-defined]
            for mid in model_ids:
                results = db.get_benchmark_results(model_id=mid, limit=10)
                if results:
                    comparison.append(
                        {
                            "model_id": mid,
                            "results": results,
                            "count": len(results),
                        }
                    )
        except Exception:
            pass

    return 200, {
        "object": "benchmark_comparison",
        "model_ids": model_ids,
        "comparison": comparison,
    }


# ---------------------------------------------------------------------------
# Governance API
# ---------------------------------------------------------------------------


def handle_get_policy(ctx: PipelineContext) -> tuple[int, dict[str, Any]]:
    """GET /v1/governance/policy — View current routing policy."""
    return 200, {"policy": ctx.policy}


def handle_update_policy(
    body: dict[str, Any], ctx: PipelineContext
) -> tuple[int, dict[str, Any]]:
    """PUT /v1/governance/policy — Update routing policy (with validation)."""
    if not isinstance(body, dict):
        return 400, error_envelope(
            "policy must be a JSON object",
            "invalid_request_error",
            "policy",
            "invalid_policy",
        )
    # Validate the policy structure
    if "inference" in body:
        inference = body["inference"]
        if not isinstance(inference, dict):
            return 400, error_envelope(
                "inference must be an object",
                "invalid_request_error",
                "inference",
                "invalid_policy",
            )
    # Update the policy in-memory
    ctx.policy.update(body)
    # Persist to database if available
    if hasattr(ctx, "registry") and hasattr(ctx.registry, "_db"):
        try:
            db = ctx.registry._db  # type: ignore[attr-defined]
            db.set_config("routing_policy", ctx.policy, "api")
        except Exception:
            pass
    return 200, {"status": "updated", "policy": ctx.policy}


def handle_get_risk_classes(ctx: PipelineContext) -> tuple[int, dict[str, Any]]:
    """GET /v1/governance/risk-classes — List risk classes and their gates."""
    from .governance import RISK_CLASSES

    risk_classes = {}
    for rc in sorted(RISK_CLASSES):
        risk_classes[rc] = {
            "name": rc,
            "description": f"Risk class: {rc}",
            "gates": ctx.policy.get("risk_gates", {}).get(rc, {}),
        }
    return 200, {"risk_classes": risk_classes}


def handle_update_risk_class(
    body: dict[str, Any], ctx: PipelineContext
) -> tuple[int, dict[str, Any]]:
    """PUT /v1/governance/risk-class/{class} — Configure risk class gates."""
    risk_class = body.get("risk_class")
    if not risk_class:
        return 400, error_envelope(
            "risk_class is required",
            "invalid_request_error",
            "risk_class",
            "missing_field",
        )
    from .governance import RISK_CLASSES

    if risk_class not in RISK_CLASSES:
        return 400, error_envelope(
            f"Invalid risk class: {risk_class}",
            "invalid_request_error",
            "risk_class",
            "invalid_risk_class",
        )
    # Update risk gates in policy
    if "risk_gates" not in ctx.policy:
        ctx.policy["risk_gates"] = {}
    ctx.policy["risk_gates"][risk_class] = body.get("gates", {})
    # Persist to database if available
    if hasattr(ctx, "registry") and hasattr(ctx.registry, "_db"):
        try:
            db = ctx.registry._db  # type: ignore[attr-defined]
            db.set_config("routing_policy", ctx.policy, "api")
        except Exception:
            pass
    return 200, {
        "status": "updated",
        "risk_class": risk_class,
        "gates": ctx.policy["risk_gates"][risk_class],
    }


# ---------------------------------------------------------------------------
# Routing API
# ---------------------------------------------------------------------------


def handle_get_portfolio(ctx: PipelineContext) -> tuple[int, dict[str, Any]]:
    """GET /v1/routing/portfolio — View current model portfolio."""
    return 200, {
        "object": "portfolio",
        "data": ctx.portfolio,
        "count": len(ctx.portfolio),
    }


def handle_add_candidate(
    body: dict[str, Any], ctx: PipelineContext
) -> tuple[int, dict[str, Any]]:
    """POST /v1/routing/candidates — Add a candidate to the portfolio."""
    required_fields = ["candidate_id", "action_kind"]
    for field in required_fields:
        if field not in body:
            return 400, error_envelope(
                f"{field} is required",
                "invalid_request_error",
                field,
                "missing_field",
            )
    ctx.portfolio.append(body)
    return 200, {"status": "added", "candidate": body}


def handle_remove_candidate(
    candidate_id: str, ctx: PipelineContext
) -> tuple[int, dict[str, Any]]:
    """DELETE /v1/routing/candidates/{id} — Remove a candidate."""
    original_len = len(ctx.portfolio)
    ctx.portfolio[:] = [
        c for c in ctx.portfolio if c.get("candidate_id") != candidate_id
    ]
    if len(ctx.portfolio) == original_len:
        return 404, error_envelope(
            f"candidate {candidate_id!r} not found",
            "invalid_request_error",
            "candidate_id",
            "candidate_not_found",
        )
    return 200, {"status": "removed", "candidate_id": candidate_id}


def handle_update_candidate(
    candidate_id: str, body: dict[str, Any], ctx: PipelineContext
) -> tuple[int, dict[str, Any]]:
    """PUT /v1/routing/candidates/{id} — Update a candidate."""
    for i, c in enumerate(ctx.portfolio):
        if c.get("candidate_id") == candidate_id:
            ctx.portfolio[i].update(body)
            return 200, {"status": "updated", "candidate": ctx.portfolio[i]}
    return 404, error_envelope(
        f"candidate {candidate_id!r} not found",
        "invalid_request_error",
        "candidate_id",
        "candidate_not_found",
    )


# ---------------------------------------------------------------------------
# API Key Management API
# ---------------------------------------------------------------------------


def handle_create_api_key(
    body: dict[str, Any], ctx: PipelineContext
) -> tuple[int, dict[str, Any]]:
    """POST /v1/api-keys — Create a new API key."""
    name = body.get("name")
    if not name:
        return 400, error_envelope(
            "name is required",
            "invalid_request_error",
            "name",
            "missing_field",
        )
    if not hasattr(ctx, "registry") or not hasattr(ctx.registry, "_db"):
        return 501, error_envelope(
            "Database not enabled",
            "server_error",
            None,
            "database_not_enabled",
        )
    try:
        from .api_keys import APIKeyManager

        db = ctx.registry._db  # type: ignore[attr-defined]
        manager = APIKeyManager(db)
        result = manager.create_key(
            name=name,
            role=body.get("role", "operator"),
            rate_limit_rate=body.get("rate_limit_rate", 10.0),
            rate_limit_burst=body.get("rate_limit_burst", 20),
            tenant_id=body.get("tenant_id", "default"),
        )
        return 201, result
    except Exception as exc:
        return 500, error_envelope(
            f"Failed to create API key: {exc}",
            "server_error",
            None,
            "key_creation_failed",
        )


def handle_list_api_keys(
    ctx: PipelineContext, tenant_id: str | None = None
) -> tuple[int, dict[str, Any]]:
    """GET /v1/api-keys — List all API keys."""
    if not hasattr(ctx, "registry") or not hasattr(ctx.registry, "_db"):
        return 501, error_envelope(
            "Database not enabled",
            "server_error",
            None,
            "database_not_enabled",
        )
    try:
        from .api_keys import APIKeyManager

        db = ctx.registry._db  # type: ignore[attr-defined]
        manager = APIKeyManager(db)
        keys = manager.list_keys(tenant_id=tenant_id)
        return 200, {"object": "list", "data": keys, "count": len(keys)}
    except Exception as exc:
        return 500, error_envelope(
            f"Failed to list API keys: {exc}",
            "server_error",
            None,
            "key_list_failed",
        )


def handle_revoke_api_key(
    key_id: str, ctx: PipelineContext
) -> tuple[int, dict[str, Any]]:
    """DELETE /v1/api-keys/{id} — Revoke an API key."""
    if not hasattr(ctx, "registry") or not hasattr(ctx.registry, "_db"):
        return 501, error_envelope(
            "Database not enabled",
            "server_error",
            None,
            "database_not_enabled",
        )
    try:
        from .api_keys import APIKeyManager

        db = ctx.registry._db  # type: ignore[attr-defined]
        manager = APIKeyManager(db)
        success = manager.revoke_key(key_id)
        if not success:
            return 404, error_envelope(
                f"API key {key_id!r} not found or already revoked",
                "invalid_request_error",
                "key_id",
                "key_not_found",
            )
        return 200, {"status": "revoked", "key_id": key_id}
    except Exception as exc:
        return 500, error_envelope(
            f"Failed to revoke API key: {exc}",
            "server_error",
            None,
            "key_revoke_failed",
        )


def handle_rotate_api_key(
    key_id: str, ctx: PipelineContext
) -> tuple[int, dict[str, Any]]:
    """POST /v1/api-keys/{id}/rotate — Rotate an API key."""
    if not hasattr(ctx, "registry") or not hasattr(ctx.registry, "_db"):
        return 501, error_envelope(
            "Database not enabled",
            "server_error",
            None,
            "database_not_enabled",
        )
    try:
        from .api_keys import APIKeyManager

        db = ctx.registry._db  # type: ignore[attr-defined]
        manager = APIKeyManager(db)
        result = manager.rotate_key(key_id)
        return 200, result
    except ValueError:
        return 404, error_envelope(
            f"API key {key_id!r} not found",
            "invalid_request_error",
            "key_id",
            "key_not_found",
        )
    except Exception as exc:
        return 500, error_envelope(
            f"Failed to rotate API key: {exc}",
            "server_error",
            None,
            "key_rotate_failed",
        )


# ---------------------------------------------------------------------------
# Analytics API
# ---------------------------------------------------------------------------


def _get_analytics_engine(ctx: PipelineContext):
    """Get or create the AnalyticsEngine from the pipeline context."""
    if ctx.analytics is not None:
        return ctx.analytics
    # Fallback: create engine from db if available
    if hasattr(ctx, "registry") and hasattr(ctx.registry, "_db"):
        from .analytics import AnalyticsEngine
        return AnalyticsEngine(ctx.registry._db)  # type: ignore[attr-defined]
    return None


def handle_cost_analytics(
    query_params: dict[str, Any], ctx: PipelineContext
) -> tuple[int, dict[str, Any]]:
    """GET /v1/analytics/cost — Cost breakdown by model, risk class, time range."""
    engine = _get_analytics_engine(ctx)
    if engine is None:
        return 501, error_envelope(
            "Database not enabled",
            "server_error",
            None,
            "database_not_enabled",
        )
    try:
        group_by = query_params.get("group_by", "model")
        days = query_params.get("days")
        action = query_params.get("action", "summary")

        if action == "trend":
            data = engine.cost_trend(days=int(days) if days else 30)
            return 200, {"object": "cost_trend", "data": data}
        elif action == "breakdown":
            data = engine.cost_breakdown(
                from_ts=query_params.get("from"),
                to_ts=query_params.get("to"),
            )
            return 200, data
        elif action == "forecast":
            data = engine.cost_forecast(days=int(days) if days else 7)
            return 200, data
        elif action == "anomalies":
            data = engine.cost_anomalies(window_days=int(query_params.get("window_days", 7)))
            return 200, {"object": "cost_anomalies", "data": data}
        else:
            data = engine.cost_summary(
                from_ts=query_params.get("from"),
                to_ts=query_params.get("to"),
                group_by=group_by,
            )
            return 200, data
    except Exception as exc:
        return 500, error_envelope(
            f"Failed to get cost analytics: {exc}",
            "server_error",
            None,
            "analytics_failed",
        )


def handle_performance_analytics(
    query_params: dict[str, Any], ctx: PipelineContext
) -> tuple[int, dict[str, Any]]:
    """GET /v1/analytics/performance — Model performance trends."""
    engine = _get_analytics_engine(ctx)
    if engine is None:
        return 501, error_envelope(
            "Database not enabled",
            "server_error",
            None,
            "database_not_enabled",
        )
    try:
        model_id = query_params.get("model_id")
        action = query_params.get("action", "summary")

        if action == "trend" and model_id:
            days = int(query_params.get("days", 30))
            data = engine.model_performance_trend(model_id, days=days)
            return 200, {"object": "performance_trend", "model_id": model_id, "data": data}
        elif action == "comparison":
            model_ids = query_params.get("model_ids")
            if isinstance(model_ids, str):
                model_ids = [m.strip() for m in model_ids.split(",") if m.strip()]
            data = engine.model_comparison(model_ids=model_ids if isinstance(model_ids, list) else None)
            return 200, data
        elif action == "ranking":
            metric = query_params.get("metric", "true_cost")
            data = engine.model_ranking(metric=metric)
            return 200, {"object": "model_ranking", "data": data}
        else:
            data = engine.model_performance_summary(model_id=model_id)
            return 200, {"object": "performance", "data": data}
    except Exception as exc:
        return 500, error_envelope(
            f"Failed to get performance analytics: {exc}",
            "server_error",
            None,
            "analytics_failed",
        )


def handle_usage_analytics(
    query_params: dict[str, Any], ctx: PipelineContext
) -> tuple[int, dict[str, Any]]:
    """GET /v1/analytics/usage — Request volume, tokens, peak usage."""
    engine = _get_analytics_engine(ctx)
    if engine is None:
        return 501, error_envelope(
            "Database not enabled",
            "server_error",
            None,
            "database_not_enabled",
        )
    try:
        action = query_params.get("action", "summary")

        if action == "trend":
            days = int(query_params.get("days", 30))
            granularity = query_params.get("granularity", "daily")
            data = engine.usage_trend(days=days, granularity=granularity)
            return 200, {"object": "usage_trend", "data": data}
        elif action == "peak":
            days = int(query_params.get("days", 7))
            data = engine.peak_usage(days=days)
            return 200, data
        elif action == "by_model":
            data = engine.usage_by_model(
                from_ts=query_params.get("from"),
                to_ts=query_params.get("to"),
            )
            return 200, {"object": "usage_by_model", "data": data}
        else:
            data = engine.usage_summary(
                from_ts=query_params.get("from"),
                to_ts=query_params.get("to"),
            )
            return 200, data
    except Exception as exc:
        return 500, error_envelope(
            f"Failed to get usage analytics: {exc}",
            "server_error",
            None,
            "analytics_failed",
        )


def handle_escalation_analytics(
    query_params: dict[str, Any], ctx: PipelineContext
) -> tuple[int, dict[str, Any]]:
    """GET /v1/analytics/escalations — Escalation analysis (HIR, RR, trends)."""
    engine = _get_analytics_engine(ctx)
    if engine is None:
        return 501, error_envelope(
            "Database not enabled",
            "server_error",
            None,
            "database_not_enabled",
        )
    try:
        action = query_params.get("action", "summary")

        if action == "trend":
            days = int(query_params.get("days", 30))
            data = engine.escalation_trend(days=days)
            return 200, {"object": "escalation_trend", "data": data}
        elif action == "by_model":
            data = engine.escalation_by_model()
            return 200, {"object": "escalation_by_model", "data": data}
        elif action == "by_risk_class":
            data = engine.escalation_by_risk_class()
            return 200, {"object": "escalation_by_risk_class", "data": data}
        elif action == "triggers":
            data = engine.escalation_triggers()
            return 200, {"object": "escalation_triggers", "data": data}
        else:
            data = engine.escalation_summary(
                from_ts=query_params.get("from"),
                to_ts=query_params.get("to"),
            )
            return 200, data
    except Exception as exc:
        return 500, error_envelope(
            f"Failed to get escalation analytics: {exc}",
            "server_error",
            None,
            "analytics_failed",
        )


def handle_audit_analytics(
    query_params: dict[str, Any], ctx: PipelineContext
) -> tuple[int, dict[str, Any]]:
    """GET /v1/analytics/audit — Audit trail queries."""
    engine = _get_analytics_engine(ctx)
    if engine is None:
        return 501, error_envelope(
            "Database not enabled",
            "server_error",
            None,
            "database_not_enabled",
        )
    try:
        action = query_params.get("action", "timeline")

        if action == "summary":
            data = engine.audit_summary(
                from_ts=query_params.get("from"),
                to_ts=query_params.get("to"),
            )
            return 200, data
        elif action == "by_actor":
            data = engine.audit_by_actor(
                from_ts=query_params.get("from"),
                to_ts=query_params.get("to"),
            )
            return 200, {"object": "audit_by_actor", "data": data}
        elif action == "by_action":
            data = engine.audit_by_action(
                from_ts=query_params.get("from"),
                to_ts=query_params.get("to"),
            )
            return 200, {"object": "audit_by_action", "data": data}
        elif action == "anomalies":
            window_hours = int(query_params.get("window_hours", 24))
            data = engine.audit_anomalies(window_hours=window_hours)
            return 200, {"object": "audit_anomalies", "data": data}
        else:
            limit = int(query_params.get("limit", 100))
            data = engine.audit_timeline(
                from_ts=query_params.get("from"),
                to_ts=query_params.get("to"),
                limit=limit,
            )
            return 200, {"object": "audit_timeline", "data": data}
    except Exception as exc:
        return 500, error_envelope(
            f"Failed to get audit analytics: {exc}",
            "server_error",
            None,
            "analytics_failed",
        )


def handle_benchmark_analytics(
    query_params: dict[str, Any], ctx: PipelineContext
) -> tuple[int, dict[str, Any]]:
    """GET /v1/analytics/benchmarks — Benchmark analytics."""
    engine = _get_analytics_engine(ctx)
    if engine is None:
        return 501, error_envelope(
            "Database not enabled",
            "server_error",
            None,
            "database_not_enabled",
        )
    try:
        action = query_params.get("action", "summary")
        cohort_name = query_params.get("cohort_name")
        model_id = query_params.get("model_id")

        if action == "history":
            if not cohort_name:
                return 400, error_envelope(
                    "cohort_name is required for history",
                    "invalid_request_error",
                    "cohort_name",
                    "missing_field",
                )
            limit = int(query_params.get("limit", 20))
            data = engine.benchmark_history(
                cohort_name=cohort_name, model_id=model_id, limit=limit
            )
            return 200, {"object": "benchmark_history", "data": data}
        elif action == "regression":
            if not cohort_name or not model_id:
                return 400, error_envelope(
                    "cohort_name and model_id are required for regression",
                    "invalid_request_error",
                    None,
                    "missing_field",
                )
            data = engine.benchmark_regression(cohort_name, model_id)
            return 200, data
        elif action == "comparison":
            data = engine.benchmark_comparison(cohort_name=cohort_name)
            return 200, data
        else:
            data = engine.benchmark_summary(cohort_name=cohort_name)
            return 200, data
    except Exception as exc:
        return 500, error_envelope(
            f"Failed to get benchmark analytics: {exc}",
            "server_error",
            None,
            "analytics_failed",
        )


def handle_dashboard_data(
    ctx: PipelineContext,
) -> tuple[int, dict[str, Any]]:
    """GET /v1/analytics/dashboard — All dashboard data in one call."""
    engine = _get_analytics_engine(ctx)
    if engine is None:
        return 501, error_envelope(
            "Database not enabled",
            "server_error",
            None,
            "database_not_enabled",
        )
    try:
        data = engine.dashboard_data()
        return 200, data
    except Exception as exc:
        return 500, error_envelope(
            f"Failed to get dashboard data: {exc}",
            "server_error",
            None,
            "analytics_failed",
        )


def handle_dashboard_html() -> tuple[int, str, str]:
    """GET /dashboard — Serve the analytics dashboard HTML page."""
    from .dashboard import render_dashboard
    return 200, render_dashboard(), "text/html"


# ---------------------------------------------------------------------------
# Export / Import API
# ---------------------------------------------------------------------------


def handle_export_data(
    query_params: dict[str, Any], ctx: PipelineContext
) -> tuple[int, dict[str, Any]]:
    """GET /v1/export — Export data (ledger, receipts, benchmarks, config)."""
    if not hasattr(ctx, "registry") or not hasattr(ctx.registry, "_db"):
        return 501, error_envelope(
            "Database not enabled",
            "server_error",
            None,
            "database_not_enabled",
        )
    try:
        db = ctx.registry._db  # type: ignore[attr-defined]
        export_path = db.export_json(".noerelay/export.json")
        return 200, {
            "status": "ok",
            "export_path": export_path,
        }
    except Exception as exc:
        return 500, error_envelope(
            f"Export failed: {exc}",
            "server_error",
            None,
            "export_failed",
        )


def handle_import_data(
    body: dict[str, Any], ctx: PipelineContext
) -> tuple[int, dict[str, Any]]:
    """POST /v1/import — Import data."""
    if not hasattr(ctx, "registry") or not hasattr(ctx.registry, "_db"):
        return 501, error_envelope(
            "Database not enabled",
            "server_error",
            None,
            "database_not_enabled",
        )
    import_path = body.get("import_path")
    if not import_path:
        return 400, error_envelope(
            "import_path is required",
            "invalid_request_error",
            "import_path",
            "missing_field",
        )
    try:
        db = ctx.registry._db  # type: ignore[attr-defined]
        db.restore(import_path)
        return 200, {
            "status": "ok",
            "message": f"Imported from {import_path}",
        }
    except FileNotFoundError:
        return 404, error_envelope(
            f"Import file not found: {import_path}",
            "invalid_request_error",
            "import_path",
            "import_not_found",
        )
    except Exception as exc:
        return 500, error_envelope(
            f"Import failed: {exc}",
            "server_error",
            None,
            "import_failed",
        )


# ---------------------------------------------------------------------------
# Tenant Management API
# ---------------------------------------------------------------------------


def handle_list_tenants(
    ctx: PipelineContext, tenant_manager: Any = None
) -> tuple[int, dict[str, Any]]:
    """GET /v1/tenants — List all tenants."""
    if tenant_manager is None:
        return 501, error_envelope(
            "Tenant manager not enabled",
            "server_error",
            None,
            "tenants_not_enabled",
        )
    try:
        tenants = tenant_manager.list_tenants()
        return 200, {"tenants": tenants, "count": len(tenants)}
    except Exception as exc:
        return 500, error_envelope(
            f"Failed to list tenants: {exc}",
            "server_error",
            None,
            "tenants_failed",
        )


def handle_create_tenant(
    body: dict[str, Any], tenant_manager: Any = None
) -> tuple[int, dict[str, Any]]:
    """POST /v1/tenants — Create a new tenant."""
    if tenant_manager is None:
        return 501, error_envelope(
            "Tenant manager not enabled",
            "server_error",
            None,
            "tenants_not_enabled",
        )
    tenant_id = body.get("tenant_id")
    name = body.get("name")
    if not tenant_id or not name:
        return 400, error_envelope(
            "tenant_id and name are required",
            "invalid_request_error",
            None,
            "missing_field",
        )
    try:
        tenant = tenant_manager.create_tenant(
            tenant_id=tenant_id,
            name=name,
            budget_daily_usd=body.get("budget_daily_usd", 10.0),
            budget_monthly_usd=body.get("budget_monthly_usd", 300.0),
        )
        return 201, tenant
    except Exception as exc:
        return 500, error_envelope(
            f"Failed to create tenant: {exc}",
            "server_error",
            None,
            "tenant_create_failed",
        )


def handle_update_tenant(
    tenant_id: str, body: dict[str, Any], tenant_manager: Any = None
) -> tuple[int, dict[str, Any]]:
    """PUT /v1/tenants/{id} — Update a tenant."""
    if tenant_manager is None:
        return 501, error_envelope(
            "Tenant manager not enabled",
            "server_error",
            None,
            "tenants_not_enabled",
        )
    try:
        tenant = tenant_manager.update_tenant(tenant_id, **body)
        if tenant is None:
            return 404, error_envelope(
                f"Tenant {tenant_id!r} not found",
                "invalid_request_error",
                "tenant_id",
                "tenant_not_found",
            )
        return 200, tenant
    except Exception as exc:
        return 500, error_envelope(
            f"Failed to update tenant: {exc}",
            "server_error",
            None,
            "tenant_update_failed",
        )


def handle_delete_tenant(
    tenant_id: str, tenant_manager: Any = None
) -> tuple[int, dict[str, Any]]:
    """DELETE /v1/tenants/{id} — Delete a tenant."""
    if tenant_manager is None:
        return 501, error_envelope(
            "Tenant manager not enabled",
            "server_error",
            None,
            "tenants_not_enabled",
        )
    success = tenant_manager.delete_tenant(tenant_id)
    if not success:
        return 404, error_envelope(
            f"Tenant {tenant_id!r} not found",
            "invalid_request_error",
            "tenant_id",
            "tenant_not_found",
        )
    return 200, {"status": "ok", "tenant_id": tenant_id}


def handle_tenant_budget(
    tenant_id: str, tenant_manager: Any = None
) -> tuple[int, dict[str, Any]]:
    """GET /v1/tenants/{id}/budget — Check tenant budget."""
    if tenant_manager is None:
        return 501, error_envelope(
            "Tenant manager not enabled",
            "server_error",
            None,
            "tenants_not_enabled",
        )
    budget = tenant_manager.check_budget(tenant_id)
    return 200, budget


# ---------------------------------------------------------------------------
# Alert Management API
# ---------------------------------------------------------------------------


def handle_list_alerts(
    alert_manager: Any = None,
    severity: str | None = None,
    acknowledged: bool | None = None,
    limit: int = 50,
) -> tuple[int, dict[str, Any]]:
    """GET /v1/alerts — List alerts."""
    if alert_manager is None:
        return 501, error_envelope(
            "Alert manager not enabled",
            "server_error",
            None,
            "alerts_not_enabled",
        )
    alerts = alert_manager.get_alerts(
        severity=severity, acknowledged=acknowledged, limit=limit
    )
    return 200, {"alerts": alerts, "count": len(alerts)}


def handle_acknowledge_alert(
    alert_id: str, acknowledged_by: str, alert_manager: Any = None
) -> tuple[int, dict[str, Any]]:
    """POST /v1/alerts/{id}/acknowledge — Acknowledge an alert."""
    if alert_manager is None:
        return 501, error_envelope(
            "Alert manager not enabled",
            "server_error",
            None,
            "alerts_not_enabled",
        )
    success = alert_manager.acknowledge_alert(alert_id, acknowledged_by)
    if not success:
        return 404, error_envelope(
            f"Alert {alert_id!r} not found",
            "invalid_request_error",
            "alert_id",
            "alert_not_found",
        )
    return 200, {"status": "ok", "alert_id": alert_id}


def handle_add_alert_rule(
    body: dict[str, Any], alert_manager: Any = None
) -> tuple[int, dict[str, Any]]:
    """POST /v1/alerts/rules — Add an alert rule."""
    if alert_manager is None:
        return 501, error_envelope(
            "Alert manager not enabled",
            "server_error",
            None,
            "alerts_not_enabled",
        )
    name = body.get("name")
    alert_type = body.get("alert_type")
    if not name or not alert_type:
        return 400, error_envelope(
            "name and alert_type are required",
            "invalid_request_error",
            None,
            "missing_field",
        )
    rule = alert_manager.add_rule(
        name=name,
        alert_type=alert_type,
        condition=body.get("condition", {}),
        severity=body.get("severity", "warning"),
    )
    return 201, rule


# ---------------------------------------------------------------------------
# Webhook Management API
# ---------------------------------------------------------------------------


def handle_list_webhooks(
    webhook_manager: Any = None, tenant_id: str | None = None
) -> tuple[int, dict[str, Any]]:
    """GET /v1/webhooks — List webhooks."""
    if webhook_manager is None:
        return 501, error_envelope(
            "Webhook manager not enabled",
            "server_error",
            None,
            "webhooks_not_enabled",
        )
    webhooks = webhook_manager.list_webhooks(tenant_id=tenant_id)
    return 200, {"webhooks": webhooks, "count": len(webhooks)}


def handle_register_webhook(
    body: dict[str, Any], webhook_manager: Any = None
) -> tuple[int, dict[str, Any]]:
    """POST /v1/webhooks — Register a webhook."""
    if webhook_manager is None:
        return 501, error_envelope(
            "Webhook manager not enabled",
            "server_error",
            None,
            "webhooks_not_enabled",
        )
    url = body.get("url")
    events = body.get("events")
    if not url or not events:
        return 400, error_envelope(
            "url and events are required",
            "invalid_request_error",
            None,
            "missing_field",
        )
    webhook = webhook_manager.register(
        url=url,
        events=events,
        secret=body.get("secret"),
        tenant_id=body.get("tenant_id"),
    )
    return 201, webhook


def handle_delete_webhook(
    webhook_id: str, webhook_manager: Any = None
) -> tuple[int, dict[str, Any]]:
    """DELETE /v1/webhooks/{id} — Delete a webhook."""
    if webhook_manager is None:
        return 501, error_envelope(
            "Webhook manager not enabled",
            "server_error",
            None,
            "webhooks_not_enabled",
        )
    success = webhook_manager.delete_webhook(webhook_id)
    if not success:
        return 404, error_envelope(
            f"Webhook {webhook_id!r} not found",
            "invalid_request_error",
            "webhook_id",
            "webhook_not_found",
        )
    return 200, {"status": "ok", "webhook_id": webhook_id}


# ---------------------------------------------------------------------------
# Config Management API
# ---------------------------------------------------------------------------


def handle_get_config(
    config_manager: Any = None
) -> tuple[int, dict[str, Any]]:
    """GET /v1/config — Get all config."""
    if config_manager is None:
        return 501, error_envelope(
            "Config manager not enabled",
            "server_error",
            None,
            "config_not_enabled",
        )
    config_data = config_manager.get_all()
    return 200, {"config": config_data}


def handle_set_config(
    key: str, body: dict[str, Any], config_manager: Any = None
) -> tuple[int, dict[str, Any]]:
    """PUT /v1/config/{key} — Set a config value."""
    if config_manager is None:
        return 501, error_envelope(
            "Config manager not enabled",
            "server_error",
            None,
            "config_not_enabled",
        )
    value = body.get("value")
    if value is None:
        return 400, error_envelope(
            "value is required",
            "invalid_request_error",
            "value",
            "missing_field",
        )
    updated_by = body.get("updated_by", "api")
    config_manager.set(key, value, updated_by)
    return 200, {"status": "ok", "key": key}


# ---------------------------------------------------------------------------
# Secret Management API
# ---------------------------------------------------------------------------


def handle_list_secrets(
    secret_manager: Any = None, tenant_id: str = "default"
) -> tuple[int, dict[str, Any]]:
    """GET /v1/secrets — List secrets."""
    if secret_manager is None:
        return 501, error_envelope(
            "Secret manager not enabled",
            "server_error",
            None,
            "secrets_not_enabled",
        )
    secrets = secret_manager.list_secrets(tenant_id=tenant_id)
    return 200, {"secrets": secrets, "count": len(secrets)}


def handle_store_secret(
    body: dict[str, Any], secret_manager: Any = None
) -> tuple[int, dict[str, Any]]:
    """POST /v1/secrets — Store a secret."""
    if secret_manager is None:
        return 501, error_envelope(
            "Secret manager not enabled",
            "server_error",
            None,
            "secrets_not_enabled",
        )
    name = body.get("name")
    value = body.get("value")
    if not name or value is None:
        return 400, error_envelope(
            "name and value are required",
            "invalid_request_error",
            None,
            "missing_field",
        )
    secret_id = secret_manager.store_secret(
        name=name,
        value=str(value),
        description=body.get("description", ""),
        tenant_id=body.get("tenant_id", "default"),
    )
    return 201, {"secret_id": secret_id, "name": name, "status": "stored"}


def handle_delete_secret(
    name: str, secret_manager: Any = None, tenant_id: str = "default"
) -> tuple[int, dict[str, Any]]:
    """DELETE /v1/secrets/{name} — Delete a secret."""
    if secret_manager is None:
        return 501, error_envelope(
            "Secret manager not enabled",
            "server_error",
            None,
            "secrets_not_enabled",
        )
    success = secret_manager.delete_secret(name, tenant_id=tenant_id)
    if not success:
        return 404, error_envelope(
            f"Secret {name!r} not found",
            "invalid_request_error",
            "name",
            "secret_not_found",
        )
    return 200, {"status": "ok", "name": name}
