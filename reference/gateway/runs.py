"""Per-run registry, hash-linked ledger, and evidence receipt issuance.

Each run owns its own hash chain; the first event has
``previous_event_hash: "GENESIS"``. ``issue_receipt`` binds the receipt to the
run's final ledger head hash, satisfying EPR-LED-004 for accepted outcomes.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from epr.ledger import append_event

from .fallback import FallbackRecorder

GATEWAY_ACTOR = {"id": "noerelay-gateway", "kind": "service", "version": "0.1.0"}
ROUTER_ACTOR = {"id": "epr-router", "kind": "service"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class RunRecord:
    run_id: str
    trace_id: str
    task_id: str | None = None
    contract: dict[str, Any] | None = None
    decision: dict[str, Any] | None = None
    openrouter_request: dict[str, Any] | None = None
    openrouter_response: dict[str, Any] | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    receipt: dict[str, Any] | None = None
    epistemic_state: Any = None
    canonical_state: Any = None
    context_package: Any = None
    fallbacks: FallbackRecorder = field(default_factory=FallbackRecorder)
    canary: bool = False
    policy_version: str | None = None
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    actual_cost_usd: float = 0.0
    latency_ms: float = 0.0
    original_prompt_tokens: int = 0
    required_human_intervention: bool = False
    required_rework: bool = False
    human_intervention_reason: str | None = None
    rework_reason: str | None = None
    decision_trace: list[dict[str, Any]] = field(default_factory=list)
    epistemic_ledger: Any = None


class RunRegistry:
    """In-memory, thread-safe run store with per-run ledger chains."""

    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}
        self._lock = threading.Lock()

    def begin(self, run_id: str, trace_id: str) -> RunRecord:
        from .epistemic import EpistemicState  # local import avoids circular dep
        from .epistemic_ledger import EpistemicLedgerEnricher

        record = RunRecord(run_id=run_id, trace_id=trace_id)
        record.epistemic_state = EpistemicState()
        record.epistemic_ledger = EpistemicLedgerEnricher()
        with self._lock:
            self._runs[run_id] = record
        return record

    def get(self, run_id: str) -> RunRecord | None:
        return self._runs.get(run_id)

    def _require(self, run_id: str) -> RunRecord:
        record = self._runs.get(run_id)
        if record is None:
            raise KeyError(run_id)
        return record

    def record_human_intervention(self, run_id: str, reason: str) -> None:
        """Mark a run as requiring human intervention and record the reason."""
        record = self._require(run_id)
        with self._lock:
            record.required_human_intervention = True
            record.human_intervention_reason = reason

    def record_rework(self, run_id: str, reason: str) -> None:
        """Mark a run as requiring rework and record the reason."""
        record = self._require(run_id)
        with self._lock:
            record.required_rework = True
            record.rework_reason = reason

    def ledger(
        self,
        run_id: str,
        event_type: str,
        actor: dict[str, Any],
        subject_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Append an immutable, hash-linked ledger event to the run's chain."""
        record = self._require(run_id)
        event = {
            "event_id": f"event-{uuid.uuid4().hex}",
            "run_id": run_id,
            "timestamp": _now(),
            "actor": actor,
            "event_type": event_type,
            "subject_id": subject_id,
            "payload": payload,
        }
        with self._lock:
            return append_event(record.events, event)

    def head_hash(self, run_id: str) -> str:
        record = self._require(run_id)
        return record.events[-1]["event_hash"] if record.events else "GENESIS"

    def issue_receipt(
        self,
        run_id: str,
        status: str,
        verification_results: list[dict[str, Any]],
        total_cost: float,
    ) -> dict[str, Any]:
        """Build and store an evidence-receipt conforming to the receipt schema."""
        record = self._require(run_id)
        decision = record.decision or {}
        contract = record.contract or {}

        receipt = {
            "receipt_id": f"receipt-{uuid.uuid4().hex}",
            "run_id": run_id,
            "task_id": contract.get("task_id", record.task_id),
            "status": status,
            "issued_at": _now(),
            "policy_version": decision.get("policy_version", ""),
            "route_decision_id": decision.get("decision_id", ""),
            "artifact_hashes": [],
            "verification_results": verification_results,
            "unresolved_claim_ids": [],
            "total_cost": {"currency": "USD", "amount": total_cost},
            "trace_id": record.trace_id,
            "ledger_head_hash": self.head_hash(run_id),
        }
        record.receipt = receipt
        return receipt

    def get_receipt(self, run_id: str) -> dict[str, Any] | None:
        record = self._runs.get(run_id)
        return record.receipt if record else None


def record_stub_verification(contract: dict[str, Any]) -> list[dict[str, Any]]:
    """Map acceptance criteria to stub verification results.

    ``observable`` and ``executable`` criteria are recorded as ``passed`` with a
    stub evidence id; other kinds (``judgmental``, ``missing``) are ``not_run``.

    **Deprecated in favour of** :func:`record_verification` **but retained for
    backward compatibility with existing tests.**
    """
    results: list[dict[str, Any]] = []
    for criterion in contract.get("acceptance_criteria", []):
        passed = criterion.get("kind") in {"observable", "executable"}
        results.append(
            {
                "criterion_id": criterion["id"],
                "status": "passed" if passed else "not_run",
                "evidence_ids": [f"evidence-{uuid.uuid4().hex}"] if passed else [],
            }
        )
    return results


def record_verification(
    contract: dict[str, Any],
    upstream_response: dict[str, Any],
    risk_class: str,
    policy: dict[str, Any],
    selected_plan: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool, list[dict[str, Any]]]:
    """Evaluate the verification DAG and produce real verification results.

    Delegates to :func:`gateway.verification.evaluate_verification` and
    returns ``(results, all_passed, evidence_records)``.

    Each result carries ``criterion_id``, ``status``
    (passed/failed/not_run/waived), and ``evidence_ids``.  Evidence records
    conform to :file:`spec/schemas/evidence.schema.json`.

    Args:
        contract: Compiled task contract.
        upstream_response: OpenAI-shaped response from the upstream model.
        risk_class: ``low``, ``medium``, ``high``, or ``critical``.
        policy: Routing-policy dict (contains the verification DAG).
        selected_plan: The plan selected by the routing kernel.

    Returns:
        ``(results, all_passed, evidence_records)``.
    """
    # Local import avoids circular dependency at module level.
    from .verification import evaluate_verification  # pragma: no cover

    return evaluate_verification(
        contract, upstream_response, risk_class, policy, selected_plan
    )