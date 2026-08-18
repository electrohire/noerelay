"""Database-backed RunRegistry for production persistence.

Extends the in-memory RunRegistry to persist all operations
to a SQLite database. Reads fall through to the database.
"""

from __future__ import annotations

import json
from typing import Any

from .database import SQLiteDatabase
from .runs import GATEWAY_ACTOR, RunRecord, RunRegistry, _now


class DatabaseRunRegistry(RunRegistry):
    """RunRegistry backed by SQLite database.

    Extends the in-memory RunRegistry to persist all operations
    to a SQLite database. Reads fall through to the database.
    """

    def __init__(self, db: SQLiteDatabase) -> None:
        super().__init__()
        self._db = db

    def begin(self, run_id: str, trace_id: str) -> RunRecord:
        record = super().begin(run_id, trace_id)
        self._db.save_run(
            {
                "run_id": record.run_id,
                "trace_id": record.trace_id,
                "task_id": record.task_id,
                "status": "pending",
                "created_at": _now(),
                "total_tokens": record.total_tokens,
                "prompt_tokens": record.prompt_tokens,
                "completion_tokens": record.completion_tokens,
                "actual_cost_usd": record.actual_cost_usd,
                "latency_ms": record.latency_ms,
                "model_id": None,
                "required_human_intervention": record.required_human_intervention,
                "required_rework": record.required_rework,
                "human_intervention_reason": record.human_intervention_reason,
                "rework_reason": record.rework_reason,
                "risk_class": "low",
                "is_local": False,
                "cache_hit": False,
                "decision_trace": record.decision_trace,
                "receipt": record.receipt,
                "openrouter_request": record.openrouter_request,
                "openrouter_response": record.openrouter_response,
            }
        )
        return record

    def ledger(
        self,
        run_id: str,
        event_type: str,
        actor: dict[str, Any],
        subject_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        event = super().ledger(run_id, event_type, actor, subject_id, payload)
        self._db.save_ledger_event(event)
        return event

    def issue_receipt(
        self,
        run_id: str,
        status: str,
        verification_results: list[dict[str, Any]],
        total_cost: float,
    ) -> dict[str, Any]:
        receipt = super().issue_receipt(
            run_id, status, verification_results, total_cost
        )
        # Update the run record with receipt and status
        record = self._runs.get(run_id)
        if record:
            self._db.save_run(
                {
                    "run_id": record.run_id,
                    "trace_id": record.trace_id,
                    "task_id": record.task_id,
                    "status": status,
                    "created_at": _now(),
                    "completed_at": _now(),
                    "total_tokens": record.total_tokens,
                    "prompt_tokens": record.prompt_tokens,
                    "completion_tokens": record.completion_tokens,
                    "actual_cost_usd": record.actual_cost_usd,
                    "latency_ms": record.latency_ms,
                    "model_id": (
                        record.decision.get("selected_plan", {}).get("model_id")
                        if record.decision
                        else None
                    ),
                    "required_human_intervention": record.required_human_intervention,
                    "required_rework": record.required_rework,
                    "human_intervention_reason": record.human_intervention_reason,
                    "rework_reason": record.rework_reason,
                    "risk_class": (
                        record.contract.get("risk_class", "low")
                        if record.contract
                        else "low"
                    ),
                    "is_local": False,
                    "cache_hit": False,
                    "decision_trace": record.decision_trace,
                    "receipt": receipt,
                    "openrouter_request": record.openrouter_request,
                    "openrouter_response": record.openrouter_response,
                }
            )
        return receipt

    def get(self, run_id: str) -> RunRecord | None:
        # Try memory first, then database
        record = super().get(run_id)
        if record is None:
            db_run = self._db.get_run(run_id)
            if db_run:
                record = self._record_from_db(db_run)
                with self._lock:
                    self._runs[run_id] = record
        return record

    def get_receipt(self, run_id: str) -> dict[str, Any] | None:
        receipt = super().get_receipt(run_id)
        if receipt is None:
            db_run = self._db.get_run(run_id)
            if db_run:
                receipt_raw = db_run.get("receipt")
                if receipt_raw:
                    if isinstance(receipt_raw, str):
                        try:
                            return json.loads(receipt_raw)
                        except (json.JSONDecodeError, TypeError):
                            return None
                    return receipt_raw
        return receipt

    def record_human_intervention(self, run_id: str, reason: str) -> None:
        super().record_human_intervention(run_id, reason)
        record = self._runs.get(run_id)
        if record:
            self._db.save_run(
                {
                    "run_id": record.run_id,
                    "trace_id": record.trace_id,
                    "task_id": record.task_id,
                    "status": "pending",
                    "created_at": _now(),
                    "total_tokens": record.total_tokens,
                    "prompt_tokens": record.prompt_tokens,
                    "completion_tokens": record.completion_tokens,
                    "actual_cost_usd": record.actual_cost_usd,
                    "latency_ms": record.latency_ms,
                    "model_id": None,
                    "required_human_intervention": True,
                    "required_rework": record.required_rework,
                    "human_intervention_reason": reason,
                    "rework_reason": record.rework_reason,
                    "risk_class": "low",
                    "is_local": False,
                    "cache_hit": False,
                    "decision_trace": record.decision_trace,
                    "receipt": record.receipt,
                    "openrouter_request": record.openrouter_request,
                    "openrouter_response": record.openrouter_response,
                }
            )

    def record_rework(self, run_id: str, reason: str) -> None:
        super().record_rework(run_id, reason)
        record = self._runs.get(run_id)
        if record:
            self._db.save_run(
                {
                    "run_id": record.run_id,
                    "trace_id": record.trace_id,
                    "task_id": record.task_id,
                    "status": "pending",
                    "created_at": _now(),
                    "total_tokens": record.total_tokens,
                    "prompt_tokens": record.prompt_tokens,
                    "completion_tokens": record.completion_tokens,
                    "actual_cost_usd": record.actual_cost_usd,
                    "latency_ms": record.latency_ms,
                    "model_id": None,
                    "required_human_intervention": record.required_human_intervention,
                    "required_rework": True,
                    "human_intervention_reason": record.human_intervention_reason,
                    "rework_reason": reason,
                    "risk_class": "low",
                    "is_local": False,
                    "cache_hit": False,
                    "decision_trace": record.decision_trace,
                    "receipt": record.receipt,
                    "openrouter_request": record.openrouter_request,
                    "openrouter_response": record.openrouter_response,
                }
            )

    def _record_from_db(self, db_run: dict[str, Any]) -> RunRecord:
        """Reconstruct a RunRecord from a database row."""
        record = RunRecord(
            run_id=db_run.get("run_id", ""),
            trace_id=db_run.get("trace_id", ""),
            task_id=db_run.get("task_id"),
            total_tokens=int(db_run.get("total_tokens", 0)),
            prompt_tokens=int(db_run.get("prompt_tokens", 0)),
            completion_tokens=int(db_run.get("completion_tokens", 0)),
            actual_cost_usd=float(db_run.get("actual_cost_usd", 0.0)),
            latency_ms=float(db_run.get("latency_ms", 0.0)),
            required_human_intervention=bool(db_run.get("required_human_intervention", False)),
            required_rework=bool(db_run.get("required_rework", False)),
            human_intervention_reason=db_run.get("human_intervention_reason"),
            rework_reason=db_run.get("rework_reason"),
        )

        # Restore JSON fields
        decision_trace = db_run.get("decision_trace")
        if decision_trace:
            if isinstance(decision_trace, str):
                try:
                    record.decision_trace = json.loads(decision_trace)
                except (json.JSONDecodeError, TypeError):
                    pass
            else:
                record.decision_trace = decision_trace

        receipt = db_run.get("receipt")
        if receipt:
            if isinstance(receipt, str):
                try:
                    record.receipt = json.loads(receipt)
                except (json.JSONDecodeError, TypeError):
                    pass
            else:
                record.receipt = receipt

        openrouter_request = db_run.get("openrouter_request")
        if openrouter_request:
            if isinstance(openrouter_request, str):
                try:
                    record.openrouter_request = json.loads(openrouter_request)
                except (json.JSONDecodeError, TypeError):
                    pass
            else:
                record.openrouter_request = openrouter_request

        openrouter_response = db_run.get("openrouter_response")
        if openrouter_response:
            if isinstance(openrouter_response, str):
                try:
                    record.openrouter_response = json.loads(openrouter_response)
                except (json.JSONDecodeError, TypeError):
                    pass
            else:
                record.openrouter_response = openrouter_response

        return record