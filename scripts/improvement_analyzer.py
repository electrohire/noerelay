"""Self-improvement analysis engine for NoeRelay.

Analyzes benchmark results across cycles to identify:
1. Bottlenecks — which cohorts/models are underperforming
2. Improvement opportunities — model swaps, config tuning, portfolio changes
3. Cost-quality Pareto frontier — which models give best quality per unit cost
4. Actionable recommendations — concrete steps to improve the stack

Integrates with:
- ``service_health_probe`` for service status context
- ``continuous_benchmark`` for benchmark results
- ``model_lifecycle`` for model discovery/recommendation
- ``cost_model`` for true TCO analysis
- ``online_learning`` for canary/promotion governance
"""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure reference/ is importable
_reference = Path(__file__).resolve().parents[1] / "reference"
if str(_reference) not in sys.path:
    sys.path.insert(0, str(_reference))

from benchmark.evaluator_contract import (
    EvaluatorResult,
    Finding,
    load_result_file,
)
from gateway.cost_model import TrueCostModel, CostComponents
from gateway.online_learning import PolicyVersionManager


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class Bottleneck:
    """A detected performance bottleneck."""

    cohort: str
    metric: str  # "accuracy", "latency", "cost", "safety", "rework_rate"
    current_value: float
    baseline_value: float
    threshold: float
    severity: str  # "critical", "warning", "info"
    recommendation: str


@dataclass
class ImprovementAction:
    """A concrete, actionable improvement recommendation."""

    action_id: str
    category: str  # "model_swap", "config_tune", "portfolio_add", "portfolio_remove", "restart_service"
    description: str
    target: str  # which model/service/config key
    current_state: str
    proposed_state: str
    expected_impact: dict[str, float]  # metric -> expected delta
    confidence: float  # 0.0-1.0
    reversible: bool = True
    requires_approval: bool = False


@dataclass
class ImprovementReport:
    """Full analysis report for one benchmark cycle."""

    timestamp: str
    cycle_number: int
    bottlenecks: list[Bottleneck] = field(default_factory=list)
    actions: list[ImprovementAction] = field(default_factory=list)
    applied_actions: list[ImprovementAction] = field(default_factory=list)
    pareto_frontier: list[dict[str, Any]] = field(default_factory=list)
    composite_score: float = 0.0
    previous_score: float = 0.0
    score_delta: float = 0.0
    convergence_progress: float = 0.0  # 0.0-1.0, how close to convergence
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "cycle_number": self.cycle_number,
            "composite_score": round(self.composite_score, 4),
            "previous_score": round(self.previous_score, 4),
            "score_delta": round(self.score_delta, 4),
            "convergence_progress": round(self.convergence_progress, 4),
            "bottlenecks": [
                {
                    "cohort": b.cohort,
                    "metric": b.metric,
                    "current_value": round(b.current_value, 4),
                    "baseline_value": round(b.baseline_value, 4),
                    "threshold": round(b.threshold, 4),
                    "severity": b.severity,
                    "recommendation": b.recommendation,
                }
                for b in self.bottlenecks
            ],
            "actions": [
                {
                    "action_id": a.action_id,
                    "category": a.category,
                    "description": a.description,
                    "target": a.target,
                    "current_state": a.current_state,
                    "proposed_state": a.proposed_state,
                    "expected_impact": a.expected_impact,
                    "confidence": round(a.confidence, 2),
                    "reversible": a.reversible,
                    "requires_approval": a.requires_approval,
                }
                for a in self.actions
            ],
            "applied_actions": [
                {
                    "action_id": a.action_id,
                    "category": a.category,
                    "description": a.description,
                }
                for a in self.applied_actions
            ],
            "pareto_frontier": self.pareto_frontier,
            "summary": self.summary,
        }


# ---------------------------------------------------------------------------
# Thresholds for bottleneck detection
# ---------------------------------------------------------------------------

COHORT_THRESHOLDS: dict[str, dict[str, float]] = {
    # Default thresholds applied to all cohorts
    "_default": {
        "accuracy_min": 0.60,
        "latency_p95_max_ms": 15000,
        "cost_per_case_max_usd": 0.05,
        "safety_min": 0.90,
        "rework_rate_max": 0.20,
        "escalation_rate_max": 0.15,
        "human_intervention_rate_max": 0.10,
    },
    # Per-cohort overrides
    "coding-tasks": {
        "accuracy_min": 0.50,
        "latency_p95_max_ms": 20000,
    },
    "safety-tasks": {
        "accuracy_min": 0.90,
        "safety_min": 0.98,
    },
    "reasoning-tasks": {
        "accuracy_min": 0.55,
    },
    "quick-test": {
        "accuracy_min": 0.40,
        "latency_p95_max_ms": 30000,
    },
}


# ---------------------------------------------------------------------------
# Analysis engine
# ---------------------------------------------------------------------------

class ImprovementAnalyzer:
    """Analyzes benchmark results and produces improvement recommendations.

    Integrates with the TrueCostModel for cost-quality tradeoff analysis
    and PolicyVersionManager for canary promotion governance.
    """

    def __init__(
        self,
        cost_model: TrueCostModel | None = None,
        policy_manager: PolicyVersionManager | None = None,
        local_models: list[dict[str, Any]] | None = None,
    ) -> None:
        self._cost_model = cost_model or TrueCostModel()
        self._policy_manager = policy_manager or PolicyVersionManager()
        self._local_models = local_models or []
        self._action_counter = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        benchmark_results: dict[str, Any],
        previous_results: dict[str, Any] | None = None,
        health_matrix: dict[str, Any] | None = None,
        cycle_number: int = 0,
    ) -> ImprovementReport:
        """Analyze benchmark results and produce an improvement report.

        Args:
            benchmark_results: Current cycle's benchmark output (from
                               ContinuousBenchmarkPipeline.run_once).
            previous_results: Previous cycle's results for delta analysis.
            health_matrix: Service health probe output for context.
            cycle_number: Current improvement cycle number.

        Returns:
            ImprovementReport with bottlenecks, actions, and convergence status.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        current_score = self._extract_composite_score(benchmark_results)
        previous_score = (
            self._extract_composite_score(previous_results)
            if previous_results
            else current_score
        )
        score_delta = current_score - previous_score

        report = ImprovementReport(
            timestamp=timestamp,
            cycle_number=cycle_number,
            composite_score=current_score,
            previous_score=previous_score,
            score_delta=score_delta,
        )

        # 1. Detect bottlenecks from legacy metrics
        report.bottlenecks = self._detect_bottlenecks(
            benchmark_results, previous_results
        )

        # 1b. Detect bottlenecks from evaluator-contract findings
        contract_bottlenecks = self._detect_contract_bottlenecks(
            benchmark_results
        )
        report.bottlenecks.extend(contract_bottlenecks)

        # 2. Generate improvement actions
        report.actions = self._generate_actions(
            benchmark_results, report.bottlenecks, health_matrix
        )

        # 3. Compute Pareto frontier
        report.pareto_frontier = self._compute_pareto_frontier(benchmark_results)

        # 4. Assess convergence
        report.convergence_progress = self._assess_convergence(
            benchmark_results, previous_results, cycle_number
        )

        # 5. Generate summary
        report.summary = self._generate_summary(report)

        return report

    def apply_action(self, action: ImprovementAction) -> bool:
        """Attempt to apply an improvement action. Returns True on success."""
        # This is a hook — actual application is done by the orchestrator
        # or external scripts. Here we validate and record.
        if action.category == "model_swap":
            return self._validate_model_swap(action)
        if action.category == "config_tune":
            return self._validate_config_tune(action)
        if action.category == "portfolio_add":
            return self._validate_portfolio_change(action)
        if action.category == "portfolio_remove":
            return self._validate_portfolio_change(action)
        return False

    # ------------------------------------------------------------------
    # Bottleneck detection
    # ------------------------------------------------------------------

    def _detect_bottlenecks(
        self,
        results: dict[str, Any],
        previous: dict[str, Any] | None = None,
    ) -> list[Bottleneck]:
        """Detect performance bottlenecks across all cohorts."""
        bottlenecks: list[Bottleneck] = []

        cohorts = results.get("cohorts", {})
        prev_cohorts = previous.get("cohorts", {}) if previous else {}

        for cohort_name, report in cohorts.items():
            if isinstance(report, dict) and "error" in report:
                bottlenecks.append(
                    Bottleneck(
                        cohort=cohort_name,
                        metric="error",
                        current_value=0.0,
                        baseline_value=0.0,
                        threshold=0.0,
                        severity="critical",
                        recommendation=f"Cohort '{cohort_name}' failed: {report['error']}",
                    )
                )
                continue

            thresholds = self._get_thresholds(cohort_name)
            accuracy = report.get("accuracy", 0.0)
            prev_accuracy = prev_cohorts.get(cohort_name, {}).get("accuracy", accuracy)

            # Accuracy bottleneck
            acc_min = thresholds.get("accuracy_min", 0.60)
            if accuracy < acc_min:
                bottlenecks.append(
                    Bottleneck(
                        cohort=cohort_name,
                        metric="accuracy",
                        current_value=accuracy,
                        baseline_value=prev_accuracy,
                        threshold=acc_min,
                        severity="critical" if accuracy < acc_min * 0.7 else "warning",
                        recommendation=(
                            f"Accuracy {accuracy:.1%} below threshold {acc_min:.1%}. "
                            f"Consider stronger model or prompt engineering for {cohort_name}."
                        ),
                    )
                )

            # Latency bottleneck
            latency = report.get("p95_latency_ms", report.get("mean_latency_ms", 0))
            latency_max = thresholds.get("latency_p95_max_ms", 15000)
            if latency > latency_max:
                bottlenecks.append(
                    Bottleneck(
                        cohort=cohort_name,
                        metric="latency",
                        current_value=latency,
                        baseline_value=prev_cohorts.get(cohort_name, {}).get(
                            "p95_latency_ms", latency
                        ),
                        threshold=latency_max,
                        severity="warning",
                        recommendation=(
                            f"P95 latency {latency:.0f}ms exceeds {latency_max}ms. "
                            f"Consider faster model or reduced context for {cohort_name}."
                        ),
                    )
                )

            # Rework rate bottleneck
            rework = report.get("rework_rate", 0.0)
            rework_max = thresholds.get("rework_rate_max", 0.20)
            if rework > rework_max:
                bottlenecks.append(
                    Bottleneck(
                        cohort=cohort_name,
                        metric="rework_rate",
                        current_value=rework,
                        baseline_value=prev_cohorts.get(cohort_name, {}).get(
                            "rework_rate", rework
                        ),
                        threshold=rework_max,
                        severity="warning",
                        recommendation=(
                            f"Rework rate {rework:.1%} exceeds {rework_max:.1%}. "
                            f"Consider better primary model or stricter verification."
                        ),
                    )
                )

            # Escalation rate bottleneck
            escalation = report.get("escalation_rate", 0.0)
            escalation_max = thresholds.get("escalation_rate_max", 0.15)
            if escalation > escalation_max:
                bottlenecks.append(
                    Bottleneck(
                        cohort=cohort_name,
                        metric="escalation_rate",
                        current_value=escalation,
                        baseline_value=prev_cohorts.get(cohort_name, {}).get(
                            "escalation_rate", escalation
                        ),
                        threshold=escalation_max,
                        severity="warning",
                        recommendation=(
                            f"Escalation rate {escalation:.1%} exceeds {escalation_max:.1%}. "
                            f"Local models may be insufficient for {cohort_name}."
                        ),
                    )
                )

            # Safety bottleneck
            safety = report.get("unsafe_accept_rate", 0.0)
            safety_max = 1.0 - thresholds.get("safety_min", 0.90)
            if safety > safety_max:
                bottlenecks.append(
                    Bottleneck(
                        cohort=cohort_name,
                        metric="safety",
                        current_value=safety,
                        baseline_value=prev_cohorts.get(cohort_name, {}).get(
                            "unsafe_accept_rate", safety
                        ),
                        threshold=safety_max,
                        severity="critical",
                        recommendation=(
                            f"Unsafe accept rate {safety:.1%} exceeds {safety_max:.1%}. "
                            f"Safety verification must be strengthened for {cohort_name}."
                        ),
                    )
                )

        return bottlenecks

    def _detect_contract_bottlenecks(
        self,
        results: dict[str, Any],
    ) -> list[Bottleneck]:
        """Detect bottlenecks from evaluator-contract findings.

        Extracts :class:`EvaluatorResult` from benchmark results and
        translates evidence-classified findings into Bottleneck objects
        for the improvement pipeline.
        """
        bottlenecks: list[Bottleneck] = []

        # Try the composed evaluator result first
        er_data = results.get("evaluator_result")
        if not er_data:
            # Fall back to per-cohort evaluator results
            for cohort_name, report in results.get("cohorts", {}).items():
                if isinstance(report, dict) and "evaluator_result" in report:
                    er_data = report["evaluator_result"]
                    break

        if not er_data or not isinstance(er_data, dict):
            return bottlenecks

        findings = er_data.get("findings", [])
        if not findings:
            return bottlenecks

        # Group findings by severity
        for f in findings:
            fid = f.get("id", "unknown")
            severity = f.get("severity", "info")
            kind = f.get("kind", "other")
            subject = f.get("subject", "unknown")
            description = f.get("description", "")
            recommended_action = f.get("recommended_action", "none")
            uncertainty = f.get("uncertainty", "none")

            # Skip passing findings — they are not bottlenecks.
            if recommended_action == "none":
                continue

            # Map evaluator-contract severity to bottleneck severity
            if severity in ("critical", "high"):
                bottleneck_severity = "critical" if severity == "critical" else "warning"
            elif severity == "medium":
                bottleneck_severity = "warning"
            else:
                bottleneck_severity = "info"

            # Map finding kind to metric
            kind_to_metric: dict[str, str] = {
                "unsupported_claim": "accuracy",
                "contradiction": "accuracy",
                "missing_evidence": "accuracy",
                "ambiguous_requirement": "accuracy",
                "unverified_assertion": "accuracy",
                "provenance_gap": "accuracy",
                "schema_violation": "accuracy",
                "policy_violation": "safety",
                "security_concern": "safety",
                "coverage_gap": "accuracy",
                "traceability_gap": "accuracy",
                "risk_unaddressed": "safety",
                "assumption_unvalidated": "accuracy",
                "other": "accuracy",
            }
            metric = kind_to_metric.get(kind, "accuracy")

            # Build recommendation from finding
            rec = description
            if recommended_action and recommended_action != "none":
                rec += " (action: %s)" % recommended_action
            if uncertainty and uncertainty not in ("none", "low"):
                rec += " [uncertainty: %s]" % uncertainty

            bottlenecks.append(
                Bottleneck(
                    cohort=subject,
                    metric=metric,
                    current_value=0.0,
                    baseline_value=0.0,
                    threshold=0.0,
                    severity=bottleneck_severity,
                    recommendation=rec,
                )
            )

        # Add aggregate finding summary as a meta-bottleneck
        outcome = er_data.get("outcome", "pass")
        if outcome in ("block", "iterate"):
            bottlenecks.append(
                Bottleneck(
                    cohort="evaluator-contract",
                    metric="outcome",
                    current_value=0.0,
                    baseline_value=0.0,
                    threshold=0.0,
                    severity="critical" if outcome == "block" else "warning",
                    recommendation=(
                        "Evaluator contract outcome is '%s'. "
                        "Address findings before proceeding."
                        % outcome
                    ),
                )
            )

        return bottlenecks

    # ------------------------------------------------------------------
    # Action generation
    # ------------------------------------------------------------------

    def _generate_actions(
        self,
        results: dict[str, Any],
        bottlenecks: list[Bottleneck],
        health_matrix: dict[str, Any] | None = None,
    ) -> list[ImprovementAction]:
        """Generate concrete improvement actions from bottlenecks."""
        actions: list[ImprovementAction] = []

        # Group bottlenecks by cohort
        by_cohort: dict[str, list[Bottleneck]] = {}
        for b in bottlenecks:
            by_cohort.setdefault(b.cohort, []).append(b)

        for cohort, cohort_bottlenecks in by_cohort.items():
            # Accuracy issues -> model swap recommendation
            acc_bottlenecks = [b for b in cohort_bottlenecks if b.metric == "accuracy"]
            if acc_bottlenecks:
                actions.extend(
                    self._recommend_model_upgrade(cohort, acc_bottlenecks[0])
                )

            # Latency issues -> config tuning or faster model
            lat_bottlenecks = [b for b in cohort_bottlenecks if b.metric == "latency"]
            if lat_bottlenecks:
                actions.extend(
                    self._recommend_latency_fix(cohort, lat_bottlenecks[0])
                )

            # Rework/escalation -> verification or model quality
            rework_bottlenecks = [
                b for b in cohort_bottlenecks
                if b.metric in ("rework_rate", "escalation_rate")
            ]
            if rework_bottlenecks:
                actions.extend(
                    self._recommend_rework_fix(cohort, rework_bottlenecks)
                )

            # Safety -> verification hardening
            safety_bottlenecks = [b for b in cohort_bottlenecks if b.metric == "safety"]
            if safety_bottlenecks:
                actions.extend(
                    self._recommend_safety_fix(cohort, safety_bottlenecks[0])
                )

        # Service health issues -> restart actions
        if health_matrix:
            actions.extend(self._recommend_service_fixes(health_matrix))

        # Deduplicate and assign IDs
        seen: set[str] = set()
        unique_actions: list[ImprovementAction] = []
        for a in actions:
            key = f"{a.category}:{a.target}"
            if key not in seen:
                seen.add(key)
                self._action_counter += 1
                a.action_id = f"action-{self._action_counter:04d}"
                unique_actions.append(a)

        return unique_actions

    def _recommend_model_upgrade(
        self, cohort: str, bottleneck: Bottleneck
    ) -> list[ImprovementAction]:
        """Recommend model upgrades for accuracy bottlenecks."""
        actions: list[ImprovementAction] = []

        # Map cohort to capability needs
        cohort_capabilities: dict[str, list[str]] = {
            "coding-tasks": ["text", "coding", "tool_calling"],
            "reasoning-tasks": ["text", "reasoning"],
            "tool-use-tasks": ["text", "tool_calling", "structured_output"],
            "multi-turn-tasks": ["text", "tool_calling"],
            "safety-tasks": ["text", "review"],
            "vision-tasks": ["text", "vision"],
            "quick-test": ["text"],
        }

        capabilities = cohort_capabilities.get(cohort, ["text"])

        # Check if we can swap to a stronger local model
        local_models = self._local_models
        stronger_local = [
            m for m in local_models
            if m.get("acceptance_lcb", 0) > bottleneck.current_value + 0.05
            and any(c in m.get("required_capabilities", []) for c in capabilities)
        ]

        if stronger_local:
            best = max(stronger_local, key=lambda m: m.get("acceptance_lcb", 0))
            actions.append(
                ImprovementAction(
                    action_id="",
                    category="model_swap",
                    description=(
                        f"Swap {cohort} primary model to "
                        f"{best.get('model_id', 'unknown')} "
                        f"(acceptance {best.get('acceptance_lcb', 0):.0%})"
                    ),
                    target=f"cohort:{cohort}:primary_model",
                    current_state=f"accuracy={bottleneck.current_value:.1%}",
                    proposed_state=best.get("model_id", "unknown"),
                    expected_impact={"accuracy": best.get("acceptance_lcb", 0) - bottleneck.current_value},
                    confidence=0.7,
                )
            )

        # If no stronger local, recommend cloud fallback enable
        if not stronger_local:
            actions.append(
                ImprovementAction(
                    action_id="",
                    category="config_tune",
                    description=(
                        f"Enable cloud fallback for {cohort} — "
                        f"no local model meets accuracy threshold"
                    ),
                    target="NOERELAY_CLOUD_FALLBACK_ALLOWED",
                    current_state="0",
                    proposed_state="1",
                    expected_impact={"accuracy": 0.10},
                    confidence=0.5,
                    requires_approval=True,
                )
            )

        return actions

    def _recommend_latency_fix(
        self, cohort: str, bottleneck: Bottleneck
    ) -> list[ImprovementAction]:
        """Recommend latency improvements."""
        actions: list[ImprovementAction] = []

        # Reduce context budget
        actions.append(
            ImprovementAction(
                action_id="",
                category="config_tune",
                description=(
                    f"Reduce context budget for {cohort} to lower latency"
                ),
                target="NOERELAY_CONTEXT_BUDGET_TOKENS",
                current_state="32768",
                proposed_state="16384",
                expected_impact={"latency_ms": -2000},
                confidence=0.6,
            )
        )

        # Consider faster model
        actions.append(
            ImprovementAction(
                action_id="",
                category="model_swap",
                description=(
                    f"Use faster model (qwen38-4b-distilled) for {cohort} "
                    f"when quality requirements are low"
                ),
                target=f"cohort:{cohort}:primary_model",
                current_state=f"latency={bottleneck.current_value:.0f}ms",
                proposed_state="qwen38-4b-distilled:latest",
                expected_impact={"latency_ms": -3000, "accuracy": -0.05},
                confidence=0.65,
            )
        )

        return actions

    def _recommend_rework_fix(
        self, cohort: str, bottlenecks: list[Bottleneck]
    ) -> list[ImprovementAction]:
        """Recommend fixes for high rework/escalation rates."""
        actions: list[ImprovementAction] = []

        actions.append(
            ImprovementAction(
                action_id="",
                category="config_tune",
                description=(
                    f"Enable independent verification for {cohort} "
                    f"to reduce rework/escalation"
                ),
                target=f"cohort:{cohort}:independent_verification",
                current_state="disabled",
                proposed_state="enabled",
                expected_impact={
                    "rework_rate": -0.10,
                    "escalation_rate": -0.05,
                    "latency_ms": 500,
                },
                confidence=0.75,
            )
        )

        return actions

    def _recommend_safety_fix(
        self, cohort: str, bottleneck: Bottleneck
    ) -> list[ImprovementAction]:
        """Recommend safety hardening."""
        return [
            ImprovementAction(
                action_id="",
                category="config_tune",
                description=(
                    f"Enable mandatory safety verification for {cohort}"
                ),
                target=f"cohort:{cohort}:safety_verification",
                current_state="optional",
                proposed_state="mandatory",
                expected_impact={"unsafe_accept_rate": -0.05},
                confidence=0.85,
            )
        ]

    def _recommend_service_fixes(
        self, health_matrix: dict[str, Any]
    ) -> list[ImprovementAction]:
        """Generate restart/recovery actions for unhealthy services."""
        actions: list[ImprovementAction] = []

        for svc in health_matrix.get("services", []):
            if not svc.get("healthy", False):
                actions.append(
                    ImprovementAction(
                        action_id="",
                        category="restart_service",
                        description=f"Restart {svc['name']} on {svc['machine']}",
                        target=svc["name"],
                        current_state="unhealthy",
                        proposed_state="healthy",
                        expected_impact={"service_health": 1.0},
                        confidence=0.9,
                        reversible=True,
                        requires_approval=False,
                    )
                )

        return actions

    # ------------------------------------------------------------------
    # Pareto frontier
    # ------------------------------------------------------------------

    def _compute_pareto_frontier(
        self, results: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Compute cost-quality Pareto frontier from cohort results.

        Returns models/cohorts that are not dominated by any other
        (i.e., no other option has both lower cost AND higher quality).
        """
        points: list[dict[str, Any]] = []

        for cohort_name, report in results.get("cohorts", {}).items():
            if isinstance(report, dict) and "error" not in report:
                accuracy = report.get("accuracy", 0.0)
                cost = report.get("total_cost_usd", report.get("mean_cost_per_correct", 0.0))
                latency = report.get("p95_latency_ms", report.get("mean_latency_ms", 0))
                points.append({
                    "cohort": cohort_name,
                    "accuracy": accuracy,
                    "cost_usd": cost,
                    "latency_ms": latency,
                    "efficiency": accuracy / max(cost, 0.0001),
                })

        # Sort by accuracy descending
        points.sort(key=lambda p: p["accuracy"], reverse=True)

        # Filter to Pareto-optimal (no point has both higher accuracy AND lower cost)
        pareto: list[dict[str, Any]] = []
        best_cost = float("inf")
        for p in points:
            if p["cost_usd"] < best_cost:
                pareto.append(p)
                best_cost = p["cost_usd"]

        return pareto

    # ------------------------------------------------------------------
    # Convergence assessment
    # ------------------------------------------------------------------

    def _assess_convergence(
        self,
        results: dict[str, Any],
        previous: dict[str, Any] | None,
        cycle_number: int,
    ) -> float:
        """Assess how close we are to convergence (dimension return).

        Convergence is defined as:
        - Score delta < 0.01 for 3 consecutive cycles
        - All cohorts above minimum thresholds
        - No critical bottlenecks

        Returns 0.0-1.0 where 1.0 = converged.
        """
        if previous is None:
            return 0.0

        current_score = self._extract_composite_score(results)
        previous_score = self._extract_composite_score(previous)
        delta = abs(current_score - previous_score)

        # Score stability (40% weight)
        score_stability = max(0.0, 1.0 - delta / 0.05)

        # Threshold satisfaction (40% weight)
        cohorts = results.get("cohorts", {})
        satisfied = 0
        total = max(len(cohorts), 1)
        for cohort_name, report in cohorts.items():
            if isinstance(report, dict) and "error" not in report:
                thresholds = self._get_thresholds(cohort_name)
                accuracy = report.get("accuracy", 0.0)
                if accuracy >= thresholds.get("accuracy_min", 0.60):
                    satisfied += 1
        threshold_sat = satisfied / total

        # Cycle progress (20% weight) — more cycles = closer to convergence
        cycle_factor = min(1.0, cycle_number / 10.0)

        progress = 0.4 * score_stability + 0.4 * threshold_sat + 0.2 * cycle_factor
        return min(1.0, progress)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_composite_score(self, results: dict[str, Any] | None) -> float:
        """Extract composite score from benchmark results."""
        if results is None:
            return 0.0
        composite = results.get("composite_score") or {}
        return float(composite.get("overall_score", 0.0))

    def _get_thresholds(self, cohort_name: str) -> dict[str, float]:
        """Get thresholds for a cohort, merging with defaults."""
        defaults = dict(COHORT_THRESHOLDS.get("_default", {}))
        overrides = COHORT_THRESHOLDS.get(cohort_name, {})
        return {**defaults, **overrides}

    def _validate_model_swap(self, action: ImprovementAction) -> bool:
        """Validate a model swap action."""
        return bool(action.target and action.proposed_state)

    def _validate_config_tune(self, action: ImprovementAction) -> bool:
        """Validate a config tuning action."""
        valid_keys = {
            "NOERELAY_CONTEXT_BUDGET_TOKENS",
            "NOERELAY_CLOUD_FALLBACK_ALLOWED",
            "NOERELAY_EXTERNAL_MODEL_EGRESS_ALLOWED",
        }
        return action.target in valid_keys or action.target.startswith("cohort:")

    def _validate_portfolio_change(self, action: ImprovementAction) -> bool:
        """Validate a portfolio change action."""
        return bool(action.target and action.proposed_state)

    def _generate_summary(self, report: ImprovementReport) -> str:
        """Generate a human-readable summary."""
        parts: list[str] = []

        parts.append(
            f"Cycle {report.cycle_number}: "
            f"score={report.composite_score:.4f} "
            f"(delta={report.score_delta:+.4f}), "
            f"convergence={report.convergence_progress:.0%}"
        )

        if report.bottlenecks:
            critical = [b for b in report.bottlenecks if b.severity == "critical"]
            warnings = [b for b in report.bottlenecks if b.severity == "warning"]
            parts.append(
                f"Bottlenecks: {len(critical)} critical, {len(warnings)} warnings"
            )
            for b in critical:
                parts.append(f"  CRITICAL [{b.cohort}] {b.metric}: {b.recommendation}")

        if report.actions:
            parts.append(f"Recommended actions: {len(report.actions)}")
            for a in report.actions[:5]:
                parts.append(f"  - {a.description}")

        if report.convergence_progress >= 0.95:
            parts.append("DIMENSION RETURN: Stack has converged. No further improvements needed.")

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Analyze NoeRelay benchmark results for improvement opportunities"
    )
    parser.add_argument(
        "results_file", type=str, help="Path to benchmark results JSON"
    )
    parser.add_argument(
        "--previous", type=str, default=None, help="Path to previous results JSON"
    )
    parser.add_argument(
        "--health", type=str, default=None, help="Path to health probe JSON"
    )
    parser.add_argument(
        "--cycle", type=int, default=0, help="Cycle number"
    )
    parser.add_argument(
        "--output", type=str, default=None, help="Output path for report JSON"
    )
    args = parser.parse_args()

    # Load results
    results_path = Path(args.results_file)
    if not results_path.exists():
        print(f"ERROR: Results file not found: {results_path}", file=sys.stderr)
        return 1
    results = json.loads(results_path.read_text("utf-8"))

    # Load previous
    previous = None
    if args.previous:
        prev_path = Path(args.previous)
        if prev_path.exists():
            previous = json.loads(prev_path.read_text("utf-8"))

    # Load health
    health = None
    if args.health:
        health_path = Path(args.health)
        if health_path.exists():
            health = json.loads(health_path.read_text("utf-8"))

    # Load local models for context
    local_models: list[dict[str, Any]] = []
    try:
        from gateway.local_portfolio import get_local_models
        local_models = get_local_models()
    except Exception:
        pass

    analyzer = ImprovementAnalyzer(local_models=local_models)
    report = analyzer.analyze(
        benchmark_results=results,
        previous_results=previous,
        health_matrix=health,
        cycle_number=args.cycle,
    )

    # Output
    report_json = json.dumps(report.to_dict(), indent=2)
    if args.output:
        Path(args.output).write_text(report_json, encoding="utf-8")
        print(f"Report saved to: {args.output}")

    print(report.summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())