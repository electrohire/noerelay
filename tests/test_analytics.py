"""Tests for the AnalyticsEngine and dashboard.

Covers:
- CostAnalyticsTests: cost_summary, cost_trend, cost_breakdown, cost_forecast, cost_anomalies
- ModelPerformanceTests: model_performance_summary, model_performance_trend, model_comparison, model_ranking
- UsageAnalyticsTests: usage_summary, usage_trend, peak_usage, usage_by_model
- EscalationAnalyticsTests: escalation_summary, escalation_trend, escalation_by_model, escalation_triggers
- AuditAnalyticsTests: audit_summary, audit_by_actor, audit_timeline, audit_anomalies
- BenchmarkAnalyticsTests: benchmark_summary, benchmark_history, benchmark_regression, benchmark_comparison
- DashboardTests: dashboard_data returns all required fields, dashboard HTML renders
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "reference"))

from gateway.analytics import AnalyticsEngine, _days_ago, _hours_ago, _now
from gateway.dashboard import render_dashboard
from gateway.database import SQLiteDatabase


class _TempDB:
    """Context manager for a temporary database."""

    def __init__(self):
        self._tmpdir: tempfile.TemporaryDirectory | None = None
        self._db: SQLiteDatabase | None = None

    def __enter__(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = os.path.join(self._tmpdir.name, "test.db")
        self._db = SQLiteDatabase(db_path)
        return self._db

    def __exit__(self, *args):
        if self._db is not None:
            self._db.close()
        if self._tmpdir is not None:
            self._tmpdir.cleanup()


def _seed_run(
    db: SQLiteDatabase,
    run_id: str,
    status: str = "accepted",
    model_id: str = "test-model",
    cost: float = 0.01,
    tokens: int = 100,
    latency: float = 50.0,
    hir: bool = False,
    rework: bool = False,
    risk_class: str = "low",
    created_at: str | None = None,
    hir_reason: str | None = None,
    rework_reason: str | None = None,
) -> None:
    """Seed a run record into the database."""
    from datetime import datetime, timezone

    ts = created_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    db.save_run(
        {
            "run_id": run_id,
            "trace_id": f"trace-{run_id}",
            "task_id": f"task-{run_id}",
            "status": status,
            "created_at": ts,
            "completed_at": ts,
            "total_tokens": tokens,
            "prompt_tokens": tokens // 2,
            "completion_tokens": tokens // 2,
            "actual_cost_usd": cost,
            "latency_ms": latency,
            "model_id": model_id,
            "required_human_intervention": hir,
            "required_rework": rework,
            "human_intervention_reason": hir_reason,
            "rework_reason": rework_reason,
            "risk_class": risk_class,
            "is_local": False,
            "cache_hit": False,
            "decision_trace": None,
            "receipt": None,
            "openrouter_request": None,
            "openrouter_response": None,
        }
    )


def _seed_benchmark(
    db: SQLiteDatabase,
    cohort_name: str = "test-cohort",
    model_id: str = "test-model",
    accuracy: float = 0.95,
    total_tokens: int = 500,
    cost: float = 0.005,
    latency: float = 45.0,
    p95_latency: float = 80.0,
    hir: float = 0.02,
    rr: float = 0.03,
    escalation_rate: float = 0.01,
) -> str:
    """Seed a benchmark result."""
    return db.save_benchmark_result(
        {
            "cohort_name": cohort_name,
            "model_id": model_id,
            "accuracy": accuracy,
            "total_tokens": total_tokens,
            "total_cost_usd": cost,
            "mean_latency_ms": latency,
            "p95_latency_ms": p95_latency,
            "hir": hir,
            "rr": rr,
            "escalation_rate": escalation_rate,
            "results": {"tasks": 10},
        }
    )


def _seed_audit(
    db: SQLiteDatabase,
    actor_id: str = "admin",
    action: str = "api_call",
    success: bool = True,
) -> str:
    """Seed an audit log entry."""
    return db.record_audit(
        actor_id=actor_id,
        action=action,
        resource_type="api",
        resource_id="/v1/test",
        ip_address="127.0.0.1",
        details={"method": "GET"},
        success=success,
    )


# ---------------------------------------------------------------------------
# Cost Analytics Tests
# ---------------------------------------------------------------------------


class CostAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = os.path.join(self._tmpdir.name, "test.db")
        self.db = SQLiteDatabase(db_path)
        self.engine = AnalyticsEngine(self.db)

    def tearDown(self):
        self.db.close()
        self._tmpdir.cleanup()

    def test_cost_summary_empty(self):
        result = self.engine.cost_summary()
        self.assertEqual(result["total_cost_usd"], 0.0)
        self.assertEqual(result["total_runs"], 0)
        self.assertEqual(result["group_by"], "model")
        self.assertEqual(len(result["groups"]), 0)

    def test_cost_summary_with_data(self):
        _seed_run(self.db, "run-1", model_id="model-a", cost=0.01)
        _seed_run(self.db, "run-2", model_id="model-a", cost=0.02)
        _seed_run(self.db, "run-3", model_id="model-b", cost=0.03)

        result = self.engine.cost_summary()
        self.assertAlmostEqual(result["total_cost_usd"], 0.06)
        self.assertEqual(result["total_runs"], 3)
        self.assertEqual(len(result["groups"]), 2)

    def test_cost_summary_group_by_risk_class(self):
        _seed_run(self.db, "run-1", risk_class="low", cost=0.01)
        _seed_run(self.db, "run-2", risk_class="high", cost=0.05)

        result = self.engine.cost_summary(group_by="risk_class")
        self.assertEqual(len(result["groups"]), 2)

    def test_cost_trend(self):
        _seed_run(self.db, "run-1", cost=0.01)
        _seed_run(self.db, "run-2", cost=0.02)

        result = self.engine.cost_trend(days=30)
        self.assertIsInstance(result, list)
        self.assertGreaterEqual(len(result), 0)

    def test_cost_breakdown(self):
        _seed_run(self.db, "run-1", status="accepted", cost=0.01)
        _seed_run(self.db, "run-2", status="escalated", cost=0.02, rework=True)

        result = self.engine.cost_breakdown()
        self.assertIn("total_cost_usd", result)
        self.assertIn("direct_cost_usd", result)
        self.assertIn("rework_cost_usd", result)
        self.assertIn("escalation_cost_usd", result)

    def test_cost_forecast(self):
        _seed_run(self.db, "run-1", cost=0.01)
        _seed_run(self.db, "run-2", cost=0.02)

        result = self.engine.cost_forecast(days=7)
        self.assertIn("forecast_days", result)
        self.assertIn("forecast_total_cost_usd", result)
        self.assertIn("forecast_daily_costs", result)
        self.assertEqual(len(result["forecast_daily_costs"]), 7)

    def test_cost_forecast_empty(self):
        result = self.engine.cost_forecast(days=7)
        self.assertEqual(result["forecast_total_cost_usd"], 0.0)
        self.assertEqual(result["confidence"], "low")

    def test_cost_anomalies(self):
        # Seed some data - no anomalies expected with uniform data
        for i in range(20):
            _seed_run(self.db, f"run-{i}", cost=0.01)

        result = self.engine.cost_anomalies(window_days=7)
        self.assertIsInstance(result, list)


# ---------------------------------------------------------------------------
# Model Performance Tests
# ---------------------------------------------------------------------------


class ModelPerformanceTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = os.path.join(self._tmpdir.name, "test.db")
        self.db = SQLiteDatabase(db_path)
        self.engine = AnalyticsEngine(self.db)

    def tearDown(self):
        self.db.close()
        self._tmpdir.cleanup()

    def test_model_performance_summary_empty(self):
        result = self.engine.model_performance_summary()
        self.assertEqual(len(result), 0)

    def test_model_performance_summary_with_data(self):
        _seed_run(self.db, "run-1", model_id="model-a", status="accepted", cost=0.01, tokens=100, latency=50)
        _seed_run(self.db, "run-2", model_id="model-a", status="accepted", cost=0.02, tokens=200, latency=60)
        _seed_run(self.db, "run-3", model_id="model-b", status="escalated", cost=0.03, tokens=150, latency=40)

        result = self.engine.model_performance_summary()
        self.assertEqual(len(result), 2)
        for entry in result:
            self.assertIn("model_id", entry)
            self.assertIn("accuracy", entry)
            self.assertIn("hir_rate", entry)
            self.assertIn("rework_rate", entry)
            self.assertIn("true_cost_per_correct", entry)

    def test_model_performance_summary_specific_model(self):
        _seed_run(self.db, "run-1", model_id="model-a", cost=0.01)
        _seed_run(self.db, "run-2", model_id="model-b", cost=0.02)

        result = self.engine.model_performance_summary(model_id="model-a")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["model_id"], "model-a")

    def test_model_performance_trend(self):
        _seed_run(self.db, "run-1", model_id="model-a", cost=0.01)

        result = self.engine.model_performance_trend("model-a", days=30)
        self.assertIsInstance(result, list)

    def test_model_comparison(self):
        _seed_run(self.db, "run-1", model_id="model-a", status="accepted", cost=0.01)
        _seed_run(self.db, "run-2", model_id="model-b", status="accepted", cost=0.02)

        result = self.engine.model_comparison(model_ids=["model-a", "model-b"])
        self.assertIn("models", result)
        self.assertEqual(result["compared_count"], 2)

    def test_model_comparison_all(self):
        _seed_run(self.db, "run-1", model_id="model-a", cost=0.01)
        result = self.engine.model_comparison()
        self.assertIn("models", result)

    def test_model_ranking(self):
        _seed_run(self.db, "run-1", model_id="model-a", status="accepted", cost=0.01, tokens=100, latency=50)
        _seed_run(self.db, "run-2", model_id="model-b", status="accepted", cost=0.02, tokens=200, latency=40)

        result = self.engine.model_ranking(metric="true_cost")
        self.assertIsInstance(result, list)
        if result:
            self.assertIn("rank", result[0])
            self.assertIn("true_cost_per_correct", result[0])

    def test_model_ranking_by_latency(self):
        _seed_run(self.db, "run-1", model_id="model-a", status="accepted", latency=50)
        _seed_run(self.db, "run-2", model_id="model-b", status="accepted", latency=40)

        result = self.engine.model_ranking(metric="latency")
        self.assertIsInstance(result, list)


# ---------------------------------------------------------------------------
# Usage Analytics Tests
# ---------------------------------------------------------------------------


class UsageAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = os.path.join(self._tmpdir.name, "test.db")
        self.db = SQLiteDatabase(db_path)
        self.engine = AnalyticsEngine(self.db)

    def tearDown(self):
        self.db.close()
        self._tmpdir.cleanup()

    def test_usage_summary_empty(self):
        result = self.engine.usage_summary()
        self.assertEqual(result["total_runs"], 0)
        self.assertEqual(result["total_tokens"], 0)

    def test_usage_summary_with_data(self):
        _seed_run(self.db, "run-1", status="accepted", tokens=100, cost=0.01)
        _seed_run(self.db, "run-2", status="escalated", tokens=200, cost=0.02)

        result = self.engine.usage_summary()
        self.assertEqual(result["total_runs"], 2)
        self.assertEqual(result["total_tokens"], 300)
        self.assertIn("by_status", result)

    def test_usage_trend(self):
        _seed_run(self.db, "run-1", tokens=100)
        result = self.engine.usage_trend(days=30)
        self.assertIsInstance(result, list)

    def test_usage_trend_hourly(self):
        _seed_run(self.db, "run-1", tokens=100)
        result = self.engine.usage_trend(days=1, granularity="hourly")
        self.assertIsInstance(result, list)

    def test_peak_usage(self):
        for i in range(5):
            _seed_run(self.db, f"run-{i}", tokens=100)
        result = self.engine.peak_usage(days=7)
        self.assertIn("peak_hours", result)
        self.assertIn("avg_hourly_runs", result)

    def test_peak_usage_empty(self):
        result = self.engine.peak_usage(days=7)
        self.assertEqual(result["max_hourly_runs"], 0)

    def test_usage_by_model(self):
        _seed_run(self.db, "run-1", model_id="model-a", tokens=100)
        _seed_run(self.db, "run-2", model_id="model-b", tokens=200)
        result = self.engine.usage_by_model()
        self.assertEqual(len(result), 2)


# ---------------------------------------------------------------------------
# Escalation Analytics Tests
# ---------------------------------------------------------------------------


class EscalationAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = os.path.join(self._tmpdir.name, "test.db")
        self.db = SQLiteDatabase(db_path)
        self.engine = AnalyticsEngine(self.db)

    def tearDown(self):
        self.db.close()
        self._tmpdir.cleanup()

    def test_escalation_summary_empty(self):
        result = self.engine.escalation_summary()
        self.assertEqual(result["total_runs"], 0)
        self.assertEqual(result["human_intervention_count"], 0)
        self.assertEqual(result["rework_count"], 0)

    def test_escalation_summary_with_data(self):
        _seed_run(self.db, "run-1", status="accepted")
        _seed_run(self.db, "run-2", status="escalated", hir=True, hir_reason="verification_failed")
        _seed_run(self.db, "run-3", rework=True, rework_reason="semantic_fallback")

        result = self.engine.escalation_summary()
        self.assertEqual(result["total_runs"], 3)
        self.assertEqual(result["human_intervention_count"], 1)
        self.assertEqual(result["rework_count"], 1)
        self.assertEqual(result["escalated_count"], 1)
        self.assertIn("hir_reasons", result)
        self.assertIn("rework_reasons", result)

    def test_escalation_trend(self):
        _seed_run(self.db, "run-1", hir=True)
        result = self.engine.escalation_trend(days=30)
        self.assertIsInstance(result, list)

    def test_escalation_by_model(self):
        _seed_run(self.db, "run-1", model_id="model-a", hir=True)
        _seed_run(self.db, "run-2", model_id="model-b", rework=True)
        _seed_run(self.db, "run-3", model_id="model-a", status="escalated")

        result = self.engine.escalation_by_model()
        self.assertIsInstance(result, list)
        self.assertGreaterEqual(len(result), 1)

    def test_escalation_by_risk_class(self):
        _seed_run(self.db, "run-1", risk_class="low", hir=True)
        _seed_run(self.db, "run-2", risk_class="high", status="escalated")

        result = self.engine.escalation_by_risk_class()
        self.assertIsInstance(result, list)

    def test_escalation_triggers(self):
        result = self.engine.escalation_triggers()
        self.assertIsInstance(result, list)


# ---------------------------------------------------------------------------
# Audit Analytics Tests
# ---------------------------------------------------------------------------


class AuditAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = os.path.join(self._tmpdir.name, "test.db")
        self.db = SQLiteDatabase(db_path)
        self.engine = AnalyticsEngine(self.db)

    def tearDown(self):
        self.db.close()
        self._tmpdir.cleanup()

    def test_audit_summary_empty(self):
        result = self.engine.audit_summary()
        self.assertEqual(result["total_events"], 0)
        self.assertEqual(result["unique_actors"], 0)

    def test_audit_summary_with_data(self):
        _seed_audit(self.db, "admin", "api_call", True)
        _seed_audit(self.db, "user-1", "api_call", True)
        _seed_audit(self.db, "admin", "config_update", True)
        _seed_audit(self.db, "user-2", "api_call", False)

        result = self.engine.audit_summary()
        self.assertEqual(result["total_events"], 4)
        self.assertEqual(result["unique_actors"], 3)
        self.assertEqual(result["success_count"], 3)
        self.assertEqual(result["failed_count"], 1)
        self.assertIn("actions", result)

    def test_audit_by_actor(self):
        _seed_audit(self.db, "admin", "api_call")
        _seed_audit(self.db, "admin", "config_update")
        _seed_audit(self.db, "user-1", "api_call")

        result = self.engine.audit_by_actor()
        self.assertEqual(len(result), 2)
        # admin should have more events
        self.assertEqual(result[0]["actor_id"], "admin")
        self.assertEqual(result[0]["event_count"], 2)

    def test_audit_by_action(self):
        _seed_audit(self.db, "admin", "api_call")
        _seed_audit(self.db, "user-1", "api_call")
        _seed_audit(self.db, "admin", "config_update")

        result = self.engine.audit_by_action()
        self.assertEqual(len(result), 2)

    def test_audit_timeline(self):
        _seed_audit(self.db, "admin", "api_call")
        _seed_audit(self.db, "user-1", "api_call")

        result = self.engine.audit_timeline(limit=10)
        self.assertEqual(len(result), 2)
        # Most recent first
        self.assertIn("audit_id", result[0])

    def test_audit_anomalies(self):
        for i in range(30):
            _seed_audit(self.db, f"actor-{i % 3}", "api_call")
        result = self.engine.audit_anomalies(window_hours=24)
        self.assertIsInstance(result, list)


# ---------------------------------------------------------------------------
# Benchmark Analytics Tests
# ---------------------------------------------------------------------------


class BenchmarkAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = os.path.join(self._tmpdir.name, "test.db")
        self.db = SQLiteDatabase(db_path)
        self.engine = AnalyticsEngine(self.db)

    def tearDown(self):
        self.db.close()
        self._tmpdir.cleanup()

    def test_benchmark_summary_empty(self):
        result = self.engine.benchmark_summary()
        self.assertIn("cohort_name", result)
        self.assertEqual(len(result["results"]), 0)

    def test_benchmark_summary_with_data(self):
        _seed_benchmark(self.db, "cohort-a", "model-x", accuracy=0.95)
        _seed_benchmark(self.db, "cohort-a", "model-y", accuracy=0.88)
        _seed_benchmark(self.db, "cohort-b", "model-x", accuracy=0.92)

        result = self.engine.benchmark_summary()
        self.assertIn("results", result)
        self.assertGreaterEqual(result["total_cohorts"], 1)

    def test_benchmark_summary_specific_cohort(self):
        _seed_benchmark(self.db, "cohort-a", "model-x", accuracy=0.95)

        result = self.engine.benchmark_summary(cohort_name="cohort-a")
        self.assertEqual(len(result["results"]), 1)

    def test_benchmark_history(self):
        _seed_benchmark(self.db, "cohort-a", "model-x", accuracy=0.95)
        _seed_benchmark(self.db, "cohort-a", "model-x", accuracy=0.93)

        result = self.engine.benchmark_history("cohort-a", "model-x", limit=10)
        self.assertEqual(len(result), 2)

    def test_benchmark_regression_detected(self):
        _seed_benchmark(self.db, "cohort-a", "model-x", accuracy=0.95)
        _seed_benchmark(self.db, "cohort-a", "model-x", accuracy=0.85)

        result = self.engine.benchmark_regression("cohort-a", "model-x")
        self.assertIn("regression_detected", result)
        self.assertIn("samples", result)

    def test_benchmark_regression_insufficient(self):
        _seed_benchmark(self.db, "cohort-a", "model-x", accuracy=0.95)

        result = self.engine.benchmark_regression("cohort-a", "model-x")
        self.assertFalse(result["regression_detected"])
        self.assertEqual(result["samples"], 1)

    def test_benchmark_comparison(self):
        _seed_benchmark(self.db, "cohort-a", "model-x", accuracy=0.95)
        _seed_benchmark(self.db, "cohort-a", "model-y", accuracy=0.88)

        result = self.engine.benchmark_comparison(cohort_name="cohort-a")
        self.assertIn("comparison", result)
        self.assertEqual(len(result["comparison"]), 2)

    def test_benchmark_comparison_all(self):
        _seed_benchmark(self.db, "cohort-a", "model-x", accuracy=0.95)
        result = self.engine.benchmark_comparison()
        self.assertIn("comparison", result)


# ---------------------------------------------------------------------------
# Dashboard Tests
# ---------------------------------------------------------------------------


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = os.path.join(self._tmpdir.name, "test.db")
        self.db = SQLiteDatabase(db_path)
        self.engine = AnalyticsEngine(self.db)

    def tearDown(self):
        self.db.close()
        self._tmpdir.cleanup()

    def test_dashboard_data_empty(self):
        result = self.engine.dashboard_data()
        self.assertIn("summary", result)
        self.assertIn("cost_trend", result)
        self.assertIn("usage_trend", result)
        self.assertIn("model_ranking", result)
        self.assertIn("recent_runs", result)
        self.assertIn("escalation_summary", result)

        summary = result["summary"]
        self.assertEqual(summary["total_runs"], 0)
        self.assertEqual(summary["accuracy"], 0.0)

    def test_dashboard_data_with_runs(self):
        _seed_run(self.db, "run-1", status="accepted", model_id="model-a", cost=0.01, tokens=100, latency=50)
        _seed_run(self.db, "run-2", status="escalated", model_id="model-b", cost=0.02, tokens=200, latency=60, hir=True)

        result = self.engine.dashboard_data()
        summary = result["summary"]
        self.assertEqual(summary["total_runs"], 2)
        self.assertGreater(summary["accuracy"], 0)
        self.assertGreater(summary["hir"], 0)

        # Recent runs should have entries
        self.assertEqual(len(result["recent_runs"]), 2)

    def test_dashboard_data_model_ranking(self):
        _seed_run(self.db, "run-1", status="accepted", model_id="model-a", cost=0.01, tokens=100, latency=50)
        _seed_run(self.db, "run-2", status="accepted", model_id="model-b", cost=0.02, tokens=200, latency=40)

        result = self.engine.dashboard_data()
        ranking = result["model_ranking"]
        self.assertIsInstance(ranking, list)
        if ranking:
            self.assertIn("rank", ranking[0])

    def test_dashboard_html_renders(self):
        html = render_dashboard()
        self.assertIsInstance(html, str)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("NoeRelay Dashboard", html)
        self.assertIn("</html>", html)
        # Check key sections exist
        self.assertIn("overview-cards", html)
        self.assertIn("cost-chart", html)
        self.assertIn("model-ranking-table", html)
        self.assertIn("recent-runs-table", html)
        self.assertIn("alerts-container", html)

    def test_dashboard_html_contains_js(self):
        html = render_dashboard()
        self.assertIn("<script>", html)
        self.assertIn("loadAll()", html)
        self.assertIn("setInterval", html)
        self.assertIn("fetchJSON", html)


# ---------------------------------------------------------------------------
# Analytics Engine Helpers
# ---------------------------------------------------------------------------


class AnalyticsHelpersTests(unittest.TestCase):
    def test_days_ago_returns_iso_format(self):
        result = _days_ago(7)
        self.assertIsInstance(result, str)
        self.assertIn("T", result)

    def test_hours_ago_returns_iso_format(self):
        result = _hours_ago(24)
        self.assertIsInstance(result, str)
        self.assertIn("T", result)

    def test_now_returns_iso_format(self):
        result = _now()
        self.assertIsInstance(result, str)
        self.assertIn("T", result)


# ---------------------------------------------------------------------------
# Analytics Engine Initialization
# ---------------------------------------------------------------------------


class AnalyticsEngineInitTests(unittest.TestCase):
    def test_init_with_db(self):
        with _TempDB() as db:
            engine = AnalyticsEngine(db)
            self.assertIsNotNone(engine)
            self.assertEqual(engine._db, db)

    def test_cost_summary_with_date_range(self):
        with _TempDB() as db:
            engine = AnalyticsEngine(db)
            _seed_run(db, "run-1", cost=0.01)
            result = engine.cost_summary(from_ts=_days_ago(30), to_ts=_now())
            self.assertIsInstance(result, dict)

    def test_usage_summary_with_date_range(self):
        with _TempDB() as db:
            engine = AnalyticsEngine(db)
            _seed_run(db, "run-1", tokens=100)
            result = engine.usage_summary(from_ts=_days_ago(30), to_ts=_now())
            self.assertIsInstance(result, dict)

    def test_escalation_summary_with_date_range(self):
        with _TempDB() as db:
            engine = AnalyticsEngine(db)
            _seed_run(db, "run-1", hir=True)
            result = engine.escalation_summary(from_ts=_days_ago(30), to_ts=_now())
            self.assertIsInstance(result, dict)


if __name__ == "__main__":
    unittest.main()