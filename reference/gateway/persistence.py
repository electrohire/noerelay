"""File-based run persistence for the gateway."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .runs import RunRecord, RunRegistry


class FileRunRegistry(RunRegistry):
    """File-based run registry that persists runs to disk."""

    def __init__(
        self,
        storage_dir: str = ".noerelay/runs",
        max_runs: int = 10000,
    ) -> None:
        super().__init__(max_runs=max_runs)
        self._storage_dir = Path(storage_dir)
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._load_existing()

    def _load_existing(self) -> None:
        """Load existing runs from disk on startup."""
        for file in self._storage_dir.glob("*.json"):
            try:
                data = json.loads(file.read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            record = self._record_from_dict(data)
            if record is not None and record.run_id:
                with self._lock:
                    self._runs[record.run_id] = record
                    self._trim_locked()

    def _record_from_dict(self, data: dict[str, Any]) -> RunRecord | None:
        if not isinstance(data, dict):
            return None
        return RunRecord(
            run_id=str(data.get("run_id", "")),
            trace_id=str(data.get("trace_id", "")),
            tenant_id=str(data.get("tenant_id", "default")),
            task_id=data.get("task_id"),
            contract=data.get("contract"),
            decision=data.get("decision"),
            openrouter_request=data.get("openrouter_request"),
            openrouter_response=data.get("openrouter_response"),
            events=data.get("events", []),
            receipt=data.get("receipt"),
            canary=bool(data.get("canary", False)),
            policy_version=data.get("policy_version"),
            total_tokens=int(data.get("total_tokens", 0)),
            prompt_tokens=int(data.get("prompt_tokens", 0)),
            completion_tokens=int(data.get("completion_tokens", 0)),
            actual_cost_usd=float(data.get("actual_cost_usd", 0.0)),
            latency_ms=float(data.get("latency_ms", 0.0)),
        )

    def _record_to_dict(self, record: RunRecord) -> dict[str, Any]:
        return {
            "run_id": record.run_id,
            "trace_id": record.trace_id,
            "tenant_id": record.tenant_id,
            "task_id": record.task_id,
            "contract": record.contract,
            "decision": record.decision,
            "openrouter_request": record.openrouter_request,
            "openrouter_response": record.openrouter_response,
            "events": record.events,
            "receipt": record.receipt,
            "canary": record.canary,
            "policy_version": record.policy_version,
            "total_tokens": record.total_tokens,
            "prompt_tokens": record.prompt_tokens,
            "completion_tokens": record.completion_tokens,
            "actual_cost_usd": record.actual_cost_usd,
            "latency_ms": record.latency_ms,
        }

    def _persist(self, run_id: str) -> None:
        """Persist a run to disk."""
        record = self.get(run_id)
        if record is None:
            return
        path = self._storage_dir / f"{run_id}.json"
        path.write_text(
            json.dumps(self._record_to_dict(record), ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    def begin(self, run_id: str, trace_id: str) -> RunRecord:
        record = super().begin(run_id, trace_id)
        self._persist(run_id)
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
        self._persist(run_id)
        return event

    def issue_receipt(
        self,
        run_id: str,
        status: str,
        verification_results: list[dict[str, Any]],
        total_cost: float,
    ) -> dict[str, Any]:
        receipt = super().issue_receipt(run_id, status, verification_results, total_cost)
        self._persist(run_id)
        return receipt
