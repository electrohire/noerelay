"""Benchmark metric aggregation and structured JSON reporting."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass
class CaseResult:
    case_id: str
    is_correct: bool
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int
    actual_cost_usd: float
    latency_ms: float
    model: str
    response: str
    expected: str
    metadata: dict[str, Any]


@dataclass
class BenchmarkResults:
    cohort_name: str
    total_cases: int
    correct_count: int
    total_tokens: int
    total_cost_usd: float
    mean_latency_ms: float
    p95_latency_ms: float
    results: list[CaseResult]
    human_intervention_rate: float = 0.0
    rework_rate: float = 0.0
    escalation_rate: float = 0.0

    @property
    def accuracy(self) -> float:
        return self.correct_count / self.total_cases if self.total_cases else 0.0

    @property
    def tokens_per_correct(self) -> float:
        return self.total_tokens / self.correct_count if self.correct_count else float("inf")

    @property
    def cost_per_correct(self) -> float:
        return self.total_cost_usd / self.correct_count if self.correct_count else float("inf")

    @property
    def mean_tokens(self) -> float:
        return self.total_tokens / self.total_cases if self.total_cases else 0.0


def compute_results(cohort_name: str, case_results: list[CaseResult]) -> BenchmarkResults:
    """Compute aggregate metrics from individual case results."""
    total = len(case_results)
    correct = sum(1 for r in case_results if r.is_correct)
    tokens = sum(r.total_tokens for r in case_results)
    cost = sum(r.actual_cost_usd for r in case_results)
    latencies = sorted([r.latency_ms for r in case_results])
    p95_idx = int(len(latencies) * 0.95)
    p95 = latencies[p95_idx] if latencies else 0.0
    mean_lat = sum(latencies) / len(latencies) if latencies else 0.0
    interventions = sum(
        1 for r in case_results if r.metadata.get("required_human_intervention", False)
    )
    rework = sum(
        1 for r in case_results if r.metadata.get("required_rework", False)
    )
    escalations = sum(
        1 for r in case_results if r.metadata.get("was_escalated", False)
    )
    return BenchmarkResults(
        cohort_name=cohort_name,
        total_cases=total,
        correct_count=correct,
        total_tokens=tokens,
        total_cost_usd=cost,
        mean_latency_ms=mean_lat,
        p95_latency_ms=p95,
        results=case_results,
        human_intervention_rate=(interventions / total) if total else 0.0,
        rework_rate=(rework / total) if total else 0.0,
        escalation_rate=(escalations / total) if total else 0.0,
    )


def _round_metric(value: Any, ndigits: int) -> Any:
    """Round a numeric metric, mapping non-finite values to ``None``.

    ``tokens_per_correct`` and ``cost_per_correct`` are ``inf`` when a cohort
    has zero correct answers; JSON cannot represent ``inf``, so those metrics
    are reported as ``null``.
    """
    try:
        numeric = float(value)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(numeric):
        return None
    return round(numeric, ndigits)


def format_report(results: BenchmarkResults) -> dict[str, Any]:
    """Format results as a structured JSON report."""
    return {
        "cohort": results.cohort_name,
        "total_cases": results.total_cases,
        "correct_count": results.correct_count,
        "accuracy": round(results.accuracy, 4),
        "tokens_per_correct_answer": _round_metric(results.tokens_per_correct, 2),
        "cost_per_correct_usd": _round_metric(results.cost_per_correct, 6),
        "mean_tokens_per_case": round(results.mean_tokens, 2),
        "total_tokens": results.total_tokens,
        "total_cost_usd": round(results.total_cost_usd, 6),
        "mean_latency_ms": round(results.mean_latency_ms, 2),
        "p95_latency_ms": round(results.p95_latency_ms, 2),
        "human_intervention_rate": round(results.human_intervention_rate, 4),
        "rework_rate": round(results.rework_rate, 4),
        "escalation_rate": round(results.escalation_rate, 4),
    }
