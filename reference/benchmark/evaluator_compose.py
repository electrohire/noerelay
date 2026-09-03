"""Compose multiple evaluator results with deterministic precedence.

Adapted from the spec-kit-evaluator extension's ``compose_results.py``.
Reads evaluator result files, validates them, and produces a composed
result with deterministic outcome resolution.

Composition strategies:
- ``strict`` (default): Most severe outcome wins.
- ``majority``: Most common outcome wins; ties break toward severity.
- ``optimistic``: Least severe outcome wins.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .evaluator_contract import (
    SCHEMA_VERSION,
    EvaluatorInfo,
    EvaluatorMetadata,
    EvaluatorResult,
    EvidenceRef,
    Finding,
    ModelRouting,
    NextAction,
    Outcome,
    TierEstimate,
    derive_next_action,
    load_result_file,
    make_timestamp,
)

# -- Outcome precedence for strict composition (most severe first) -------------
_STRICT_PRECEDENCE: list[Outcome] = [
    "block",
    "gather_evidence",
    "iterate",
    "clarify",
    "warn",
    "pass",
]

_SEVERITY_ORDER: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}


def _resolve_outcome_strict(outcomes: list[Outcome]) -> Outcome:
    """Return the most severe outcome from the list."""
    for candidate in _STRICT_PRECEDENCE:
        if candidate in outcomes:
            return candidate
    return "pass"


def _resolve_outcome_majority(outcomes: list[Outcome]) -> Outcome:
    """Return the outcome with the most evaluators supporting it.

    Ties break toward the more severe outcome (strict precedence).
    """
    counts: dict[str, int] = {}
    for o in outcomes:
        counts[o] = counts.get(o, 0) + 1
    max_count = max(counts.values())
    tied = [o for o, c in counts.items() if c == max_count]
    if len(tied) == 1:
        return tied[0]  # type: ignore[return-value]
    return _resolve_outcome_strict(tied)  # type: ignore[arg-type]


def _resolve_outcome_optimistic(outcomes: list[Outcome]) -> Outcome:
    """Return the least severe outcome."""
    for candidate in reversed(_STRICT_PRECEDENCE):
        if candidate in outcomes:
            return candidate
    return "pass"


def resolve_outcome(outcomes: list[Outcome], strategy: str = "strict") -> Outcome:
    """Resolve a composed outcome from multiple evaluator outcomes."""
    if not outcomes:
        return "pass"
    if strategy == "majority":
        return _resolve_outcome_majority(outcomes)
    if strategy == "optimistic":
        return _resolve_outcome_optimistic(outcomes)
    return _resolve_outcome_strict(outcomes)


def _severity_sort_key(finding: Finding) -> tuple[int, str]:
    return (_SEVERITY_ORDER.get(finding.severity, 99), finding.id)


def _detect_contradictions(findings: list[Finding]) -> list[dict[str, Any]]:
    """Detect pairs of findings that contradict each other on the same subject."""
    by_subject: dict[str, list[Finding]] = {}
    for f in findings:
        by_subject.setdefault(f.subject, []).append(f)

    contradictions: list[dict[str, Any]] = []
    for subject, group in by_subject.items():
        if len(group) < 2:
            continue
        kinds = {f.kind for f in group}
        has_positive = any(
            k in ("observed", "schema_violation")
            for k in kinds
        )
        has_negative = any(
            k
            in (
                "unsupported_claim",
                "contradiction",
                "missing_evidence",
                "unverified_assertion",
            )
            for k in kinds
        )
        if has_positive and has_negative:
            contradictions.append(
                {
                    "subject": subject,
                    "finding_ids": [f.id for f in group],
                    "description": "Conflicting findings on subject '%s'" % subject,
                }
            )
    return contradictions


def compose_results(
    results: list[EvaluatorResult],
    phase: str,
    strategy: str = "strict",
) -> EvaluatorResult:
    """Compose multiple evaluator results into a single result.

    Args:
        results: List of evaluator results to compose.
        phase: The lifecycle phase these results belong to.
        strategy: Composition strategy (``strict``, ``majority``, or ``optimistic``).

    Returns:
        A composed EvaluatorResult.
    """
    if not results:
        return EvaluatorResult(
            evaluator=EvaluatorInfo(id="composed", version="1.0.0", name="Composed Evaluator"),
            phase=phase,  # type: ignore[arg-type]
            outcome="pass",
            summary="No evaluator results to compose.",
            next_action=derive_next_action("pass"),
            metadata=EvaluatorMetadata(
                timestamp=make_timestamp(),
                deterministic=True,
            ),
        )

    # Collect all findings, deduplicating by ID
    seen_ids: set[str] = set()
    all_findings: list[Finding] = []
    for r in results:
        for f in r.findings:
            if f.id not in seen_ids:
                seen_ids.add(f.id)
                all_findings.append(f)

    # Sort by severity (critical first)
    all_findings.sort(key=_severity_sort_key)

    # Resolve outcome
    outcomes: list[Outcome] = [r.outcome for r in results]
    composed_outcome = resolve_outcome(outcomes, strategy)

    # Detect contradictions
    contradictions = _detect_contradictions(all_findings)

    # Merge model routing (most conservative wins)
    model_routing = _merge_model_routing(results, composed_outcome)

    # Build evaluator info from contributors
    contributor_ids = sorted({r.evaluator.id for r in results})
    evaluator = EvaluatorInfo(
        id="composed",
        version="1.0.0",
        name="Composed (%s)" % ", ".join(contributor_ids),
    )

    # Build summary
    total_findings = len(all_findings)
    by_sev: dict[str, int] = {}
    for f in all_findings:
        by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
    sev_parts = []
    for sev in ("critical", "high", "medium", "low", "info"):
        if sev in by_sev:
            sev_parts.append("%d %s" % (by_sev[sev], sev))
    summary = (
        "Composed %d evaluator(s) with %d total finding(s) (%s). "
        "Outcome: %s. Strategy: %s."
        % (
            len(results),
            total_findings,
            ", ".join(sev_parts) if sev_parts else "none",
            composed_outcome,
            strategy,
        )
    )

    # Build metadata
    total_duration = sum(r.metadata.duration_ms for r in results)
    all_artifacts: list[str] = []
    for r in results:
        for a in r.metadata.artifacts_evaluated:
            if a not in all_artifacts:
                all_artifacts.append(a)

    return EvaluatorResult(
        evaluator=evaluator,
        phase=phase,  # type: ignore[arg-type]
        outcome=composed_outcome,
        findings=all_findings,
        summary=summary,
        next_action=derive_next_action(composed_outcome),
        model_routing=model_routing,
        metadata=EvaluatorMetadata(
            timestamp=make_timestamp(),
            duration_ms=total_duration,
            artifacts_evaluated=all_artifacts,
            deterministic=all(r.metadata.deterministic for r in results),
        ),
        state={"contributor_ids": contributor_ids, "contradictions": contradictions},
    )


def _merge_model_routing(
    results: list[EvaluatorResult], composed_outcome: Outcome
) -> ModelRouting | None:
    """Merge model routing recommendations from multiple evaluators.

    When multiple evaluators provide model_routing, the most conservative
    (highest tier) recommendation wins.
    """
    routings = [r.model_routing for r in results if r.model_routing is not None]
    if not routings:
        return None

    tier_precedence = {"premium": 3, "standard": 2, "budget": 1, "portfolio": 2}

    # Most conservative (highest tier) wins
    tiers = [mr.recommended_tier for mr in routings]  # type: ignore[union-attr]
    best_tier = max(tiers, key=lambda t: tier_precedence.get(t, 0))

    # Merge escalation triggers
    all_triggers = []
    for mr in routings:
        all_triggers.extend(mr.escalation_triggers)  # type: ignore[union-attr]

    # Merge tier breakdowns (take max estimates)
    merged_breakdown: dict[str, TierEstimate] = {}
    for tier_key in ("budget", "standard", "premium"):
        estimates = [
            mr.tier_breakdown.get(tier_key)  # type: ignore[union-attr]
            for mr in routings
            if mr.tier_breakdown.get(tier_key)  # type: ignore[union-attr]
        ]
        if estimates:
            merged_breakdown[tier_key] = TierEstimate(
                estimated_tokens=max(e.estimated_tokens for e in estimates if e),
                estimated_cost_usd=max(e.estimated_cost_usd for e in estimates if e),
            )

    # Find the routing that recommended the winning tier for the reason
    winning = next(
        (mr for mr in routings if mr.recommended_tier == best_tier),  # type: ignore[union-attr]
        routings[0],
    )

    return ModelRouting(
        recommended_tier=best_tier,
        reason=winning.reason,  # type: ignore[union-attr]
        escalation_triggers=all_triggers,
        estimated_tokens=winning.estimated_tokens,  # type: ignore[union-attr]
        estimated_cost_usd=winning.estimated_cost_usd,  # type: ignore[union-attr]
        tier_breakdown=merged_breakdown,
    )


def compose_from_directory(
    results_dir: Path,
    phase: str,
    strategy: str = "strict",
) -> EvaluatorResult:
    """Load all evaluator result files from a directory and compose them.

    Args:
        results_dir: Directory containing evaluator result JSON files.
        phase: The lifecycle phase to filter by.
        strategy: Composition strategy.

    Returns:
        A composed EvaluatorResult.
    """
    results: list[EvaluatorResult] = []
    if results_dir.is_dir():
        for filepath in sorted(results_dir.glob("*.json")):
            # Skip already-composed files
            if filepath.name.startswith("composed-"):
                continue
            result = load_result_file(filepath)
            if result is not None and result.phase == phase:
                results.append(result)
    return compose_results(results, phase, strategy)


def write_composed_result(
    result: EvaluatorResult,
    output_dir: Path,
    phase: str,
) -> Path:
    """Write a composed result to the standard file path.

    Returns the path to the written file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = "composed-%s-%s.json" % (phase, ts)
    filepath = output_dir / filename
    result.write(filepath)
    return filepath