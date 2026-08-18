"""Epistemic Ledger Enricher for the NoeRelay gateway.

Enriches standard ledger events with epistemic context — the reasoning,
claims, evidence, and epistemic state that informed each decision.

The standard ledger logs WHAT happened (event type, actor, timestamp).
The epistemic ledger also logs WHY it happened.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class EpistemicLedgerEnricher:
    """Enriches ledger events with epistemic context.

    The standard ledger logs WHAT happened (event type, actor, timestamp).
    The epistemic ledger also logs WHY it happened — the reasoning,
    claims, evidence, and epistemic state that informed each decision.
    """

    def __init__(self) -> None:
        self._decision_history: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Route selection enrichment
    # ------------------------------------------------------------------

    def enrich_route_selection(
        self,
        decision: dict[str, Any],
        epistemic_state: Any,
        contract: dict[str, Any],
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        """Enrich a route_selected event with epistemic context.

        Returns a payload dict containing:
        - decision_id, selected_plan, status
        - reasoning: WHY this plan was selected
        - candidates_evaluated: full audit trail with reasons
        - epistemic_state_snapshot: all claims and their adjudication
        - policy_context: which policy rules were applied
        - contract_context: what the task required
        - risk_assessment: risk class and required LCB
        """
        selected_plan = decision.get("selected_plan", {})
        candidates = decision.get("candidates_evaluated", [])
        rejected = decision.get("rejected_reasons", {})

        # Build reasoning narrative
        model_id = selected_plan.get("model_id", "unknown")
        gateway = selected_plan.get("inference_gateway", "unknown")
        cost = selected_plan.get("expected_total_cost_usd", 0.0)
        risk_class = contract.get("risk_class", "low")

        reasoning_parts: list[str] = []
        if gateway == "local":
            reasoning_parts.append(
                f"Selected {model_id} (local, ${cost:.4f}/call) because it was "
                f"the lowest-cost admissible plan meeting all hard constraints"
            )
        else:
            reasoning_parts.append(
                f"Selected {model_id} (cloud, ${cost:.4f}/call) as the "
                f"admissible plan meeting all hard constraints"
            )

        capabilities = contract.get("required_capabilities", [])
        if capabilities:
            reasoning_parts.append(
                f"Required capabilities: {', '.join(capabilities)}"
            )

        reasoning_parts.append(f"Risk class: {risk_class}")

        # Build candidates evaluated audit trail
        candidates_audit: list[dict[str, Any]] = []
        for cand in candidates:
            cid = cand.get("candidate_id", cand.get("model_id", "unknown"))
            c_reason = rejected.get(cid, "")
            candidates_audit.append({
                "candidate_id": cid,
                "model_id": cand.get("model_id", ""),
                "gateway": cand.get("inference_gateway", ""),
                "cost": cand.get("expected_total_cost_usd", 0.0),
                "admissible": cid not in rejected,
                "rejection_reason": c_reason if cid in rejected else None,
            })

        # Snapshot epistemic state
        snapshot = self.snapshot_epistemic_state(epistemic_state)

        # Policy context
        policy_context = {
            "version": policy.get("version", ""),
            "acceptance_lcb_floor": policy.get("acceptance_lcb_floor", 0.0),
            "risk_class": risk_class,
        }

        # Contract context
        contract_context = {
            "task_id": contract.get("task_id", ""),
            "goal": contract.get("goal", ""),
            "task_kind": contract.get("task_kind", ""),
            "required_capabilities": contract.get("required_capabilities", []),
            "data_policy": contract.get("governance", {}).get("data_policy", ""),
        }

        # Risk assessment
        risk_assessment = {
            "risk_class": risk_class,
            "required_lcb": policy.get("acceptance_lcb_floor", 0.85),
        }

        payload = {
            "decision_id": decision.get("decision_id", ""),
            "selected_plan": selected_plan,
            "status": decision.get("status", ""),
            "reasoning": " ".join(reasoning_parts),
            "candidates_evaluated": candidates_audit,
            "epistemic_state_snapshot": snapshot,
            "policy_context": policy_context,
            "contract_context": contract_context,
            "risk_assessment": risk_assessment,
        }

        # Record in decision history
        self._decision_history.append({
            "step": "route_selection",
            "timestamp": _now(),
            "reasoning": payload["reasoning"],
            "epistemic_state": snapshot,
            "evidence": list(epistemic_state.iter_evidence().values())
            if hasattr(epistemic_state, "iter_evidence") else [],
            "decision": {
                "selected_plan": selected_plan,
                "candidates_evaluated": candidates_audit,
                "rejected_reasons": rejected,
            },
        })

        return payload

    # ------------------------------------------------------------------
    # Verification enrichment
    # ------------------------------------------------------------------

    def enrich_verification(
        self,
        verification_results: list[dict[str, Any]],
        epistemic_state: Any,
        risk_class: str,
        dag_steps: list[str],
    ) -> dict[str, Any]:
        """Enrich a verification_completed event with epistemic context.

        Returns a payload dict containing:
        - verification_results: pass/fail per criterion
        - dag_steps: which verification steps were executed
        - epistemic_state_snapshot: claims and their status at verification time
        - blocking_conflicts: any conflicted claims that blocked acceptance
        - evidence_summary: what evidence was produced
        - risk_class: the risk class that determined the DAG depth
        - reasoning: WHY verification passed or failed
        """
        snapshot = self.snapshot_epistemic_state(epistemic_state)

        # Determine blocking conflicts
        blocking_conflicts: list[str] = []
        if hasattr(epistemic_state, "conflicted_claim_ids"):
            blocking_conflicts = epistemic_state.conflicted_claim_ids()

        # Evidence summary
        evidence_count = 0
        evidence_kinds: dict[str, int] = {}
        if hasattr(epistemic_state, "iter_evidence"):
            for ev in epistemic_state.iter_evidence().values():
                evidence_count += 1
                kind = ev.get("kind", "unknown")
                evidence_kinds[kind] = evidence_kinds.get(kind, 0) + 1

        # Determine pass/fail
        all_passed = all(
            r.get("status") == "passed"
            for r in verification_results
            if r.get("mandatory")
        )
        failed_criteria = [
            r["criterion_id"]
            for r in verification_results
            if r.get("mandatory") and r.get("status") != "passed"
        ]

        # Build reasoning
        if all_passed:
            reasoning = (
                f"Verification DAG for {risk_class} risk: {dag_steps}. "
                f"All mandatory criteria passed. "
            )
            if blocking_conflicts:
                reasoning += (
                    f"However, {len(blocking_conflicts)} blocking conflicts "
                    f"exist: {blocking_conflicts}."
                )
            else:
                reasoning += "No blocking conflicts."
        else:
            reasoning = (
                f"Verification DAG for {risk_class} risk: {dag_steps}. "
                f"Failed mandatory criteria: {failed_criteria}. "
            )
            if blocking_conflicts:
                reasoning += (
                    f"Blocking conflicts: {blocking_conflicts}."
                )

        payload = {
            "verification_results": verification_results,
            "dag_steps": dag_steps,
            "epistemic_state_snapshot": snapshot,
            "blocking_conflicts": blocking_conflicts,
            "evidence_summary": {
                "total_evidence": evidence_count,
                "by_kind": evidence_kinds,
            },
            "risk_class": risk_class,
            "reasoning": reasoning,
        }

        # Record in decision history
        self._decision_history.append({
            "step": "verification",
            "timestamp": _now(),
            "reasoning": reasoning,
            "epistemic_state": snapshot,
            "verification_results": verification_results,
            "dag_steps": dag_steps,
        })

        return payload

    # ------------------------------------------------------------------
    # Outcome enrichment
    # ------------------------------------------------------------------

    def enrich_outcome(
        self,
        status: str,
        decision: dict[str, Any],
        epistemic_state: Any,
        verification_results: list[dict[str, Any]],
        total_cost: float,
        true_cost_breakdown: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Enrich an outcome_accepted/rejected event with full epistemic context.

        Returns a payload dict containing:
        - status: accepted/escalated/rejected
        - decision_summary: route, model, cost
        - epistemic_summary: all claims, their status, unresolved claims
        - verification_summary: pass/fail counts, evidence count
        - cost_summary: direct, rework, human, escalation, latency, infrastructure
        - reasoning: WHY the outcome was accepted/rejected
        - unresolved_claims: claims still in unknown/conflicted state
        - evidence_chain: hash-linked evidence references
        """
        snapshot = self.snapshot_epistemic_state(epistemic_state)
        selected_plan = decision.get("selected_plan", {})

        # Decision summary
        decision_summary = {
            "route": selected_plan.get("model_id", "unknown"),
            "gateway": selected_plan.get("inference_gateway", "unknown"),
            "cost": total_cost,
        }

        # Epistemic summary
        unresolved = snapshot.get("unresolved_claim_ids", [])
        conflicted = snapshot.get("conflicted_claim_ids", [])
        epistemic_summary = {
            "total_claims": len(snapshot.get("claims", {})),
            "supported_claims": sum(
                1 for c in snapshot.get("claims", {}).values()
                if c.get("status") == "supported"
            ),
            "refuted_claims": sum(
                1 for c in snapshot.get("claims", {}).values()
                if c.get("status") == "refuted"
            ),
            "unknown_claims": sum(
                1 for c in snapshot.get("claims", {}).values()
                if c.get("status") == "unknown"
            ),
            "conflicted_claims": sum(
                1 for c in snapshot.get("claims", {}).values()
                if c.get("status") == "conflicted"
            ),
            "unresolved_claim_ids": unresolved,
            "conflicted_claim_ids": conflicted,
        }

        # Verification summary
        passed_count = sum(
            1 for r in verification_results if r.get("status") == "passed"
        )
        failed_count = sum(
            1 for r in verification_results if r.get("status") == "failed"
        )
        verification_summary = {
            "passed": passed_count,
            "failed": failed_count,
            "total": len(verification_results),
            "evidence_count": snapshot.get("evidence_count", 0),
        }

        # Cost summary
        if true_cost_breakdown is None:
            true_cost_breakdown = {}
        cost_summary = {
            "direct": true_cost_breakdown.get("direct_cost", total_cost),
            "rework": true_cost_breakdown.get("rework_cost", 0.0),
            "human": true_cost_breakdown.get("human_cost", 0.0),
            "escalation": true_cost_breakdown.get("escalation_cost", 0.0),
            "latency": true_cost_breakdown.get("latency_cost", 0.0),
            "infrastructure": true_cost_breakdown.get("infrastructure_cost", 0.0),
            "total": total_cost,
        }

        # Evidence chain
        evidence_chain: list[str] = []
        if hasattr(epistemic_state, "iter_evidence"):
            evidence_chain = [
                ev.get("content_hash", "")
                for ev in epistemic_state.iter_evidence().values()
                if ev.get("content_hash")
            ]

        # Build reasoning
        if status == "accepted":
            reasoning = (
                f"Outcome accepted. All verification criteria passed. "
            )
            if unresolved:
                reasoning += (
                    f"{len(unresolved)} unresolved claims remain: {unresolved}. "
                )
            else:
                reasoning += "No unresolved claims. "
            if conflicted:
                reasoning += (
                    f"{len(conflicted)} blocking conflicts: {conflicted}. "
                )
            else:
                reasoning += "No blocking conflicts. "
            reasoning += (
                f"Total cost: ${total_cost:.4f} "
                f"({decision_summary['gateway']} model). "
                f"Evidence receipt issued."
            )
        else:
            reasoning = (
                f"Outcome rejected. "
                f"Verification: {passed_count} passed, {failed_count} failed. "
            )
            if unresolved:
                reasoning += f"{len(unresolved)} unresolved claims. "
            if conflicted:
                reasoning += f"{len(conflicted)} blocking conflicts. "
            reasoning += f"Total cost: ${total_cost:.4f}."

        payload = {
            "status": status,
            "decision_summary": decision_summary,
            "epistemic_summary": epistemic_summary,
            "verification_summary": verification_summary,
            "cost_summary": cost_summary,
            "reasoning": reasoning,
            "unresolved_claims": unresolved,
            "evidence_chain": evidence_chain,
        }

        # Record in decision history
        self._decision_history.append({
            "step": "outcome",
            "timestamp": _now(),
            "reasoning": reasoning,
            "epistemic_state": snapshot,
            "cost_summary": cost_summary,
            "unresolved_claims": unresolved,
        })

        return payload

    # ------------------------------------------------------------------
    # Fallback enrichment
    # ------------------------------------------------------------------

    def enrich_fallback(
        self,
        fallback_class: str,
        from_model: str,
        to_model: str,
        reason: str,
        epistemic_state: Any,
    ) -> dict[str, Any]:
        """Enrich a fallback_triggered event with epistemic context.

        Returns a payload dict containing:
        - fallback_class: provider_fallback / semantic_fallback / etc.
        - from_model, to_model
        - reason: WHY the fallback was needed
        - epistemic_state_snapshot: claims at fallback time
        - decision_context: what was the original decision
        """
        snapshot = self.snapshot_epistemic_state(epistemic_state)

        # Build reasoning
        reasoning = (
            f"Fallback triggered ({fallback_class}): "
            f"from {from_model} to {to_model}. "
            f"Reason: {reason}."
        )

        # Decision context from history
        decision_context: dict[str, Any] = {}
        if self._decision_history:
            decision_context = {
                "last_step": self._decision_history[-1].get("step", ""),
                "last_reasoning": self._decision_history[-1].get("reasoning", ""),
            }

        payload = {
            "fallback_class": fallback_class,
            "from": from_model,
            "to": to_model,
            "reason": reason,
            "reasoning": reasoning,
            "epistemic_state_snapshot": snapshot,
            "decision_context": decision_context,
        }

        # Record in decision history
        self._decision_history.append({
            "step": "fallback",
            "timestamp": _now(),
            "reasoning": reasoning,
            "epistemic_state": snapshot,
            "fallback_class": fallback_class,
            "from_model": from_model,
            "to_model": to_model,
        })

        return payload

    # ------------------------------------------------------------------
    # Human review enrichment
    # ------------------------------------------------------------------

    def enrich_human_review(
        self,
        reason: str,
        epistemic_state: Any,
        verification_results: list[dict[str, Any]],
        risk_class: str,
    ) -> dict[str, Any]:
        """Enrich a human_review_requested event with epistemic context.

        Returns a payload dict containing:
        - reason: WHY human review was needed
        - epistemic_state_snapshot: claims requiring human judgment
        - verification_results: what failed
        - risk_class: why this risk class requires human review
        - unresolved_claims: claims the human needs to resolve
        - evidence_summary: what evidence exists for/against
        """
        snapshot = self.snapshot_epistemic_state(epistemic_state)

        unresolved = snapshot.get("unresolved_claim_ids", [])
        conflicted = snapshot.get("conflicted_claim_ids", [])

        # Evidence summary
        evidence_summary: list[dict[str, Any]] = []
        if hasattr(epistemic_state, "iter_evidence"):
            for ev in epistemic_state.iter_evidence().values():
                evidence_summary.append({
                    "evidence_id": ev.get("evidence_id", ""),
                    "kind": ev.get("kind", ""),
                    "strength": ev.get("strength", 0.0),
                })

        # Build reasoning
        reasoning = (
            f"Human review requested for {risk_class} risk task. "
            f"Reason: {reason}. "
        )
        if unresolved:
            reasoning += (
                f"{len(unresolved)} claims require human resolution: {unresolved}. "
            )
        if conflicted:
            reasoning += (
                f"{len(conflicted)} conflicted claims: {conflicted}. "
            )

        payload = {
            "reason": reason,
            "reasoning": reasoning,
            "epistemic_state_snapshot": snapshot,
            "verification_results": verification_results,
            "risk_class": risk_class,
            "unresolved_claims": unresolved,
            "evidence_summary": evidence_summary,
        }

        # Record in decision history
        self._decision_history.append({
            "step": "human_review",
            "timestamp": _now(),
            "reasoning": reasoning,
            "epistemic_state": snapshot,
            "reason": reason,
            "risk_class": risk_class,
        })

        return payload

    # ------------------------------------------------------------------
    # Claim transition enrichment
    # ------------------------------------------------------------------

    def enrich_claim_transition(
        self,
        claim_id: str,
        old_status: str,
        new_status: str,
        evidence_ids: list[str],
        reasoning: str,
    ) -> dict[str, Any]:
        """Enrich a claim_transitioned event.

        Returns a payload dict containing:
        - claim_id, old_status, new_status
        - evidence_ids: what evidence caused the transition
        - reasoning: WHY the claim transitioned
        - support_lcb, refutation_lcb: the confidence values
        """
        payload = {
            "claim_id": claim_id,
            "old_status": old_status,
            "new_status": new_status,
            "evidence_ids": evidence_ids,
            "reasoning": reasoning,
            "support_lcb": 0.0,
            "refutation_lcb": 0.0,
        }

        # Record in decision history
        self._decision_history.append({
            "step": "claim_transition",
            "timestamp": _now(),
            "reasoning": reasoning,
            "claim_id": claim_id,
            "old_status": old_status,
            "new_status": new_status,
            "evidence_ids": evidence_ids,
        })

        return payload

    # ------------------------------------------------------------------
    # Epistemic state snapshot
    # ------------------------------------------------------------------

    def snapshot_epistemic_state(self, epistemic_state: Any) -> dict[str, Any]:
        """Take a snapshot of the current epistemic state.

        Returns a dict containing:
        - claims: all claims with their current adjudication
        - evidence: all evidence records with their kinds
        - unresolved_claim_ids: claims in unknown/conflicted state
        - conflicted_claim_ids: claims that are conflicted
        - calibration_summary: ECE per model
        """
        claims: dict[str, dict[str, Any]] = {}
        evidence: dict[str, dict[str, Any]] = {}
        unresolved: list[str] = []
        conflicted: list[str] = []

        if hasattr(epistemic_state, "iter_claims"):
            claims = epistemic_state.iter_claims()
            for cid, claim in claims.items():
                status = claim.get("status", "unknown")
                if status in ("unknown", "conflicted"):
                    unresolved.append(cid)
                if status == "conflicted":
                    conflicted.append(cid)

        if hasattr(epistemic_state, "iter_evidence"):
            evidence = epistemic_state.iter_evidence()

        # Calibration summary
        calibration_summary: dict[str, float] = {}
        if hasattr(epistemic_state, "calibration"):
            cal = epistemic_state.calibration
            if hasattr(cal, "_records"):
                for model_id in cal._records:
                    if hasattr(cal, "compute_ece"):
                        calibration_summary[model_id] = cal.compute_ece(model_id)

        return {
            "claims": claims,
            "evidence": evidence,
            "evidence_count": len(evidence),
            "unresolved_claim_ids": unresolved,
            "conflicted_claim_ids": conflicted,
            "calibration_summary": calibration_summary,
        }

    # ------------------------------------------------------------------
    # Decision trace
    # ------------------------------------------------------------------

    def decision_trace(self, run_id: str) -> list[dict[str, Any]]:
        """Return the full decision trace for a run.

        This is the epistemic audit trail — every decision point
        with its reasoning, evidence, and epistemic context.
        """
        return list(self._decision_history)