"""Comprehensive analytics engine for NoeRelay.

Provides analytics for:
- Cost: per-model, per-tenant, per-risk-class, time trends, forecasting
- Model performance: accuracy, latency, tokens, cost trends over time
- Usage: request volume, token consumption, peak usage, user-agent breakdown
- Escalations: HIR, RR, escalation triggers, model failure patterns
- Audit: access patterns, unusual activity, compliance reports
- Benchmarks: historical results, model comparison, regression detection

All analytics are computed from the database; the engine is dependency-free
and uses only the stdlib.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _days_ago(n: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=n)
    return dt.isoformat().replace("+00:00", "Z")


def _hours_ago(n: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=n)
    return dt.isoformat().replace("+00:00", "Z")


class AnalyticsEngine:
    """Comprehensive analytics engine for NoeRelay.

    Provides analytics for:
    - Cost: per-model, per-tenant, per-risk-class, time trends, forecasting
    - Model performance: accuracy, latency, tokens, cost trends over time
    - Usage: request volume, token consumption, peak usage, user-agent breakdown
    - Escalations: HIR, RR, escalation triggers, model failure patterns
    - Audit: access patterns, unusual activity, compliance reports
    - Benchmarks: historical results, model comparison, regression detection
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        """Execute a query and return results as list of dicts."""
        conn = self._db._get_conn()
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def _query_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        """Execute a query and return the first row as dict."""
        conn = self._db._get_conn()
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row is not None else None

    def _build_where(
        self,
        from_ts: str | None = None,
        to_ts: str | None = None,
        extra: list[tuple[str, Any]] | None = None,
    ) -> tuple[str, list[Any]]:
        """Build a WHERE clause from timestamp filters."""
        conditions: list[str] = []
        params: list[Any] = []
        if from_ts:
            conditions.append("created_at >= ?")
            params.append(from_ts)
        if to_ts:
            conditions.append("created_at <= ?")
            params.append(to_ts)
        if extra:
            for cond, val in extra:
                conditions.append(cond)
                params.append(val)
        where = " AND ".join(conditions) if conditions else "1=1"
        return where, params

    # ------------------------------------------------------------------
    # Cost Analytics
    # ------------------------------------------------------------------

    def cost_summary(
        self,
        from_ts: str | None = None,
        to_ts: str | None = None,
        tenant_id: str | None = None,
        group_by: str = "model",
    ) -> dict[str, Any]:
        """Get cost summary grouped by model/tenant/risk-class/day."""
        where, params = self._build_where(from_ts, to_ts)

        # Total cost
        total = self._query_one(
            f"SELECT COALESCE(SUM(actual_cost_usd), 0) AS total_cost, "
            f"COALESCE(SUM(total_tokens), 0) AS total_tokens, "
            f"COUNT(*) AS total_runs "
            f"FROM runs WHERE {where}",
            tuple(params),
        )

        # Grouped breakdown
        group_col = "model_id"
        if group_by == "risk_class":
            group_col = "risk_class"
        elif group_by == "day":
            group_col = "date(created_at)"

        grouped = self._query(
            f"SELECT {group_col} AS group_key, "
            f"COUNT(*) AS run_count, "
            f"COALESCE(SUM(actual_cost_usd), 0) AS total_cost, "
            f"COALESCE(AVG(actual_cost_usd), 0) AS avg_cost, "
            f"COALESCE(SUM(total_tokens), 0) AS total_tokens "
            f"FROM runs WHERE {where} AND {group_col} IS NOT NULL "
            f"GROUP BY {group_col} ORDER BY total_cost DESC",
            tuple(params),
        )

        return {
            "total_cost_usd": total["total_cost"] if total else 0.0,
            "total_tokens": total["total_tokens"] if total else 0,
            "total_runs": total["total_runs"] if total else 0,
            "group_by": group_by,
            "groups": grouped,
        }

    def cost_trend(
        self, days: int = 30, tenant_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Get daily cost trend over time."""
        from_ts = _days_ago(days)
        return self._query(
            "SELECT date(created_at) AS day, "
            "COUNT(*) AS run_count, "
            "COALESCE(SUM(actual_cost_usd), 0) AS total_cost, "
            "COALESCE(AVG(actual_cost_usd), 0) AS avg_cost "
            "FROM runs WHERE created_at >= ? "
            "GROUP BY day ORDER BY day ASC",
            (from_ts,),
        )

    def cost_breakdown(
        self, from_ts: str | None = None, to_ts: str | None = None
    ) -> dict[str, Any]:
        """Get cost breakdown by component (direct, rework, human, escalation, latency, infrastructure)."""
        where, params = self._build_where(from_ts, to_ts)

        total = self._query_one(
            f"SELECT COALESCE(SUM(actual_cost_usd), 0) AS total_cost, "
            f"COUNT(*) AS total_runs "
            f"FROM runs WHERE {where}",
            tuple(params),
        )

        # Direct cost (accepted runs)
        direct = self._query_one(
            f"SELECT COALESCE(SUM(actual_cost_usd), 0) AS direct_cost "
            f"FROM runs WHERE {where} AND status = 'accepted'",
            tuple(params),
        )

        # Rework cost (runs with rework)
        rework = self._query_one(
            f"SELECT COALESCE(SUM(actual_cost_usd), 0) AS rework_cost, "
            f"COUNT(*) AS rework_count "
            f"FROM runs WHERE {where} AND required_rework = 1",
            tuple(params),
        )

        # Human intervention cost
        human = self._query_one(
            f"SELECT COALESCE(SUM(actual_cost_usd), 0) AS human_cost, "
            f"COUNT(*) AS human_count "
            f"FROM runs WHERE {where} AND required_human_intervention = 1",
            tuple(params),
        )

        # Escalation cost
        escalation = self._query_one(
            f"SELECT COALESCE(SUM(actual_cost_usd), 0) AS escalation_cost, "
            f"COUNT(*) AS escalation_count "
            f"FROM runs WHERE {where} AND status = 'escalated'",
            tuple(params),
        )

        total_cost = total["total_cost"] if total else 0.0
        total_runs = total["total_runs"] if total else 0

        return {
            "total_cost_usd": total_cost,
            "total_runs": total_runs,
            "direct_cost_usd": direct["direct_cost"] if direct else 0.0,
            "rework_cost_usd": rework["rework_cost"] if rework else 0.0,
            "rework_count": rework["rework_count"] if rework else 0,
            "human_intervention_cost_usd": human["human_cost"] if human else 0.0,
            "human_intervention_count": human["human_count"] if human else 0,
            "escalation_cost_usd": escalation["escalation_cost"] if escalation else 0.0,
            "escalation_count": escalation["escalation_count"] if escalation else 0,
        }

    def cost_forecast(
        self, days: int = 7, tenant_id: str | None = None
    ) -> dict[str, Any]:
        """Forecast future costs based on historical trends using simple moving average."""
        # Get last 30 days of daily costs
        from_ts = _days_ago(30)
        trend = self._query(
            "SELECT date(created_at) AS day, "
            "COALESCE(SUM(actual_cost_usd), 0) AS total_cost "
            "FROM runs WHERE created_at >= ? "
            "GROUP BY day ORDER BY day ASC",
            (from_ts,),
        )

        if not trend:
            return {
                "forecast_days": days,
                "forecast_total_cost_usd": 0.0,
                "forecast_daily_costs": [0.0] * days,
                "confidence": "low",
                "method": "moving_average",
            }

        costs = [r["total_cost"] for r in trend]
        avg_daily = sum(costs) / len(costs) if costs else 0.0

        # Simple moving average forecast
        if len(costs) >= 7:
            avg_daily = sum(costs[-7:]) / 7

        # Calculate trend direction
        if len(costs) >= 14:
            first_half = sum(costs[: len(costs) // 2]) / (len(costs) // 2)
            second_half = sum(costs[len(costs) // 2 :]) / (len(costs) - len(costs) // 2)
            if second_half > first_half * 1.1:
                trend_direction = "increasing"
            elif second_half < first_half * 0.9:
                trend_direction = "decreasing"
            else:
                trend_direction = "stable"
        else:
            trend_direction = "stable"

        # Simple linear trend for forecasting
        forecast_daily = []
        if len(costs) >= 7:
            recent = costs[-7:]
            # Linear regression slope
            n = len(recent)
            x_mean = (n - 1) / 2
            y_mean = sum(recent) / n
            num = sum((i - x_mean) * (recent[i] - y_mean) for i in range(n))
            den = sum((i - x_mean) ** 2 for i in range(n))
            slope = num / den if den != 0 else 0.0
            for i in range(days):
                forecast_daily.append(round(max(0.0, y_mean + slope * (n + i - x_mean)), 6))
        else:
            forecast_daily = [round(avg_daily, 6)] * days

        return {
            "forecast_days": days,
            "forecast_total_cost_usd": round(sum(forecast_daily), 6),
            "forecast_daily_costs": forecast_daily,
            "avg_daily_cost_usd": round(avg_daily, 6),
            "trend_direction": trend_direction,
            "confidence": "medium" if len(costs) >= 14 else "low",
            "method": "linear_regression",
        }

    def cost_anomalies(self, window_days: int = 7) -> list[dict[str, Any]]:
        """Detect cost anomalies (spikes, unusual patterns)."""
        from_ts = _days_ago(window_days * 4)
        trend = self._query(
            "SELECT date(created_at) AS day, "
            "COALESCE(SUM(actual_cost_usd), 0) AS total_cost, "
            "COUNT(*) AS run_count "
            "FROM runs WHERE created_at >= ? "
            "GROUP BY day ORDER BY day ASC",
            (from_ts,),
        )

        if len(trend) < window_days * 2:
            return []

        costs = [r["total_cost"] for r in trend]
        # Calculate rolling mean and std
        anomalies = []
        for i in range(window_days, len(costs)):
            window = costs[i - window_days : i]
            mean = sum(window) / len(window) if window else 0.0
            std = (
                (sum((x - mean) ** 2 for x in window) / len(window)) ** 0.5
                if window
                else 0.0
            )
            threshold = mean + 2.0 * std
            if costs[i] > threshold and threshold > 0:
                anomalies.append(
                    {
                        "day": trend[i]["day"],
                        "cost_usd": costs[i],
                        "expected_cost_usd": round(mean, 6),
                        "threshold_usd": round(threshold, 6),
                        "deviation_pct": (
                            round((costs[i] - mean) / mean * 100, 1) if mean > 0 else 0.0
                        ),
                        "run_count": trend[i]["run_count"],
                    }
                )

        return anomalies

    # ------------------------------------------------------------------
    # Model Performance Analytics
    # ------------------------------------------------------------------

    def model_performance_summary(
        self, model_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Get performance summary for all models or a specific model."""
        if model_id:
            rows = self._query(
                "SELECT model_id, "
                "COUNT(*) AS total_runs, "
                "COALESCE(AVG(latency_ms), 0) AS avg_latency_ms, "
                "COALESCE(MIN(latency_ms), 0) AS min_latency_ms, "
                "COALESCE(MAX(latency_ms), 0) AS max_latency_ms, "
                "COALESCE(AVG(actual_cost_usd), 0) AS avg_cost_usd, "
                "COALESCE(AVG(total_tokens), 0) AS avg_tokens, "
                "COALESCE(SUM(total_tokens), 0) AS total_tokens, "
                "COALESCE(SUM(actual_cost_usd), 0) AS total_cost_usd, "
                "SUM(CASE WHEN required_human_intervention = 1 THEN 1 ELSE 0 END) AS hir_count, "
                "SUM(CASE WHEN required_rework = 1 THEN 1 ELSE 0 END) AS rework_count, "
                "SUM(CASE WHEN status = 'accepted' THEN 1 ELSE 0 END) AS accepted_count, "
                "SUM(CASE WHEN status = 'escalated' THEN 1 ELSE 0 END) AS escalated_count "
                "FROM runs WHERE model_id = ? "
                "GROUP BY model_id",
                (model_id,),
            )
        else:
            rows = self._query(
                "SELECT model_id, "
                "COUNT(*) AS total_runs, "
                "COALESCE(AVG(latency_ms), 0) AS avg_latency_ms, "
                "COALESCE(MIN(latency_ms), 0) AS min_latency_ms, "
                "COALESCE(MAX(latency_ms), 0) AS max_latency_ms, "
                "COALESCE(AVG(actual_cost_usd), 0) AS avg_cost_usd, "
                "COALESCE(AVG(total_tokens), 0) AS avg_tokens, "
                "COALESCE(SUM(total_tokens), 0) AS total_tokens, "
                "COALESCE(SUM(actual_cost_usd), 0) AS total_cost_usd, "
                "SUM(CASE WHEN required_human_intervention = 1 THEN 1 ELSE 0 END) AS hir_count, "
                "SUM(CASE WHEN required_rework = 1 THEN 1 ELSE 0 END) AS rework_count, "
                "SUM(CASE WHEN status = 'accepted' THEN 1 ELSE 0 END) AS accepted_count, "
                "SUM(CASE WHEN status = 'escalated' THEN 1 ELSE 0 END) AS escalated_count "
                "FROM runs WHERE model_id IS NOT NULL "
                "GROUP BY model_id ORDER BY total_runs DESC"
            )

        results = []
        for r in rows:
            total = r["total_runs"]
            entry = dict(r)
            entry["accuracy"] = round((r["accepted_count"] / total * 100), 1) if total > 0 else 0.0
            entry["hir_rate"] = round((r["hir_count"] / total * 100), 1) if total > 0 else 0.0
            entry["rework_rate"] = round((r["rework_count"] / total * 100), 1) if total > 0 else 0.0
            entry["escalation_rate"] = round((r["escalated_count"] / total * 100), 1) if total > 0 else 0.0
            entry["tokens_per_correct"] = (
                round(r["avg_tokens"] / (entry["accuracy"] / 100), 1)
                if entry["accuracy"] > 0 else 0.0
            )
            entry["true_cost_per_correct"] = (
                round(r["avg_cost_usd"] / (entry["accuracy"] / 100), 6)
                if entry["accuracy"] > 0 else 0.0
            )
            results.append(entry)

        return results

    def model_performance_trend(
        self, model_id: str, days: int = 30
    ) -> list[dict[str, Any]]:
        """Get performance trend for a model over time."""
        from_ts = _days_ago(days)
        return self._query(
            "SELECT date(created_at) AS day, "
            "COUNT(*) AS run_count, "
            "COALESCE(AVG(latency_ms), 0) AS avg_latency_ms, "
            "COALESCE(AVG(total_tokens), 0) AS avg_tokens, "
            "COALESCE(AVG(actual_cost_usd), 0) AS avg_cost_usd, "
            "SUM(CASE WHEN status = 'accepted' THEN 1 ELSE 0 END) AS accepted_count, "
            "SUM(CASE WHEN required_human_intervention = 1 THEN 1 ELSE 0 END) AS hir_count "
            "FROM runs WHERE model_id = ? AND created_at >= ? "
            "GROUP BY day ORDER BY day ASC",
            (model_id, from_ts),
        )

    def model_comparison(self, model_ids: list[str] | None = None) -> dict[str, Any]:
        """Compare models on key metrics."""
        if model_ids:
            placeholders = ",".join("?" * len(model_ids))
            rows = self._query(
                f"SELECT model_id, "
                f"COUNT(*) AS total_runs, "
                f"COALESCE(AVG(latency_ms), 0) AS avg_latency_ms, "
                f"COALESCE(AVG(actual_cost_usd), 0) AS avg_cost_usd, "
                f"COALESCE(AVG(total_tokens), 0) AS avg_tokens, "
                f"SUM(CASE WHEN status = 'accepted' THEN 1 ELSE 0 END) AS accepted_count, "
                f"SUM(CASE WHEN required_human_intervention = 1 THEN 1 ELSE 0 END) AS hir_count, "
                f"SUM(CASE WHEN required_rework = 1 THEN 1 ELSE 0 END) AS rework_count "
                f"FROM runs WHERE model_id IN ({placeholders}) "
                f"GROUP BY model_id ORDER BY total_runs DESC",
                tuple(model_ids),
            )
        else:
            rows = self._query(
                "SELECT model_id, "
                "COUNT(*) AS total_runs, "
                "COALESCE(AVG(latency_ms), 0) AS avg_latency_ms, "
                "COALESCE(AVG(actual_cost_usd), 0) AS avg_cost_usd, "
                "COALESCE(AVG(total_tokens), 0) AS avg_tokens, "
                "SUM(CASE WHEN status = 'accepted' THEN 1 ELSE 0 END) AS accepted_count, "
                "SUM(CASE WHEN required_human_intervention = 1 THEN 1 ELSE 0 END) AS hir_count, "
                "SUM(CASE WHEN required_rework = 1 THEN 1 ELSE 0 END) AS rework_count "
                "FROM runs WHERE model_id IS NOT NULL "
                "GROUP BY model_id ORDER BY total_runs DESC"
            )

        results = []
        for r in rows:
            total = r["total_runs"]
            entry = dict(r)
            entry["accuracy"] = round((r["accepted_count"] / total * 100), 1) if total > 0 else 0.0
            entry["hir_rate"] = round((r["hir_count"] / total * 100), 1) if total > 0 else 0.0
            entry["rework_rate"] = round((r["rework_count"] / total * 100), 1) if total > 0 else 0.0
            results.append(entry)

        return {"models": results, "compared_count": len(results)}

    def model_ranking(self, metric: str = "true_cost") -> list[dict[str, Any]]:
        """Rank models by a specific metric."""
        perf = self.model_performance_summary()

        reverse = metric in ("total_runs", "accuracy", "accepted_count")
        if metric == "true_cost":
            key = "true_cost_per_correct"
            reverse = False
        elif metric == "tokens":
            key = "tokens_per_correct"
            reverse = False
        elif metric == "latency":
            key = "avg_latency_ms"
            reverse = False
        elif metric == "hir":
            key = "hir_rate"
            reverse = False
        elif metric == "rework":
            key = "rework_rate"
            reverse = False
        else:
            key = metric
            reverse = True

        sorted_perf = sorted(perf, key=lambda x: x.get(key, 0) or 0, reverse=reverse)

        result = []
        for rank, entry in enumerate(sorted_perf, 1):
            result.append(
                {
                    "rank": rank,
                    "model_id": entry["model_id"],
                    "total_runs": entry["total_runs"],
                    "accuracy": entry["accuracy"],
                    "avg_cost_usd": entry["avg_cost_usd"],
                    "avg_latency_ms": entry["avg_latency_ms"],
                    "avg_tokens": entry["avg_tokens"],
                    "tokens_per_correct": entry["tokens_per_correct"],
                    "true_cost_per_correct": entry["true_cost_per_correct"],
                    "hir_rate": entry["hir_rate"],
                    "rework_rate": entry["rework_rate"],
                }
            )
        return result

    # ------------------------------------------------------------------
    # Usage Analytics
    # ------------------------------------------------------------------

    def usage_summary(
        self, from_ts: str | None = None, to_ts: str | None = None
    ) -> dict[str, Any]:
        """Get usage summary (total requests, tokens, cost, latency)."""
        where, params = self._build_where(from_ts, to_ts)

        row = self._query_one(
            f"SELECT COUNT(*) AS total_runs, "
            f"COALESCE(SUM(total_tokens), 0) AS total_tokens, "
            f"COALESCE(SUM(prompt_tokens), 0) AS total_prompt_tokens, "
            f"COALESCE(SUM(completion_tokens), 0) AS total_completion_tokens, "
            f"COALESCE(SUM(actual_cost_usd), 0) AS total_cost_usd, "
            f"COALESCE(AVG(latency_ms), 0) AS avg_latency_ms, "
            f"COALESCE(AVG(total_tokens), 0) AS avg_tokens_per_run, "
            f"SUM(CASE WHEN cache_hit = 1 THEN 1 ELSE 0 END) AS cache_hits, "
            f"SUM(CASE WHEN is_local = 1 THEN 1 ELSE 0 END) AS local_runs "
            f"FROM runs WHERE {where}",
            tuple(params),
        )

        if row is None:
            return {
                "total_runs": 0,
                "total_tokens": 0,
                "total_cost_usd": 0.0,
                "avg_latency_ms": 0.0,
            }

        result = dict(row)
        # Per-status breakdown
        status_rows = self._query(
            f"SELECT status, COUNT(*) AS count "
            f"FROM runs WHERE {where} GROUP BY status",
            tuple(params),
        )
        result["by_status"] = {r["status"]: r["count"] for r in status_rows}
        return result

    def usage_trend(
        self, days: int = 30, granularity: str = "daily"
    ) -> list[dict[str, Any]]:
        """Get usage trend over time."""
        from_ts = _days_ago(days)
        if granularity == "hourly":
            date_expr = "strftime('%Y-%m-%dT%H:00:00', created_at)"
        else:
            date_expr = "date(created_at)"

        return self._query(
            f"SELECT {date_expr} AS period, "
            f"COUNT(*) AS run_count, "
            f"COALESCE(SUM(total_tokens), 0) AS total_tokens, "
            f"COALESCE(SUM(actual_cost_usd), 0) AS total_cost_usd, "
            f"COALESCE(AVG(latency_ms), 0) AS avg_latency_ms "
            f"FROM runs WHERE created_at >= ? "
            f"GROUP BY period ORDER BY period ASC",
            (from_ts,),
        )

    def peak_usage(self, days: int = 7) -> dict[str, Any]:
        """Identify peak usage periods."""
        from_ts = _days_ago(days)
        rows = self._query(
            "SELECT strftime('%Y-%m-%dT%H:00:00', created_at) AS hour, "
            "COUNT(*) AS run_count, "
            "COALESCE(SUM(total_tokens), 0) AS total_tokens, "
            "COALESCE(SUM(actual_cost_usd), 0) AS total_cost_usd "
            "FROM runs WHERE created_at >= ? "
            "GROUP BY hour ORDER BY run_count DESC LIMIT 10",
            (from_ts,),
        )

        if not rows:
            return {"peak_hours": [], "avg_hourly_runs": 0.0, "max_hourly_runs": 0}

        all_hours = self._query(
            "SELECT strftime('%Y-%m-%dT%H:00:00', created_at) AS hour, "
            "COUNT(*) AS run_count "
            "FROM runs WHERE created_at >= ? "
            "GROUP BY hour",
            (from_ts,),
        )

        avg = sum(r["run_count"] for r in all_hours) / len(all_hours) if all_hours else 0.0

        return {
            "peak_hours": rows,
            "avg_hourly_runs": round(avg, 1),
            "max_hourly_runs": rows[0]["run_count"] if rows else 0,
            "total_hours_analyzed": len(all_hours),
        }

    def usage_by_model(
        self, from_ts: str | None = None, to_ts: str | None = None
    ) -> list[dict[str, Any]]:
        """Get usage breakdown by model."""
        where, params = self._build_where(from_ts, to_ts)
        return self._query(
            f"SELECT model_id, "
            f"COUNT(*) AS run_count, "
            f"COALESCE(SUM(total_tokens), 0) AS total_tokens, "
            f"COALESCE(SUM(actual_cost_usd), 0) AS total_cost_usd, "
            f"COALESCE(AVG(latency_ms), 0) AS avg_latency_ms "
            f"FROM runs WHERE {where} AND model_id IS NOT NULL "
            f"GROUP BY model_id ORDER BY run_count DESC",
            tuple(params),
        )

    # ------------------------------------------------------------------
    # Escalation Analytics
    # ------------------------------------------------------------------

    def escalation_summary(
        self, from_ts: str | None = None, to_ts: str | None = None
    ) -> dict[str, Any]:
        """Get escalation summary (HIR, RR, escalation rate, counts)."""
        where, params = self._build_where(from_ts, to_ts)

        total = self._query_one(
            f"SELECT COUNT(*) AS total_runs FROM runs WHERE {where}", tuple(params)
        )
        total_runs = total["total_runs"] if total else 0

        hir = self._query_one(
            f"SELECT COUNT(*) AS hir_count FROM runs WHERE {where} AND required_human_intervention = 1",
            tuple(params),
        )
        rr = self._query_one(
            f"SELECT COUNT(*) AS rework_count FROM runs WHERE {where} AND required_rework = 1",
            tuple(params),
        )
        escalated = self._query_one(
            f"SELECT COUNT(*) AS escalated_count FROM runs WHERE {where} AND status = 'escalated'",
            tuple(params),
        )

        # Escalation reasons
        hir_reasons = self._query(
            f"SELECT human_intervention_reason, COUNT(*) AS count "
            f"FROM runs WHERE {where} AND required_human_intervention = 1 "
            f"AND human_intervention_reason IS NOT NULL "
            f"GROUP BY human_intervention_reason ORDER BY count DESC",
            tuple(params),
        )
        rework_reasons = self._query(
            f"SELECT rework_reason, COUNT(*) AS count "
            f"FROM runs WHERE {where} AND required_rework = 1 "
            f"AND rework_reason IS NOT NULL "
            f"GROUP BY rework_reason ORDER BY count DESC",
            tuple(params),
        )

        hir_count = hir["hir_count"] if hir else 0
        rr_count = rr["rework_count"] if rr else 0
        esc_count = escalated["escalated_count"] if escalated else 0

        return {
            "total_runs": total_runs,
            "human_intervention_count": hir_count,
            "human_intervention_rate": round((hir_count / total_runs * 100), 1) if total_runs > 0 else 0.0,
            "rework_count": rr_count,
            "rework_rate": round((rr_count / total_runs * 100), 1) if total_runs > 0 else 0.0,
            "escalated_count": esc_count,
            "escalation_rate": round((esc_count / total_runs * 100), 1) if total_runs > 0 else 0.0,
            "hir_reasons": [dict(r) for r in hir_reasons],
            "rework_reasons": [dict(r) for r in rework_reasons],
        }

    def escalation_trend(self, days: int = 30) -> list[dict[str, Any]]:
        """Get escalation trend over time."""
        from_ts = _days_ago(days)
        return self._query(
            "SELECT date(created_at) AS day, "
            "COUNT(*) AS total_runs, "
            "SUM(CASE WHEN required_human_intervention = 1 THEN 1 ELSE 0 END) AS hir_count, "
            "SUM(CASE WHEN required_rework = 1 THEN 1 ELSE 0 END) AS rework_count, "
            "SUM(CASE WHEN status = 'escalated' THEN 1 ELSE 0 END) AS escalated_count "
            "FROM runs WHERE created_at >= ? "
            "GROUP BY day ORDER BY day ASC",
            (from_ts,),
        )

    def escalation_by_model(self) -> list[dict[str, Any]]:
        """Get escalation breakdown by model (which models escalate most)."""
        return self._query(
            "SELECT model_id, "
            "COUNT(*) AS total_runs, "
            "SUM(CASE WHEN required_human_intervention = 1 THEN 1 ELSE 0 END) AS hir_count, "
            "SUM(CASE WHEN required_rework = 1 THEN 1 ELSE 0 END) AS rework_count, "
            "SUM(CASE WHEN status = 'escalated' THEN 1 ELSE 0 END) AS escalated_count "
            "FROM runs WHERE model_id IS NOT NULL "
            "GROUP BY model_id "
            "HAVING hir_count > 0 OR rework_count > 0 OR escalated_count > 0 "
            "ORDER BY escalated_count DESC, hir_count DESC"
        )

    def escalation_by_risk_class(self) -> list[dict[str, Any]]:
        """Get escalation breakdown by risk class."""
        return self._query(
            "SELECT risk_class, "
            "COUNT(*) AS total_runs, "
            "SUM(CASE WHEN required_human_intervention = 1 THEN 1 ELSE 0 END) AS hir_count, "
            "SUM(CASE WHEN required_rework = 1 THEN 1 ELSE 0 END) AS rework_count, "
            "SUM(CASE WHEN status = 'escalated' THEN 1 ELSE 0 END) AS escalated_count "
            "FROM runs GROUP BY risk_class "
            "ORDER BY total_runs DESC"
        )

    def escalation_triggers(self) -> list[dict[str, Any]]:
        """Analyze what triggers escalations."""
        # Analyze ledger events for escalation-related event types
        triggers = self._query(
            "SELECT event_type, COUNT(*) AS count "
            "FROM ledger_events "
            "WHERE event_type IN ('verification_failed', 'human_review_requested', "
            "'fallback_triggered', 'outcome_rejected') "
            "GROUP BY event_type ORDER BY count DESC"
        )
        return [dict(r) for r in triggers]

    # ------------------------------------------------------------------
    # Audit Analytics
    # ------------------------------------------------------------------

    def audit_summary(
        self, from_ts: str | None = None, to_ts: str | None = None
    ) -> dict[str, Any]:
        """Get audit summary (total events, unique actors, actions breakdown)."""
        conn = self._db._get_conn()
        conditions: list[str] = []
        params: list[Any] = []

        if from_ts:
            conditions.append("timestamp >= ?")
            params.append(from_ts)
        if to_ts:
            conditions.append("timestamp <= ?")
            params.append(to_ts)

        where = " AND ".join(conditions) if conditions else "1=1"

        total = conn.execute(
            f"SELECT COUNT(*) AS total_events FROM audit_log WHERE {where}",
            params,
        ).fetchone()
        unique_actors = conn.execute(
            f"SELECT COUNT(DISTINCT actor_id) AS unique_actors FROM audit_log WHERE {where}",
            params,
        ).fetchone()

        # Actions breakdown
        actions = conn.execute(
            f"SELECT action, COUNT(*) AS count "
            f"FROM audit_log WHERE {where} "
            f"GROUP BY action ORDER BY count DESC",
            params,
        ).fetchall()

        # Success rate
        success = conn.execute(
            f"SELECT COUNT(*) AS success_count FROM audit_log WHERE {where} AND success = 1",
            params,
        ).fetchone()
        failed = conn.execute(
            f"SELECT COUNT(*) AS failed_count FROM audit_log WHERE {where} AND success = 0",
            params,
        ).fetchone()

        total_events = total["total_events"] if total else 0

        return {
            "total_events": total_events,
            "unique_actors": unique_actors["unique_actors"] if unique_actors else 0,
            "success_count": success["success_count"] if success else 0,
            "failed_count": failed["failed_count"] if failed else 0,
            "success_rate": (
                round((success["success_count"] / total_events * 100), 1)
                if success and total_events > 0 else 0.0
            ),
            "actions": [dict(r) for r in actions],
        }

    def audit_by_actor(
        self, from_ts: str | None = None, to_ts: str | None = None
    ) -> list[dict[str, Any]]:
        """Get audit breakdown by actor."""
        conn = self._db._get_conn()
        conditions: list[str] = []
        params: list[Any] = []

        if from_ts:
            conditions.append("timestamp >= ?")
            params.append(from_ts)
        if to_ts:
            conditions.append("timestamp <= ?")
            params.append(to_ts)

        where = " AND ".join(conditions) if conditions else "1=1"

        rows = conn.execute(
            f"SELECT actor_id, COUNT(*) AS event_count, "
            f"COUNT(DISTINCT action) AS distinct_actions, "
            f"SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS failed_count "
            f"FROM audit_log WHERE {where} "
            f"GROUP BY actor_id ORDER BY event_count DESC",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def audit_by_action(
        self, from_ts: str | None = None, to_ts: str | None = None
    ) -> list[dict[str, Any]]:
        """Get audit breakdown by action type."""
        conn = self._db._get_conn()
        conditions: list[str] = []
        params: list[Any] = []

        if from_ts:
            conditions.append("timestamp >= ?")
            params.append(from_ts)
        if to_ts:
            conditions.append("timestamp <= ?")
            params.append(to_ts)

        where = " AND ".join(conditions) if conditions else "1=1"

        rows = conn.execute(
            f"SELECT action, COUNT(*) AS count, "
            f"COUNT(DISTINCT actor_id) AS unique_actors, "
            f"SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS failed_count "
            f"FROM audit_log WHERE {where} "
            f"GROUP BY action ORDER BY count DESC",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def audit_timeline(
        self,
        from_ts: str | None = None,
        to_ts: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get audit timeline (chronological events)."""
        conn = self._db._get_conn()
        conditions: list[str] = []
        params: list[Any] = []

        if from_ts:
            conditions.append("timestamp >= ?")
            params.append(from_ts)
        if to_ts:
            conditions.append("timestamp <= ?")
            params.append(to_ts)

        where = " AND ".join(conditions) if conditions else "1=1"
        params.append(limit)

        rows = conn.execute(
            f"SELECT * FROM audit_log WHERE {where} "
            f"ORDER BY timestamp DESC LIMIT ?",
            params,
        ).fetchall()

        results = []
        for r in rows:
            d = dict(r)
            if d.get("details") and isinstance(d["details"], str):
                try:
                    d["details"] = json.loads(d["details"])
                except (json.JSONDecodeError, TypeError):
                    pass
            results.append(d)

        return results

    def audit_anomalies(self, window_hours: int = 24) -> list[dict[str, Any]]:
        """Detect unusual audit patterns."""
        from_ts = _hours_ago(window_hours * 4)
        conn = self._db._get_conn()

        # Get hourly event counts
        rows = conn.execute(
            "SELECT strftime('%Y-%m-%dT%H:00:00', timestamp) AS hour, "
            "COUNT(*) AS event_count "
            "FROM audit_log WHERE timestamp >= ? "
            "GROUP BY hour ORDER BY hour ASC",
            (from_ts,),
        ).fetchall()

        if len(rows) < window_hours * 2:
            return []

        counts = [r["event_count"] for r in rows]
        anomalies = []

        for i in range(window_hours, len(counts)):
            window = counts[i - window_hours : i]
            mean = sum(window) / len(window) if window else 0.0
            std = (
                (sum((x - mean) ** 2 for x in window) / len(window)) ** 0.5
                if window
                else 0.0
            )
            threshold = mean + 2.0 * std
            if counts[i] > threshold and threshold > 0:
                anomalies.append(
                    {
                        "hour": rows[i]["hour"],
                        "event_count": counts[i],
                        "expected_count": round(mean, 1),
                        "threshold": round(threshold, 1),
                    }
                )

        return anomalies

    # ------------------------------------------------------------------
    # Benchmark Analytics
    # ------------------------------------------------------------------

    def benchmark_summary(self, cohort_name: str | None = None) -> dict[str, Any]:
        """Get benchmark summary for a cohort or all cohorts."""
        if cohort_name:
            rows = self._query(
                "SELECT cohort_name, model_id, "
                "COUNT(*) AS run_count, "
                "COALESCE(AVG(accuracy), 0) AS avg_accuracy, "
                "COALESCE(AVG(mean_latency_ms), 0) AS avg_latency_ms, "
                "COALESCE(AVG(p95_latency_ms), 0) AS avg_p95_latency, "
                "COALESCE(AVG(total_cost_usd), 0) AS avg_cost_usd, "
                "COALESCE(AVG(hir), 0) AS avg_hir, "
                "COALESCE(AVG(rr), 0) AS avg_rr, "
                "COALESCE(AVG(escalation_rate), 0) AS avg_escalation_rate, "
                "MAX(timestamp) AS last_run "
                "FROM benchmark_results WHERE cohort_name = ? "
                "GROUP BY cohort_name, model_id ORDER BY avg_accuracy DESC",
                (cohort_name,),
            )
        else:
            rows = self._query(
                "SELECT cohort_name, "
                "COUNT(*) AS total_results, "
                "COUNT(DISTINCT model_id) AS models_tested, "
                "COALESCE(AVG(accuracy), 0) AS avg_accuracy, "
                "MAX(timestamp) AS last_run "
                "FROM benchmark_results GROUP BY cohort_name ORDER BY last_run DESC"
            )

        return {
            "cohort_name": cohort_name,
            "results": [dict(r) for r in rows],
            "total_cohorts": len(set(r.get("cohort_name", "") for r in rows)),
        }

    def benchmark_history(
        self,
        cohort_name: str,
        model_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Get historical benchmark results."""
        if model_id:
            return self._query(
                "SELECT * FROM benchmark_results "
                "WHERE cohort_name = ? AND model_id = ? "
                "ORDER BY timestamp DESC LIMIT ?",
                (cohort_name, model_id, limit),
            )
        return self._query(
            "SELECT * FROM benchmark_results "
            "WHERE cohort_name = ? "
            "ORDER BY timestamp DESC LIMIT ?",
            (cohort_name, limit),
        )

    def benchmark_regression(
        self, cohort_name: str, model_id: str
    ) -> dict[str, Any]:
        """Detect performance regression for a model in a cohort."""
        history = self._query(
            "SELECT * FROM benchmark_results "
            "WHERE cohort_name = ? AND model_id = ? "
            "ORDER BY timestamp DESC LIMIT 10",
            (cohort_name, model_id),
        )

        if len(history) < 2:
            return {
                "cohort_name": cohort_name,
                "model_id": model_id,
                "regression_detected": False,
                "message": "Insufficient data for regression detection (need at least 2 runs)",
                "samples": len(history),
            }

        # Compare latest to previous
        latest = history[0]
        previous = history[1]

        metrics = ["accuracy", "mean_latency_ms", "hir", "rr", "escalation_rate"]
        regressions: list[dict[str, Any]] = []

        for metric in metrics:
            current_val = latest.get(metric)
            previous_val = previous.get(metric)
            if current_val is not None and previous_val is not None and previous_val != 0:
                change_pct = round((current_val - previous_val) / abs(previous_val) * 100, 1)
                # For accuracy, decreasing is bad. For others, increasing is bad.
                if metric == "accuracy":
                    if change_pct < -5:
                        regressions.append(
                            {"metric": metric, "change_pct": change_pct, "direction": "degraded"}
                        )
                else:
                    if change_pct > 10:
                        regressions.append(
                            {"metric": metric, "change_pct": change_pct, "direction": "degraded"}
                        )

        return {
            "cohort_name": cohort_name,
            "model_id": model_id,
            "regression_detected": len(regressions) > 0,
            "regressions": regressions,
            "latest_run": dict(latest),
            "previous_run": dict(previous),
            "samples": len(history),
        }

    def benchmark_comparison(self, cohort_name: str | None = None) -> dict[str, Any]:
        """Compare models across benchmark cohorts."""
        if cohort_name:
            rows = self._query(
                "SELECT model_id, "
                "COUNT(*) AS run_count, "
                "COALESCE(AVG(accuracy), 0) AS avg_accuracy, "
                "COALESCE(AVG(mean_latency_ms), 0) AS avg_latency_ms, "
                "COALESCE(AVG(p95_latency_ms), 0) AS avg_p95_latency, "
                "COALESCE(AVG(total_cost_usd), 0) AS avg_cost_usd, "
                "COALESCE(AVG(hir), 0) AS avg_hir, "
                "COALESCE(AVG(rr), 0) AS avg_rr, "
                "COALESCE(AVG(escalation_rate), 0) AS avg_escalation_rate "
                "FROM benchmark_results WHERE cohort_name = ? "
                "GROUP BY model_id ORDER BY avg_accuracy DESC",
                (cohort_name,),
            )
        else:
            rows = self._query(
                "SELECT cohort_name, model_id, "
                "COUNT(*) AS run_count, "
                "COALESCE(AVG(accuracy), 0) AS avg_accuracy, "
                "COALESCE(AVG(mean_latency_ms), 0) AS avg_latency_ms, "
                "COALESCE(AVG(total_cost_usd), 0) AS avg_cost_usd "
                "FROM benchmark_results "
                "GROUP BY cohort_name, model_id ORDER BY cohort_name, avg_accuracy DESC"
            )

        return {
            "cohort_name": cohort_name,
            "comparison": [dict(r) for r in rows],
            "total_models": len(set(r.get("model_id", "") for r in rows)),
        }

    # ------------------------------------------------------------------
    # Dashboard Data
    # ------------------------------------------------------------------

    def dashboard_data(self) -> dict[str, Any]:
        """Get all data needed for the dashboard in one call.

        Returns:
        - summary: total runs, accuracy, cost, HIR, RR
        - cost_trend: last 30 days
        - usage_trend: last 30 days
        - model_ranking: current model ranking
        - recent_alerts: last 10 alerts
        - recent_runs: last 10 runs
        - escalation_summary: current HIR/RR
        """
        # Summary stats
        total = self._query_one("SELECT COUNT(*) AS total_runs FROM runs")
        accepted = self._query_one(
            "SELECT COUNT(*) AS accepted FROM runs WHERE status = 'accepted'"
        )
        hir = self._query_one(
            "SELECT COUNT(*) AS hir_count FROM runs WHERE required_human_intervention = 1"
        )
        rr = self._query_one(
            "SELECT COUNT(*) AS rework_count FROM runs WHERE required_rework = 1"
        )
        cost_today = self._query_one(
            "SELECT COALESCE(SUM(actual_cost_usd), 0) AS cost_today "
            "FROM runs WHERE date(created_at) = date('now')"
        )

        total_runs = total["total_runs"] if total else 0
        accepted_count = accepted["accepted"] if accepted else 0
        hir_count = hir["hir_count"] if hir else 0
        rr_count = rr["rework_count"] if rr else 0
        cost_today_val = cost_today["cost_today"] if cost_today else 0.0

        summary = {
            "total_runs": total_runs,
            "accepted_runs": accepted_count,
            "accuracy": round((accepted_count / total_runs * 100), 1) if total_runs > 0 else 0.0,
            "cost_today_usd": round(cost_today_val, 6),
            "hir": round((hir_count / total_runs * 100), 1) if total_runs > 0 else 0.0,
            "rr": round((rr_count / total_runs * 100), 1) if total_runs > 0 else 0.0,
            "hir_count": hir_count,
            "rework_count": rr_count,
        }

        # Cost trend (last 30 days)
        cost_trend = self.cost_trend(days=30)

        # Usage trend (last 30 days)
        usage_trend = self.usage_trend(days=30)

        # Model ranking
        model_ranking = self.model_ranking(metric="true_cost")

        # Recent runs (last 10)
        recent_runs = self._query(
            "SELECT run_id, status, model_id, total_tokens, actual_cost_usd, "
            "latency_ms, required_human_intervention, required_rework, "
            "risk_class, created_at "
            "FROM runs ORDER BY created_at DESC LIMIT 10"
        )
        recent_runs_list = [dict(r) for r in recent_runs]

        # Escalation summary
        escalation_summary = self.escalation_summary()

        return {
            "summary": summary,
            "cost_trend": cost_trend,
            "usage_trend": usage_trend,
            "model_ranking": model_ranking,
            "recent_runs": recent_runs_list,
            "escalation_summary": escalation_summary,
        }