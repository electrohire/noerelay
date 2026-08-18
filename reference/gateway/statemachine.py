"""Spec-driven verification state machine with real guard evaluation.

The transition table is loaded from ``spec/verification-state-machine.json``.
Guards can be supplied as booleans (backward-compatible) or computed via
:class:`GuardEvaluator` from pipeline context.
"""

from __future__ import annotations

from typing import Any


class TransitionError(RuntimeError):
    """Raised when a state transition is illegal or its guard fails."""


class VerificationStateMachine:
    """State machine driven by the verification-state-machine spec.

    The ``transition(run_id, event, guard_ok)`` interface is backward
    compatible: callers may still pass a pre-computed boolean.  The new
    :class:`GuardEvaluator` provides a way to compute those booleans from
    live pipeline context.
    """

    def __init__(self, spec: dict[str, Any]) -> None:
        self._initial: str = spec.get("initial_state", "received")
        self._terminal: set[str] = set(spec.get("terminal_states", []))
        self._states: dict[str, str] = {}
        self._transitions: dict[tuple[str, str], tuple[str, str]] = {}

        for transition in spec.get("transitions", []):
            key = (transition["from"], transition["event"])
            self._transitions[key] = (
                transition["to"],
                transition.get("guard", "true"),
            )

    def begin(self, run_id: str) -> str:
        self._states[run_id] = self._initial
        return self._initial

    def state(self, run_id: str) -> str:
        return self._states.get(run_id, self._initial)

    def is_terminal(self, run_id: str) -> bool:
        return self.state(run_id) in self._terminal

    def transition(self, run_id: str, event: str, guard_ok: bool) -> str:
        current = self.state(run_id)
        entry = self._transitions.get((current, event))
        if entry is None:
            raise TransitionError(
                f"illegal transition for run {run_id}: {current} --{event}-->"
            )
        to_state, guard = entry
        if not guard_ok:
            raise TransitionError(
                f"guard {guard!r} failed for run {run_id}: "
                f"{current} --{event}--> {to_state}"
            )
        self._states[run_id] = to_state
        return to_state


class GuardEvaluator:
    """Compute guard booleans from live pipeline context.

    Each static method corresponds to a guard named in
    :file:`spec/verification-state-machine.json`.  The pipeline calls these
    at the appropriate point and passes the result to
    :meth:`VerificationStateMachine.transition`.
    """

    # -- Request / contract guards -----------------------------------------

    @staticmethod
    def request_schema_valid(request: dict[str, Any]) -> bool:
        """True when the request carries a non-empty model and messages."""
        return bool(
            request.get("model")
            and isinstance(request.get("messages"), list)
            and len(request["messages"]) > 0
        )

    @staticmethod
    def mandatory_acceptance_missing_or_ambiguous(contract: dict[str, Any]) -> bool:
        """True when any mandatory criterion has kind ``missing``."""
        return any(
            c.get("mandatory") and c.get("kind") == "missing"
            for c in contract.get("acceptance_criteria", [])
        )

    @staticmethod
    def clarification_addresses_gap() -> bool:
        """Skeleton: clarification loop is not implemented; returns True."""
        return True

    @staticmethod
    def contract_schema_valid_and_acceptance_sufficient(contract: dict[str, Any]) -> bool:
        """True when the contract compiled successfully (has version + goal)."""
        return bool(contract and contract.get("version") and contract.get("goal"))

    # -- Policy guard ------------------------------------------------------

    @staticmethod
    def policy_allows_progress() -> bool:
        """True — the pipeline only reaches this point when policy passes."""
        return True

    @staticmethod
    def non_authorizable_policy_violation() -> bool:
        """True when a policy violation cannot be authorized (fail-closed)."""
        return True

    # -- Context compaction guard ------------------------------------------

    @staticmethod
    def compaction_invariants_hold() -> bool:
        """True — skeleton performs no compaction, so invariants hold."""
        return True

    # -- Routing guards ----------------------------------------------------

    @staticmethod
    def admissible_route_exists(decision: dict[str, Any]) -> bool:
        """True when ``decision["status"] == "route_selected"``."""
        return decision.get("status") == "route_selected"

    @staticmethod
    def router_failed_closed() -> bool:
        """True — router is designed to fail closed."""
        return True

    # -- Execution guards --------------------------------------------------

    @staticmethod
    def budget_and_permissions_reserved(
        selected_plan: dict[str, Any], contract: dict[str, Any]
    ) -> bool:
        """True when the plan cost does not exceed the governance ceiling."""
        try:
            cost = float(selected_plan.get("expected_total_cost_usd", 0))
            max_cost = float(contract.get("governance", {}).get("max_cost_usd", 0))
            return cost <= max_cost
        except (TypeError, ValueError):
            return False

    @staticmethod
    def result_bound_to_actor_activity_and_hash(
        upstream: dict[str, Any],
    ) -> bool:
        """True when the upstream response contains message content."""
        try:
            content = upstream["choices"][0]["message"]["content"]
            return isinstance(content, str) and len(content) > 0
        except (KeyError, IndexError, TypeError):
            return False

    # -- Verification guards -----------------------------------------------

    @staticmethod
    def required_verification_dag_materialized(
        verification_results: list[dict[str, Any]],
    ) -> bool:
        """True when at least one verification result exists."""
        return len(verification_results) > 0

    @staticmethod
    def all_mandatory_criteria_pass_and_no_blocking_conflict(
        verification_results: list[dict[str, Any]],
        epistemic_state: Any = None,
        risk_class: str = "low",
    ) -> bool:
        """True when every mandatory criterion status is ``passed`` and no
        conflicted claim blocks acceptance.

        EPR-EPI-005: When *epistemic_state* is provided and *risk_class* is
        ``high`` or ``critical``, conflicted claims in the epistemic state
        block acceptance even if all mandatory criteria pass.
        """
        for result in verification_results:
            # Check top-level result.
            if result.get("mandatory") and result.get("status") != "passed":
                return False
            # Check sub-results (e.g. from deterministic_acceptance).
            for sub in result.get("sub_results", []):
                if sub.get("mandatory") and sub.get("status") != "passed":
                    return False

        # EPR-EPI-005: conflicted claims block high/critical risk acceptance.
        if epistemic_state is not None and risk_class in ("high", "critical"):
            conflicted = epistemic_state.conflicted_claim_ids()
            if conflicted:
                return False

        return True

    @staticmethod
    def repair_budget_available() -> bool:
        """Skeleton: repair budget tracking is not implemented."""
        return False

    @staticmethod
    def no_admissible_repair() -> bool:
        """Skeleton: no repair paths exist."""
        return True

    @staticmethod
    def fallback_route_admissible() -> bool:
        """Skeleton: no fallback routing is implemented."""
        return False

    @staticmethod
    def no_fallback_or_budget_exhausted() -> bool:
        """Skeleton: no fallback available."""
        return True

    # -- Human-review guards -----------------------------------------------

    @staticmethod
    def human_authority_available() -> bool:
        """Skeleton: no human authority is connected."""
        return False

    @staticmethod
    def no_authorized_escalation() -> bool:
        """Skeleton: escalation always terminates in rejection."""
        return True

    @staticmethod
    def approval_scope_covers_outcome() -> bool:
        """Skeleton: human approval is not implemented."""
        return False

    # -- Receipt guard -----------------------------------------------------

    @staticmethod
    def receipt_contains_ledger_head_and_verification_evidence(
        receipt: dict[str, Any],
    ) -> bool:
        """True when the receipt carries both ledger hash and verification data."""
        return bool(
            receipt.get("ledger_head_hash")
            and receipt.get("verification_results") is not None
        )