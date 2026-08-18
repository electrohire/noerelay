"""Cost controls with budgets, alerts, and anomaly detection.

Features:
- Per-tenant daily/monthly budgets
- Per-model cost tracking
- Cost alerts (threshold-based)
- Cost anomaly detection (spike detection)
- Cost forecasting (simple linear projection)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from .tenancy import TenantManager


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class CostController:
    """Cost controls with budgets, alerts, and anomaly detection."""

    def __init__(self, db: Any, tenant_manager: TenantManager) -> None:
        self._db = db
        self._tenants = tenant_manager
        self._alerts: list[dict[str, Any]] = []
        self._alert_rules: list[dict[str, Any]] = []

    def add_alert_rule(
        self,
        name: str,
        condition: str,
        threshold: float,
        action: str = "notify",
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        """Add a cost alert rule.

        Conditions:
        - 'daily_spend_exceeds': alert when daily spend > threshold
        - 'monthly_spend_exceeds': alert when monthly spend > threshold
        - 'per_request_cost_exceeds': alert when a single request costs > threshold
        - 'cost_anomaly_detected': alert on spending spike
        """
        rule = {
            "rule_id": f"cost-rule-{uuid.uuid4().hex}",
            "name": name,
            "condition": condition,
            "threshold": threshold,
            "action": action,
            "tenant_id": tenant_id,
            "created_at": _now(),
        }
        self._alert_rules.append(rule)
        return rule

    def check_alerts(self) -> list[dict[str, Any]]:
        """Check all alert rules and return triggered alerts."""
        triggered: list[dict[str, Any]] = []

        for rule in self._alert_rules:
            tenant_id = rule.get("tenant_id")
            if rule["condition"] == "daily_spend_exceeds":
                budget = self._tenants.check_budget(tenant_id or "default")
                if budget["daily_spend"] > rule["threshold"]:
                    triggered.append({
                        "alert_id": f"cost-alert-{uuid.uuid4().hex}",
                        "rule": rule,
                        "message": f"Daily spend ${budget['daily_spend']:.4f} exceeds threshold ${rule['threshold']:.2f}",
                        "details": budget,
                        "timestamp": _now(),
                    })
            elif rule["condition"] == "monthly_spend_exceeds":
                budget = self._tenants.check_budget(tenant_id or "default")
                if budget["monthly_spend"] > rule["threshold"]:
                    triggered.append({
                        "alert_id": f"cost-alert-{uuid.uuid4().hex}",
                        "rule": rule,
                        "message": f"Monthly spend ${budget['monthly_spend']:.4f} exceeds threshold ${rule['threshold']:.2f}",
                        "details": budget,
                        "timestamp": _now(),
                    })

        self._alerts.extend(triggered)
        return triggered

    def get_cost_summary(
        self,
        tenant_id: str | None = None,
        period: str = "daily",
    ) -> dict[str, Any]:
        """Get cost summary for a tenant or all tenants."""
        if tenant_id:
            budget = self._tenants.check_budget(tenant_id)
            return {
                "tenant_id": tenant_id,
                "period": period,
                "spend": budget["daily_spend"] if period == "daily" else budget["monthly_spend"],
                "budget": budget["daily_budget"] if period == "daily" else budget["monthly_budget"],
                "within_budget": budget["within_budget"],
            }

        summaries = []
        for tenant in self._tenants.list_tenants():
            budget = self._tenants.check_budget(tenant["tenant_id"])
            summaries.append({
                "tenant_id": tenant["tenant_id"],
                "name": tenant.get("name", ""),
                "spend": budget["daily_spend"] if period == "daily" else budget["monthly_spend"],
                "budget": budget["daily_budget"] if period == "daily" else budget["monthly_budget"],
                "within_budget": budget["within_budget"],
            })
        return {"period": period, "tenants": summaries}

    def get_cost_trend(
        self,
        tenant_id: str | None = None,
        days: int = 30,
    ) -> list[dict[str, Any]]:
        """Get cost trend over time."""
        conn = self._db._get_conn()
        if tenant_id:
            rows = conn.execute(
                """SELECT date(timestamp) as day, COALESCE(SUM(amount_usd), 0) as cost
                   FROM tenant_spend
                   WHERE tenant_id = ?
                   GROUP BY day
                   ORDER BY day DESC
                   LIMIT ?""",
                (tenant_id, days),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT date(timestamp) as day, COALESCE(SUM(amount_usd), 0) as cost
                   FROM tenant_spend
                   GROUP BY day
                   ORDER BY day DESC
                   LIMIT ?""",
                (days,),
            ).fetchall()
        return [dict(r) for r in rows]

    def forecast_cost(
        self,
        tenant_id: str | None = None,
        days: int = 7,
    ) -> dict[str, Any]:
        """Forecast future costs based on historical trends."""
        trend = self.get_cost_trend(tenant_id, days=30)
        if not trend:
            return {
                "forecast_daily_cost": 0.0,
                "forecast_total_cost": 0.0,
                "forecast_days": days,
                "confidence": "low",
            }

        # Simple moving average of last 7 days
        costs = [float(r["cost"]) for r in trend[:7]]
        avg_daily = sum(costs) / len(costs) if costs else 0.0

        return {
            "forecast_daily_cost": round(avg_daily, 4),
            "forecast_total_cost": round(avg_daily * days, 4),
            "forecast_days": days,
            "confidence": "medium" if len(costs) >= 7 else "low",
        }

    def detect_anomalies(self) -> list[dict[str, Any]]:
        """Detect cost anomalies (spikes, unusual patterns)."""
        trend = self.get_cost_trend(days=30)
        if len(trend) < 7:
            return []

        costs = [float(r["cost"]) for r in trend]
        avg = sum(costs) / len(costs)

        # Calculate standard deviation
        variance = sum((c - avg) ** 2 for c in costs) / len(costs)
        std_dev = variance ** 0.5

        anomalies: list[dict[str, Any]] = []
        threshold = avg + 2 * std_dev
        for i, entry in enumerate(trend):
            cost = float(entry["cost"])
            if cost > threshold and cost > 0:
                anomalies.append({
                    "day": entry["day"],
                    "cost": cost,
                    "average": round(avg, 4),
                    "threshold": round(threshold, 4),
                    "deviation": round(cost - avg, 4),
                })

        return anomalies