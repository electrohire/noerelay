"""Inference request pipeline — stage helpers and the top-level sequencer.

The sequencer orchestrates: request ledgering -> governance -> contract ->
route -> execute -> verify (real DAG) -> receipt -> OpenAI response.

Every state transition appends a ledger event before the next step runs
(EPR-LED-001). Guards are computed by :class:`gateway.statemachine.GuardEvaluator`
from live context rather than hard-coded to ``True``. Errors short-circuit
via :class:`PipelineError` with an HTTP status and an OpenAI-style body.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from epr.kernel import select_route

from .compression import compress_messages
from .config import ConfigError, GatewayConfig
from .context import ContextCompactor, ContextCompiler, build_canonical_state
from .contracts import compile_task_contract, requires_clarification
from .decoding import DecodingPhaseManager
from .escalation_policy import EscalationPolicy
from .governance import default_governance, merge_governance, validate_governance
from .local_models import (
    LocalModelError,
    StubLocalModelClient as StubLocalClient,
)
from .local_policy import extend_policy_with_local
from .local_portfolio import local_candidates
from .openrouter import (
    HttpOpenRouterClient,
    OpenRouterClient,
    OpenRouterError,
    StubOpenRouterClient,
    build_chat_payload,
)
from .policy import load_policy, validate_portfolio_against_policy
from .portfolio import load_portfolio
from .render import (
    error_envelope,
    render_chat_completion,
    render_clarification_error,
    render_epr_metadata,
    render_escalation_error,
    render_responses_object,
    render_validation_error,
)
from .epistemic import make_model_assertion_evidence
from .online_learning import CanaryTrafficRouter, PolicyVersionManager
from .provenance import ProvenanceMapper
from .runs import GATEWAY_ACTOR, ROUTER_ACTOR, RunRegistry
from .statemachine import GuardEvaluator, VerificationStateMachine
from .verification import evaluate_verification


class PipelineError(Exception):
    """Short-circuit an inference pipeline with an HTTP status and body."""

    def __init__(self, status: int, body: dict[str, Any]) -> None:
        super().__init__(f"pipeline error {status}")
        self.status = status
        self.body = body


@dataclass
class PipelineContext:
    config: GatewayConfig
    policy: dict[str, Any]
    portfolio: list[dict[str, Any]]
    openrouter_client: OpenRouterClient
    state_machine: VerificationStateMachine
    registry: RunRegistry
    escalation_policy: EscalationPolicy | None = None
    local_model_client: Any = None
    prefer_local: bool = False
    response_cache: Any = None
    logger: Any = None
    # Governance components (Phase 3)
    rbac: Any = None
    audit_logger: Any = None
    tenant_manager: Any = None
    cost_controller: Any = None
    alert_manager: Any = None
    webhook_manager: Any = None
    config_manager: Any = None
    secret_manager: Any = None
    # Analytics (Phase 5)
    analytics: Any = None
    auth: Any = None
    request_local: Any = field(default_factory=threading.local)


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


# EPR-CTX-002: narrative compaction is only attempted once context exceeds
# this size.  The skeleton's in-memory runs stay far below the threshold, so
# compaction remains a no-op in normal operation.
_CONTEXT_COMPACTION_THRESHOLD = 1000

# Fallback per-token pricing (USD per 1K tokens) used when OpenRouter does not
# return an explicit cost in its usage block. These are conservative estimates
# for the models in the shipped portfolio.
_DEFAULT_PROMPT_USD_PER_1K = 0.0015
_DEFAULT_COMPLETION_USD_PER_1K = 0.006
_MODEL_RATES_USD_PER_1K: dict[str, tuple[float, float]] = {
    # Cloud models (OpenRouter)
    "qwen/qwen3.6-35b-a3b": (0.00035, 0.0014),
    "qwen/qwen3.6-27b": (0.0003, 0.0012),
    "anthropic/claude-sonnet-4.6": (0.003, 0.015),
    "anthropic/claude-haiku-4.5": (0.001, 0.005),
    # Local models (Ollama) — zero API cost
    "qwen3:8b": (0.0, 0.0),
    "qwen3-coder:30b": (0.0, 0.0),
    "qwen3-vl:8b-thinking": (0.0, 0.0),
    "qwen38-4b-distilled:latest": (0.0, 0.0),
}


def _estimate_cost(usage: dict[str, Any], selected_plan: dict[str, Any]) -> float:
    """Estimate USD cost from token counts and model rates.

    Used only as a fallback when the upstream ``usage`` block has no explicit
    ``total_cost``/``cost`` field.  Local models always return $0.0.
    """
    gateway = str(selected_plan.get("inference_gateway") or "")
    if gateway == "local":
        return 0.0  # Local models have zero API cost
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    model_id = str(selected_plan.get("model_id") or "")
    prompt_rate, completion_rate = _MODEL_RATES_USD_PER_1K.get(
        model_id, (_DEFAULT_PROMPT_USD_PER_1K, _DEFAULT_COMPLETION_USD_PER_1K)
    )
    cost = (
        (prompt_tokens / 1000.0) * prompt_rate
        + (completion_tokens / 1000.0) * completion_rate
    )
    return round(cost, 8)


def _context_is_large(events: list[dict[str, Any]], epistemic_state: Any) -> bool:
    """True when the run context has grown enough to warrant compaction."""
    total = (
        len(events)
        + epistemic_state.claim_count()
        + epistemic_state.evidence_count()
    )
    return total >= _CONTEXT_COMPACTION_THRESHOLD


def _narrative_for(contract: dict[str, Any]) -> str:
    """Deterministic L3 narrative placeholder (skeleton has no LLM summarizer)."""
    return f"Context compiled for task {contract.get('task_id')}: {contract.get('goal')}"


def stage_route(
    contract: dict[str, Any],
    portfolio: list[dict[str, Any]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    """Run ``select_route`` and return the route decision.

    The caller inspects ``decision["status"]``: ``route_selected`` proceeds to
    execution; anything else triggers escalation handling.
    """
    return select_route(contract, portfolio, policy)


def _merge_portfolio_with_local(
    portfolio: list[dict[str, Any]],
    merged_governance: dict[str, Any],
) -> list[dict[str, Any]]:
    """Merge local model candidates into the portfolio for one request.

    Local models satisfy every data policy (they never send data to an external
    service), so each candidate's ``data_policy`` is aligned with the request's
    merged governance before routing.
    """
    return list(portfolio) + local_candidates(
        data_policy=merged_governance["data_policy"]
    )


def _plan_provider(plan: dict[str, Any]) -> str:
    """Return the provider identifier used for provider-fallback recording."""
    return str(plan.get("provider_family") or plan.get("model_id") or "")


def _plan_model(plan: dict[str, Any]) -> str:
    """Return the model identifier used for semantic-fallback recording."""
    return str(
        plan.get("model_id")
        or plan.get("action_id")
        or plan.get("candidate_id")
        or ""
    )


def _plan_requires_canary(plan: dict[str, Any]) -> bool:
    """True when a plan is marked experimental/canary-only."""
    return bool(plan.get("experimental") or plan.get("canary"))


def _runtime_flag(ctx: PipelineContext, key: str) -> bool:
    """Read a fail-safe boolean runtime control from persistent config."""
    if ctx.config_manager is None:
        return False
    value = ctx.config_manager.get(key, False)
    return value is True or (
        isinstance(value, str) and value.casefold() in {"1", "true", "yes", "on"}
    )


def _apply_route_kill_switches(
    ctx: PipelineContext, candidates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Mark administratively disabled providers/models unavailable."""
    filtered: list[dict[str, Any]] = []
    for candidate in candidates:
        item = dict(candidate)
        model_id = str(item.get("model_id") or "")
        provider = str(item.get("provider_family") or "")
        if (
            (model_id and _runtime_flag(ctx, f"kill_switch.model:{model_id}"))
            or (provider and _runtime_flag(ctx, f"kill_switch.provider:{provider}"))
        ):
            item["available"] = False
            item["disabled_by_kill_switch"] = True
        filtered.append(item)
    return filtered


def _record_fallback(
    ctx: PipelineContext,
    run_id: str,
    subject_id: str,
    record: Any,
    fallback_class: str,
    from_id: str,
    to_id: str,
    reason: str,
) -> dict[str, Any]:
    """Record a fallback event in the run record and as a ledger event."""
    event = record.fallbacks.record(fallback_class, from_id, to_id, reason)
    fallback_payload: dict[str, Any] = {
        "fallback_class": fallback_class,
        "from": from_id,
        "to": to_id,
        "reason": reason,
    }
    if record.epistemic_ledger is not None:
        enriched = record.epistemic_ledger.enrich_fallback(
            fallback_class, from_id, to_id, reason,
            record.epistemic_state,
        )
        fallback_payload.update(enriched)
    ctx.registry.ledger(
        run_id,
        "fallback_triggered",
        GATEWAY_ACTOR,
        subject_id,
        fallback_payload,
    )
    return event


def _call_openrouter(
    ctx: PipelineContext,
    request: dict[str, Any],
    plan: dict[str, Any],
    record: Any,
    client: Any = None,
) -> dict[str, Any]:
    """Send one upstream call for *plan*, honouring decoding phases.

    When *client* is provided it is used in place of the default OpenRouter
    client, allowing the same instrumentation path to serve local models.
    Also records per-run instrumentation (latency, token usage, and actual
    cost) on the run record for reporting and receipt/benchmark aggregation.
    """
    transport = client if client is not None else ctx.openrouter_client
    conformance_tested = {
        (str(pair[0]), str(pair[1]))
        for pair in (ctx.policy.get("conformance_tested_combinations") or [])
    }
    phase_manager = DecodingPhaseManager(conformance_tested=conformance_tested)
    model_id = plan.get("model_id", "unknown")
    serving_config = plan.get("inference_gateway", "")

    start = time.perf_counter()
    if phase_manager.needs_separate_phases(request, model_id, serving_config):
        phase1_payload = phase_manager.build_phase1_payload(
            request, plan, ctx.policy
        )
        phase1_result = transport.create_chat_completion(phase1_payload)
        phase2_payload = phase_manager.build_phase2_payload(
            request, plan, ctx.policy, phase1_result
        )
        record.openrouter_request = phase2_payload
        upstream = transport.create_chat_completion(phase2_payload)
    else:
        payload = build_chat_payload(plan, request, ctx.policy, ctx.config)
        record.openrouter_request = payload
        upstream = transport.create_chat_completion(payload)

    record.latency_ms = (time.perf_counter() - start) * 1000

    usage = upstream.get("usage") or {}
    record.total_tokens = int(usage.get("total_tokens") or 0)
    record.prompt_tokens = int(usage.get("prompt_tokens") or 0)
    record.completion_tokens = int(usage.get("completion_tokens") or 0)

    usage_cost = usage.get("total_cost")
    if usage_cost is None:
        usage_cost = usage.get("cost")
    record.actual_cost_usd = float(usage_cost or 0.0) or _estimate_cost(usage, plan)

    return upstream


def _execute_with_fallback(
    ctx: PipelineContext,
    request: dict[str, Any],
    decision: dict[str, Any],
    record: Any,
    run_id: str,
    subject_id: str,
) -> dict[str, Any]:
    """Execute the selected plan, retrying fallback plans on transport failure.

    EPR-ROUTE-005: provider-availability fallbacks are recorded separately from
    semantic-quality fallbacks.

    When a plan's ``inference_gateway`` is ``local`` the local model client is
    used; transport errors from local models are caught alongside OpenRouter
    errors so fallback plans can be tried.
    """
    plans = [decision["selected_plan"]] + decision.get("fallback_plans", [])
    last_error: OpenRouterError | LocalModelError | None = None
    for index, plan in enumerate(plans):
        if index > 0:
            _record_fallback(
                ctx,
                run_id,
                subject_id,
                record,
                "provider_fallback",
                _plan_provider(plans[index - 1]),
                _plan_provider(plan),
                str(last_error),
            )
        try:
            if plan.get("inference_gateway") == "local" and ctx.local_model_client is not None:
                return _call_openrouter(ctx, request, plan, record, client=ctx.local_model_client)
            return _call_openrouter(ctx, request, plan, record)
        except (OpenRouterError, LocalModelError) as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    raise OpenRouterError("no admissible plan")


def _policy_for(ctx: PipelineContext) -> EscalationPolicy:
    """Return the context escalation policy or a fresh default."""
    return ctx.escalation_policy if ctx.escalation_policy is not None else EscalationPolicy()


def _should_escalate_local(
    ctx: PipelineContext,
    prefer_local: bool,
    selected_plan: dict[str, Any],
    risk_class: str,
) -> tuple[bool, str]:
    """Decide whether a failed local attempt should escalate to cloud."""
    if not prefer_local:
        return False, "not_preferring_local"
    if selected_plan.get("inference_gateway") != "local":
        return False, "not_a_local_model"
    if ctx.local_model_client is None:
        return False, "no_local_client"
    return _policy_for(ctx).should_escalate_to_cloud(True, risk_class)


def _resolve_human_review(
    ctx: PipelineContext,
    verification_failed: bool,
    has_blocking_conflict: bool,
    risk_class: str,
) -> tuple[bool, str]:
    """Decide whether human review should be requested."""
    return _policy_for(ctx).should_request_human_review(
        verification_failed, has_blocking_conflict, risk_class
    )


def _record_completed_run(ctx: PipelineContext, record: Any) -> None:
    """Record a completed run into the escalation policy's rolling window."""
    policy = ctx.escalation_policy
    if policy is None:
        return
    policy.record_run(
        {
            "run_id": record.run_id,
            "required_human_intervention": record.required_human_intervention,
            "required_rework": record.required_rework,
        }
    )


def _execute_and_verify(
    ctx: PipelineContext,
    request: dict[str, Any],
    decision: dict[str, Any],
    record: Any,
    run_id: str,
    subject_id: str,
    contract: dict[str, Any],
    merged: dict[str, Any],
    routing_policy: dict[str, Any],
    *,
    advance_state: bool = True,
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    bool,
    bool,
    dict[str, Any],
]:
    """Execute one route decision and run the verification DAG.

    Returns ``(upstream, verification_results, evidence_records, all_dag_passed,
    dag_criteria_pass, selected_plan)``.  When *advance_state* is ``False`` the
    state-machine transitions for result recording and verification start are
    skipped (used by the local-to-cloud retry so the machine stays in
    ``verifying`` until a final pass/fail decision is made).
    """
    selected_plan = decision["selected_plan"]
    upstream = _execute_with_fallback(
        ctx, request, decision, record, run_id, subject_id
    )
    record.openrouter_response = upstream
    ctx.registry.ledger(
        run_id,
        "action_completed",
        GATEWAY_ACTOR,
        subject_id,
        {"model": upstream.get("model"), "usage": upstream.get("usage")},
    )

    # EPR-EPI-002: model output is model_assertion evidence.
    model_content = upstream.get("choices", [{}])[0].get("message", {}).get("content", "")
    model_id = upstream.get("model", selected_plan.get("model_id", "unknown"))
    model_evidence = make_model_assertion_evidence(
        model_id=model_id,
        content=model_content,
        activity_id=subject_id,
    )
    model_evidence = ProvenanceMapper.enrich_evidence(model_evidence)
    record.epistemic_state.add_evidence(model_evidence)

    verification_results, all_dag_passed, evidence_records = evaluate_verification(
        contract, upstream, merged["risk_class"], routing_policy, selected_plan,
    )

    for ev in evidence_records:
        ev = ProvenanceMapper.enrich_evidence(ev)
        record.epistemic_state.add_evidence(ev)

    if advance_state:
        # Advance through result recording and verification start.  We pass
        # True for record_result so an unbound (empty) upstream response is
        # handled as a verification failure by the schema check below rather
        # than as a state-machine crash.  The schema check enforces the same
        # non-empty-content invariant.
        ctx.state_machine.transition(run_id, "record_result", True)
        ctx.state_machine.transition(
            run_id, "start_verification",
            GuardEvaluator.required_verification_dag_materialized(verification_results),
        )

    # Build evidence_recorded payload with epistemic context
    evidence_payload: dict[str, Any] = {
        "verification_results": [
            {
                "criterion_id": r["criterion_id"],
                "status": r["status"],
                "evidence_ids": r.get("evidence_ids", []),
            }
            for r in verification_results
        ],
        "evidence_count": len(evidence_records),
    }
    if record.epistemic_ledger is not None:
        dag_steps = routing_policy.get("verification", {}).get(merged["risk_class"], [])
        enriched = record.epistemic_ledger.enrich_verification(
            verification_results, record.epistemic_state,
            merged["risk_class"], dag_steps,
        )
        evidence_payload.update(enriched)
    ctx.registry.ledger(
        run_id,
        "evidence_recorded",
        GATEWAY_ACTOR,
        subject_id,
        evidence_payload,
    )

    # Flush any pending claim transitions as ledger events
    if record.epistemic_ledger is not None:
        transitions = record.epistemic_state.get_claim_transitions()
        for t in transitions:
            enriched = record.epistemic_ledger.enrich_claim_transition(
                t["claim_id"], t["old_status"], t["new_status"],
                t["evidence_ids"], t["reasoning"],
            )
            ctx.registry.ledger(
                run_id,
                "claim_transitioned",
                GATEWAY_ACTOR,
                subject_id,
                enriched,
            )

    dag_criteria_pass = (
        GuardEvaluator.all_mandatory_criteria_pass_and_no_blocking_conflict(
            verification_results,
            epistemic_state=record.epistemic_state,
            risk_class=merged["risk_class"],
        )
    )

    return (
        upstream,
        verification_results,
        evidence_records,
        all_dag_passed,
        dag_criteria_pass,
        selected_plan,
    )


def build_pipeline_context(config: GatewayConfig) -> PipelineContext:
    """Assemble a production context, failing closed on any policy violation.

    When ``config.local_model_enabled`` is ``True`` the policy is extended to
    allow the ``local`` gateway and local model candidates are merged into the
    portfolio.  An :class:`EscalationPolicy` is always wired so HIR/RR metrics
    can be tracked.
    """
    policy = load_policy(config.policy_path)
    portfolio = load_portfolio(config.portfolio_path)

    # Extend policy and merge local models when enabled.
    if config.local_model_enabled:
        policy = extend_policy_with_local(policy)
        # Dynamic model discovery: query Ollama for available models.
        if config.openrouter_mode == "live":
            from .local_portfolio import discover_local_models

            available = discover_local_models(config.local_model_url)
            if available:
                local_cands = [
                    c
                    for c in local_candidates(data_policy="zdr")
                    if c["model_id"] in available
                ]
            else:
                local_cands = local_candidates(data_policy="zdr")
        else:
            local_cands = local_candidates(data_policy="zdr")
        portfolio = list(portfolio) + local_cands

    errors = validate_portfolio_against_policy(portfolio, policy)
    if errors:
        raise ConfigError("portfolio violates policy: " + "; ".join(errors))

    state_machine_spec = json.loads(config.state_machine_path.read_text("utf-8"))

    if config.openrouter_mode == "live":
        openrouter_client: OpenRouterClient = HttpOpenRouterClient(config)
    else:
        openrouter_client = StubOpenRouterClient(policy)

    if config.database_enabled:
        from .database import SQLiteDatabase  # local import avoids cycles
        from .db_registry import DatabaseRunRegistry  # local import avoids cycles

        db = SQLiteDatabase(config.database_path)
        registry: RunRegistry = DatabaseRunRegistry(
            db, max_runs=config.run_retention_max
        )
    elif config.persistence_dir:
        from .persistence import FileRunRegistry  # local import avoids cycles

        registry = FileRunRegistry(
            config.persistence_dir,
            max_runs=config.run_retention_max,
        )
    else:
        registry = RunRegistry(max_runs=config.run_retention_max)

    # Wire escalation policy with configured thresholds.
    escalation_policy = EscalationPolicy(
        hir_threshold=config.escalation_hir_threshold,
        rr_threshold=config.escalation_rr_threshold,
    )

    # Wire local model client when enabled.  In live mode use the real HTTP
    # client so ``--prefer-local`` actually reaches the local Ollama server;
    # in stub mode keep the deterministic, network-free stub.
    local_model_client = None
    if config.local_model_enabled:
        if config.openrouter_mode == "live":
            from .local_models import LocalModelClient

            local_model_client = LocalModelClient(
                base_url=config.local_model_url,
                model_id="default",  # Overridden per-request from the plan
                timeout=120,
            )
        else:
            from .local_models import StubLocalModelClient as StubLocal

            local_model_client = StubLocal(model_id="qwen3:8b")

    # Wire response cache when enabled.
    response_cache = None
    if config.cache_enabled:
        from .cache import ResponseCache

        response_cache = ResponseCache(
            max_size=config.cache_max_size,
            ttl_seconds=config.cache_ttl_seconds,
        )

    # Wire structured logger.
    from .structured_logging import StructuredLogger

    logger = StructuredLogger(
        name="noerelay",
        level=config.log_level,
        output=config.log_output,
        file_path=config.log_file_path if config.log_output == "file" else None,
    )

    # Wire governance components (Phase 3).
    rbac = None
    audit_logger = None
    tenant_manager = None
    cost_controller = None
    alert_manager = None
    webhook_manager = None
    config_manager = None
    secret_manager = None
    analytics = None
    auth = None

    if config.database_enabled:
        from .analytics import AnalyticsEngine
        from .rbac import RBACMiddleware
        from .audit import AuditLogger
        from .tenancy import TenantManager
        from .cost_controls import CostController
        from .alerting import AlertManager
        from .webhooks import WebhookManager
        from .config_manager import ConfigManager
        from .secrets import SecretConfigurationError, SecretManager

        rbac = RBACMiddleware()
        audit_logger = AuditLogger(db)
        tenant_manager = TenantManager(db)
        cost_controller = CostController(db, tenant_manager)
        alert_manager = AlertManager()
        webhook_manager = WebhookManager(db)
        config_manager = ConfigManager(db)
        try:
            secret_manager = SecretManager(db)
        except SecretConfigurationError:
            logger.warning(
                "secret store disabled because NOERELAY_MASTER_KEY is not configured",
                component="secrets",
            )
        analytics = AnalyticsEngine(db)

    from .auth import AuthMiddleware
    from .rate_limit import PerKeyRateLimiter

    api_key_manager = None
    if config.database_enabled:
        from .api_keys import APIKeyManager

        api_key_manager = APIKeyManager(db)
    auth = AuthMiddleware(
        api_keys={
            key.strip()
            for key in (config.auth_api_keys or "").split(",")
            if key.strip()
        },
        api_key_manager=api_key_manager,
        rate_limiter=PerKeyRateLimiter(),
        require_auth=config.auth_required,
        default_rate=config.rate_limit_rate,
        default_burst=config.rate_limit_burst,
    )

    return PipelineContext(
        config=config,
        policy=policy,
        portfolio=portfolio,
        openrouter_client=openrouter_client,
        state_machine=VerificationStateMachine(state_machine_spec),
        registry=registry,
        escalation_policy=escalation_policy,
        local_model_client=local_model_client,
        response_cache=response_cache,
        logger=logger,
        rbac=rbac,
        audit_logger=audit_logger,
        tenant_manager=tenant_manager,
        cost_controller=cost_controller,
        alert_manager=alert_manager,
        webhook_manager=webhook_manager,
        config_manager=config_manager,
        secret_manager=secret_manager,
        analytics=analytics,
        auth=auth,
    )


def run_inference_pipeline(
    request: dict[str, Any], ctx: PipelineContext, *, response_format: str = "chat"
) -> dict[str, Any]:
    """Run one inference request end-to-end and return the response body.

    ``request`` is the internal normalized shape with keys ``model``,
    ``messages``, ``governance`` (optional), and ``passthrough``.
    """
    tenant_id = str(request.get("tenant_id") or "default")
    governance = request.get("governance") or {}
    project_id = str(governance.get("project_id") or "")
    if (
        _runtime_flag(ctx, "kill_switch.global")
        or _runtime_flag(ctx, f"kill_switch.tenant:{tenant_id}")
        or (project_id and _runtime_flag(ctx, f"kill_switch.project:{project_id}"))
    ):
        raise PipelineError(
            503,
            error_envelope(
                "inference is disabled by an administrative kill switch",
                "server_error",
                None,
                "kill_switch_active",
            ),
        )

    # Check response cache only after kill switches so cached responses cannot
    # bypass an emergency stop.
    if ctx.response_cache is not None:
        cached = ctx.response_cache.get(request)
        if cached is not None:
            cached["epr"]["cache_hit"] = True
            return cached

    run_id = f"run-{uuid.uuid4().hex}"
    trace_id = f"trace-{uuid.uuid4().hex}"
    subject_id = f"task-{uuid.uuid4().hex}"

    record = ctx.registry.begin(run_id, trace_id)
    record.tenant_id = tenant_id
    registry_db = getattr(ctx.registry, "_db", None)
    if registry_db is not None and hasattr(registry_db, "set_run_tenant"):
        registry_db.set_run_tenant(run_id, tenant_id)
    ctx.state_machine.begin(run_id)

    # Resolve local-model routing preference for this request.
    prefer_local = bool(ctx.prefer_local) or bool(
        request.get("passthrough", {}).get("prefer_local", False)
    )

    request_material = json.dumps(
        {"messages": request["messages"], "passthrough": request.get("passthrough", {})},
        sort_keys=True,
        separators=(",", ":"),
    )
    ctx.registry.ledger(
        run_id,
        "request_received",
        GATEWAY_ACTOR,
        subject_id,
        {"request_sha256": _sha256(request_material)},
    )

    # Governance (EPR-API-003: deterministic defaults when absent).
    defaults = default_governance(
        max_cost_usd=ctx.config.default_max_cost_usd,
        max_latency_ms=ctx.config.default_max_latency_ms,
    )
    merged = merge_governance(request.get("governance"), defaults)
    governance_errors = validate_governance(merged)
    if governance_errors:
        ctx.registry.ledger(
            run_id,
            "outcome_rejected",
            GATEWAY_ACTOR,
            subject_id,
            {"governance_errors": governance_errors},
        )
        raise PipelineError(
            422,
            render_validation_error(
                run_id, trace_id, ctx.registry.head_hash(run_id), governance_errors
            ),
        )

    # Routing policy and portfolio.  When prefer_local is set, extend the policy
    # to allow the local gateway and merge local candidates whose data_policy
    # matches the request's merged governance.
    routing_policy = ctx.policy
    routing_portfolio = _apply_route_kill_switches(ctx, ctx.portfolio)
    if prefer_local and ctx.local_model_client is not None:
        routing_policy = extend_policy_with_local(ctx.policy)
        routing_portfolio = _apply_route_kill_switches(
            ctx,
            _merge_portfolio_with_local(ctx.portfolio, merged),
        )

    # EPR-ROUTE-006: canary traffic detection and policy version tracking.
    canary_router = CanaryTrafficRouter()
    is_canary = canary_router.is_canary(merged)
    record.canary = is_canary
    policy_manager = PolicyVersionManager(production_version=ctx.policy["version"])
    record.policy_version = policy_manager.get_production_policy_version()

    # Contract.
    contract = compile_task_contract(
        request["messages"], merged, request.get("passthrough"), task_id=subject_id
    )
    record.contract = contract
    record.task_id = subject_id

    # Record human intervention for critical-risk tasks.
    if contract["governance"].get("human_approval_required"):
        ctx.registry.record_human_intervention(
            run_id, "critical_risk_requires_human_approval"
        )

    ctx.state_machine.transition(
        run_id, "propose_contract",
        GuardEvaluator.request_schema_valid(request),
    )
    ctx.registry.ledger(
        run_id, "contract_proposed", GATEWAY_ACTOR, subject_id,
        {"goal": contract["goal"]},
    )

    # EPR-CON-003: high/critical-risk work with missing acceptance criteria
    # MUST NOT execute autonomously — transition to clarification instead.
    if requires_clarification(contract):
        ctx.state_machine.transition(run_id, "request_clarification", True)
        raise PipelineError(
            422,
            render_clarification_error(
                run_id, trace_id, ctx.registry.head_hash(run_id), contract
            ),
        )

    ctx.state_machine.transition(
        run_id, "validate_contract",
        GuardEvaluator.contract_schema_valid_and_acceptance_sufficient(contract),
    )
    ctx.registry.ledger(
        run_id, "contract_validated", GATEWAY_ACTOR, subject_id,
        {"version": contract["version"]},
    )
    ctx.state_machine.transition(
        run_id, "check_policy",
        GuardEvaluator.policy_allows_progress(),
    )
    policy_actor = {
        "id": "epr-default-routing-policy",
        "kind": "policy",
        "version": ctx.policy["version"],
    }
    ctx.registry.ledger(
        run_id,
        "policy_checked",
        policy_actor,
        subject_id,
        {
            "allowed": True,
            "policy_version": record.policy_version,
            "canary": record.canary,
        },
    )
    # EPR-CTX-006: compile the context package by graph reachability from the
    # current task contract, not by transcript recency alone.
    compiler = ContextCompiler()
    record.context_package = compiler.compile(
        contract, record.epistemic_state, list(record.events)
    )

    # EPR-CTX-001: capture canonical L1 state that survives compaction.
    record.canonical_state = build_canonical_state(
        record.epistemic_state, record.events
    )

    # EPR-CTX-002: compact only when context grows large.  The skeleton's
    # in-memory runs stay below the threshold, so this is a no-op normally.
    capsule_id = None
    if _context_is_large(record.events, record.epistemic_state):
        compactor = ContextCompactor()
        capsule = compactor.compact(
            record.canonical_state,
            narrative=_narrative_for(contract),
            canonical_claims=[],
            mandatory_failed_check_ids=sorted(
                record.canonical_state.failed_mandatory_check_ids
            ),
            task_id=subject_id,
            source_ledger_head_hash=ctx.registry.head_hash(run_id),
        )
        capsule_id = capsule["capsule_id"]
        record.context_package["capsule"] = capsule

    ctx.state_machine.transition(
        run_id, "compile_context",
        GuardEvaluator.compaction_invariants_hold(),
    )
    ctx.registry.ledger(
        run_id, "context_compiled", GATEWAY_ACTOR, subject_id, {"capsule_id": capsule_id}
    )

    # ------------------------------------------------------------------
    # RTK Phase 1: context compression (between context_compiled and
    # route_selected). The router sees original token counts; the LLM
    # receives compressed messages.
    # ------------------------------------------------------------------
    original_messages = list(request["messages"])
    compression_result = compress_messages(request["messages"], ctx.config.compression)

    # Record original token count before compression for cost tracking.
    record.original_prompt_tokens = compression_result.original_token_count

    if not compression_result.skipped:
        # Replace request messages with compressed version for the LLM call.
        request["messages"] = compression_result.compressed_messages

    ctx.registry.ledger(
        run_id,
        "context_compressed",
        GATEWAY_ACTOR,
        subject_id,
        {
            "original_token_count": compression_result.original_token_count,
            "compressed_token_count": compression_result.compressed_token_count,
            "compression_ratio": compression_result.compression_ratio,
            "strategy": compression_result.strategy,
            "duration_ms": compression_result.duration_ms,
            "tokens_saved": compression_result.tokens_saved,
            "skipped": compression_result.skipped,
        },
    )

    # Route (EPR-ROUTE-004: retain rejected-candidate reasons).
    decision = stage_route(contract, routing_portfolio, routing_policy)
    record.decision = decision

    if decision["status"] != "route_selected":
        ctx.state_machine.transition(
            run_id, "no_admissible_route",
            GuardEvaluator.router_failed_closed(),
        )
        ctx.registry.ledger(run_id, "route_selected", ROUTER_ACTOR, subject_id, decision)
        ctx.state_machine.transition(
            run_id, "reject",
            GuardEvaluator.no_authorized_escalation(),
        )
        ctx.registry.ledger(
            run_id,
            "outcome_rejected",
            GATEWAY_ACTOR,
            subject_id,
            {"decision_id": decision.get("decision_id")},
        )
        ctx.registry.issue_receipt(run_id, "escalated", [], 0.0)
        raise PipelineError(
            424,
            render_escalation_error(
                run_id, trace_id, ctx.registry.head_hash(run_id), decision
            ),
        )

    ctx.state_machine.transition(
        run_id, "select_route",
        GuardEvaluator.admissible_route_exists(decision),
    )
    # Enrich route_selected with epistemic context
    route_payload = dict(decision)
    if record.epistemic_ledger is not None:
        enriched = record.epistemic_ledger.enrich_route_selection(
            decision, record.epistemic_state, contract, routing_policy
        )
        route_payload.update(enriched)
        record.decision_trace = record.epistemic_ledger.decision_trace(run_id)
    ctx.registry.ledger(run_id, "route_selected", ROUTER_ACTOR, subject_id, route_payload)

    # EPR-ROUTE-006: production traffic must not use experimental models/policies.
    if _plan_requires_canary(decision["selected_plan"]) and not is_canary:
        ctx.state_machine.transition(
            run_id, "reject", GuardEvaluator.no_authorized_escalation()
        )
        ctx.registry.ledger(
            run_id,
            "outcome_rejected",
            GATEWAY_ACTOR,
            subject_id,
            {
                "decision_id": decision.get("decision_id"),
                "reason": "experimental_requires_canary",
            },
        )
        ctx.registry.issue_receipt(run_id, "escalated", [], 0.0)
        raise PipelineError(
            403,
            error_envelope(
                "experimental model requires canary traffic",
                "policy_denied_error",
                "model",
                "experimental_requires_canary",
            ),
        )

    # Execute.
    selected_plan = decision["selected_plan"]
    ctx.state_machine.transition(
        run_id, "start_action",
        GuardEvaluator.budget_and_permissions_reserved(selected_plan, contract),
    )
    ctx.registry.ledger(
        run_id,
        "action_started",
        GATEWAY_ACTOR,
        subject_id,
        {
            "model_id": selected_plan.get("model_id"),
            "gateway": selected_plan.get("inference_gateway"),
        },
    )
    (
        upstream,
        verification_results,
        evidence_records,
        all_dag_passed,
        dag_criteria_pass,
        selected_plan,
    ) = _execute_and_verify(
        ctx, request, decision, record, run_id, subject_id,
        contract, merged, routing_policy,
    )

    # Local-to-cloud escalation: retry with the cloud (OpenRouter) model.
    if not (dag_criteria_pass and all_dag_passed):
        ctx.registry.record_rework(run_id, "initial_attempt_failed_verification")

        should_escalate, _escalate_reason = _should_escalate_local(
            ctx, prefer_local, selected_plan, merged["risk_class"]
        )
        if should_escalate:
            cloud_decision = stage_route(contract, ctx.portfolio, ctx.policy)
            if cloud_decision.get("status") == "route_selected":
                _record_fallback(
                    ctx, run_id, subject_id, record,
                    "semantic_fallback",
                    _plan_model(selected_plan),
                    _plan_model(cloud_decision["selected_plan"]),
                    "local_to_cloud_escalation",
                )
                ctx.registry.record_rework(run_id, "local_to_cloud_escalation")
                (
                    upstream,
                    verification_results,
                    evidence_records,
                    all_dag_passed,
                    dag_criteria_pass,
                    selected_plan,
                ) = _execute_and_verify(
                    ctx, request, cloud_decision, record, run_id, subject_id,
                    contract, merged, ctx.policy, advance_state=False,
                )

    if not (dag_criteria_pass and all_dag_passed):
        # EPR-ROUTE-005: record a semantic fallback when a fallback plan exists.
        fallback_plans = decision.get("fallback_plans", [])
        if fallback_plans:
            _record_fallback(
                ctx,
                run_id,
                subject_id,
                record,
                "semantic_fallback",
                _plan_model(selected_plan),
                _plan_model(fallback_plans[0]),
                "verification_failed",
            )
            ctx.registry.record_rework(
                run_id, "semantic_fallback_after_verification_failure"
            )

        # Escalation policy decides whether human review is required.
        has_blocking_conflict = bool(
            record.epistemic_state.conflicted_claim_ids()
        )
        should_review, review_reason = _resolve_human_review(
            ctx, True, has_blocking_conflict, merged["risk_class"]
        )
        if should_review:
            ctx.registry.record_human_intervention(run_id, review_reason)
            # Record human_review_requested ledger event with epistemic context
            hr_payload: dict[str, Any] = {
                "reason": review_reason,
                "risk_class": merged["risk_class"],
            }
            if record.epistemic_ledger is not None:
                enriched = record.epistemic_ledger.enrich_human_review(
                    review_reason, record.epistemic_state,
                    verification_results, merged["risk_class"],
                )
                hr_payload.update(enriched)
            ctx.registry.ledger(
                run_id,
                "human_review_requested",
                GATEWAY_ACTOR,
                subject_id,
                hr_payload,
            )

        # Verification failed — escalate (skeleton: no repair paths).
        verif_fail_payload: dict[str, Any] = {
            "status": "failed", "risk_class": merged["risk_class"]
        }
        if record.epistemic_ledger is not None:
            dag_steps = routing_policy.get("verification", {}).get(merged["risk_class"], [])
            enriched = record.epistemic_ledger.enrich_verification(
                verification_results, record.epistemic_state,
                merged["risk_class"], dag_steps,
            )
            verif_fail_payload.update(enriched)
        ctx.registry.ledger(
            run_id,
            "verification_completed",
            GATEWAY_ACTOR,
            subject_id,
            verif_fail_payload,
        )
        ctx.state_machine.transition(
            run_id, "verification_failed_unresolved",
            GuardEvaluator.no_admissible_repair(),
        )
        ctx.state_machine.transition(
            run_id, "reject",
            GuardEvaluator.no_authorized_escalation(),
        )
        # Enrich outcome_rejected with epistemic context
        outcome_rej_payload: dict[str, Any] = {
            "decision_id": decision.get("decision_id"), "reason": "verification_failed"
        }
        if record.epistemic_ledger is not None:
            enriched = record.epistemic_ledger.enrich_outcome(
                "rejected", decision, record.epistemic_state,
                verification_results, 0.0,
            )
            outcome_rej_payload.update(enriched)
        ctx.registry.ledger(
            run_id,
            "outcome_rejected",
            GATEWAY_ACTOR,
            subject_id,
            outcome_rej_payload,
        )
        ctx.registry.issue_receipt(run_id, "escalated", verification_results, 0.0)
        _record_completed_run(ctx, record)
        raise PipelineError(
            500,
            render_escalation_error(
                run_id, trace_id, ctx.registry.head_hash(run_id), decision
            ),
        )

    ctx.state_machine.transition(run_id, "verification_passed", True)

    # Enrich verification_completed (passed) with epistemic context
    verif_pass_payload: dict[str, Any] = {
        "status": "passed", "risk_class": merged["risk_class"]
    }
    if record.epistemic_ledger is not None:
        dag_steps = routing_policy.get("verification", {}).get(merged["risk_class"], [])
        enriched = record.epistemic_ledger.enrich_verification(
            verification_results, record.epistemic_state,
            merged["risk_class"], dag_steps,
        )
        verif_pass_payload.update(enriched)
    ctx.registry.ledger(
        run_id, "verification_completed", GATEWAY_ACTOR, subject_id,
        verif_pass_payload,
    )

    # Accept + receipt. ``outcome_accepted`` is appended last, then the receipt
    # is constructed so its ledger_head_hash binds the complete chain.
    ctx.state_machine.transition(run_id, "issue_receipt", True)
    total_cost = float(selected_plan["expected_total_cost_usd"])
    # Enrich outcome_accepted with epistemic context
    outcome_acc_payload: dict[str, Any] = {
        "decision_id": decision.get("decision_id")
    }
    if record.epistemic_ledger is not None:
        enriched = record.epistemic_ledger.enrich_outcome(
            "accepted", decision, record.epistemic_state,
            verification_results, total_cost,
        )
        outcome_acc_payload.update(enriched)
        record.decision_trace = record.epistemic_ledger.decision_trace(run_id)
    ctx.registry.ledger(
        run_id,
        "outcome_accepted",
        GATEWAY_ACTOR,
        subject_id,
        outcome_acc_payload,
    )
    ctx.registry.issue_receipt(run_id, "accepted", verification_results, total_cost)

    _record_completed_run(ctx, record)

    content = upstream["choices"][0]["message"]["content"]
    fallback_summary = record.fallbacks.get_fallback_summary()
    compression_meta = None
    if not compression_result.skipped:
        compression_meta = {
            "enabled": True,
            "original_tokens": compression_result.original_token_count,
            "compressed_tokens": compression_result.compressed_token_count,
            "ratio": compression_result.compression_ratio,
            "strategy": compression_result.strategy,
            "duration_ms": compression_result.duration_ms,
            "tokens_saved": compression_result.tokens_saved,
        }
    epr = render_epr_metadata(
        run_id=run_id,
        trace_id=trace_id,
        status="accepted",
        route_decision_id=decision["decision_id"],
        external_base_url=ctx.config.external_base_url,
        ledger_head_hash=ctx.registry.head_hash(run_id),
        total_cost_usd=total_cost,
        actual_cost_usd=record.actual_cost_usd,
        total_tokens=record.total_tokens,
        latency_ms=record.latency_ms,
        provider_fallback_count=fallback_summary["provider_fallback_count"],
        semantic_fallback_count=fallback_summary["semantic_fallback_count"],
        required_human_intervention=record.required_human_intervention,
        required_rework=record.required_rework,
        compression=compression_meta,
    )
    usage = {
        "prompt_tokens": record.prompt_tokens,
        "completion_tokens": record.completion_tokens,
        "total_tokens": record.total_tokens,
    }
    if response_format == "responses":
        result = render_responses_object(
            run_id=run_id, content=content, epr=epr, usage=usage
        )
    else:
        result = render_chat_completion(
            run_id=run_id, content=content, epr=epr, usage=usage
        )

    # Store successful response in cache.
    if ctx.response_cache is not None:
        ctx.response_cache.put(request, result)

    # Phase 3: Record tenant spend.
    if ctx.tenant_manager is not None and record.actual_cost_usd > 0:
        ctx.tenant_manager.record_spend(tenant_id, record.actual_cost_usd, run_id)

    # Phase 3: Deliver webhook for run.completed.
    if ctx.webhook_manager is not None:
        ctx.webhook_manager.deliver(
            "run.completed",
            {
                "run_id": run_id,
                "trace_id": trace_id,
                "status": "accepted",
                "model_id": selected_plan.get("model_id", "unknown"),
                "cost_usd": record.actual_cost_usd,
                "latency_ms": record.latency_ms,
                "total_tokens": record.total_tokens,
                "hir": record.required_human_intervention,
                "rr": record.required_rework,
            },
        )

    # Phase 3: Trigger alerts for high HIR or rework.
    if ctx.alert_manager is not None:
        if record.required_human_intervention:
            ctx.alert_manager.trigger_alert(
                "high_hir",
                "warning",
                f"Human intervention required for run {run_id}",
                {"run_id": run_id, "reason": record.human_intervention_reason},
            )
        if record.required_rework:
            ctx.alert_manager.trigger_alert(
                "high_rr",
                "warning",
                f"Rework required for run {run_id}",
                {"run_id": run_id, "reason": record.rework_reason},
            )

    return result
