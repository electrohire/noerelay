"""Epistemic state engine for the NoeRelay gateway.

Manages claims, evidence, four-valued fact adjudication, and model
calibration tracking.  Implements EPR-EPI-002 through EPR-EPI-006.

.. note::
   This module is dependency-free (stdlib only) and imports
   :func:`epr.epistemic.adjudicate_fact` for the core four-valued
   adjudication logic.
"""

from __future__ import annotations

import hashlib
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from epr.epistemic import adjudicate_fact

# ---------------------------------------------------------------------------
# Evidence kind constants
# ---------------------------------------------------------------------------

EVIDENCE_KIND_DIRECT_OBSERVATION = "direct_observation"
EVIDENCE_KIND_MODEL_ASSERTION = "model_assertion"
EVIDENCE_KIND_TOOL_RESULT = "tool_result"
EVIDENCE_KIND_TEST_RESULT = "test_result"
EVIDENCE_KIND_DERIVED = "derived"

# Kinds that count as "non-model-assertion" for corroboration (EPR-EPI-003).
NON_MODEL_ASSERTION_KINDS: frozenset[str] = frozenset({
    EVIDENCE_KIND_DIRECT_OBSERVATION,
    EVIDENCE_KIND_TOOL_RESULT,
    EVIDENCE_KIND_TEST_RESULT,
})

# Four-valued adjudication outcomes.
STATUS_UNKNOWN = "unknown"
STATUS_SUPPORTED = "supported"
STATUS_REFUTED = "refuted"
STATUS_CONFLICTED = "conflicted"

# Default confidence threshold for adjudication.
DEFAULT_ADJUDICATION_THRESHOLD = 0.5

# Conservative discount factor when calibration data is insufficient.
FALLBACK_CALIBRATION_FACTOR = 0.5

# Minimum records needed before using calibration data.
MIN_CALIBRATION_RECORDS = 10

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Calibration Store (EPR-EPI-006)
# ---------------------------------------------------------------------------


@dataclass
class CalibrationStore:
    """Tracks calibration data so that model self-confidence can be calibrated
    against environment-labeled outcomes.

    EPR-EPI-006: Confidence used for routing MUST be calibrated on
    environment-labeled outcomes; model self-confidence alone is insufficient.
    """

    _records: dict[str, list[tuple[float, bool]]] = field(default_factory=dict)

    def record_outcome(
        self, model_id: str, predicted_probability: float, actual_outcome: bool
    ) -> None:
        """Record a single (predicted, actual) calibration data point."""
        if not 0.0 <= predicted_probability <= 1.0:
            raise ValueError("predicted_probability must be in [0, 1]")
        self._records.setdefault(model_id, []).append(
            (predicted_probability, actual_outcome)
        )

    def record_count(self, model_id: str) -> int:
        """Return the number of calibration records for *model_id*."""
        return len(self._records.get(model_id, []))

    def compute_ece(self, model_id: str, n_bins: int = 10) -> float:
        """Compute Expected Calibration Error (ECE) for *model_id*.

        Returns 0.0 when fewer than *MIN_CALIBRATION_RECORDS* records exist
        (insufficient data to estimate calibration).
        """
        records = self._records.get(model_id, [])
        if len(records) < MIN_CALIBRATION_RECORDS:
            return 0.0

        bin_boundaries = [i / n_bins for i in range(n_bins + 1)]
        bin_totals = [0.0] * n_bins
        bin_correct = [0.0] * n_bins
        bin_counts = [0] * n_bins

        for predicted, actual in records:
            # Find the bin: predicted in [bin_boundaries[i], bin_boundaries[i+1])
            # Edge case: predicted == 1.0 goes into the last bin.
            bin_idx = min(int(predicted * n_bins), n_bins - 1)
            bin_totals[bin_idx] += predicted
            bin_counts[bin_idx] += 1
            if actual:
                bin_correct[bin_idx] += 1.0

        ece = 0.0
        total = len(records)
        for i in range(n_bins):
            if bin_counts[i] == 0:
                continue
            avg_confidence = bin_totals[i] / bin_counts[i]
            avg_accuracy = bin_correct[i] / bin_counts[i]
            ece += (bin_counts[i] / total) * abs(avg_confidence - avg_accuracy)

        return ece

    def is_calibrated(self, model_id: str, threshold: float = 0.04) -> bool:
        """Return True when the model's ECE is at or below *threshold*.

        EPR-EPI-006: The benchmark gate threshold is 0.04.
        """
        if self.record_count(model_id) < MIN_CALIBRATION_RECORDS:
            return False
        return self.compute_ece(model_id) <= threshold

    def calibration_factor(self, model_id: str) -> float:
        """Compute a calibration multiplier from observed accuracy / mean confidence.

        Returns 1.0 when insufficient data exists (caller should apply
        conservative discount separately).
        """
        records = self._records.get(model_id, [])
        if len(records) < MIN_CALIBRATION_RECORDS:
            return 1.0
        mean_confidence = sum(p for p, _ in records) / len(records)
        accuracy = sum(1 for _, a in records if a) / len(records)
        if mean_confidence == 0:
            return 1.0
        factor = accuracy / mean_confidence
        return max(0.0, min(factor, 1.0))

    def calibrated_confidence(self, model_id: str, raw_confidence: float) -> float:
        """Return a calibrated confidence for *model_id*.

        When sufficient calibration data exists the raw confidence is
        multiplied by the calibration factor.  Otherwise a conservative
        discount (0.5 × raw) is applied (EPR-EPI-006).
        """
        if self.record_count(model_id) >= MIN_CALIBRATION_RECORDS:
            factor = self.calibration_factor(model_id)
            return raw_confidence * factor
        return raw_confidence * FALLBACK_CALIBRATION_FACTOR


# ---------------------------------------------------------------------------
# Epistemic State
# ---------------------------------------------------------------------------


@dataclass
class EpistemicState:
    """Manages claims, evidence, and four-valued fact adjudication.

    The state is per-run: each :class:`RunRecord` owns one instance.
    Evidence is ingested via :meth:`add_evidence`, claims are created
    via :meth:`add_claim` or :meth:`add_derived_claim`, and the
    four-valued status is retrieved with :meth:`adjudicate`.

    EPR-EPI-002: Evidence is classified by *kind* so that model
    assertions are never mistaken for direct observations.

    EPR-EPI-003: Corroboration requires at least one non-model-assertion
    evidence source.

    EPR-EPI-004: Derived claims reference premises and cannot exceed the
    weakest premise confidence.

    EPR-EPI-005: Conflicted claims block high/critical-risk acceptance.
    """

    _claims: dict[str, dict[str, Any]] = field(default_factory=dict)
    _evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    _calibration: CalibrationStore = field(default_factory=CalibrationStore)
    _claim_transitions: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Evidence
    # ------------------------------------------------------------------

    def add_evidence(self, evidence: dict[str, Any]) -> str:
        """Register an evidence record and return its ``evidence_id``.

        The evidence dict should conform to
        :file:`spec/schemas/evidence.schema.json` and MUST carry at least
        ``evidence_id``, ``kind``, and ``strength``.

        EPR-EPI-002: The *kind* field is preserved as-is; callers are
        responsible for setting the correct kind (e.g. ``model_assertion``
        for LLM outputs, ``direct_observation`` for sensor/tool results).
        """
        eid = evidence["evidence_id"]
        self._evidence[eid] = dict(evidence)
        return eid

    def get_evidence(self, evidence_id: str) -> dict[str, Any] | None:
        """Return the evidence record for *evidence_id*, or None."""
        return self._evidence.get(evidence_id)

    def evidence_count(self) -> int:
        """Return the total number of registered evidence records."""
        return len(self._evidence)

    def iter_evidence(self) -> dict[str, dict[str, Any]]:
        """Return a shallow copy of all evidence records keyed by ``evidence_id``."""
        return dict(self._evidence)

    # ------------------------------------------------------------------
    # Claims
    # ------------------------------------------------------------------

    def add_claim(
        self,
        claim_id: str,
        statement: str,
        evidence_ids: list[str],
        threshold: float = DEFAULT_ADJUDICATION_THRESHOLD,
    ) -> str:
        """Create or update a claim backed by *evidence_ids*.

        Evidence is grouped into supporting (strength > 0.5) and refuting
        (strength < 0.5).  The four-valued status is computed via
        :func:`epr.epistemic.adjudicate_fact`.

        EPR-EPI-003: If all supporting evidence is ``model_assertion``,
        the claim cannot be promoted to ``supported`` even if the
        adjudication function would return ``supported`` — it is clamped
        to ``unknown``.

        Returns the four-valued status.
        """
        # Validate evidence IDs.
        for eid in evidence_ids:
            if eid not in self._evidence:
                raise KeyError(f"evidence {eid!r} not found")

        supporting_strengths: list[float] = []
        supporting_kinds: list[str] = []
        refuting_strengths: list[float] = []

        for eid in evidence_ids:
            ev = self._evidence[eid]
            strength = float(ev.get("strength", 0.5))
            kind = ev.get("kind", EVIDENCE_KIND_DIRECT_OBSERVATION)
            if strength > 0.5:
                supporting_strengths.append(strength)
                supporting_kinds.append(kind)
            elif strength < 0.5:
                refuting_strengths.append(1.0 - strength)

        support_lcb = max(supporting_strengths) if supporting_strengths else 0.0
        refutation_lcb = max(refuting_strengths) if refuting_strengths else 0.0

        raw_status = adjudicate_fact(support_lcb, refutation_lcb, threshold)

        # EPR-EPI-003: model assertions alone cannot corroborate.
        if raw_status == STATUS_SUPPORTED:
            all_model_assertion = all(
                k == EVIDENCE_KIND_MODEL_ASSERTION for k in supporting_kinds
            )
            if all_model_assertion and supporting_kinds:
                raw_status = STATUS_UNKNOWN

        # Record claim transition if status changed
        old_claim = self._claims.get(claim_id)
        old_status = old_claim["status"] if old_claim else "unknown"
        if old_status != raw_status:
            self._claim_transitions.append({
                "claim_id": claim_id,
                "old_status": old_status,
                "new_status": raw_status,
                "evidence_ids": list(evidence_ids),
                "reasoning": (
                    f"Claim {claim_id!r} transitioned from {old_status} to "
                    f"{raw_status} based on {len(evidence_ids)} evidence items"
                ),
            })

        claim = {
            "claim_id": claim_id,
            "statement": statement,
            "status": raw_status,
            "confidence": support_lcb,
            "refutation_confidence": refutation_lcb,
            "evidence_ids": list(evidence_ids),
            "is_derived": False,
            "premise_evidence_ids": [],
        }
        self._claims[claim_id] = claim
        return raw_status

    def add_derived_claim(
        self,
        claim_id: str,
        statement: str,
        premise_evidence_ids: list[str],
        threshold: float = DEFAULT_ADJUDICATION_THRESHOLD,
    ) -> str:
        """Create a derived claim whose confidence is bounded by its premises.

        EPR-EPI-004:
        - Must reference at least one premise.
        - Confidence cannot exceed the weakest premise.
        - Rejected if any premise is ``refuted`` or ``conflicted``.
        """
        if len(premise_evidence_ids) < 1:
            raise ValueError("derived claim must reference at least one premise")

        # Validate all premise evidence exists.
        for eid in premise_evidence_ids:
            if eid not in self._evidence:
                raise KeyError(f"premise evidence {eid!r} not found")

        # Check if any premise has a claim that is refuted or conflicted.
        # We look up claims that reference these evidence IDs.
        premise_statuses: list[str] = []
        premise_confidences: list[float] = []

        for eid in premise_evidence_ids:
            ev = self._evidence[eid]
            # The premise evidence itself has a strength — treat it as a
            # single-evidence "claim" for purposes of derivation.
            strength = float(ev.get("strength", 0.5))
            # If strength < 0.5, this evidence refutes.
            if strength < 0.5:
                premise_statuses.append(STATUS_REFUTED)
            elif strength > 0.5:
                premise_statuses.append(STATUS_SUPPORTED)
            else:
                premise_statuses.append(STATUS_UNKNOWN)
            premise_confidences.append(strength)

        # EPR-EPI-004: reject if any premise is refuted or conflicted.
        for ps in premise_statuses:
            if ps in (STATUS_REFUTED, STATUS_CONFLICTED):
                # Record claim transition
                old_claim = self._claims.get(claim_id)
                old_status = old_claim["status"] if old_claim else "unknown"
                if old_status != STATUS_REFUTED:
                    self._claim_transitions.append({
                        "claim_id": claim_id,
                        "old_status": old_status,
                        "new_status": STATUS_REFUTED,
                        "evidence_ids": list(premise_evidence_ids),
                        "reasoning": (
                            f"Derived claim {claim_id!r} transitioned from "
                            f"{old_status} to {STATUS_REFUTED}: premise refuted"
                        ),
                    })
                claim = {
                    "claim_id": claim_id,
                    "statement": statement,
                    "status": STATUS_REFUTED,
                    "confidence": 0.0,
                    "refutation_confidence": 1.0,
                    "evidence_ids": [],
                    "is_derived": True,
                    "premise_evidence_ids": list(premise_evidence_ids),
                }
                self._claims[claim_id] = claim
                return STATUS_REFUTED

        # EPR-EPI-004: derived confidence = min(premise confidences).
        derived_confidence = min(premise_confidences) if premise_confidences else 0.0

        status: str
        if derived_confidence >= threshold:
            status = STATUS_SUPPORTED
        elif derived_confidence > 0.0:
            status = STATUS_UNKNOWN
        else:
            status = STATUS_UNKNOWN

        claim = {
            "claim_id": claim_id,
            "statement": statement,
            "status": status,
            "confidence": derived_confidence,
            "refutation_confidence": 0.0,
            "evidence_ids": [],
            "is_derived": True,
            "premise_evidence_ids": list(premise_evidence_ids),
        }
        # Record claim transition if status changed
        old_claim = self._claims.get(claim_id)
        old_status = old_claim["status"] if old_claim else "unknown"
        if old_status != status:
            self._claim_transitions.append({
                "claim_id": claim_id,
                "old_status": old_status,
                "new_status": status,
                "evidence_ids": list(premise_evidence_ids),
                "reasoning": (
                    f"Derived claim {claim_id!r} transitioned from {old_status} "
                    f"to {status} based on {len(premise_evidence_ids)} premises"
                ),
            })
        self._claims[claim_id] = claim
        return status

    # ------------------------------------------------------------------
    # Adjudication
    # ------------------------------------------------------------------

    def adjudicate(self, claim_id: str) -> str:
        """Return the four-valued status for *claim_id*.

        Raises :class:`KeyError` if the claim does not exist.
        """
        claim = self._claims[claim_id]
        return claim["status"]

    def get_claim(self, claim_id: str) -> dict[str, Any] | None:
        """Return the full claim dict, or None."""
        return self._claims.get(claim_id)

    def claim_count(self) -> int:
        """Return the total number of registered claims."""
        return len(self._claims)

    def get_claim_transitions(self) -> list[dict[str, Any]]:
        """Return and clear the list of pending claim transitions."""
        transitions = list(self._claim_transitions)
        self._claim_transitions.clear()
        return transitions

    def clear_claim_transitions(self) -> None:
        """Clear pending claim transitions without returning them."""
        self._claim_transitions.clear()

    def iter_claims(self) -> dict[str, dict[str, Any]]:
        """Return a shallow copy of all claims keyed by ``claim_id``."""
        return dict(self._claims)

    # ------------------------------------------------------------------
    # Conflict detection (EPR-EPI-005)
    # ------------------------------------------------------------------

    def has_blocking_conflict(self, claim_ids: list[str]) -> bool:
        """Return True if any claim in *claim_ids* is ``conflicted``.

        EPR-EPI-005: When a claim is conflicted, high-risk and
        critical-risk acceptance gates that depend on it MUST be blocked.
        """
        for cid in claim_ids:
            claim = self._claims.get(cid)
            if claim is not None and claim["status"] == STATUS_CONFLICTED:
                return True
        return False

    def conflicted_claim_ids(self) -> list[str]:
        """Return all claim IDs currently in ``conflicted`` status."""
        return [
            cid
            for cid, claim in self._claims.items()
            if claim["status"] == STATUS_CONFLICTED
        ]

    # ------------------------------------------------------------------
    # Corroboration (EPR-EPI-003)
    # ------------------------------------------------------------------

    def can_promote_by_corroboration(self, evidence_ids: list[str]) -> bool:
        """Return True when at least one evidence item is not a model assertion.

        EPR-EPI-003: Two model assertions agreeing is not sufficient;
        at least one non-model-assertion evidence source is required.
        """
        for eid in evidence_ids:
            ev = self._evidence.get(eid)
            if ev is None:
                continue
            kind = ev.get("kind", "")
            if kind in NON_MODEL_ASSERTION_KINDS:
                return True
        return False

    # ------------------------------------------------------------------
    # Calibration access
    # ------------------------------------------------------------------

    @property
    def calibration(self) -> CalibrationStore:
        """Return the :class:`CalibrationStore` for this epistemic state."""
        return self._calibration


# ---------------------------------------------------------------------------
# Convenience factory for model_assertion evidence
# ---------------------------------------------------------------------------

GATEWAY_PRODUCER: dict[str, Any] = {
    "id": "noerelay-gateway",
    "kind": "service",
    "version": "0.1.0",
}


def make_model_assertion_evidence(
    model_id: str,
    content: Any,
    confidence: float = 0.7,
    activity_id: str = "model-inference",
    producer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a ``model_assertion`` evidence record.

    EPR-EPI-002: Model outputs MUST be classified as ``model_assertion``,
    not ``direct_observation``.

    Args:
        model_id: The model that produced the claim (e.g. ``qwen/qwen3.6-35b-a3b``).
        content: The model output or claim content.
        confidence: Raw model confidence (0–1).  Default 0.7 for skeleton.
        activity_id: Identifier for the producing activity.
        producer: Producer metadata dict.

    Returns:
        An evidence dict conforming to :file:`spec/schemas/evidence.schema.json`.
    """
    if producer is None:
        producer = GATEWAY_PRODUCER
    content_str = (
        content if isinstance(content, str) else __import__("json").dumps(
            content, sort_keys=True, separators=(",", ":")
        )
    )
    return {
        "evidence_id": f"evidence-{uuid.uuid4().hex}",
        "kind": EVIDENCE_KIND_MODEL_ASSERTION,
        "produced_at": _now(),
        "producer": dict(producer),
        "activity_id": activity_id,
        "content_hash": _sha256(content_str),
        "location": "pipeline.model_inference",
        "strength": confidence,
        "model_id": model_id,
    }


def make_tool_result_evidence(
    tool_name: str,
    result: Any,
    success: bool,
    activity_id: str = "tool-execution",
    producer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a ``tool_result`` evidence record."""
    if producer is None:
        producer = GATEWAY_PRODUCER
    content_str = __import__("json").dumps(
        {"tool": tool_name, "success": success, "result": result},
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "evidence_id": f"evidence-{uuid.uuid4().hex}",
        "kind": EVIDENCE_KIND_TOOL_RESULT,
        "produced_at": _now(),
        "producer": dict(producer),
        "activity_id": activity_id,
        "content_hash": _sha256(content_str),
        "location": f"pipeline.tool_result.{tool_name}",
        "strength": 1.0 if success else 0.0,
        "tool_name": tool_name,
    }


def make_direct_observation_evidence(
    content: Any,
    strength: float,
    activity_id: str = "observation",
    location: str = "pipeline.observation",
    producer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a ``direct_observation`` evidence record."""
    if producer is None:
        producer = GATEWAY_PRODUCER
    content_str = __import__("json").dumps(
        content, sort_keys=True, separators=(",", ":")
    )
    return {
        "evidence_id": f"evidence-{uuid.uuid4().hex}",
        "kind": EVIDENCE_KIND_DIRECT_OBSERVATION,
        "produced_at": _now(),
        "producer": dict(producer),
        "activity_id": activity_id,
        "content_hash": _sha256(content_str),
        "location": location,
        "strength": strength,
    }