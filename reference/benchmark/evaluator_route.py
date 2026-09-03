"""Model routing recommendations based on evaluator findings.

Adapted from the spec-kit-evaluator extension's ``speckit.evaluator.route``
command. Analyzes evaluator findings and recommends which model tier to use
for the next SDD phase — enabling the portfolio approach: budget models for
routine generation, standard models for review, premium models for critical
decisions.
"""

from __future__ import annotations

from typing import Any

from .evaluator_contract import (
    EscalationTrigger,
    EvaluatorResult,
    Finding,
    ModelRouting,
    ModelTier,
    TierEstimate,
)

# -- Phase risk baselines -----------------------------------------------------
# Each SDD phase has an inherent risk baseline that shifts the threshold.
PHASE_RISK_BASELINE: dict[str, float] = {
    "specify": 0.10,
    "plan": 0.15,
    "tasks": 0.05,
    "implement": 0.20,
    "analyze": 0.10,
    "checklist": 0.00,
    "clarify": 0.15,
    "constitution": 0.20,
    "converge": 0.15,
}

# -- Model tier pricing reference (USD per 1M tokens) -------------------------
TIER_PRICING: dict[str, dict[str, float]] = {
    "budget": {"input": 0.15, "output": 0.75},
    "standard": {"input": 3.00, "output": 15.00},
    "premium": {"input": 15.00, "output": 75.00},
    "portfolio": {"input": 1.30, "output": 6.40},
}

# -- Tier precedence for escalation -------------------------------------------
_TIER_LEVEL: dict[str, int] = {"budget": 0, "standard": 1, "premium": 2, "portfolio": 1}


def compute_risk_score(findings: list[Finding]) -> float:
    """Compute a risk score (0.0-1.0) from evaluator findings.

    | Factor | Weight | How Measured |
    |--------|--------|-------------|
    | Critical findings | 40% | Count of critical severity findings |
    | High findings | 30% | Count of high severity findings |
    | Evidence gaps | 20% | Count of unsupported_claim + missing_evidence |
    | Contradictions | 10% | Count of contradictory finding pairs |
    """
    if not findings:
        return 0.0

    critical_count = sum(1 for f in findings if f.severity == "critical")
    high_count = sum(1 for f in findings if f.severity == "high")
    evidence_gaps = sum(
        1
        for f in findings
        if f.kind in ("unsupported_claim", "missing_evidence")
    )

    # Detect contradictions: same subject, conflicting kinds
    by_subject: dict[str, list[Finding]] = {}
    for f in findings:
        by_subject.setdefault(f.subject, []).append(f)
    contradiction_count = 0
    for group in by_subject.values():
        if len(group) < 2:
            continue
        kinds = {f.kind for f in group}
        has_positive = any(k in ("observed", "schema_violation") for k in kinds)
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
            contradiction_count += 1

    total_weight = (
        critical_count * 0.4
        + high_count * 0.3
        + evidence_gaps * 0.2
        + contradiction_count * 0.1
    )
    max_possible = len(findings) * 0.4
    return total_weight / max_possible if max_possible > 0 else 0.0


def determine_tier(risk_score: float) -> ModelTier:
    """Determine the recommended model tier from a risk score.

    | Risk Score | Recommended Tier |
    |-----------|-----------------|
    | 0.0-0.2 | budget |
    | 0.2-0.5 | standard |
    | 0.5-0.8 | premium |
    | 0.8-1.0 | premium + escalation |
    """
    if risk_score <= 0.2:
        return "budget"
    if risk_score <= 0.5:
        return "standard"
    return "premium"


def estimate_tokens(phase: str, tier: ModelTier) -> int:
    """Estimate token usage for a phase at a given tier.

    Higher tiers use fewer tokens (better models need fewer attempts).
    """
    base_tokens: dict[str, int] = {
        "specify": 8000,
        "plan": 12000,
        "tasks": 6000,
        "implement": 20000,
        "analyze": 10000,
        "checklist": 4000,
        "clarify": 6000,
        "constitution": 8000,
        "converge": 8000,
    }
    base = base_tokens.get(phase, 10000)
    efficiency: dict[str, float] = {
        "budget": 1.5,
        "standard": 1.0,
        "premium": 0.8,
        "portfolio": 1.0,
    }
    return int(base * efficiency.get(tier, 1.0))


def estimate_cost(tokens: int, tier: ModelTier) -> float:
    """Estimate USD cost for a given token count at a tier.

    Assumes 70% input / 30% output split.
    """
    pricing = TIER_PRICING.get(tier, TIER_PRICING["standard"])
    input_tokens = int(tokens * 0.7)
    output_tokens = int(tokens * 0.3)
    cost = (
        input_tokens / 1_000_000 * pricing["input"]
        + output_tokens / 1_000_000 * pricing["output"]
    )
    return round(cost, 4)


def build_escalation_triggers(
    findings: list[Finding], current_tier: ModelTier
) -> list[EscalationTrigger]:
    """Build escalation triggers based on findings and current tier."""
    triggers: list[EscalationTrigger] = []

    if current_tier == "budget":
        triggers.append(
            EscalationTrigger(
                condition="Any new critical finding",
                escalate_to="premium",
            )
        )
        triggers.append(
            EscalationTrigger(
                condition="More than 3 unsupported claims in next evaluation",
                escalate_to="standard",
            )
        )
    elif current_tier == "standard":
        triggers.append(
            EscalationTrigger(
                condition="Any new critical finding",
                escalate_to="premium",
            )
        )
        triggers.append(
            EscalationTrigger(
                condition="More than 5 high-severity findings",
                escalate_to="premium",
            )
        )

    return triggers


def recommend_route(
    result: EvaluatorResult,
    next_phase: str,
    budget_constraint: float | None = None,
) -> ModelRouting:
    """Recommend a model tier for the next SDD phase based on evaluator findings.

    Args:
        result: The evaluator result (or composed result) to base the
            recommendation on.
        next_phase: The next SDD phase to route for (e.g., ``plan``,
            ``implement``).
        budget_constraint: Optional maximum USD budget. If provided, the
            recommendation will be downgraded if the estimated cost exceeds
            the budget.

    Returns:
        A ModelRouting recommendation.
    """
    # 1. Compute risk score from findings
    risk_score = compute_risk_score(result.findings)

    # 2. Apply phase risk baseline
    baseline = PHASE_RISK_BASELINE.get(next_phase, 0.10)
    adjusted_risk = min(1.0, risk_score + baseline)

    # 3. Determine recommended tier
    tier = determine_tier(adjusted_risk)

    # 4. Apply budget constraint
    tokens = estimate_tokens(next_phase, tier)
    cost = estimate_cost(tokens, tier)

    if budget_constraint is not None and cost > budget_constraint:
        # Try downgrading
        tier_order: list[ModelTier] = ["premium", "standard", "budget"]
        current_idx = tier_order.index(tier) if tier in tier_order else 1
        for downgrade in tier_order[current_idx + 1 :]:
            d_tokens = estimate_tokens(next_phase, downgrade)
            d_cost = estimate_cost(d_tokens, downgrade)
            if d_cost <= budget_constraint:
                tier = downgrade
                tokens = d_tokens
                cost = d_cost
                break
        else:
            tier = "budget"
            tokens = estimate_tokens(next_phase, "budget")
            cost = estimate_cost(tokens, "budget")

    # 5. Build tier breakdown
    tier_breakdown: dict[str, TierEstimate] = {}
    for t in ("budget", "standard", "premium"):
        t_tokens = estimate_tokens(next_phase, t)  # type: ignore[arg-type]
        t_cost = estimate_cost(t_tokens, t)  # type: ignore[arg-type]
        tier_breakdown[t] = TierEstimate(
            estimated_tokens=t_tokens,
            estimated_cost_usd=t_cost,
        )

    # 6. Build reason
    reason_parts = []
    if risk_score <= 0.2:
        reason_parts.append("low risk (%.2f)" % risk_score)
    elif risk_score <= 0.5:
        reason_parts.append("moderate risk (%.2f)" % risk_score)
    else:
        reason_parts.append("high risk (%.2f)" % risk_score)

    if result.critical_count > 0:
        reason_parts.append("%d critical finding(s)" % result.critical_count)
    if result.high_count > 0:
        reason_parts.append("%d high-severity finding(s)" % result.high_count)
    if result.evidence_gap_count > 0:
        reason_parts.append("%d evidence gap(s)" % result.evidence_gap_count)

    reason = "%s - %s recommended for %s phase" % (
        ", ".join(reason_parts),
        tier,
        next_phase,
    )

    if budget_constraint is not None:
        reason += " (budget: $%.2f)" % budget_constraint

    # 7. Build escalation triggers
    triggers = build_escalation_triggers(result.findings, tier)

    return ModelRouting(
        recommended_tier=tier,
        reason=reason,
        escalation_triggers=triggers,
        estimated_tokens=tokens,
        estimated_cost_usd=cost,
        tier_breakdown=tier_breakdown,
    )


def compute_savings(routing: ModelRouting) -> dict[str, Any]:
    """Compute estimated savings vs always using premium tier."""
    premium = routing.tier_breakdown.get("premium")
    if premium is None:
        return {"savings_usd": 0.0, "savings_pct": 0.0}

    premium_cost = premium.estimated_cost_usd
    actual_cost = routing.estimated_cost_usd
    savings = premium_cost - actual_cost
    pct = (savings / premium_cost * 100) if premium_cost > 0 else 0.0

    return {
        "savings_usd": round(savings, 4),
        "savings_pct": round(pct, 1),
        "premium_cost_usd": premium_cost,
        "recommended_cost_usd": actual_cost,
    }