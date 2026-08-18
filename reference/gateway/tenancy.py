"""Multi-tenant isolation for runs, API keys, cost tracking, and policies.

Each tenant (project/team/department) has:
- Isolated API keys
- Isolated run history and cost tracking
- Isolated benchmark results
- Optional per-tenant routing policy overrides
- Per-tenant cost budgets and alerts
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ensure_schema(db: Any) -> None:
    """Ensure tenant-related tables exist."""
    conn = db._get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tenants (
            tenant_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            budget_daily_usd REAL DEFAULT 10.0,
            budget_monthly_usd REAL DEFAULT 300.0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tenant_spend (
            spend_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            amount_usd REAL NOT NULL,
            timestamp TEXT NOT NULL,
            run_id TEXT,
            FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
        );

        CREATE INDEX IF NOT EXISTS idx_tenant_spend_tenant ON tenant_spend(tenant_id);
        CREATE INDEX IF NOT EXISTS idx_tenant_spend_ts ON tenant_spend(timestamp);
    """)


class TenantManager:
    """Multi-tenant isolation for runs, API keys, cost tracking, and policies."""

    def __init__(self, db: Any) -> None:
        self._db = db
        self._tenants: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        _ensure_schema(db)
        self._load_tenants()

    def _load_tenants(self) -> None:
        """Load all tenants from the database into memory."""
        conn = self._db._get_conn()
        rows = conn.execute("SELECT * FROM tenants").fetchall()
        with self._lock:
            self._tenants = {row["tenant_id"]: dict(row) for row in rows}

    def create_tenant(
        self,
        tenant_id: str,
        name: str,
        budget_daily_usd: float = 10.0,
        budget_monthly_usd: float = 300.0,
    ) -> dict[str, Any]:
        """Create a new tenant."""
        now = _now()
        conn = self._db._get_conn()
        conn.execute(
            """INSERT INTO tenants (tenant_id, name, budget_daily_usd, budget_monthly_usd, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (tenant_id, name, budget_daily_usd, budget_monthly_usd, now, now),
        )
        with self._lock:
            self._tenants[tenant_id] = {
                "tenant_id": tenant_id,
                "name": name,
                "budget_daily_usd": budget_daily_usd,
                "budget_monthly_usd": budget_monthly_usd,
                "created_at": now,
                "updated_at": now,
            }
        return self._tenants[tenant_id]

    def get_tenant(self, tenant_id: str) -> dict[str, Any] | None:
        """Get tenant info."""
        with self._lock:
            return self._tenants.get(tenant_id)

    def list_tenants(self) -> list[dict[str, Any]]:
        """List all tenants."""
        with self._lock:
            return list(self._tenants.values())

    def update_tenant(self, tenant_id: str, **updates: Any) -> dict[str, Any] | None:
        """Update tenant config (budgets, name, etc.)."""
        with self._lock:
            if tenant_id not in self._tenants:
                return None
            tenant = self._tenants[tenant_id]
            allowed = {"name", "budget_daily_usd", "budget_monthly_usd"}
            for key, value in updates.items():
                if key in allowed:
                    tenant[key] = value
            tenant["updated_at"] = _now()

        conn = self._db._get_conn()
        conn.execute(
            """UPDATE tenants SET name=?, budget_daily_usd=?, budget_monthly_usd=?, updated_at=?
               WHERE tenant_id=?""",
            (tenant["name"], tenant["budget_daily_usd"], tenant["budget_monthly_usd"],
             tenant["updated_at"], tenant_id),
        )
        return tenant

    def delete_tenant(self, tenant_id: str) -> bool:
        """Delete a tenant and all associated data."""
        conn = self._db._get_conn()
        conn.execute("DELETE FROM tenant_spend WHERE tenant_id = ?", (tenant_id,))
        conn.execute("DELETE FROM tenants WHERE tenant_id = ?", (tenant_id,))
        with self._lock:
            return self._tenants.pop(tenant_id, None) is not None

    def check_budget(self, tenant_id: str) -> dict[str, Any]:
        """Check if tenant is within budget.

        Returns {within_budget, daily_spend, monthly_spend, daily_budget,
                 monthly_budget, remaining_daily, remaining_monthly}
        """
        with self._lock:
            tenant = self._tenants.get(tenant_id)
        if tenant is None:
            return {
                "within_budget": True,
                "daily_spend": 0.0,
                "monthly_spend": 0.0,
                "daily_budget": 0.0,
                "monthly_budget": 0.0,
                "remaining_daily": 0.0,
                "remaining_monthly": 0.0,
            }

        # Get today's spend
        today = _now()[:10]  # YYYY-MM-DD
        conn = self._db._get_conn()
        daily = conn.execute(
            "SELECT COALESCE(SUM(amount_usd), 0) as total FROM tenant_spend "
            "WHERE tenant_id = ? AND timestamp >= ?",
            (tenant_id, today + "T00:00:00Z"),
        ).fetchone()
        daily_spend = float(daily["total"]) if daily else 0.0

        # Get this month's spend
        month_start = today[:7] + "-01T00:00:00Z"
        monthly = conn.execute(
            "SELECT COALESCE(SUM(amount_usd), 0) as total FROM tenant_spend "
            "WHERE tenant_id = ? AND timestamp >= ?",
            (tenant_id, month_start),
        ).fetchone()
        monthly_spend = float(monthly["total"]) if monthly else 0.0

        daily_budget = float(tenant.get("budget_daily_usd", 10.0))
        monthly_budget = float(tenant.get("budget_monthly_usd", 300.0))

        return {
            "within_budget": daily_spend <= daily_budget and monthly_spend <= monthly_budget,
            "daily_spend": daily_spend,
            "monthly_spend": monthly_spend,
            "daily_budget": daily_budget,
            "monthly_budget": monthly_budget,
            "remaining_daily": max(0.0, daily_budget - daily_spend),
            "remaining_monthly": max(0.0, monthly_budget - monthly_spend),
        }

    def record_spend(self, tenant_id: str, amount_usd: float, run_id: str | None = None) -> None:
        """Record a spend for a tenant."""
        spend_id = f"spend-{uuid.uuid4().hex}"
        conn = self._db._get_conn()
        conn.execute(
            "INSERT INTO tenant_spend (spend_id, tenant_id, amount_usd, timestamp, run_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (spend_id, tenant_id, amount_usd, _now(), run_id),
        )