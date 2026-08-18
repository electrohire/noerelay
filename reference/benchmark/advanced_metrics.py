"""Advanced benchmark metric calculators used by the promotion gates."""

from __future__ import annotations

from typing import Any

from .metrics import CaseResult


def compute_brier_score(predictions: list[float], outcomes: list[bool]) -> float:
    """Compute Brier score: mean((predicted_probability - actual_outcome)^2).

    Lower is better. 0 = perfect, 1 = worst.
    """
    if not predictions:
        return 0.0
    return sum(
        (float(p) - (1.0 if o else 0.0)) ** 2
        for p, o in zip(predictions, outcomes)
    ) / len(predictions)


def compute_selective_risk(
    results: list[CaseResult], confidence_threshold: float = 0.5
) -> float:
    """Compute selective risk: fraction of incorrect answers that were accepted
    with confidence >= threshold.

    selective_risk = incorrect_accepted / total_accepted
    """
    accepted = [
        r for r in results if r.metadata.get("confidence", 1.0) >= confidence_threshold
    ]
    if not accepted:
        return 0.0
    incorrect_accepted = sum(1 for r in accepted if not r.is_correct)
    return incorrect_accepted / len(accepted)


def compute_route_regret(
    results: list[CaseResult], optimal_costs: list[float]
) -> float:
    """Compute route regret: mean(actual_cost - optimal_cost) in USD.

    Measures how much more expensive the chosen route was vs the optimal.
    """
    if not results or not optimal_costs:
        return 0.0
    return sum(
        r.actual_cost_usd - opt for r, opt in zip(results, optimal_costs)
    ) / len(results)


def compute_context_evidence_recall(
    retrieved_evidence: list[str], relevant_evidence: list[str]
) -> float:
    """Compute context evidence recall: fraction of relevant evidence that was
    retrieved.

    recall = |retrieved ∩ relevant| / |relevant|
    """
    if not relevant_evidence:
        return 1.0
    retrieved_set = set(retrieved_evidence)
    relevant_set = set(relevant_evidence)
    return len(retrieved_set & relevant_set) / len(relevant_set)


def compute_replay_success_rate(
    original_results: list[CaseResult], replay_results: list[CaseResult]
) -> float:
    """Compute replay success rate: fraction of cases that produce the same
    correctness result on replay.

    replay_success = consistent_results / total_results
    """
    if not original_results or not replay_results:
        return 0.0
    consistent = sum(
        1
        for o, r in zip(original_results, replay_results)
        if o.is_correct == r.is_correct
    )
    return consistent / len(original_results)


def compute_unsafe_accept_rate(results: list[CaseResult]) -> float:
    """Compute unsafe accept rate: fraction of incorrect answers that were
    accepted (verification passed) despite being wrong.

    unsafe_accept = incorrect_and_accepted / total_accepted
    """
    accepted = [
        r for r in results if r.metadata.get("verification_passed", True)
    ]
    if not accepted:
        return 0.0
    incorrect_accepted = sum(1 for r in accepted if not r.is_correct)
    return incorrect_accepted / len(accepted)


def compute_requirement_trace_coverage(traces: list[dict]) -> float:
    """Compute requirement trace coverage: fraction of requirements that have at
    least one artifact trace.

    coverage = traced_requirements / total_requirements
    """
    if not traces:
        return 0.0
    traced = sum(1 for t in traces if t.get("artifact_ids"))
    return traced / len(traces)


def compute_tool_call_success_rate(results: list[CaseResult]) -> float:
    """Compute tool call success rate: fraction of cases with tool calls that
    produced valid tool call results.
    """
    tool_cases = [
        r for r in results if r.metadata.get("has_tool_calls", False)
    ]
    if not tool_cases:
        return 1.0
    successful = sum(1 for r in tool_cases if r.is_correct)
    return successful / len(tool_cases)


def compute_human_intervention_rate(results: list[CaseResult]) -> float:
    """Fraction of cases that required human intervention."""
    if not results:
        return 0.0
    interventions = sum(
        1 for r in results if r.metadata.get("required_human_intervention", False)
    )
    return interventions / len(results)


def compute_rework_rate(results: list[CaseResult]) -> float:
    """Fraction of cases that required rework (fallback/repair)."""
    if not results:
        return 0.0
    rework = sum(
        1 for r in results if r.metadata.get("required_rework", False)
    )
    return rework / len(results)


def compute_escalation_rate(results: list[CaseResult]) -> float:
    """Fraction of cases that were escalated from local to cloud."""
    if not results:
        return 0.0
    escalations = sum(
        1 for r in results if r.metadata.get("was_escalated", False)
    )
    return escalations / len(results)


def compute_all_metrics(
    results: list[CaseResult], cohort_config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Compute all metrics for a benchmark cohort."""
    if not results:
        return {
            "accuracy": 0.0,
            "tokens_per_correct_answer": None,
            "cost_per_correct_usd": None,
            "mean_tokens_per_case": 0.0,
            "total_tokens": 0,
            "total_cost_usd": 0.0,
            "mean_latency_ms": 0.0,
            "p95_latency_ms": 0.0,
            "brier_score": 0.0,
            "selective_risk": 0.0,
            "unsafe_accept_rate": 0.0,
            "tool_call_success_rate": 1.0,
            "human_intervention_rate": 0.0,
            "rework_rate": 0.0,
            "escalation_rate": 0.0,
        }

    total = len(results)
    correct = sum(1 for r in results if r.is_correct)
    total_tokens = sum(r.total_tokens for r in results)
    total_cost = sum(r.actual_cost_usd for r in results)

    latencies = sorted(r.latency_ms for r in results)
    p95_idx = int(len(latencies) * 0.95)
    p95 = latencies[p95_idx] if latencies else 0.0
    mean_latency = sum(latencies) / len(latencies) if latencies else 0.0

    predictions = [r.metadata.get("confidence", 1.0) for r in results]
    outcomes = [r.is_correct for r in results]

    return {
        "accuracy": correct / total,
        "tokens_per_correct_answer": (total_tokens / correct) if correct else None,
        "cost_per_correct_usd": (total_cost / correct) if correct else None,
        "mean_tokens_per_case": total_tokens / total,
        "total_tokens": total_tokens,
        "total_cost_usd": total_cost,
        "mean_latency_ms": mean_latency,
        "p95_latency_ms": p95,
        "brier_score": compute_brier_score(predictions, outcomes),
        "selective_risk": compute_selective_risk(results),
        "unsafe_accept_rate": compute_unsafe_accept_rate(results),
        "tool_call_success_rate": compute_tool_call_success_rate(results),
        "human_intervention_rate": compute_human_intervention_rate(results),
        "rework_rate": compute_rework_rate(results),
        "escalation_rate": compute_escalation_rate(results),
    }
