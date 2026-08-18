"""Escalation policy driven by human intervention rate and rework rate.

Uses rolling-window metrics to decide when to escalate from local to cloud
models and when to request human review.  Dependency-free (stdlib only).
"""

from __future__ import annotations

from typing import Any


class EscalationPolicy:
    """Uses human intervention rate and rework rate to inform escalation decisions.

    Maintains a rolling window of recent runs and computes HIR/RR from that
    window.  When either rate exceeds its threshold the policy recommends
    escalating to a more capable (cloud) model or requesting human review.
    """

    def __init__(
        self,
        hir_threshold: float = 0.15,
        rr_threshold: float = 0.25,
        min_sample_size: int = 10,
    ) -> None:
        self._hir_threshold = hir_threshold
        self._rr_threshold = rr_threshold
        self._min_sample_size = min_sample_size
        self._history: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_run(self, run_record: dict[str, Any]) -> None:
        """Record a completed run for policy evaluation.

        The *run_record* dict should carry at least ``required_human_intervention``
        (bool) and ``required_rework`` (bool).
        """
        self._history.append(run_record)
        # Keep only the last 100 runs.
        if len(self._history) > 100:
            self._history = self._history[-100:]

    # ------------------------------------------------------------------
    # Metric queries
    # ------------------------------------------------------------------

    def current_hir(self) -> float:
        """Current human intervention rate (0.0 – 1.0)."""
        if len(self._history) < self._min_sample_size:
            return 0.0
        interventions = sum(
            1 for r in self._history if r.get("required_human_intervention")
        )
        return interventions / len(self._history)

    def current_rr(self) -> float:
        """Current rework rate (0.0 – 1.0)."""
        if len(self._history) < self._min_sample_size:
            return 0.0
        rework = sum(1 for r in self._history if r.get("required_rework"))
        return rework / len(self._history)

    @property
    def history_size(self) -> int:
        """Number of runs in the rolling window."""
        return len(self._history)

    # ------------------------------------------------------------------
    # Decision methods
    # ------------------------------------------------------------------

    def should_escalate_to_cloud(
        self, local_model_failed: bool, risk_class: str
    ) -> tuple[bool, str]:
        """Decide whether to escalate from local to cloud model.

        Returns:
            ``(should_escalate, reason)``.
        """
        hir = self.current_hir()
        rr = self.current_rr()

        # Always escalate if local model failed.
        if local_model_failed:
            return True, "local_model_failed"

        # Escalate high/critical risk to cloud for safety.
        if risk_class in ("high", "critical"):
            return True, f"high_risk_class_{risk_class}"

        # Escalate if HIR is too high (too many human interventions needed).
        if hir > self._hir_threshold:
            return True, f"hir_exceeded_threshold_{hir:.2f}"

        # Escalate if RR is too high (too many reworks needed).
        if rr > self._rr_threshold:
            return True, f"rr_exceeded_threshold_{rr:.2f}"

        return False, "local_model_sufficient"

    def should_request_human_review(
        self,
        verification_failed: bool,
        has_blocking_conflict: bool,
        risk_class: str,
    ) -> tuple[bool, str]:
        """Decide whether to request human review.

        Returns:
            ``(should_request, reason)``.
        """
        if risk_class == "critical":
            return True, "critical_risk_requires_human"
        if has_blocking_conflict:
            return True, "blocking_epistemic_conflict"
        if verification_failed and self.current_rr() > self._rr_threshold:
            return True, f"verification_failed_with_high_rr_{self.current_rr():.2f}"
        return False, "no_human_review_needed"