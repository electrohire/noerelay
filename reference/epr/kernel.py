"""Dependency-free deterministic route selection for EPR-1."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _sum_costs(candidate: dict[str, Any], fields: list[str]) -> float:
    costs = candidate.get("costs", {})
    return round(sum(float(costs.get(field, 0)) for field in fields), 8)


def _required_lcb(contract: dict[str, Any], policy: dict[str, Any]) -> float:
    risk = contract["risk_class"]
    policy_floor = float(policy["risk_acceptance_lcb"][risk])
    requested = float(contract["governance"].get("required_acceptance_probability", 0))
    return max(policy_floor, requested)


def _missing_acceptance(contract: dict[str, Any]) -> bool:
    return any(
        criterion.get("mandatory", False) and criterion.get("kind") == "missing"
        for criterion in contract.get("acceptance_criteria", [])
    )


def _base_reasons(
    contract: dict[str, Any],
    candidate: dict[str, Any],
    required_lcb: float,
) -> list[str]:
    reasons: list[str] = []
    governance = contract["governance"]
    required_caps = set(contract.get("required_capabilities", []))
    candidate_caps = set(candidate.get("capabilities", []))
    family = candidate.get("provider_family")

    if not candidate.get("available", False):
        reasons.append("unavailable")
    missing_caps = sorted(required_caps - candidate_caps)
    if missing_caps:
        reasons.append("missing_capabilities:" + ",".join(missing_caps))
    if governance["data_policy"] not in candidate.get("data_policies", []):
        reasons.append("data_policy_mismatch")
    allowed = governance.get("allowed_provider_families", [])
    denied = governance.get("denied_provider_families", [])
    if allowed and family not in allowed:
        reasons.append("provider_not_allowed")
    if family in denied:
        reasons.append("provider_denied")
    acceptance_lcb = float(candidate.get("acceptance", {}).get("lower_bound", 0))
    if acceptance_lcb < required_lcb:
        reasons.append(f"acceptance_lcb_below_{required_lcb:.3f}")
    if int(candidate.get("latency_ms_p95", 0)) > int(governance["max_latency_ms"]):
        reasons.append("latency_limit_exceeded")
    return reasons


def _verifier_reasons(
    contract: dict[str, Any],
    worker: dict[str, Any],
    verifier: dict[str, Any],
    required_lcb: float,
) -> list[str]:
    reasons: list[str] = []
    governance = contract["governance"]
    if "verify" not in verifier.get("roles", []):
        reasons.append("not_a_verifier")
    if not verifier.get("available", False):
        reasons.append("unavailable")
    if verifier.get("provider_family") == worker.get("provider_family"):
        reasons.append("verifier_not_independent")
    if governance["data_policy"] not in verifier.get("data_policies", []):
        reasons.append("data_policy_mismatch")
    allowed = governance.get("allowed_provider_families", [])
    denied = governance.get("denied_provider_families", [])
    family = verifier.get("provider_family")
    if allowed and family not in allowed:
        reasons.append("provider_not_allowed")
    if family in denied:
        reasons.append("provider_denied")
    verifier_lcb = float(verifier.get("acceptance", {}).get("lower_bound", 0))
    if verifier_lcb < required_lcb:
        reasons.append(f"verifier_lcb_below_{required_lcb:.3f}")
    return reasons


def select_route(
    contract: dict[str, Any],
    candidates: list[dict[str, Any]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    """Select the least-cost admissible action plan.

    Safety, capability, privacy, calibrated acceptance, verification, budget, and
    latency are constraints. Cost is only a ranking criterion after they pass.
    """

    required_lcb = _required_lcb(contract, policy)
    risk = contract["risk_class"]
    decision = {
        "decision_id": f"route-{contract['task_id']}",
        "task_id": contract["task_id"],
        "status": "escalation_required",
        "policy_version": policy["version"],
        "required_acceptance_lcb": required_lcb,
        "fallback_plans": [],
        "candidate_audit": [],
        "explanation": "No admissible route exists; fail-closed escalation is required.",
    }

    if _missing_acceptance(contract):
        behavior = policy["missing_acceptance_behavior"][risk]
        decision["status"] = "rejected" if behavior == "rejected_until_specified" else "clarification_required"
        decision["explanation"] = f"Mandatory acceptance criteria are missing; policy requires {behavior}."
        return decision

    requires_independent = risk in policy["independent_verification_required_for"]
    cost_fields = policy["expected_total_cost_fields"]
    workers = [candidate for candidate in candidates if "execute" in candidate.get("roles", [])]
    verifiers = [candidate for candidate in candidates if "verify" in candidate.get("roles", [])]
    plans: list[dict[str, Any]] = []

    for worker in workers:
        reasons = _base_reasons(contract, worker, required_lcb)
        audit = {
            "candidate_id": worker["candidate_id"],
            "admissible": False,
            "reasons": reasons.copy(),
        }

        if reasons:
            decision["candidate_audit"].append(audit)
            continue

        verifier_options: list[dict[str, Any] | None] = [None]
        if requires_independent:
            verifier_options = [
                verifier
                for verifier in verifiers
                if not _verifier_reasons(contract, worker, verifier, required_lcb)
            ]
            if not verifier_options:
                audit["reasons"].append("no_admissible_independent_verifier")
                decision["candidate_audit"].append(audit)
                continue

        worker_had_plan = False
        for verifier in verifier_options:
            total_cost = _sum_costs(worker, cost_fields)
            latency = int(worker["latency_ms_p95"])
            plan_lcb = float(worker["acceptance"]["lower_bound"])
            if verifier is not None:
                total_cost += _sum_costs(verifier, cost_fields)
                latency += int(verifier["latency_ms_p95"])
                plan_lcb = min(plan_lcb, float(verifier["acceptance"]["lower_bound"]))

            if total_cost > float(contract["governance"]["max_cost_usd"]):
                continue
            if latency > int(contract["governance"]["max_latency_ms"]):
                continue

            plan = {
                "action_id": worker["candidate_id"],
                "action_kind": worker["action_kind"],
                "provider_family": worker["provider_family"],
                "expected_total_cost_usd": round(total_cost, 8),
                "acceptance_lcb": plan_lcb,
                "latency_ms_p95": latency,
            }
            if verifier is not None:
                plan["verifier_id"] = verifier["candidate_id"]
                plan["verifier_family"] = verifier["provider_family"]
            plans.append(plan)
            worker_had_plan = True

        audit["admissible"] = worker_had_plan
        if not worker_had_plan:
            audit["reasons"].append("plan_exceeds_cost_or_latency")
        decision["candidate_audit"].append(audit)

    for verifier in verifiers:
        decision["candidate_audit"].append(
            {
                "candidate_id": verifier["candidate_id"],
                "admissible": False,
                "reasons": ["verification_candidate_not_ranked_as_primary_action"],
            }
        )

    if not plans:
        return decision

    plans.sort(
        key=lambda plan: (
            plan["expected_total_cost_usd"],
            plan["latency_ms_p95"],
            -plan["acceptance_lcb"],
        )
    )
    decision["status"] = "route_selected"
    decision["selected_plan"] = deepcopy(plans[0])
    decision["fallback_plans"] = deepcopy(plans[1:])
    decision["explanation"] = "Selected the least-cost plan after all hard constraints passed."
    return decision
