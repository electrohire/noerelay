"""True Total Cost of Ownership (TCO) model for model cost analysis.

The key insight: **optimal cost ≠ cheapest per-token cost**. A model that
costs $0.001/1K tokens but has a 30% rework rate (requiring cloud escalation
at $0.01/1K) and 10% human intervention rate (at $75/hour human time) is MORE
expensive than a model that costs $0.005/1K tokens with 0% rework and 0% human
intervention.

Dependency-free Python (stdlib only).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CostComponents:
    """Breakdown of all cost components for a model."""

    # Direct inference cost
    per_token_cost_usd: float  # cost per 1K tokens (prompt + completion weighted)
    tokens_per_case: float  # mean tokens consumed per case

    # Rework cost (when verification fails and fallback/repair is needed)
    rework_rate: float  # fraction of cases requiring rework (0.0-1.0)
    rework_cost_per_incident_usd: float  # cost of each rework (fallback model call + verification)

    # Human intervention cost (when human review is needed)
    human_intervention_rate: float  # fraction of cases requiring human review (0.0-1.0)
    human_time_per_intervention_minutes: float  # mean time per human intervention
    human_hourly_rate_usd: float  # cost of human time ($/hour)

    # Escalation cost (when local model fails and cloud is used)
    escalation_rate: float  # fraction of cases escalated to cloud (0.0-1.0)
    escalation_cost_per_incident_usd: float  # cost of each cloud escalation

    # Latency cost (opportunity cost of slow responses)
    mean_latency_ms: float
    latency_cost_per_second_usd: float  # opportunity cost of latency

    # Infrastructure cost (for local models: electricity, hardware amortization)
    infrastructure_cost_per_hour_usd: float  # hardware/electricity cost
    utilization_rate: float  # fraction of time the model is actually used (0.0-1.0)

    @property
    def direct_cost_per_case(self) -> float:
        """Direct inference cost per case."""
        return self.per_token_cost_usd * (self.tokens_per_case / 1000)

    @property
    def rework_cost_per_case(self) -> float:
        """Expected rework cost per case."""
        return self.rework_rate * self.rework_cost_per_incident_usd

    @property
    def human_cost_per_case(self) -> float:
        """Expected human intervention cost per case."""
        return (
            self.human_intervention_rate
            * (self.human_time_per_intervention_minutes / 60)
            * self.human_hourly_rate_usd
        )

    @property
    def escalation_cost_per_case(self) -> float:
        """Expected escalation cost per case."""
        return self.escalation_rate * self.escalation_cost_per_incident_usd

    @property
    def latency_cost_per_case(self) -> float:
        """Opportunity cost of latency per case."""
        return (self.mean_latency_ms / 1000) * self.latency_cost_per_second_usd

    @property
    def infrastructure_cost_per_case(self) -> float:
        """Amortized infrastructure cost per case."""
        cases_per_hour = 1000 * self.utilization_rate
        return self.infrastructure_cost_per_hour_usd / max(cases_per_hour, 1)

    @property
    def total_cost_per_case(self) -> float:
        """TRUE total cost of ownership per case — all components."""
        return (
            self.direct_cost_per_case
            + self.rework_cost_per_case
            + self.human_cost_per_case
            + self.escalation_cost_per_case
            + self.latency_cost_per_case
            + self.infrastructure_cost_per_case
        )

    def to_dict(self) -> dict[str, float]:
        """Return a dict of all cost components for JSON serialization."""
        return {
            "direct": round(self.direct_cost_per_case, 8),
            "rework": round(self.rework_cost_per_case, 8),
            "human": round(self.human_cost_per_case, 8),
            "escalation": round(self.escalation_cost_per_case, 8),
            "latency": round(self.latency_cost_per_case, 8),
            "infrastructure": round(self.infrastructure_cost_per_case, 8),
            "total_per_case": round(self.total_cost_per_case, 8),
        }


class TrueCostModel:
    """Computes true total cost of ownership for models.

    The key insight: optimal cost ≠ cheapest per-token cost.
    A model with higher per-token cost but lower rework/human/escalation rates
    may have lower TRUE total cost.
    """

    # Default cost parameters (configurable)
    DEFAULTS: dict[str, float] = {
        "human_hourly_rate_usd": 75.0,  # $75/hour for skilled human reviewer
        "human_time_per_intervention_minutes": 15.0,  # 15 minutes per review
        "rework_cost_per_incident_usd": 0.01,  # Cost of a fallback model call + re-verification
        "escalation_cost_per_incident_usd": 0.05,  # Cost of cloud escalation (cloud model call)
        "latency_cost_per_second_usd": 0.001,  # $0.001/second opportunity cost
        "infrastructure_cost_per_hour_usd": 0.50,  # Hardware/electricity for local GPU
        "utilization_rate": 0.3,  # 30% utilization (most of the time the GPU is idle)
    }

    def __init__(self, **overrides: float) -> None:
        self._params: dict[str, float] = {**self.DEFAULTS, **overrides}

    def compute_cost(
        self, model_stats: dict[str, Any], is_local: bool = False
    ) -> CostComponents:
        """Compute true cost components for a model based on benchmark stats.

        Args:
            model_stats: Dict with keys: accuracy, mean_tokens_per_case,
                        mean_cost_per_correct, mean_latency_ms,
                        human_intervention_rate, rework_rate, escalation_rate
            is_local: Whether this is a local model (affects infrastructure cost)

        Returns:
            CostComponents with all cost breakdowns computed.
        """
        tokens_per_case = float(model_stats.get("mean_tokens_per_case", 0) or 0)
        mean_cost_per_correct = float(model_stats.get("mean_cost_per_correct", 0) or 0)

        # Derive per_token_cost from mean_cost_per_correct and tokens_per_case
        if tokens_per_case > 0:
            per_token_cost_usd = mean_cost_per_correct / (tokens_per_case / 1000)
        else:
            per_token_cost_usd = 0.0

        return CostComponents(
            per_token_cost_usd=per_token_cost_usd,
            tokens_per_case=tokens_per_case,
            rework_rate=float(model_stats.get("rework_rate", 0) or 0),
            rework_cost_per_incident_usd=self._params["rework_cost_per_incident_usd"],
            human_intervention_rate=float(model_stats.get("human_intervention_rate", 0) or 0),
            human_time_per_intervention_minutes=self._params["human_time_per_intervention_minutes"],
            human_hourly_rate_usd=self._params["human_hourly_rate_usd"],
            escalation_rate=float(model_stats.get("escalation_rate", 0) or 0),
            escalation_cost_per_incident_usd=self._params["escalation_cost_per_incident_usd"],
            mean_latency_ms=float(model_stats.get("mean_latency_ms", 0) or 0),
            latency_cost_per_second_usd=self._params["latency_cost_per_second_usd"],
            infrastructure_cost_per_hour_usd=(
                self._params["infrastructure_cost_per_hour_usd"] if is_local else 0.0
            ),
            utilization_rate=(
                self._params["utilization_rate"] if is_local else 1.0
            ),
        )

    def compute_true_cost_per_correct(
        self, model_stats: dict[str, Any], is_local: bool = False
    ) -> float:
        """Compute the TRUE total cost per correct answer.

        This factors in:
        - Direct inference cost (per-token)
        - Rework cost (verification failures → fallback)
        - Human intervention cost (human review time)
        - Escalation cost (local→cloud fallback)
        - Latency opportunity cost
        - Infrastructure cost (for local models)

        And divides by accuracy to get cost per CORRECT answer.
        """
        components = self.compute_cost(model_stats, is_local)
        accuracy = float(model_stats.get("accuracy", 1.0) or 0)
        if accuracy <= 0:
            return float("inf")
        total_per_case = components.total_cost_per_case
        return total_per_case / accuracy

    def rank_by_true_cost(self, models: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Rank models by TRUE total cost per correct answer (lowest is best).

        Each model dict should have:
        - model_id
        - is_local (bool)
        - accuracy, mean_tokens_per_case, mean_cost_per_correct, mean_latency_ms
        - human_intervention_rate, rework_rate, escalation_rate
        """
        ranked: list[dict[str, Any]] = []
        for model in models:
            is_local = bool(model.get("is_local", False))
            true_cost = self.compute_true_cost_per_correct(model, is_local)
            components = self.compute_cost(model, is_local)
            ranked.append(
                {
                    **model,
                    "true_cost_per_correct": round(true_cost, 8),
                    "cost_breakdown": components.to_dict(),
                }
            )
        ranked.sort(key=lambda x: x["true_cost_per_correct"])
        return ranked

    def explain_cost_difference(
        self, model_a: dict[str, Any], model_b: dict[str, Any]
    ) -> dict[str, Any]:
        """Explain why one model is cheaper than another in true cost terms.

        Useful for understanding when a more expensive per-token model
        is actually cheaper overall.
        """
        is_local_a = bool(model_a.get("is_local", False))
        is_local_b = bool(model_b.get("is_local", False))
        cost_a = self.compute_cost(model_a, is_local_a)
        cost_b = self.compute_cost(model_b, is_local_b)

        return {
            "model_a": model_a.get("model_id", "unknown"),
            "model_b": model_b.get("model_id", "unknown"),
            "per_token_cheaper": (
                model_a.get("model_id", "unknown")
                if cost_a.direct_cost_per_case < cost_b.direct_cost_per_case
                else model_b.get("model_id", "unknown")
            ),
            "true_cost_cheaper": (
                model_a.get("model_id", "unknown")
                if cost_a.total_cost_per_case < cost_b.total_cost_per_case
                else model_b.get("model_id", "unknown")
            ),
            "cost_difference": {
                "direct": round(cost_b.direct_cost_per_case - cost_a.direct_cost_per_case, 8),
                "rework": round(cost_b.rework_cost_per_case - cost_a.rework_cost_per_case, 8),
                "human": round(cost_b.human_cost_per_case - cost_a.human_cost_per_case, 8),
                "escalation": round(
                    cost_b.escalation_cost_per_case - cost_a.escalation_cost_per_case, 8
                ),
                "latency": round(cost_b.latency_cost_per_case - cost_a.latency_cost_per_case, 8),
                "infrastructure": round(
                    cost_b.infrastructure_cost_per_case - cost_a.infrastructure_cost_per_case, 8
                ),
            },
            "explanation": self._generate_explanation(cost_a, cost_b, model_a, model_b),
        }

    def _generate_explanation(
        self,
        cost_a: CostComponents,
        cost_b: CostComponents,
        model_a: dict[str, Any],
        model_b: dict[str, Any],
    ) -> str:
        """Generate human-readable explanation of cost difference."""
        name_a = model_a.get("model_id", "Model A")
        name_b = model_b.get("model_id", "Model B")

        parts: list[str] = []
        total_a = cost_a.total_cost_per_case
        total_b = cost_b.total_cost_per_case

        if total_a < total_b:
            parts.append(
                f"{name_a} has lower total cost per case (${total_a:.6f} vs ${total_b:.6f})"
            )
        else:
            parts.append(
                f"{name_b} has lower total cost per case (${total_b:.6f} vs ${total_a:.6f})"
            )

        # Check if per-token cost is misleading
        if cost_a.direct_cost_per_case < cost_b.direct_cost_per_case and total_a > total_b:
            parts.append(
                f"However, {name_a} has lower direct cost (${cost_a.direct_cost_per_case:.6f} "
                f"vs ${cost_b.direct_cost_per_case:.6f}) but higher total cost due to rework, "
                f"human intervention, or escalation costs."
            )
        elif cost_b.direct_cost_per_case < cost_a.direct_cost_per_case and total_b > total_a:
            parts.append(
                f"However, {name_b} has lower direct cost (${cost_b.direct_cost_per_case:.6f} "
                f"vs ${cost_a.direct_cost_per_case:.6f}) but higher total cost due to rework, "
                f"human intervention, or escalation costs."
            )

        # Highlight dominant cost components
        if cost_a.rework_cost_per_case > 0.001:
            parts.append(
                f"{name_a} rework cost: ${cost_a.rework_cost_per_case:.6f}/case "
                f"({cost_a.rework_rate:.0%} rework rate)"
            )
        if cost_b.rework_cost_per_case > 0.001:
            parts.append(
                f"{name_b} rework cost: ${cost_b.rework_cost_per_case:.6f}/case "
                f"({cost_b.rework_rate:.0%} rework rate)"
            )
        if cost_a.human_cost_per_case > 0.001:
            parts.append(
                f"{name_a} human intervention cost: ${cost_a.human_cost_per_case:.6f}/case "
                f"({cost_a.human_intervention_rate:.0%} intervention rate)"
            )
        if cost_b.human_cost_per_case > 0.001:
            parts.append(
                f"{name_b} human intervention cost: ${cost_b.human_cost_per_case:.6f}/case "
                f"({cost_b.human_intervention_rate:.0%} intervention rate)"
            )

        return ". ".join(parts) + "."