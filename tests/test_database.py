"""Tests for Phase 1 corporate readiness: database, structured logging, TLS, graceful shutdown, backup/restore."""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reference"))

from gateway.config import ConfigError, GatewayConfig
from gateway.database import SQLiteDatabase
from gateway.db_registry import DatabaseRunRegistry
from gateway.structured_logging import JsonFormatter, StructuredLogger
from gateway.runs import RunRegistry
from gateway.server import _ShutdownState, _setup_graceful_shutdown, create_server
from gateway.pipeline import PipelineContext, build_pipeline_context


class DatabaseTests(unittest.TestCase):
    """Test CRUD for runs, ledger events, API keys, audit log, benchmark results, config."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmpdir.name) / "test.db")
        self.db = SQLiteDatabase(self.db_path)

    def tearDown(self):
        self.db.close()
        self._tmpdir.cleanup()

    # --- Run operations ---

    def test_save_and_get_run(self):
        run = {
            "run_id": "run-001",
            "trace_id": "trace-001",
            "task_id": "task-001",
            "status": "pending",
            "created_at": "2024-01-01T00:00:00Z",
            "total_tokens": 100,
            "prompt_tokens": 50,
            "completion_tokens": 50,
            "actual_cost_usd": 0.01,
            "latency_ms": 500.0,
            "model_id": "test-model",
            "required_human_intervention": False,
            "required_rework": False,
            "risk_class": "low",
            "decision_trace": [{"step": "route", "decision": "selected"}],
            "receipt": {"receipt_id": "r-001", "status": "accepted"},
        }
        self.db.save_run(run)
        retrieved = self.db.get_run("run-001")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["run_id"], "run-001")
        self.assertEqual(retrieved["trace_id"], "trace-001")
        self.assertEqual(retrieved["total_tokens"], 100)
        self.assertEqual(retrieved["actual_cost_usd"], 0.01)
        self.assertEqual(retrieved["decision_trace"], [{"step": "route", "decision": "selected"}])
        self.assertEqual(retrieved["receipt"], {"receipt_id": "r-001", "status": "accepted"})

    def test_get_nonexistent_run(self):
        self.assertIsNone(self.db.get_run("nonexistent"))

    def test_list_runs(self):
        for i in range(5):
            self.db.save_run({
                "run_id": f"run-{i:03d}",
                "trace_id": f"trace-{i:03d}",
                "status": "accepted" if i % 2 == 0 else "pending",
                "created_at": f"2024-01-0{i+1}T00:00:00Z",
            })
        runs = self.db.list_runs(limit=10)
        self.assertEqual(len(runs), 5)

        accepted = self.db.list_runs(status="accepted")
        self.assertEqual(len(accepted), 3)

        pending = self.db.list_runs(status="pending")
        self.assertEqual(len(pending), 2)

    def test_list_runs_pagination(self):
        for i in range(10):
            self.db.save_run({
                "run_id": f"run-{i:03d}",
                "trace_id": f"trace-{i:03d}",
                "status": "accepted",
                "created_at": f"2024-01-{i+1:02d}T00:00:00Z",
            })
        page1 = self.db.list_runs(limit=3, offset=0)
        self.assertEqual(len(page1), 3)
        page2 = self.db.list_runs(limit=3, offset=3)
        self.assertEqual(len(page2), 3)

    def test_save_run_update(self):
        self.db.save_run({
            "run_id": "run-001",
            "trace_id": "trace-001",
            "status": "pending",
            "created_at": "2024-01-01T00:00:00Z",
        })
        self.db.save_run({
            "run_id": "run-001",
            "trace_id": "trace-001",
            "status": "accepted",
            "created_at": "2024-01-01T00:00:00Z",
            "completed_at": "2024-01-01T00:01:00Z",
            "total_tokens": 200,
        })
        retrieved = self.db.get_run("run-001")
        self.assertEqual(retrieved["status"], "accepted")
        self.assertEqual(retrieved["total_tokens"], 200)

    # --- Ledger operations ---

    def _create_test_run(self, run_id="run-001"):
        self.db.save_run({
            "run_id": run_id,
            "trace_id": f"trace-{run_id}",
            "status": "pending",
            "created_at": "2024-01-01T00:00:00Z",
        })

    def test_save_and_get_ledger_events(self):
        self._create_test_run()
        event = {
            "event_id": "event-001",
            "run_id": "run-001",
            "sequence": 0,
            "timestamp": "2024-01-01T00:00:00Z",
            "actor": {"id": "gateway", "kind": "service"},
            "event_type": "route_selected",
            "subject_id": "subj-001",
            "payload": {"model": "test-model"},
            "previous_event_hash": "GENESIS",
            "event_hash": "sha256:abc123",
        }
        self.db.save_ledger_event(event)
        events = self.db.get_ledger_events(run_id="run-001")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_id"], "event-001")
        self.assertEqual(events[0]["actor"], {"id": "gateway", "kind": "service"})
        self.assertEqual(events[0]["payload"], {"model": "test-model"})

    def test_get_ledger_events_filtering(self):
        self._create_test_run()
        for i in range(3):
            self.db.save_ledger_event({
                "event_id": f"event-{i:03d}",
                "run_id": "run-001",
                "sequence": i,
                "timestamp": f"2024-01-0{i+1}T00:00:00Z",
                "actor": {"id": "gateway", "kind": "service"},
                "event_type": "route_selected" if i == 0 else "action_completed",
                "subject_id": "subj-001",
                "payload": {},
                "previous_event_hash": "GENESIS" if i == 0 else f"sha256:{i}",
                "event_hash": f"sha256:event{i}",
            })
        all_events = self.db.get_ledger_events(run_id="run-001")
        self.assertEqual(len(all_events), 3)

        filtered = self.db.get_ledger_events(event_type="route_selected")
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["event_type"], "route_selected")

    def test_get_ledger_chain(self):
        self._create_test_run()
        for i in range(3):
            self.db.save_ledger_event({
                "event_id": f"event-{i:03d}",
                "run_id": "run-001",
                "sequence": i,
                "timestamp": f"2024-01-0{i+1}T00:00:00Z",
                "actor": {"id": "gateway"},
                "event_type": "test_event",
                "subject_id": "subj-001",
                "payload": {},
                "previous_event_hash": "GENESIS" if i == 0 else f"sha256:{i}",
                "event_hash": f"sha256:event{i}",
            })
        chain = self.db.get_ledger_chain("run-001")
        self.assertEqual(len(chain), 3)
        self.assertEqual(chain[0]["sequence"], 0)
        self.assertEqual(chain[2]["sequence"], 2)

    def test_verify_ledger_chain(self):
        from epr.ledger import append_event

        self._create_test_run()
        events: list[dict] = []
        for i in range(3):
            event = {
                "event_id": f"event-{i:03d}",
                "run_id": "run-001",
                "timestamp": f"2024-01-0{i+1}T00:00:00Z",
                "actor": {"id": "gateway"},
                "event_type": "test_event",
                "subject_id": "subj-001",
                "payload": {},
            }
            appended = append_event(events, event)
            self.db.save_ledger_event(appended)

        result = self.db.verify_ledger_chain("run-001")
        self.assertTrue(result["valid"], f"Chain invalid: {result.get('message')}")
        self.assertEqual(result["event_count"], 3)

    # --- API key operations ---

    def test_create_and_get_api_key(self):
        key_id = self.db.create_api_key(
            key_hash="sha256:testhash123",
            name="test-key",
            role="operator",
        )
        self.assertTrue(key_id.startswith("key-"))

        key = self.db.get_api_key_by_hash("sha256:testhash123")
        self.assertIsNotNone(key)
        self.assertEqual(key["name"], "test-key")
        self.assertEqual(key["role"], "operator")

    def test_get_api_key_by_hash_nonexistent(self):
        self.assertIsNone(self.db.get_api_key_by_hash("nonexistent"))

    def test_list_api_keys(self):
        self.db.create_api_key("sha256:hash1", "key-1", "operator")
        self.db.create_api_key("sha256:hash2", "key-2", "admin")
        keys = self.db.list_api_keys()
        self.assertEqual(len(keys), 2)
        # Hash should not be in the listing
        self.assertNotIn("key_hash", keys[0])

    def test_revoke_api_key(self):
        key_id = self.db.create_api_key("sha256:hash1", "key-1", "operator")
        self.assertTrue(self.db.revoke_api_key(key_id))
        # Should not be findable after revocation
        self.assertIsNone(self.db.get_api_key_by_hash("sha256:hash1"))
        # Revoking again should return False
        self.assertFalse(self.db.revoke_api_key(key_id))

    def test_update_last_used(self):
        key_id = self.db.create_api_key("sha256:hash1", "key-1", "operator")
        self.db.update_last_used(key_id)
        key = self.db.get_api_key_by_hash("sha256:hash1")
        self.assertIsNotNone(key["last_used_at"])

    # --- Audit log operations ---

    def test_record_and_query_audit(self):
        audit_id = self.db.record_audit(
            actor_id="user-001",
            action="create_run",
            resource_type="run",
            resource_id="run-001",
            ip_address="127.0.0.1",
            details={"model": "test-model"},
            success=True,
        )
        self.assertTrue(audit_id.startswith("audit-"))

        entries = self.db.query_audit_log(actor_id="user-001")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["action"], "create_run")
        self.assertEqual(entries[0]["resource_type"], "run")

    def test_query_audit_log_filtering(self):
        self.db.record_audit("user-001", "action_a", "run", "run-001")
        self.db.record_audit("user-002", "action_b", "run", "run-002")
        self.db.record_audit("user-001", "action_c", "model", "model-001")

        by_actor = self.db.query_audit_log(actor_id="user-001")
        self.assertEqual(len(by_actor), 2)

        by_action = self.db.query_audit_log(action="action_b")
        self.assertEqual(len(by_action), 1)

    # --- Benchmark operations ---

    def test_save_and_get_benchmark_results(self):
        result_id = self.db.save_benchmark_result({
            "cohort_name": "coding-tasks",
            "model_id": "test-model",
            "accuracy": 0.95,
            "total_tokens": 1000,
            "total_cost_usd": 0.05,
            "mean_latency_ms": 200.0,
            "p95_latency_ms": 500.0,
            "hir": 0.05,
            "rr": 0.02,
        })
        self.assertTrue(result_id.startswith("bm-"))

        results = self.db.get_benchmark_results(cohort_name="coding-tasks")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["accuracy"], 0.95)

    def test_get_benchmark_results_filtering(self):
        self.db.save_benchmark_result({
            "cohort_name": "coding-tasks",
            "model_id": "model-a",
            "accuracy": 0.9,
        })
        self.db.save_benchmark_result({
            "cohort_name": "reasoning-tasks",
            "model_id": "model-b",
            "accuracy": 0.85,
        })

        by_cohort = self.db.get_benchmark_results(cohort_name="coding-tasks")
        self.assertEqual(len(by_cohort), 1)

        by_model = self.db.get_benchmark_results(model_id="model-b")
        self.assertEqual(len(by_model), 1)

    # --- Config operations ---

    def test_set_and_get_config(self):
        self.db.set_config("test_key", "test_value")
        self.assertEqual(self.db.get_config("test_key"), "test_value")

        self.db.set_config("complex_key", {"nested": True, "count": 42})
        self.assertEqual(self.db.get_config("complex_key"), {"nested": True, "count": 42})

    def test_get_config_nonexistent(self):
        self.assertIsNone(self.db.get_config("nonexistent"))

    def test_get_all_config(self):
        self.db.set_config("key1", "value1")
        self.db.set_config("key2", 42)
        all_config = self.db.get_all_config()
        self.assertEqual(all_config["key1"], "value1")
        self.assertEqual(all_config["key2"], 42)

    # --- Backup / Restore ---

    def test_backup_and_restore(self):
        self.db.save_run({
            "run_id": "run-001",
            "trace_id": "trace-001",
            "status": "accepted",
            "created_at": "2024-01-01T00:00:00Z",
        })
        self.db.set_config("test_key", "test_value")

        backup_path = str(Path(self._tmpdir.name) / "backup.db")
        result = self.db.backup(backup_path)
        self.assertEqual(result, backup_path)
        self.assertTrue(Path(backup_path).exists())

        # Create a new DB and restore
        new_db_path = str(Path(self._tmpdir.name) / "new.db")
        new_db = SQLiteDatabase(new_db_path)
        try:
            new_db.restore(backup_path)

            run = new_db.get_run("run-001")
            self.assertIsNotNone(run)
            self.assertEqual(run["status"], "accepted")
            self.assertEqual(new_db.get_config("test_key"), "test_value")
        finally:
            new_db.close()

    def test_restore_nonexistent_file(self):
        with self.assertRaises(FileNotFoundError):
            self.db.restore(str(Path(self._tmpdir.name) / "nonexistent" / "backup.db"))

    def test_export_json(self):
        self.db.save_run({
            "run_id": "run-001",
            "trace_id": "trace-001",
            "status": "accepted",
            "created_at": "2024-01-01T00:00:00Z",
        })
        self.db.set_config("test_key", "test_value")

        export_path = str(Path(self._tmpdir.name) / "export.json")
        result = self.db.export_json(export_path)
        self.assertEqual(result, export_path)

        with open(export_path, "r", encoding="utf-8") as f:
            data = json.loads(f.read())
        self.assertIn("runs", data)
        self.assertIn("config", data)
        self.assertEqual(len(data["runs"]), 1)
        self.assertEqual(data["config"]["test_key"], "test_value")

    # --- Analytics ---

    def test_get_run_stats(self):
        self.db.save_run({
            "run_id": "run-001", "trace_id": "t1", "status": "accepted",
            "created_at": "2024-01-01T00:00:00Z",
        })
        self.db.save_run({
            "run_id": "run-002", "trace_id": "t2", "status": "escalated",
            "created_at": "2024-01-02T00:00:00Z",
            "required_human_intervention": True,
        })
        self.db.save_run({
            "run_id": "run-003", "trace_id": "t3", "status": "failed",
            "created_at": "2024-01-03T00:00:00Z",
            "required_rework": True,
        })

        stats = self.db.get_run_stats()
        self.assertEqual(stats["runs_total"], 3)
        self.assertEqual(stats["runs_accepted"], 1)
        self.assertEqual(stats["runs_escalated"], 1)
        self.assertEqual(stats["runs_failed"], 1)
        self.assertEqual(stats["human_intervention_count"], 1)
        self.assertEqual(stats["rework_count"], 1)

    def test_get_cost_analytics(self):
        self.db.save_run({
            "run_id": "run-001", "trace_id": "t1", "status": "accepted",
            "created_at": "2024-01-01T00:00:00Z",
            "actual_cost_usd": 0.05, "total_tokens": 100,
            "model_id": "model-a", "risk_class": "low",
        })
        self.db.save_run({
            "run_id": "run-002", "trace_id": "t2", "status": "accepted",
            "created_at": "2024-01-02T00:00:00Z",
            "actual_cost_usd": 0.10, "total_tokens": 200,
            "model_id": "model-b", "risk_class": "medium",
        })

        analytics = self.db.get_cost_analytics()
        self.assertAlmostEqual(analytics["total_cost_usd"], 0.15)
        self.assertEqual(analytics["total_tokens"], 300)
        self.assertEqual(len(analytics["per_model"]), 2)
        self.assertEqual(len(analytics["per_risk_class"]), 2)

    def test_get_model_performance(self):
        self.db.save_run({
            "run_id": "run-001", "trace_id": "t1", "status": "accepted",
            "created_at": "2024-01-01T00:00:00Z",
            "model_id": "model-a", "latency_ms": 100.0,
            "actual_cost_usd": 0.01, "total_tokens": 50,
        })
        self.db.save_run({
            "run_id": "run-002", "trace_id": "t2", "status": "accepted",
            "created_at": "2024-01-02T00:00:00Z",
            "model_id": "model-a", "latency_ms": 200.0,
            "actual_cost_usd": 0.02, "total_tokens": 100,
        })

        perf = self.db.get_model_performance(model_id="model-a")
        self.assertEqual(len(perf), 1)
        self.assertEqual(perf[0]["total_runs"], 2)
        self.assertEqual(perf[0]["avg_latency_ms"], 150.0)

    # --- Thread safety ---

    def test_concurrent_access(self):
        errors = []

        def worker(worker_id: int):
            try:
                for i in range(10):
                    run_id = f"run-w{worker_id}-{i:03d}"
                    self.db.save_run({
                        "run_id": run_id,
                        "trace_id": f"trace-w{worker_id}-{i:03d}",
                        "status": "accepted",
                        "created_at": "2024-01-01T00:00:00Z",
                    })
                    retrieved = self.db.get_run(run_id)
                    if retrieved is None:
                        errors.append(f"Worker {worker_id}: run {run_id} not found")
            except Exception as exc:
                errors.append(f"Worker {worker_id}: {exc}")

        threads = []
        for w in range(5):
            t = threading.Thread(target=worker, args=(w,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Errors: {errors}")
        runs = self.db.list_runs(limit=100)
        self.assertEqual(len(runs), 50)


class DatabaseRegistryTests(unittest.TestCase):
    """Test DatabaseRunRegistry integration with pipeline."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmpdir.name) / "test.db")
        self.db = SQLiteDatabase(self.db_path)
        self.registry = DatabaseRunRegistry(self.db)

    def tearDown(self):
        self.db.close()
        self._tmpdir.cleanup()

    def test_begin_persists_to_db(self):
        record = self.registry.begin("run-001", "trace-001")
        self.assertEqual(record.run_id, "run-001")

        db_run = self.db.get_run("run-001")
        self.assertIsNotNone(db_run)
        self.assertEqual(db_run["trace_id"], "trace-001")
        self.assertEqual(db_run["status"], "pending")

    def test_ledger_persists_to_db(self):
        self.registry.begin("run-001", "trace-001")
        event = self.registry.ledger(
            "run-001",
            "route_selected",
            {"id": "gateway", "kind": "service"},
            "subj-001",
            {"model": "test-model"},
        )
        self.assertIn("event_hash", event)

        db_events = self.db.get_ledger_events(run_id="run-001")
        self.assertEqual(len(db_events), 1)
        self.assertEqual(db_events[0]["event_type"], "route_selected")

    def test_issue_receipt_persists_to_db(self):
        self.registry.begin("run-001", "trace-001")
        receipt = self.registry.issue_receipt(
            "run-001", "accepted", [], 0.05
        )
        self.assertEqual(receipt["status"], "accepted")

        db_run = self.db.get_run("run-001")
        self.assertIsNotNone(db_run)
        self.assertEqual(db_run["status"], "accepted")
        self.assertIsNotNone(db_run["receipt"])

    def test_get_falls_through_to_db(self):
        # Save directly to DB, then retrieve via registry
        self.db.save_run({
            "run_id": "run-001",
            "trace_id": "trace-001",
            "status": "accepted",
            "created_at": "2024-01-01T00:00:00Z",
            "total_tokens": 100,
            "receipt": {"receipt_id": "r-001", "status": "accepted"},
        })

        record = self.registry.get("run-001")
        self.assertIsNotNone(record)
        self.assertEqual(record.run_id, "run-001")
        self.assertEqual(record.total_tokens, 100)

        receipt = self.registry.get_receipt("run-001")
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt["receipt_id"], "r-001")

    def test_get_receipt_nonexistent(self):
        self.assertIsNone(self.registry.get_receipt("nonexistent"))

    def test_record_human_intervention_persists(self):
        self.registry.begin("run-001", "trace-001")
        self.registry.record_human_intervention("run-001", "needs review")

        db_run = self.db.get_run("run-001")
        self.assertTrue(db_run["required_human_intervention"])
        self.assertEqual(db_run["human_intervention_reason"], "needs review")

    def test_record_rework_persists(self):
        self.registry.begin("run-001", "trace-001")
        self.registry.record_rework("run-001", "needs rework")

        db_run = self.db.get_run("run-001")
        self.assertTrue(db_run["required_rework"])
        self.assertEqual(db_run["rework_reason"], "needs rework")

    def test_inherits_in_memory_behavior(self):
        """DatabaseRunRegistry should behave like RunRegistry for in-memory ops."""
        # Test that begin creates a record accessible via get
        record = self.registry.begin("run-001", "trace-001")
        self.assertEqual(self.registry.get("run-001"), record)

        # Test that ledger appends events
        event = self.registry.ledger(
            "run-001", "test_event",
            {"id": "gw"}, "subj-001", {"key": "value"},
        )
        self.assertEqual(len(record.events), 1)
        self.assertEqual(record.events[0]["event_type"], "test_event")

        # Test head_hash
        head = self.registry.head_hash("run-001")
        self.assertNotEqual(head, "GENESIS")

        # Test issue_receipt
        receipt = self.registry.issue_receipt("run-001", "accepted", [], 0.0)
        self.assertEqual(receipt["status"], "accepted")
        self.assertEqual(self.registry.get_receipt("run-001"), receipt)


class StructuredLoggerTests(unittest.TestCase):
    """Test JSON log format, levels, extra fields."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._log_path = str(Path(self._tmpdir.name) / "test.log")

    def tearDown(self):
        # Close all logging handlers to release file handles
        root = logging.getLogger()
        for handler in root.handlers[:]:
            handler.close()
            root.removeHandler(handler)
        self._tmpdir.cleanup()

    def test_json_formatter_produces_valid_json(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="test message", args=(), exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        self.assertEqual(parsed["level"], "INFO")
        self.assertEqual(parsed["message"], "test message")
        self.assertIn("timestamp", parsed)

    def test_json_formatter_passes_through_json_strings(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg='{"already": "json"}', args=(), exc_info=None,
        )
        output = formatter.format(record)
        self.assertEqual(output, '{"already": "json"}')

    def test_structured_logger_info(self):
        logger = StructuredLogger(
            name="test-logger-info",
            level="DEBUG",
            output="file",
            file_path=self._log_path,
        )
        logger.info("test info message", correlation_id="run-001", component="gateway")
        # Close handler to flush
        for h in logger._logger.handlers:
            h.close()

        with open(self._log_path, "r", encoding="utf-8") as f:
            line = f.readline().strip()

        parsed = json.loads(line)
        self.assertEqual(parsed["level"], "INFO")
        self.assertEqual(parsed["message"], "test info message")
        self.assertEqual(parsed["correlation_id"], "run-001")
        self.assertEqual(parsed["component"], "gateway")

    def test_structured_logger_levels(self):
        log_path_levels = str(Path(self._tmpdir.name) / "levels.log")
        logger = StructuredLogger(
            name="test-logger-levels",
            level="DEBUG",
            output="file",
            file_path=log_path_levels,
        )
        logger.debug("debug message")
        logger.info("info message")
        logger.warning("warning message")
        logger.error("error message")
        # Close handler to flush
        for h in logger._logger.handlers:
            h.close()

        with open(log_path_levels, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]

        self.assertEqual(len(lines), 4)
        levels = [l["level"] for l in lines]
        self.assertEqual(levels, ["DEBUG", "INFO", "WARNING", "ERROR"])

    def test_structured_logger_stdout(self):
        logger = StructuredLogger(
            name="test-logger-stdout",
            level="INFO",
            output="stdout",
        )
        # Should not raise
        logger.info("stdout test", extra_field="value")

    def test_structured_logger_extra_fields(self):
        log_path_extra = str(Path(self._tmpdir.name) / "extra.log")
        logger = StructuredLogger(
            name="test-logger-extra",
            level="INFO",
            output="file",
            file_path=log_path_extra,
        )
        logger.info("cost event", model_id="test-model", cost=0.05, latency_ms=200.0)
        # Close handler to flush
        for h in logger._logger.handlers:
            h.close()

        with open(log_path_extra, "r", encoding="utf-8") as f:
            parsed = json.loads(f.readline().strip())

        self.assertEqual(parsed["model_id"], "test-model")
        self.assertEqual(parsed["cost"], 0.05)
        self.assertEqual(parsed["latency_ms"], 200.0)


class ConfigNewFieldsTests(unittest.TestCase):
    """Test the new config fields added in Phase 1."""

    def test_default_database_config(self):
        config = GatewayConfig.from_env({})
        self.assertEqual(config.database_path, ".noerelay/noerelay.db")
        self.assertTrue(config.database_enabled)

    def test_default_logging_config(self):
        config = GatewayConfig.from_env({})
        self.assertEqual(config.log_level, "INFO")
        self.assertEqual(config.log_output, "stdout")
        self.assertEqual(config.log_file_path, ".noerelay/noerelay.log")

    def test_default_tls_config(self):
        config = GatewayConfig.from_env({})
        self.assertFalse(config.tls_enabled)
        self.assertIsNone(config.tls_cert_path)
        self.assertIsNone(config.tls_key_path)

    def test_custom_database_config(self):
        config = GatewayConfig.from_env({
            "NOERELAY_DATABASE_PATH": "/custom/path/db.sqlite",
            "NOERELAY_DATABASE_ENABLED": "0",
        })
        self.assertEqual(config.database_path, "/custom/path/db.sqlite")
        self.assertFalse(config.database_enabled)

    def test_custom_logging_config(self):
        config = GatewayConfig.from_env({
            "NOERELAY_LOG_LEVEL": "DEBUG",
            "NOERELAY_LOG_OUTPUT": "file",
            "NOERELAY_LOG_FILE_PATH": "/var/log/noerelay.log",
        })
        self.assertEqual(config.log_level, "DEBUG")
        self.assertEqual(config.log_output, "file")
        self.assertEqual(config.log_file_path, "/var/log/noerelay.log")

    def test_invalid_log_level_raises(self):
        with self.assertRaises(ConfigError):
            GatewayConfig.from_env({"NOERELAY_LOG_LEVEL": "TRACE"})

    def test_invalid_log_output_raises(self):
        with self.assertRaises(ConfigError):
            GatewayConfig.from_env({"NOERELAY_LOG_OUTPUT": "syslog"})

    def test_tls_config(self):
        with tempfile.TemporaryDirectory() as directory:
            cert_path = Path(directory) / "cert.pem"
            key_path = Path(directory) / "key.pem"
            cert_path.write_text("test certificate", encoding="utf-8")
            key_path.write_text("test key", encoding="utf-8")
            config = GatewayConfig.from_env({
                "NOERELAY_TLS_ENABLED": "1",
                "NOERELAY_TLS_CERT_PATH": str(cert_path),
                "NOERELAY_TLS_KEY_PATH": str(key_path),
            })
            self.assertTrue(config.tls_enabled)
            self.assertEqual(Path(config.tls_cert_path), cert_path)
            self.assertEqual(Path(config.tls_key_path), key_path)


class TLSTests(unittest.TestCase):
    """Test TLS config (without actual TLS handshake)."""

    def test_tls_disabled_by_default(self):
        config = GatewayConfig.from_env({})
        self.assertFalse(config.tls_enabled)

    def test_tls_config_fields_present(self):
        with tempfile.TemporaryDirectory() as directory:
            cert_path = Path(directory) / "cert.pem"
            key_path = Path(directory) / "key.pem"
            cert_path.touch()
            key_path.touch()
            config = GatewayConfig.from_env({
                "NOERELAY_TLS_ENABLED": "1",
                "NOERELAY_TLS_CERT_PATH": str(cert_path),
                "NOERELAY_TLS_KEY_PATH": str(key_path),
            })
            self.assertTrue(config.tls_enabled)
            self.assertIsNotNone(config.tls_cert_path)
            self.assertIsNotNone(config.tls_key_path)

    def test_tls_relative_paths_resolved(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            temp_root = Path(directory)
            cert_path = temp_root / "cert.pem"
            key_path = temp_root / "key.pem"
            cert_path.touch()
            key_path.touch()
            cert_relative = cert_path.relative_to(ROOT)
            key_relative = key_path.relative_to(ROOT)
            config = GatewayConfig.from_env({
                "NOERELAY_TLS_ENABLED": "1",
                "NOERELAY_TLS_CERT_PATH": str(cert_relative),
                "NOERELAY_TLS_KEY_PATH": str(key_relative),
            })
            self.assertEqual(Path(config.tls_cert_path), cert_path)
            self.assertEqual(Path(config.tls_key_path), key_path)


class BackupRestoreTests(unittest.TestCase):
    """Test backup and restore functionality."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmpdir.name) / "test.db")
        self.db = SQLiteDatabase(self.db_path)

    def tearDown(self):
        self.db.close()
        self._tmpdir.cleanup()

    def test_backup_creates_file(self):
        self.db.save_run({
            "run_id": "run-001",
            "trace_id": "trace-001",
            "status": "accepted",
            "created_at": "2024-01-01T00:00:00Z",
        })
        backup_path = str(Path(self._tmpdir.name) / "backup.db")
        result = self.db.backup(backup_path)
        self.assertTrue(Path(result).exists())

    def test_restore_recovers_data(self):
        self.db.save_run({
            "run_id": "run-001",
            "trace_id": "trace-001",
            "status": "accepted",
            "created_at": "2024-01-01T00:00:00Z",
        })
        self.db.set_config("key1", "value1")

        backup_path = str(Path(self._tmpdir.name) / "backup.db")
        self.db.backup(backup_path)

        # New DB
        new_db_path = str(Path(self._tmpdir.name) / "new.db")
        new_db = SQLiteDatabase(new_db_path)
        try:
            new_db.restore(backup_path)

            run = new_db.get_run("run-001")
            self.assertIsNotNone(run)
            self.assertEqual(run["status"], "accepted")
            self.assertEqual(new_db.get_config("key1"), "value1")
        finally:
            new_db.close()

    def test_export_json_comprehensive(self):
        self.db.save_run({
            "run_id": "run-001",
            "trace_id": "trace-001",
            "status": "accepted",
            "created_at": "2024-01-01T00:00:00Z",
        })
        self.db.save_ledger_event({
            "event_id": "event-001",
            "run_id": "run-001",
            "sequence": 0,
            "timestamp": "2024-01-01T00:00:00Z",
            "actor": {"id": "gw"},
            "event_type": "test",
            "subject_id": "subj-001",
            "payload": {},
            "previous_event_hash": "GENESIS",
            "event_hash": "sha256:abc",
        })
        self.db.set_config("key1", "value1")

        export_path = str(Path(self._tmpdir.name) / "export.json")
        result = self.db.export_json(export_path)

        with open(result, "r", encoding="utf-8") as f:
            data = json.loads(f.read())

        self.assertEqual(len(data["runs"]), 1)
        self.assertEqual(len(data["ledger_events"]), 1)
        self.assertEqual(data["config"]["key1"], "value1")
        self.assertIn("exported_at", data)


class GracefulShutdownTests(unittest.TestCase):
    """Test shutdown flag and request counting."""

    def test_shutdown_state_initial(self):
        state = _ShutdownState()
        self.assertFalse(state.shutdown_requested)
        self.assertEqual(state.active_requests, 0)

    def test_shutdown_state_flag(self):
        state = _ShutdownState()
        with state.lock:
            state.shutdown_requested = True
        self.assertTrue(state.shutdown_requested)

    def test_active_request_counting(self):
        state = _ShutdownState()
        with state.lock:
            state.active_requests += 1
        self.assertEqual(state.active_requests, 1)

        with state.lock:
            state.active_requests -= 1
        self.assertEqual(state.active_requests, 0)

    def test_shutdown_state_thread_safety(self):
        state = _ShutdownState()
        errors = []

        def increment():
            try:
                for _ in range(100):
                    with state.lock:
                        state.active_requests += 1
                    with state.lock:
                        state.active_requests -= 1
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=increment) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        self.assertEqual(state.active_requests, 0)


class PipelineContextLoggerTests(unittest.TestCase):
    """Test that PipelineContext includes logger when built from config."""

    def test_pipeline_context_has_logger(self):
        config = GatewayConfig.from_env({})
        ctx = build_pipeline_context(config)
        self.assertIsNotNone(ctx.logger)
        self.assertTrue(hasattr(ctx.logger, "info"))
        self.assertTrue(hasattr(ctx.logger, "warning"))
        self.assertTrue(hasattr(ctx.logger, "error"))

    def test_pipeline_context_uses_database_registry(self):
        config = GatewayConfig.from_env({
            "NOERELAY_DATABASE_ENABLED": "1",
            "NOERELAY_DATABASE_PATH": ".noerelay/test_pipeline.db",
        })
        ctx = None
        try:
            ctx = build_pipeline_context(config)
            from gateway.db_registry import DatabaseRunRegistry
            self.assertIsInstance(ctx.registry, DatabaseRunRegistry)
        finally:
            if ctx is not None and hasattr(ctx.registry, "_db"):
                ctx.registry._db.close()
            # Cleanup
            db_path = Path(".noerelay/test_pipeline.db")
            if db_path.exists():
                db_path.unlink()
            for suffix in ("-wal", "-shm"):
                wal = Path(f".noerelay/test_pipeline.db{suffix}")
                if wal.exists():
                    wal.unlink()

    def test_pipeline_context_falls_back_to_memory(self):
        config = GatewayConfig.from_env({
            "NOERELAY_DATABASE_ENABLED": "0",
        })
        ctx = build_pipeline_context(config)
        from gateway.runs import RunRegistry
        from gateway.db_registry import DatabaseRunRegistry
        self.assertIsInstance(ctx.registry, RunRegistry)
        self.assertNotIsInstance(ctx.registry, DatabaseRunRegistry)


if __name__ == "__main__":
    unittest.main()
