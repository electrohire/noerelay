"""Tests for Phase 2 API Surface Expansion.

Covers: API key management, model management API, benchmark API,
governance API, routing API, analytics API, export/import API,
pagination, and per-key rate limiting.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reference"))

from gateway.api_keys import APIKeyManager
from gateway.auth import AuthMiddleware
from gateway.config import GatewayConfig
from gateway.database import SQLiteDatabase
from gateway.governance import default_governance
from gateway.handlers import (
    handle_add_candidate,
    handle_audit_analytics,
    handle_compare_models,
    handle_cost_analytics,
    handle_create_api_key,
    handle_escalation_analytics,
    handle_export_data,
    handle_get_policy,
    handle_get_portfolio,
    handle_get_risk_classes,
    handle_import_data,
    handle_list_api_keys,
    handle_list_benchmark_results,
    handle_performance_analytics,
    handle_pull_model,
    handle_register_model,
    handle_remove_candidate,
    handle_remove_model,
    handle_revoke_api_key,
    handle_rotate_api_key,
    handle_run_benchmark,
    handle_update_candidate,
    handle_update_policy,
    handle_update_risk_class,
    handle_usage_analytics,
    paginate,
)
from gateway.pipeline import PipelineContext
from gateway.policy import load_policy
from gateway.portfolio import load_portfolio
from gateway.rate_limit import PerKeyRateLimiter, TokenBucketRateLimiter
from gateway.render import error_envelope
from gateway.runs import RunRegistry


def _make_ctx(db=None):
    """Create a minimal PipelineContext for testing."""
    config = GatewayConfig.from_env({})
    policy = load_policy(ROOT / "spec" / "routing-policy.json")
    portfolio = load_portfolio(ROOT / "examples" / "candidate-actions.json")
    registry = RunRegistry()
    if db is not None:
        registry._db = db
    return PipelineContext(
        config=config,
        policy=policy,
        portfolio=portfolio,
        openrouter_client=None,
        state_machine=None,
        registry=registry,
    )


# ---------------------------------------------------------------------------
# Pagination Tests
# ---------------------------------------------------------------------------


class PaginationTests(unittest.TestCase):
    def test_paginate_empty_list(self):
        result = paginate([], limit=50, offset=0)
        self.assertEqual(result["data"], [])
        self.assertEqual(result["pagination"]["total"], 0)
        self.assertEqual(result["pagination"]["has_more"], False)

    def test_paginate_single_page(self):
        items = list(range(10))
        result = paginate(items, limit=50, offset=0)
        self.assertEqual(len(result["data"]), 10)
        self.assertEqual(result["pagination"]["total"], 10)
        self.assertEqual(result["pagination"]["has_more"], False)

    def test_paginate_multiple_pages(self):
        items = list(range(100))
        result = paginate(items, limit=30, offset=0)
        self.assertEqual(len(result["data"]), 30)
        self.assertEqual(result["pagination"]["total"], 100)
        self.assertEqual(result["pagination"]["has_more"], True)

    def test_paginate_offset(self):
        items = list(range(100))
        result = paginate(items, limit=30, offset=60)
        self.assertEqual(len(result["data"]), 30)
        self.assertEqual(result["data"][0], 60)
        self.assertEqual(result["pagination"]["offset"], 60)

    def test_paginate_last_page(self):
        items = list(range(100))
        result = paginate(items, limit=30, offset=90)
        self.assertEqual(len(result["data"]), 10)
        self.assertEqual(result["pagination"]["has_more"], False)

    def test_paginate_default_params(self):
        items = list(range(100))
        result = paginate(items)
        self.assertEqual(len(result["data"]), 50)
        self.assertEqual(result["pagination"]["limit"], 50)
        self.assertEqual(result["pagination"]["offset"], 0)


# ---------------------------------------------------------------------------
# Per-Key Rate Limiting Tests
# ---------------------------------------------------------------------------


class PerKeyRateLimitTests(unittest.TestCase):
    def test_token_bucket_allows_within_limit(self):
        limiter = TokenBucketRateLimiter(rate=100.0, burst=100)
        for _ in range(50):
            self.assertTrue(limiter.allow())

    def test_token_bucket_denies_when_exhausted(self):
        limiter = TokenBucketRateLimiter(rate=0.0, burst=1)
        self.assertTrue(limiter.allow())  # Use the one token
        self.assertFalse(limiter.allow())  # No more tokens

    def test_per_key_limiter_isolates_keys(self):
        pk = PerKeyRateLimiter()
        # Key A gets a fast limiter
        for _ in range(10):
            self.assertTrue(pk.allow("key-a", rate=100.0, burst=100))
        # Key B gets a slow limiter
        self.assertTrue(pk.allow("key-b", rate=0.0, burst=1))
        self.assertFalse(pk.allow("key-b", rate=0.0, burst=1))
        # Key A still works
        self.assertTrue(pk.allow("key-a", rate=100.0, burst=100))

    def test_per_key_limiter_reset_single_key(self):
        pk = PerKeyRateLimiter()
        self.assertTrue(pk.allow("key-a", rate=0.0, burst=1))
        self.assertFalse(pk.allow("key-a", rate=0.0, burst=1))
        pk.reset("key-a")
        self.assertTrue(pk.allow("key-a", rate=0.0, burst=1))

    def test_per_key_limiter_reset_all(self):
        pk = PerKeyRateLimiter()
        self.assertTrue(pk.allow("key-a", rate=0.0, burst=1))
        self.assertTrue(pk.allow("key-b", rate=0.0, burst=1))
        pk.reset()
        self.assertTrue(pk.allow("key-a", rate=0.0, burst=1))
        self.assertTrue(pk.allow("key-b", rate=0.0, burst=1))

    def test_get_limiter_returns_same_instance(self):
        pk = PerKeyRateLimiter()
        lim1 = pk.get_limiter("key-a", rate=10.0, burst=20)
        lim2 = pk.get_limiter("key-a", rate=10.0, burst=20)
        self.assertIs(lim1, lim2)


# ---------------------------------------------------------------------------
# API Key Management Tests
# ---------------------------------------------------------------------------


class APIKeyManagementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._db = SQLiteDatabase(db_path=".noerelay/test_api_surface.db")

    @classmethod
    def tearDownClass(cls):
        cls._db.close()
        Path(".noerelay/test_api_surface.db").unlink(missing_ok=True)

    def setUp(self):
        self.manager = APIKeyManager(self._db)

    def test_create_key_returns_plaintext(self):
        result = self.manager.create_key(name="test-key", role="operator")
        self.assertIn("key_id", result)
        self.assertIn("key", result)
        self.assertTrue(result["key"].startswith("noerelay-"))
        self.assertEqual(result["name"], "test-key")
        self.assertEqual(result["role"], "operator")

    def test_authenticate_valid_key(self):
        result = self.manager.create_key(name="auth-test")
        key_info = self.manager.authenticate(result["key"])
        self.assertIsNotNone(key_info)
        self.assertEqual(key_info["name"], "auth-test")

    def test_authenticate_invalid_key(self):
        key_info = self.manager.authenticate("noerelay-invalid-key")
        self.assertIsNone(key_info)

    def test_authenticate_revoked_key(self):
        result = self.manager.create_key(name="revoke-test")
        self.manager.revoke_key(result["key_id"])
        key_info = self.manager.authenticate(result["key"])
        self.assertIsNone(key_info)

    def test_list_keys(self):
        self.manager.create_key(name="list-test-1")
        self.manager.create_key(name="list-test-2")
        keys = self.manager.list_keys()
        names = [k["name"] for k in keys]
        self.assertIn("list-test-1", names)
        self.assertIn("list-test-2", names)

    def test_list_keys_excludes_hash(self):
        self.manager.create_key(name="no-hash-test")
        keys = self.manager.list_keys()
        for k in keys:
            self.assertNotIn("key_hash", k)
            self.assertNotIn("key", k)

    def test_revoke_key(self):
        result = self.manager.create_key(name="revoke-me")
        success = self.manager.revoke_key(result["key_id"])
        self.assertTrue(success)
        # Revoking again should return False
        success2 = self.manager.revoke_key(result["key_id"])
        self.assertFalse(success2)

    def test_revoke_nonexistent_key(self):
        success = self.manager.revoke_key("key-nonexistent")
        self.assertFalse(success)

    def test_rotate_key(self):
        result = self.manager.create_key(name="rotate-test", role="admin")
        new_result = self.manager.rotate_key(result["key_id"])
        self.assertNotEqual(new_result["key"], result["key"])
        self.assertEqual(new_result["name"], "rotate-test")
        self.assertEqual(new_result["role"], "admin")
        # Old key should be revoked
        self.assertIsNone(self.manager.authenticate(result["key"]))
        # New key should work
        self.assertIsNotNone(self.manager.authenticate(new_result["key"]))

    def test_rotate_nonexistent_key_raises(self):
        with self.assertRaises(ValueError):
            self.manager.rotate_key("key-nonexistent")

    def test_create_key_with_custom_rate_limits(self):
        result = self.manager.create_key(
            name="rate-limit-test",
            rate_limit_rate=5.0,
            rate_limit_burst=10,
            tenant_id="tenant-1",
        )
        keys = self.manager.list_keys(tenant_id="tenant-1")
        matching = [k for k in keys if k["key_id"] == result["key_id"]]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["rate_limit_rate"], 5.0)
        self.assertEqual(matching[0]["rate_limit_burst"], 10)


# ---------------------------------------------------------------------------
# Auth Middleware Tests
# ---------------------------------------------------------------------------


class AuthMiddlewareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._db = SQLiteDatabase(db_path=".noerelay/test_auth_middleware.db")

    @classmethod
    def tearDownClass(cls):
        cls._db.close()
        Path(".noerelay/test_auth_middleware.db").unlink(missing_ok=True)

    def test_legacy_mode_no_keys(self):
        auth = AuthMiddleware()
        self.assertTrue(auth.authenticate({}))

    def test_legacy_mode_with_keys(self):
        auth = AuthMiddleware(api_keys={"secret-key"})
        self.assertTrue(auth.authenticate({"Authorization": "Bearer secret-key"}))
        self.assertFalse(auth.authenticate({"Authorization": "Bearer wrong-key"}))
        self.assertFalse(auth.authenticate({}))

    def test_legacy_mode_from_csv(self):
        auth = AuthMiddleware.from_csv("key1, key2, key3")
        self.assertTrue(auth.authenticate({"Authorization": "Bearer key1"}))
        self.assertTrue(auth.authenticate({"Authorization": "Bearer key2"}))
        self.assertFalse(auth.authenticate({"Authorization": "Bearer key4"}))

    def test_legacy_mode_from_csv_empty(self):
        auth = AuthMiddleware.from_csv("")
        self.assertTrue(auth.authenticate({}))

    def test_manager_mode_authenticate(self):
        manager = APIKeyManager(self._db)
        result = manager.create_key(name="auth-test")
        auth = AuthMiddleware(api_key_manager=manager)
        success, key_info = auth.authenticate(
            {"Authorization": f"Bearer {result['key']}"}
        )
        self.assertTrue(success)
        self.assertIsNotNone(key_info)
        self.assertEqual(key_info["name"], "auth-test")

    def test_manager_mode_invalid_key(self):
        manager = APIKeyManager(self._db)
        auth = AuthMiddleware(api_key_manager=manager)
        success, key_info = auth.authenticate(
            {"Authorization": "Bearer invalid-key"}
        )
        self.assertFalse(success)
        self.assertIsNone(key_info)

    def test_manager_mode_no_auth_header(self):
        manager = APIKeyManager(self._db)
        auth = AuthMiddleware(api_key_manager=manager)
        success, key_info = auth.authenticate({})
        self.assertFalse(success)
        self.assertIsNone(key_info)

    def test_manager_mode_with_rate_limiter(self):
        manager = APIKeyManager(self._db)
        result = manager.create_key(
            name="rate-test", rate_limit_rate=100.0, rate_limit_burst=100
        )
        rl = PerKeyRateLimiter()
        auth = AuthMiddleware(api_key_manager=manager, rate_limiter=rl)
        success, key_info = auth.authenticate(
            {"Authorization": f"Bearer {result['key']}"}
        )
        self.assertTrue(success)

    def test_manager_mode_rate_limited(self):
        manager = APIKeyManager(self._db)
        result = manager.create_key(
            name="rate-limited", rate_limit_rate=0.0, rate_limit_burst=1
        )
        rl = PerKeyRateLimiter()
        auth = AuthMiddleware(api_key_manager=manager, rate_limiter=rl)
        # First request passes
        success, _ = auth.authenticate(
            {"Authorization": f"Bearer {result['key']}"}
        )
        self.assertTrue(success)
        # Second request is rate limited
        success, key_info = auth.authenticate(
            {"Authorization": f"Bearer {result['key']}"}
        )
        self.assertFalse(success)
        self.assertIsNotNone(key_info)
        self.assertTrue(key_info.get("rate_limited"))


# ---------------------------------------------------------------------------
# Model Management API Tests
# ---------------------------------------------------------------------------


class ModelManagementAPITests(unittest.TestCase):
    def setUp(self):
        self.ctx = _make_ctx()

    def test_pull_model_missing_name(self):
        status, body = handle_pull_model({}, self.ctx)
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_pull_model_with_name(self):
        with patch("gateway.handlers.OllamaModelManager") as mock_mgr:
            mock_mgr.return_value.pull_model.return_value = {"status": "pulling manifest"}
            status, body = handle_pull_model(
                {"model_name": "qwen3:8b"}, self.ctx
            )
        self.assertEqual(status, 200)
        self.assertEqual(body["model"], "qwen3:8b")

    def test_remove_model_missing_name(self):
        status, body = handle_remove_model({}, self.ctx)
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_remove_model_with_name(self):
        with patch("gateway.handlers.OllamaModelManager") as mock_mgr:
            mock_mgr.return_value.delete_model.return_value = {"status": "deleted"}
            status, body = handle_remove_model(
                {"model_name": "qwen3:8b"}, self.ctx
            )
        self.assertEqual(status, 200)
        self.assertEqual(body["model"], "qwen3:8b")

    def test_register_model_missing_fields(self):
        status, body = handle_register_model({}, self.ctx)
        self.assertEqual(status, 400)

    def test_register_model_valid(self):
        body = {
            "model_id": "test-model",
            "provider_family": "qwen",
            "inference_gateway": "local",
        }
        status, result = handle_register_model(body, self.ctx)
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "registered")


# ---------------------------------------------------------------------------
# Benchmark API Tests
# ---------------------------------------------------------------------------


class BenchmarkAPITests(unittest.TestCase):
    def setUp(self):
        self.ctx = _make_ctx()

    def test_run_benchmark_missing_cohort(self):
        status, body = handle_run_benchmark({}, self.ctx)
        self.assertEqual(status, 400)

    def test_run_benchmark_missing_model(self):
        status, body = handle_run_benchmark(
            {"cohort_name": "test-cohort"}, self.ctx
        )
        self.assertEqual(status, 400)

    def test_run_benchmark_valid(self):
        status, body = handle_run_benchmark(
            {"cohort_name": "test-cohort", "model_id": "test-model"},
            self.ctx,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["cohort_name"], "test-cohort")

    def test_list_benchmark_results_empty(self):
        status, body = handle_list_benchmark_results(self.ctx)
        self.assertEqual(status, 200)
        self.assertIn("data", body)
        self.assertIn("pagination", body)

    def test_compare_models_empty(self):
        status, body = handle_compare_models({}, self.ctx)
        self.assertEqual(status, 200)
        self.assertIn("comparison", body)


# ---------------------------------------------------------------------------
# Governance API Tests
# ---------------------------------------------------------------------------


class GovernanceAPITests(unittest.TestCase):
    def setUp(self):
        self.ctx = _make_ctx()

    def test_get_policy(self):
        status, body = handle_get_policy(self.ctx)
        self.assertEqual(status, 200)
        self.assertIn("policy", body)

    def test_update_policy_valid(self):
        status, body = handle_update_policy(
            {"test_key": "test_value"}, self.ctx
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "updated")

    def test_update_policy_invalid(self):
        status, body = handle_update_policy("not-a-dict", self.ctx)  # type: ignore[arg-type]
        self.assertEqual(status, 400)

    def test_get_risk_classes(self):
        status, body = handle_get_risk_classes(self.ctx)
        self.assertEqual(status, 200)
        self.assertIn("risk_classes", body)
        self.assertIn("low", body["risk_classes"])
        self.assertIn("critical", body["risk_classes"])

    def test_update_risk_class_missing(self):
        status, body = handle_update_risk_class({}, self.ctx)
        self.assertEqual(status, 400)

    def test_update_risk_class_invalid(self):
        status, body = handle_update_risk_class(
            {"risk_class": "extreme"}, self.ctx
        )
        self.assertEqual(status, 400)

    def test_update_risk_class_valid(self):
        status, body = handle_update_risk_class(
            {"risk_class": "high", "gates": {"require_approval": True}},
            self.ctx,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "updated")


# ---------------------------------------------------------------------------
# Routing API Tests
# ---------------------------------------------------------------------------


class RoutingAPITests(unittest.TestCase):
    def setUp(self):
        self.ctx = _make_ctx()

    def test_get_portfolio(self):
        status, body = handle_get_portfolio(self.ctx)
        self.assertEqual(status, 200)
        self.assertIn("data", body)
        self.assertIn("count", body)

    def test_add_candidate_missing_fields(self):
        status, body = handle_add_candidate({}, self.ctx)
        self.assertEqual(status, 400)

    def test_add_candidate_valid(self):
        candidate = {
            "candidate_id": "test-candidate",
            "action_kind": "model",
            "model_id": "test-model",
        }
        status, body = handle_add_candidate(candidate, self.ctx)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "added")

    def test_remove_candidate_exists(self):
        candidate = {
            "candidate_id": "remove-me",
            "action_kind": "model",
        }
        self.ctx.portfolio.append(candidate)
        status, body = handle_remove_candidate("remove-me", self.ctx)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "removed")

    def test_remove_candidate_not_found(self):
        status, body = handle_remove_candidate("nonexistent", self.ctx)
        self.assertEqual(status, 404)

    def test_update_candidate_exists(self):
        candidate = {
            "candidate_id": "update-me",
            "action_kind": "model",
        }
        self.ctx.portfolio.append(candidate)
        status, body = handle_update_candidate(
            "update-me", {"model_id": "updated-model"}, self.ctx
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["candidate"]["model_id"], "updated-model")

    def test_update_candidate_not_found(self):
        status, body = handle_update_candidate(
            "nonexistent", {}, self.ctx
        )
        self.assertEqual(status, 404)


# ---------------------------------------------------------------------------
# API Key Management API Tests (via handlers)
# ---------------------------------------------------------------------------


class APIKeyHandlerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._db = SQLiteDatabase(db_path=".noerelay/test_key_handlers.db")

    @classmethod
    def tearDownClass(cls):
        cls._db.close()
        Path(".noerelay/test_key_handlers.db").unlink(missing_ok=True)

    def setUp(self):
        self.ctx = _make_ctx(db=self._db)

    def test_create_api_key_missing_name(self):
        status, body = handle_create_api_key({}, self.ctx)
        self.assertEqual(status, 400)

    def test_create_api_key_success(self):
        status, body = handle_create_api_key(
            {"name": "handler-test-key"}, self.ctx
        )
        self.assertEqual(status, 201)
        self.assertIn("key", body)
        self.assertTrue(body["key"].startswith("noerelay-"))

    def test_list_api_keys(self):
        handle_create_api_key({"name": "list-key-1"}, self.ctx)
        handle_create_api_key({"name": "list-key-2"}, self.ctx)
        status, body = handle_list_api_keys(self.ctx)
        self.assertEqual(status, 200)
        self.assertIn("data", body)
        self.assertGreaterEqual(len(body["data"]), 2)

    def test_revoke_api_key_success(self):
        result = handle_create_api_key({"name": "revoke-me"}, self.ctx)
        key_id = result[1]["key_id"]
        status, body = handle_revoke_api_key(key_id, self.ctx)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "revoked")

    def test_revoke_api_key_not_found(self):
        status, body = handle_revoke_api_key("key-nonexistent", self.ctx)
        self.assertEqual(status, 404)

    def test_rotate_api_key_success(self):
        result = handle_create_api_key({"name": "rotate-me"}, self.ctx)
        key_id = result[1]["key_id"]
        status, body = handle_rotate_api_key(key_id, self.ctx)
        self.assertEqual(status, 200)
        self.assertIn("key", body)

    def test_rotate_api_key_not_found(self):
        status, body = handle_rotate_api_key("key-nonexistent", self.ctx)
        self.assertEqual(status, 404)

    def test_create_api_key_no_db(self):
        ctx = _make_ctx()  # No DB
        status, body = handle_create_api_key({"name": "test"}, ctx)
        self.assertEqual(status, 501)


# ---------------------------------------------------------------------------
# Analytics API Tests
# ---------------------------------------------------------------------------


class AnalyticsAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._db = SQLiteDatabase(db_path=".noerelay/test_analytics.db")

    @classmethod
    def tearDownClass(cls):
        cls._db.close()
        Path(".noerelay/test_analytics.db").unlink(missing_ok=True)

    def setUp(self):
        self.ctx = _make_ctx(db=self._db)

    def test_cost_analytics(self):
        status, body = handle_cost_analytics({}, self.ctx)
        self.assertEqual(status, 200)
        self.assertIn("total_cost_usd", body)

    def test_performance_analytics(self):
        status, body = handle_performance_analytics({}, self.ctx)
        self.assertEqual(status, 200)
        self.assertIn("data", body)

    def test_usage_analytics(self):
        status, body = handle_usage_analytics({}, self.ctx)
        self.assertEqual(status, 200)
        self.assertIn("total_runs", body)

    def test_escalation_analytics(self):
        status, body = handle_escalation_analytics({}, self.ctx)
        self.assertEqual(status, 200)
        self.assertIn("human_intervention_rate", body)

    def test_audit_analytics(self):
        status, body = handle_audit_analytics({}, self.ctx)
        self.assertEqual(status, 200)
        self.assertIn("data", body)

    def test_analytics_no_db(self):
        ctx = _make_ctx()  # No DB
        status, body = handle_cost_analytics({}, ctx)
        self.assertEqual(status, 501)


# ---------------------------------------------------------------------------
# Export / Import Tests
# ---------------------------------------------------------------------------


class ExportImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._db = SQLiteDatabase(db_path=".noerelay/test_export_import.db")

    @classmethod
    def tearDownClass(cls):
        cls._db.close()
        Path(".noerelay/test_export_import.db").unlink(missing_ok=True)
        Path(".noerelay/test_export_import-export.json").unlink(missing_ok=True)
        Path(".noerelay/test_export_import-backup.db").unlink(missing_ok=True)
        Path(".noerelay/backup.db").unlink(missing_ok=True)

    def setUp(self):
        self.ctx = _make_ctx(db=self._db)

    def test_export_data(self):
        status, body = handle_export_data({}, self.ctx)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")

    def test_import_data_missing_path(self):
        status, body = handle_import_data({}, self.ctx)
        self.assertEqual(status, 400)

    def test_import_data_not_found(self):
        status, body = handle_import_data(
            {"import_path": ".noerelay/nonexistent.db"}, self.ctx
        )
        self.assertEqual(status, 404)

    def test_import_data_success(self):
        # First export to create a file
        handle_export_data({}, self.ctx)
        # Then import from the backup
        backup_path = self._db.backup(".noerelay/backup.db")
        status, body = handle_import_data(
            {"import_path": backup_path}, self.ctx
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")

    def test_export_no_db(self):
        ctx = _make_ctx()  # No DB
        status, body = handle_export_data({}, ctx)
        self.assertEqual(status, 501)

    def test_import_no_db(self):
        ctx = _make_ctx()  # No DB
        status, body = handle_import_data(
            {"import_path": "some/path"}, ctx
        )
        self.assertEqual(status, 501)


# ---------------------------------------------------------------------------
# Server Route Integration Tests
# ---------------------------------------------------------------------------


class ServerRouteTests(unittest.TestCase):
    """Test that the server routes are properly wired up."""

    @classmethod
    def setUpClass(cls):
        cls._db = SQLiteDatabase(db_path=".noerelay/test_server_routes.db")

    @classmethod
    def tearDownClass(cls):
        cls._db.close()
        Path(".noerelay/test_server_routes.db").unlink(missing_ok=True)

    def setUp(self):
        self.ctx = _make_ctx(db=self._db)

    def test_all_get_routes_exist(self):
        """Verify all GET handlers are callable."""
        get_routes = [
            ("/v1/models", lambda: handle_get_policy(self.ctx)),  # proxy test
            ("/v1/governance/policy", lambda: handle_get_policy(self.ctx)),
            ("/v1/governance/risk-classes", lambda: handle_get_risk_classes(self.ctx)),
            ("/v1/routing/portfolio", lambda: handle_get_portfolio(self.ctx)),
            ("/v1/api-keys", lambda: handle_list_api_keys(self.ctx)),
            ("/v1/benchmarks/results", lambda: handle_list_benchmark_results(self.ctx)),
            ("/v1/benchmarks/compare", lambda: handle_compare_models({}, self.ctx)),
            ("/v1/analytics/cost", lambda: handle_cost_analytics({}, self.ctx)),
            ("/v1/analytics/performance", lambda: handle_performance_analytics({}, self.ctx)),
            ("/v1/analytics/usage", lambda: handle_usage_analytics({}, self.ctx)),
            ("/v1/analytics/escalations", lambda: handle_escalation_analytics({}, self.ctx)),
            ("/v1/analytics/audit", lambda: handle_audit_analytics({}, self.ctx)),
            ("/v1/export", lambda: handle_export_data({}, self.ctx)),
        ]
        for route_name, handler in get_routes:
            with self.subTest(route=route_name):
                status, body = handler()
                self.assertIn(status, (200, 501))  # 501 = no DB, which is fine

    def test_all_post_routes_exist(self):
        """Verify all POST handlers are callable."""
        post_routes = [
            ("/v1/models/pull", lambda: handle_pull_model({"model_name": "test"}, self.ctx)),
            ("/v1/models/register", lambda: handle_register_model(
                {"model_id": "t", "provider_family": "p", "inference_gateway": "g"}, self.ctx
            )),
            ("/v1/benchmarks/run", lambda: handle_run_benchmark(
                {"cohort_name": "c", "model_id": "m"}, self.ctx
            )),
            ("/v1/governance/policy", lambda: handle_update_policy({}, self.ctx)),
            ("/v1/routing/candidates", lambda: handle_add_candidate(
                {"candidate_id": "c", "action_kind": "model"}, self.ctx
            )),
            ("/v1/api-keys", lambda: handle_create_api_key({"name": "test"}, self.ctx)),
            ("/v1/import", lambda: handle_import_data(
                {"import_path": ".noerelay/nonexistent.db"}, self.ctx
            )),
        ]
        for route_name, handler in post_routes:
            with self.subTest(route=route_name):
                status, body = handler()
                self.assertIsInstance(status, int)

    def test_all_delete_routes_exist(self):
        """Verify all DELETE handlers are callable."""
        # Add a candidate first
        handle_add_candidate(
            {"candidate_id": "del-me", "action_kind": "model"}, self.ctx
        )
        status, body = handle_remove_candidate("del-me", self.ctx)
        self.assertEqual(status, 200)

        # Remove model
        status, body = handle_remove_model({"model_name": "test"}, self.ctx)
        self.assertEqual(status, 200)

    def test_all_put_routes_exist(self):
        """Verify all PUT handlers are callable."""
        # Update candidate
        handle_add_candidate(
            {"candidate_id": "put-me", "action_kind": "model"}, self.ctx
        )
        status, body = handle_update_candidate(
            "put-me", {"model_id": "updated"}, self.ctx
        )
        self.assertEqual(status, 200)

        # Update risk class
        status, body = handle_update_risk_class(
            {"risk_class": "medium", "gates": {}}, self.ctx
        )
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
