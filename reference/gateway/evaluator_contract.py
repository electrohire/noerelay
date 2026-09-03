"""Live agent evaluator contract consumer for NoeRelay gateway responses.

Parses the ``x-noerelay-evaluator-result`` header from gateway HTTP responses
and converts it into :class:`EvaluatorResult` objects from the benchmark
evaluator contract module. This bridges the Rust-side evaluator contract
(produced by the gateway's ``release_response()``) with the Python-side
self-improvement pipeline.

Usage::

    from reference.gateway.evaluator_contract import parse_evaluator_result

    response = requests.post("http://127.0.0.1:8080/v1/chat/completions", ...)
    result = parse_evaluator_result(response.headers)
    if result:
        print(f"Outcome: {result.outcome}")
        for f in result.findings:
            print(f"  {f.severity}: {f.description}")
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Ensure reference/benchmark is importable
_reference = Path(__file__).resolve().parents[1] / "benchmark"
if str(_reference.parent) not in sys.path:
    sys.path.insert(0, str(_reference.parent))

from benchmark.evaluator_contract import (
    EvaluatorInfo,
    EvaluatorMetadata,
    EvaluatorResult,
    EvidenceRef,
    Finding,
    ModelRouting,
    NextAction,
    validate_result,
)

# Header name used by the gateway
EVALUATOR_RESULT_HEADER = "x-noerelay-evaluator-result"


def parse_evaluator_result(
    headers: dict[str, str] | Any,
) -> EvaluatorResult | None:
    """Parse an evaluator result from HTTP response headers.

    Args:
        headers: Response headers dict (case-insensitive) or a requests-like
                 headers object.

    Returns:
        An :class:`EvaluatorResult` if the header is present and valid,
        or ``None`` if the header is missing or malformed.
    """
    # Normalize to a plain dict
    if not isinstance(headers, dict):
        try:
            headers = dict(headers)
        except (TypeError, ValueError):
            return None

    # Case-insensitive lookup
    value = None
    for key, val in headers.items():
        if key.lower() == EVALUATOR_RESULT_HEADER:
            value = val
            break

    if not value:
        return None

    return parse_evaluator_result_json(value)


def parse_evaluator_result_json(json_str: str) -> EvaluatorResult | None:
    """Parse an evaluator result from a JSON string.

    Args:
        json_str: JSON string conforming to the evaluator result schema.

    Returns:
        An :class:`EvaluatorResult` if valid, or ``None`` if parsing fails.
    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return None

    errors = validate_result(data)
    if errors:
        return None

    return _from_dict(data)


def _from_dict(data: dict[str, Any]) -> EvaluatorResult:
    """Deserialize an EvaluatorResult from a dict."""
    ev = data["evaluator"]
    evaluator = EvaluatorInfo(
        id=ev["id"],
        version=ev["version"],
        name=ev.get("name", ""),
        url=ev.get("url", ""),
    )

    findings = []
    for fd in data.get("findings", []):
        evidence_refs = [
            EvidenceRef(
                ref=er["ref"],
                kind=er["kind"],
                description=er.get("description", ""),
            )
            for er in fd.get("evidence_refs", [])
        ]
        findings.append(
            Finding(
                id=fd["id"],
                severity=fd["severity"],
                kind=fd["kind"],
                subject=fd["subject"],
                description=fd.get("description", ""),
                evidence_refs=evidence_refs,
                provenance_refs=fd.get("provenance_refs", []),
                uncertainty=fd.get("uncertainty", "none"),
                recommended_action=fd.get("recommended_action", "none"),
                rationale=fd.get("rationale", ""),
            )
        )

    na = data.get("next_action")
    next_action = None
    if na:
        next_action = NextAction(
            kind=na["kind"],
            target_phase=na.get("target_phase"),
            message=na.get("message", ""),
        )

    mr = data.get("model_routing")
    model_routing = None
    if mr:
        from benchmark.evaluator_contract import EscalationTrigger, TierEstimate

        triggers = [
            EscalationTrigger(
                condition=et["condition"], escalate_to=et["escalate_to"]
            )
            for et in mr.get("escalation_triggers", [])
        ]
        breakdown = {}
        for tier_key in ("budget", "standard", "premium"):
            if tier_key in mr.get("tier_breakdown", {}):
                tb = mr["tier_breakdown"][tier_key]
                breakdown[tier_key] = TierEstimate(
                    estimated_tokens=tb.get("estimated_tokens", 0),
                    estimated_cost_usd=tb.get("estimated_cost_usd", 0.0),
                )
        model_routing = ModelRouting(
            recommended_tier=mr["recommended_tier"],
            reason=mr.get("reason", ""),
            escalation_triggers=triggers,
            estimated_tokens=mr.get("estimated_tokens", 0),
            estimated_cost_usd=mr.get("estimated_cost_usd", 0.0),
            tier_breakdown=breakdown,
        )

    md = data.get("metadata", {})
    metadata = EvaluatorMetadata(
        timestamp=md.get("timestamp", ""),
        duration_ms=md.get("duration_ms", 0),
        artifacts_evaluated=md.get("artifacts_evaluated", []),
        model=md.get("model"),
        deterministic=md.get("deterministic", True),
    )

    return EvaluatorResult(
        evaluator=evaluator,
        phase=data["phase"],
        outcome=data["outcome"],
        findings=findings,
        summary=data.get("summary", ""),
        next_action=next_action,
        model_routing=model_routing,
        metadata=metadata,
        state=data.get("state", {}),
    )


def extract_evaluator_result_from_response(
    response: Any,
) -> EvaluatorResult | None:
    """Extract evaluator result from a requests-like response object.

    Checks both the ``x-noerelay-evaluator-result`` header and the response
    body for an ``epr.evaluator_result`` field.

    Args:
        response: A response object with ``.headers`` and ``.json()``.

    Returns:
        An :class:`EvaluatorResult` if found, or ``None``.
    """
    # Try header first
    result = parse_evaluator_result(response.headers)
    if result is not None:
        return result

    # Try body
    try:
        body = response.json()
    except (ValueError, AttributeError):
        return None

    if isinstance(body, dict):
        epr = body.get("epr", {})
        if isinstance(epr, dict):
            er_data = epr.get("evaluator_result")
            if isinstance(er_data, dict):
                errors = validate_result(er_data)
                if not errors:
                    return _from_dict(er_data)

    return None


def build_evaluator_result_from_check_results(
    run_id: str,
    check_results: list[dict[str, Any]],
    check_kinds: list[dict[str, Any]] | None = None,
) -> EvaluatorResult:
    """Build an :class:`EvaluatorResult` from raw check results.

    This mirrors the Rust-side ``build_evaluator_result()`` function in the
    gateway, allowing Python consumers to reconstruct evaluator results from
    stored check data.

    Args:
        run_id: The run identifier.
        check_results: List of check result dicts with evaluator-contract fields.
        check_kinds: Optional list of check kind dicts for context.

    Returns:
        An :class:`EvaluatorResult`.
    """
    findings = []
    for cr in check_results:
        check_id = cr.get("check_id", "unknown")
        status = cr.get("status", "not_run")
        evidence_id = cr.get("observed_evidence_id")

        # Map status to finding fields
        if status == "passed":
            severity = cr.get("finding_severity", "info")
            kind = cr.get("finding_kind", "other")
            description = cr.get("description", "Check '%s' passed." % check_id)
            recommended_action = cr.get("recommended_action", "none")
            evidence_kind = cr.get("evidence_kind", "observed")
            uncertainty = cr.get("uncertainty", "none")
        elif status == "failed":
            severity = cr.get("finding_severity", "high")
            kind = cr.get("finding_kind", "schema_violation")
            description = cr.get("description", "Check '%s' failed." % check_id)
            recommended_action = cr.get("recommended_action", "revise")
            evidence_kind = cr.get("evidence_kind", "observed")
            uncertainty = cr.get("uncertainty", "none")
        elif status == "claimed":
            severity = cr.get("finding_severity", "medium")
            kind = cr.get("finding_kind", "unverified_assertion")
            description = cr.get(
                "description", "Check '%s' was claimed but not observed." % check_id
            )
            recommended_action = cr.get("recommended_action", "gather_evidence")
            evidence_kind = cr.get("evidence_kind", "asserted")
            uncertainty = cr.get("uncertainty", "medium")
        else:  # not_run
            severity = cr.get("finding_severity", "medium")
            kind = cr.get("finding_kind", "missing_evidence")
            description = cr.get(
                "description", "Check '%s' was not run." % check_id
            )
            recommended_action = cr.get("recommended_action", "gather_evidence")
            evidence_kind = cr.get("evidence_kind", "unsupported")
            uncertainty = cr.get("uncertainty", "insufficient_evidence")

        evidence_refs = []
        if evidence_id:
            evidence_refs.append(
                EvidenceRef(
                    ref=evidence_id,
                    kind=evidence_kind,
                    description="%s evidence for check '%s'"
                    % (evidence_kind.title(), check_id),
                )
            )

        findings.append(
            Finding(
                id="CHK-%s" % check_id[:8],
                severity=severity,
                kind=kind,
                subject=run_id,
                description=description,
                evidence_refs=evidence_refs,
                provenance_refs=[run_id],
                uncertainty=uncertainty,
                recommended_action=recommended_action,
                rationale=cr.get("rationale", ""),
            )
        )

    from benchmark.evaluator_contract import make_result

    return make_result(
        evaluator_id="noerelay-gateway",
        evaluator_version="1.0.0",
        phase="after_implement",
        findings=findings,
        evaluator_name="NoeRelay Gateway",
        deterministic=True,
    )