"""SQLite-based production persistence for NoeRelay.

Stores runs, ledger events, evidence receipts, API keys, and audit logs
in a SQLite database. Thread-safe with connection pooling.

Schema:
- runs: run_id, trace_id, task_id, status, created_at, completed_at,
        total_tokens, actual_cost_usd, latency_ms, model_id,
        required_human_intervention, required_rework, risk_class,
        decision_trace (JSON), receipt (JSON)
- ledger_events: event_id, run_id, sequence, timestamp, actor (JSON),
                 event_type, subject_id, payload (JSON),
                 previous_event_hash, event_hash
- api_keys: key_id, key_hash, name, role, created_at, revoked_at,
            last_used_at, rate_limit_rate, rate_limit_burst
- audit_log: audit_id, timestamp, actor_id, action, resource_type,
             resource_id, ip_address, details (JSON), success
- benchmark_results: result_id, cohort_name, model_id, timestamp,
                     accuracy, total_tokens, total_cost, mean_latency,
                     p95_latency, hir, rr, results (JSON)
- config: key, value (JSON), updated_at, updated_by
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class SQLiteDatabase:
    """SQLite-based production persistence for NoeRelay.

    Stores runs, ledger events, evidence receipts, API keys, and audit logs
    in a SQLite database. Thread-safe with connection pooling.
    """

    def __init__(self, db_path: str = ".noerelay/noerelay.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            try:
                self._db_path.parent.chmod(0o700)
            except OSError:
                pass
        self._local = threading.local()
        self._lock = threading.Lock()
        self._connections: list[sqlite3.Connection] = []
        self._init_schema()
        self._restrict_file_permissions(self._db_path)

    @staticmethod
    def _restrict_file_permissions(path: Path) -> None:
        """Restrict sensitive database artifacts on POSIX hosts."""
        if os.name != "nt" and path.exists():
            try:
                path.chmod(0o600)
            except OSError:
                pass

    def close_thread_connection(self) -> None:
        """Close the current worker thread's SQLite connection."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            return
        self._local.conn = None
        with self._lock:
            try:
                self._connections.remove(conn)
            except ValueError:
                pass
        try:
            conn.close()
        except sqlite3.Error:
            pass

    def close(self) -> None:
        """Close all database connections."""
        with self._lock:
            for conn in self._connections:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            self._connections.clear()
        if hasattr(self._local, "conn") and self._local.conn is not None:
            try:
                self._local.conn.close()
            except sqlite3.Error:
                pass
            self._local.conn = None

    def _get_conn(self) -> sqlite3.Connection:
        """Get a thread-local connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(
                str(self._db_path),
                check_same_thread=False,
                isolation_level=None,  # autocommit mode
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
            with self._lock:
                self._connections.append(conn)
        return self._local.conn

    def _init_schema(self) -> None:
        """Initialize database schema."""
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                task_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                completed_at TEXT,
                total_tokens INTEGER DEFAULT 0,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                actual_cost_usd REAL DEFAULT 0.0,
                latency_ms REAL DEFAULT 0.0,
                model_id TEXT,
                required_human_intervention INTEGER DEFAULT 0,
                required_rework INTEGER DEFAULT 0,
                human_intervention_reason TEXT,
                rework_reason TEXT,
                risk_class TEXT DEFAULT 'low',
                is_local INTEGER DEFAULT 0,
                cache_hit INTEGER DEFAULT 0,
                decision_trace TEXT,
                receipt TEXT,
                openrouter_request TEXT,
                openrouter_response TEXT
            );

            CREATE TABLE IF NOT EXISTS ledger_events (
                event_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                actor TEXT NOT NULL,
                event_type TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                payload TEXT,
                previous_event_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES runs(run_id)
            );

            CREATE INDEX IF NOT EXISTS idx_ledger_run_id ON ledger_events(run_id);
            CREATE INDEX IF NOT EXISTS idx_ledger_event_type ON ledger_events(event_type);
            CREATE INDEX IF NOT EXISTS idx_ledger_timestamp ON ledger_events(timestamp);

            CREATE TABLE IF NOT EXISTS api_keys (
                key_id TEXT PRIMARY KEY,
                key_hash TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'operator',
                created_at TEXT NOT NULL,
                revoked_at TEXT,
                last_used_at TEXT,
                rate_limit_rate REAL DEFAULT 10.0,
                rate_limit_burst INTEGER DEFAULT 20,
                tenant_id TEXT DEFAULT 'default'
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                audit_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                actor_id TEXT,
                action TEXT NOT NULL,
                resource_type TEXT,
                resource_id TEXT,
                ip_address TEXT,
                details TEXT,
                success INTEGER DEFAULT 1
            );

            CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
            CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor_id);

            CREATE TABLE IF NOT EXISTS benchmark_results (
                result_id TEXT PRIMARY KEY,
                cohort_name TEXT NOT NULL,
                model_id TEXT,
                timestamp TEXT NOT NULL,
                accuracy REAL,
                total_tokens INTEGER,
                total_cost_usd REAL,
                mean_latency_ms REAL,
                p95_latency_ms REAL,
                hir REAL,
                rr REAL,
                escalation_rate REAL,
                results TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_benchmark_cohort ON benchmark_results(cohort_name);
            CREATE INDEX IF NOT EXISTS idx_benchmark_model ON benchmark_results(model_id);

            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT NOT NULL,
                updated_by TEXT
            );

            CREATE TABLE IF NOT EXISTS run_tenants (
                run_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL DEFAULT 'default',
                FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
            );
        """)

    def set_run_tenant(self, run_id: str, tenant_id: str) -> None:
        """Persist the tenant owner separately from the stable run schema."""
        self._get_conn().execute(
            "INSERT OR REPLACE INTO run_tenants (run_id, tenant_id) VALUES (?, ?)",
            (run_id, tenant_id),
        )

    def get_run_tenant(self, run_id: str) -> str:
        """Return the owning tenant for a run, defaulting legacy rows safely."""
        row = self._get_conn().execute(
            "SELECT tenant_id FROM run_tenants WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return str(row["tenant_id"]) if row is not None else "default"

    # ------------------------------------------------------------------
    # Run operations
    # ------------------------------------------------------------------

    def save_run(self, run_record: dict[str, Any]) -> None:
        """Insert or update a run record."""
        conn = self._get_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO runs (
                run_id, trace_id, task_id, status, created_at, completed_at,
                total_tokens, prompt_tokens, completion_tokens,
                actual_cost_usd, latency_ms, model_id,
                required_human_intervention, required_rework,
                human_intervention_reason, rework_reason,
                risk_class, is_local, cache_hit,
                decision_trace, receipt,
                openrouter_request, openrouter_response
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_record.get("run_id"),
                run_record.get("trace_id"),
                run_record.get("task_id"),
                run_record.get("status", "pending"),
                run_record.get("created_at", _now()),
                run_record.get("completed_at"),
                run_record.get("total_tokens", 0),
                run_record.get("prompt_tokens", 0),
                run_record.get("completion_tokens", 0),
                run_record.get("actual_cost_usd", 0.0),
                run_record.get("latency_ms", 0.0),
                run_record.get("model_id"),
                int(run_record.get("required_human_intervention", False)),
                int(run_record.get("required_rework", False)),
                run_record.get("human_intervention_reason"),
                run_record.get("rework_reason"),
                run_record.get("risk_class", "low"),
                int(run_record.get("is_local", False)),
                int(run_record.get("cache_hit", False)),
                json.dumps(run_record.get("decision_trace")) if run_record.get("decision_trace") is not None else None,
                json.dumps(run_record.get("receipt")) if run_record.get("receipt") is not None else None,
                json.dumps(run_record.get("openrouter_request")) if run_record.get("openrouter_request") is not None else None,
                json.dumps(run_record.get("openrouter_response")) if run_record.get("openrouter_response") is not None else None,
            ),
        )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Get a run by ID."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def list_runs(
        self,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List runs with pagination and optional status filter."""
        conn = self._get_conn()
        if status:
            rows = conn.execute(
                "SELECT * FROM runs WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Ledger operations
    # ------------------------------------------------------------------

    def save_ledger_event(self, event: dict[str, Any]) -> None:
        """Save a ledger event."""
        conn = self._get_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO ledger_events (
                event_id, run_id, sequence, timestamp, actor,
                event_type, subject_id, payload,
                previous_event_hash, event_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.get("event_id"),
                event.get("run_id"),
                event.get("sequence", 0),
                event.get("timestamp", _now()),
                json.dumps(event.get("actor", {})),
                event.get("event_type"),
                event.get("subject_id", ""),
                json.dumps(event.get("payload", {})),
                event.get("previous_event_hash", "GENESIS"),
                event.get("event_hash", ""),
            ),
        )

    def get_ledger_events(
        self,
        run_id: str | None = None,
        event_type: str | None = None,
        actor_id: str | None = None,
        from_ts: str | None = None,
        to_ts: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Query ledger events with filtering."""
        conn = self._get_conn()
        conditions: list[str] = []
        params: list[Any] = []

        if run_id:
            conditions.append("run_id = ?")
            params.append(run_id)
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)
        if actor_id:
            conditions.append("actor LIKE ?")
            params.append(f'%"id": "{actor_id}"%')
        if from_ts:
            conditions.append("timestamp >= ?")
            params.append(from_ts)
        if to_ts:
            conditions.append("timestamp <= ?")
            params.append(to_ts)

        where = " AND ".join(conditions) if conditions else "1=1"
        query = f"SELECT * FROM ledger_events WHERE {where} ORDER BY timestamp ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()
        return [self._ledger_row_to_dict(r) for r in rows]

    def get_ledger_chain(self, run_id: str) -> list[dict[str, Any]]:
        """Get the full hash-linked chain for a run."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM ledger_events WHERE run_id = ? ORDER BY sequence ASC",
            (run_id,),
        ).fetchall()
        return [self._ledger_row_to_dict(r) for r in rows]

    def verify_ledger_chain(self, run_id: str) -> dict[str, Any]:
        """Verify the integrity of a run's ledger chain."""
        from epr.ledger import verify_chain

        events = self.get_ledger_chain(run_id)
        valid, message = verify_chain(events)
        return {
            "run_id": run_id,
            "valid": valid,
            "message": message,
            "event_count": len(events),
        }

    # ------------------------------------------------------------------
    # API key operations
    # ------------------------------------------------------------------

    def create_api_key(
        self,
        key_hash: str,
        name: str,
        role: str = "operator",
        rate_limit_rate: float = 10.0,
        rate_limit_burst: int = 20,
        tenant_id: str = "default",
    ) -> str:
        """Create a new API key record. Returns key_id."""
        key_id = f"key-{uuid.uuid4().hex}"
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO api_keys (
                key_id, key_hash, name, role, created_at,
                rate_limit_rate, rate_limit_burst, tenant_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key_id,
                key_hash,
                name,
                role,
                _now(),
                rate_limit_rate,
                rate_limit_burst,
                tenant_id,
            ),
        )
        return key_id

    def get_api_key_by_hash(self, key_hash: str) -> dict[str, Any] | None:
        """Get API key by hash (for authentication)."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM api_keys WHERE key_hash = ? AND revoked_at IS NULL",
            (key_hash,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def list_api_keys(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        """List all API keys (excluding the hash)."""
        conn = self._get_conn()
        if tenant_id:
            rows = conn.execute(
                "SELECT key_id, name, role, created_at, revoked_at, last_used_at, "
                "rate_limit_rate, rate_limit_burst, tenant_id "
                "FROM api_keys WHERE tenant_id = ? ORDER BY created_at DESC",
                (tenant_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT key_id, name, role, created_at, revoked_at, last_used_at, "
                "rate_limit_rate, rate_limit_burst, tenant_id "
                "FROM api_keys ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def revoke_api_key(self, key_id: str) -> bool:
        """Revoke an API key."""
        conn = self._get_conn()
        cursor = conn.execute(
            "UPDATE api_keys SET revoked_at = ? WHERE key_id = ? AND revoked_at IS NULL",
            (_now(), key_id),
        )
        return cursor.rowcount > 0

    def rotate_api_key(
        self, key_id: str, new_key_hash: str
    ) -> dict[str, Any] | None:
        """Revoke one key and insert its replacement in one transaction."""
        conn = self._get_conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            old = conn.execute(
                "SELECT * FROM api_keys WHERE key_id = ? AND revoked_at IS NULL",
                (key_id,),
            ).fetchone()
            if old is None:
                conn.execute("ROLLBACK")
                return None
            now = _now()
            new_key_id = f"key-{uuid.uuid4().hex}"
            conn.execute(
                "UPDATE api_keys SET revoked_at = ? WHERE key_id = ?",
                (now, key_id),
            )
            conn.execute(
                """
                INSERT INTO api_keys (
                    key_id, key_hash, name, role, created_at,
                    rate_limit_rate, rate_limit_burst, tenant_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_key_id,
                    new_key_hash,
                    old["name"],
                    old["role"],
                    now,
                    old["rate_limit_rate"],
                    old["rate_limit_burst"],
                    old["tenant_id"],
                ),
            )
            conn.execute("COMMIT")
        except sqlite3.Error:
            conn.execute("ROLLBACK")
            raise
        return {
            "key_id": new_key_id,
            "name": old["name"],
            "role": old["role"],
            "tenant_id": old["tenant_id"],
        }

    def update_last_used(self, key_id: str) -> None:
        """Update last_used_at timestamp."""
        conn = self._get_conn()
        conn.execute(
            "UPDATE api_keys SET last_used_at = ? WHERE key_id = ?",
            (_now(), key_id),
        )

    # ------------------------------------------------------------------
    # Audit log operations
    # ------------------------------------------------------------------

    def record_audit(
        self,
        actor_id: str,
        action: str,
        resource_type: str | None = None,
        resource_id: str | None = None,
        ip_address: str | None = None,
        details: dict[str, Any] | None = None,
        success: bool = True,
    ) -> str:
        """Record an audit log entry."""
        audit_id = f"audit-{uuid.uuid4().hex}"
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO audit_log (
                audit_id, timestamp, actor_id, action,
                resource_type, resource_id, ip_address,
                details, success
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                _now(),
                actor_id,
                action,
                resource_type,
                resource_id,
                ip_address,
                json.dumps(details) if details else None,
                int(success),
            ),
        )
        return audit_id

    def query_audit_log(
        self,
        actor_id: str | None = None,
        action: str | None = None,
        from_ts: str | None = None,
        to_ts: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Query audit log with filtering."""
        conn = self._get_conn()
        conditions: list[str] = []
        params: list[Any] = []

        if actor_id:
            conditions.append("actor_id = ?")
            params.append(actor_id)
        if action:
            conditions.append("action = ?")
            params.append(action)
        if from_ts:
            conditions.append("timestamp >= ?")
            params.append(from_ts)
        if to_ts:
            conditions.append("timestamp <= ?")
            params.append(to_ts)

        where = " AND ".join(conditions) if conditions else "1=1"
        query = f"SELECT * FROM audit_log WHERE {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Benchmark operations
    # ------------------------------------------------------------------

    def save_benchmark_result(self, result: dict[str, Any]) -> str:
        """Save a benchmark result."""
        result_id = result.get("result_id", f"bm-{uuid.uuid4().hex}")
        conn = self._get_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO benchmark_results (
                result_id, cohort_name, model_id, timestamp,
                accuracy, total_tokens, total_cost_usd,
                mean_latency_ms, p95_latency_ms,
                hir, rr, escalation_rate, results
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result_id,
                result.get("cohort_name", ""),
                result.get("model_id"),
                result.get("timestamp", _now()),
                result.get("accuracy"),
                result.get("total_tokens"),
                result.get("total_cost_usd"),
                result.get("mean_latency_ms"),
                result.get("p95_latency_ms"),
                result.get("hir"),
                result.get("rr"),
                result.get("escalation_rate"),
                json.dumps(result.get("results")) if result.get("results") is not None else None,
            ),
        )
        return result_id

    def get_benchmark_results(
        self,
        cohort_name: str | None = None,
        model_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Query benchmark results."""
        conn = self._get_conn()
        conditions: list[str] = []
        params: list[Any] = []

        if cohort_name:
            conditions.append("cohort_name = ?")
            params.append(cohort_name)
        if model_id:
            conditions.append("model_id = ?")
            params.append(model_id)

        where = " AND ".join(conditions) if conditions else "1=1"
        query = f"SELECT * FROM benchmark_results WHERE {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Config operations
    # ------------------------------------------------------------------

    def get_config(self, key: str) -> Any:
        """Get a config value."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT value FROM config WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        raw = row["value"]
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw

    def set_config(self, key: str, value: Any, updated_by: str = "system") -> None:
        """Set a config value."""
        conn = self._get_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO config (key, value, updated_at, updated_by)
            VALUES (?, ?, ?, ?)
            """,
            (key, json.dumps(value), _now(), updated_by),
        )

    def get_all_config(self) -> dict[str, Any]:
        """Get all config values."""
        conn = self._get_conn()
        rows = conn.execute("SELECT key, value FROM config").fetchall()
        result: dict[str, Any] = {}
        for row in rows:
            raw = row["value"]
            try:
                result[row["key"]] = json.loads(raw) if raw else None
            except (json.JSONDecodeError, TypeError):
                result[row["key"]] = raw
        return result

    # ------------------------------------------------------------------
    # Backup / Restore
    # ------------------------------------------------------------------

    def backup(self, backup_path: str) -> str:
        """Backup the database to a file. Returns backup path."""
        dest = Path(backup_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            src = sqlite3.connect(str(self._db_path))
            dst = sqlite3.connect(str(dest))
            src.backup(dst)
            dst.close()
            src.close()

        self._restrict_file_permissions(dest)
        return str(dest)

    def restore(self, backup_path: str) -> None:
        """Restore the database from a backup file."""
        src_path = Path(backup_path)
        if not src_path.exists():
            raise FileNotFoundError(f"Backup file not found: {backup_path}")

        with self._lock:
            # Close all existing connections
            if hasattr(self._local, "conn") and self._local.conn is not None:
                self._local.conn.close()
                self._local.conn = None

            src = sqlite3.connect(str(src_path))
            dst = sqlite3.connect(str(self._db_path))
            src.backup(dst)
            dst.close()
            src.close()

    def export_json(self, export_path: str) -> str:
        """Export all data as JSON. Returns export path."""
        data: dict[str, Any] = {
            "runs": self.list_runs(limit=100000),
            "ledger_events": self.get_ledger_events(limit=100000),
            "api_keys": self.list_api_keys(),
            "audit_log": self.query_audit_log(limit=100000),
            "benchmark_results": self.get_benchmark_results(limit=100000),
            "config": self.get_all_config(),
            "exported_at": _now(),
        }

        dest = Path(export_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
        self._restrict_file_permissions(dest)
        return str(dest)

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    def get_run_stats(self) -> dict[str, Any]:
        """Get aggregate run statistics."""
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) as cnt FROM runs").fetchone()
        accepted = conn.execute(
            "SELECT COUNT(*) as cnt FROM runs WHERE status = 'accepted'"
        ).fetchone()
        escalated = conn.execute(
            "SELECT COUNT(*) as cnt FROM runs WHERE status = 'escalated'"
        ).fetchone()
        failed = conn.execute(
            "SELECT COUNT(*) as cnt FROM runs WHERE status = 'failed'"
        ).fetchone()
        pending = conn.execute(
            "SELECT COUNT(*) as cnt FROM runs WHERE status = 'pending'"
        ).fetchone()

        hir = conn.execute(
            "SELECT COUNT(*) as cnt FROM runs WHERE required_human_intervention = 1"
        ).fetchone()
        rework = conn.execute(
            "SELECT COUNT(*) as cnt FROM runs WHERE required_rework = 1"
        ).fetchone()

        return {
            "runs_total": total["cnt"] if total else 0,
            "runs_accepted": accepted["cnt"] if accepted else 0,
            "runs_escalated": escalated["cnt"] if escalated else 0,
            "runs_failed": failed["cnt"] if failed else 0,
            "runs_pending": pending["cnt"] if pending else 0,
            "human_intervention_count": hir["cnt"] if hir else 0,
            "rework_count": rework["cnt"] if rework else 0,
        }

    def get_cost_analytics(
        self, from_ts: str | None = None, to_ts: str | None = None
    ) -> dict[str, Any]:
        """Get cost analytics (per-model, per-risk-class, totals)."""
        conn = self._get_conn()
        conditions: list[str] = []
        params: list[Any] = []

        if from_ts:
            conditions.append("created_at >= ?")
            params.append(from_ts)
        if to_ts:
            conditions.append("created_at <= ?")
            params.append(to_ts)

        where = " AND ".join(conditions) if conditions else "1=1"

        # Total cost
        total_row = conn.execute(
            f"SELECT COALESCE(SUM(actual_cost_usd), 0) as total, "
            f"COALESCE(SUM(total_tokens), 0) as tokens FROM runs WHERE {where}",
            params,
        ).fetchone()

        # Per-model
        model_rows = conn.execute(
            f"SELECT model_id, COUNT(*) as cnt, "
            f"COALESCE(SUM(actual_cost_usd), 0) as cost, "
            f"COALESCE(AVG(latency_ms), 0) as avg_latency "
            f"FROM runs WHERE {where} AND model_id IS NOT NULL "
            f"GROUP BY model_id ORDER BY cost DESC",
            params,
        ).fetchall()

        # Per-risk-class
        risk_rows = conn.execute(
            f"SELECT risk_class, COUNT(*) as cnt, "
            f"COALESCE(SUM(actual_cost_usd), 0) as cost "
            f"FROM runs WHERE {where} "
            f"GROUP BY risk_class ORDER BY cost DESC",
            params,
        ).fetchall()

        return {
            "total_cost_usd": total_row["total"] if total_row else 0.0,
            "total_tokens": total_row["tokens"] if total_row else 0,
            "per_model": [dict(r) for r in model_rows],
            "per_risk_class": [dict(r) for r in risk_rows],
        }

    def get_model_performance(
        self, model_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Get model performance analytics."""
        conn = self._get_conn()
        if model_id:
            rows = conn.execute(
                "SELECT model_id, COUNT(*) as total_runs, "
                "COALESCE(AVG(latency_ms), 0) as avg_latency_ms, "
                "COALESCE(AVG(actual_cost_usd), 0) as avg_cost_usd, "
                "COALESCE(AVG(total_tokens), 0) as avg_tokens, "
                "SUM(CASE WHEN required_human_intervention = 1 THEN 1 ELSE 0 END) as hir_count, "
                "SUM(CASE WHEN required_rework = 1 THEN 1 ELSE 0 END) as rework_count "
                "FROM runs WHERE model_id = ? "
                "GROUP BY model_id",
                (model_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT model_id, COUNT(*) as total_runs, "
                "COALESCE(AVG(latency_ms), 0) as avg_latency_ms, "
                "COALESCE(AVG(actual_cost_usd), 0) as avg_cost_usd, "
                "COALESCE(AVG(total_tokens), 0) as avg_tokens, "
                "SUM(CASE WHEN required_human_intervention = 1 THEN 1 ELSE 0 END) as hir_count, "
                "SUM(CASE WHEN required_rework = 1 THEN 1 ELSE 0 END) as rework_count "
                "FROM runs WHERE model_id IS NOT NULL "
                "GROUP BY model_id ORDER BY total_runs DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        """Convert a runs row to a dict, deserializing JSON fields."""
        d = dict(row)
        for field in ("decision_trace", "receipt", "openrouter_request", "openrouter_response"):
            if d.get(field):
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        return d

    def _ledger_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        """Convert a ledger_events row to a dict, deserializing JSON fields."""
        d = dict(row)
        for field in ("actor", "payload"):
            if d.get(field):
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        return d
