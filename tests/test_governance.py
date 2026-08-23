"""Phase 3: Corporate Governance tests.

Tests RBAC, multi-tenancy, audit trail, cost controls, alerting,
webhooks, config management, and secret management.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reference"))

from gateway.database import SQLiteDatabase
from gateway.rbac import Role, Permission, ROLE_PERMISSIONS, RBACMiddleware
from gateway.tenancy import TenantManager
from gateway.audit import AuditLogger
from gateway.cost_controls import CostController
from gateway.alerting import AlertManager
from gateway.webhooks import WebhookManager, validate_webhook_destination
from gateway.config_manager import ConfigManager
from gateway.secrets import SecretManager


def _temp_db():
    """Create a temporary SQLiteDatabase for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = SQLiteDatabase(path)
    db._db_path = Path(path)
    return db, path


# ---------------------------------------------------------------------------
# RBAC Tests
# ---------------------------------------------------------------------------


class RBACTests(unittest.TestCase):
    def setUp(self):
        self.rbac = RBACMiddleware()

    def test_all_roles_defined(self):
        self.assertEqual(len(Role), 5)
        self.assertIn(Role.ADMIN, Role)
        self.assertIn(Role.OPERATOR, Role)
        self.assertIn(Role.AUDITOR, Role)
        self.assertIn(Role.DEVELOPER, Role)
        self.assertIn(Role.VIEWER, Role)

    def test_admin_has_all_permissions(self):
        self.assertEqual(ROLE_PERMISSIONS[Role.ADMIN], set(Permission))

    def test_operator_permissions(self):
        perms = ROLE_PERMISSIONS[Role.OPERATOR]
        self.assertIn(Permission.CHAT_COMPLETIONS, perms)
        self.assertIn(Permission.MODELS_WRITE, perms)
        self.assertIn(Permission.BENCHMARKS_WRITE, perms)
        self.assertNotIn(Permission.API_KEYS_WRITE, perms)
        self.assertNotIn(Permission.ADMIN_BACKUP, perms)

    def test_auditor_permissions(self):
        perms = ROLE_PERMISSIONS[Role.AUDITOR]
        self.assertIn(Permission.LEDGER_READ, perms)
        self.assertIn(Permission.LEDGER_VERIFY, perms)
        self.assertIn(Permission.AUDIT_READ, perms)
        self.assertNotIn(Permission.CHAT_COMPLETIONS, perms)

    def test_developer_permissions(self):
        perms = ROLE_PERMISSIONS[Role.DEVELOPER]
        self.assertIn(Permission.CHAT_COMPLETIONS, perms)
        self.assertIn(Permission.BENCHMARKS_WRITE, perms)
        self.assertNotIn(Permission.MODELS_WRITE, perms)

    def test_viewer_permissions(self):
        perms = ROLE_PERMISSIONS[Role.VIEWER]
        self.assertIn(Permission.MODELS_READ, perms)
        self.assertNotIn(Permission.CHAT_COMPLETIONS, perms)

    def test_admin_can_access_all_routes(self):
        for (method, path), _ in RBACMiddleware.ROUTE_PERMISSIONS.items():
            allowed, reason = self.rbac.check_permission(method, path, "admin")
            self.assertTrue(allowed, f"Admin should be allowed: {method} {path}")

    def test_viewer_cannot_chat(self):
        allowed, reason = self.rbac.check_permission(
            "POST", "/v1/chat/completions", "viewer"
        )
        self.assertFalse(allowed)
        self.assertIn("viewer", reason or "")

    def test_viewer_can_read_models(self):
        allowed, _ = self.rbac.check_permission("GET", "/v1/models", "viewer")
        self.assertTrue(allowed)

    def test_no_role_means_open_access(self):
        allowed, _ = self.rbac.check_permission("POST", "/v1/chat/completions", None)
        self.assertTrue(allowed)

    def test_unknown_role_rejected(self):
        allowed, reason = self.rbac.check_permission(
            "GET", "/v1/models", "unknown_role"
        )
        self.assertFalse(allowed)

    def test_health_endpoint_no_permission_required(self):
        allowed, _ = self.rbac.check_permission("GET", "/health", "viewer")
        self.assertTrue(allowed)

    def test_unknown_route_is_denied_to_non_admin(self):
        allowed, _ = self.rbac.check_permission("GET", "/unknown/route", "viewer")
        self.assertFalse(allowed)

    def test_operator_can_chat(self):
        allowed, _ = self.rbac.check_permission("POST", "/v1/chat/completions", "operator")
        self.assertTrue(allowed)

    def test_operator_cannot_write_api_keys(self):
        allowed, _ = self.rbac.check_permission("POST", "/v1/api-keys", "operator")
        self.assertFalse(allowed)

    def test_auditor_can_read_ledger(self):
        allowed, _ = self.rbac.check_permission("GET", "/v1/epr/ledger/events", "auditor")
        self.assertTrue(allowed)

    def test_auditor_cannot_chat(self):
        allowed, _ = self.rbac.check_permission("POST", "/v1/chat/completions", "auditor")
        self.assertFalse(allowed)

    def test_developer_can_benchmark_write(self):
        allowed, _ = self.rbac.check_permission("POST", "/v1/benchmarks/run", "developer")
        self.assertTrue(allowed)

    def test_prefix_match_for_path_params(self):
        # Test that /v1/tenants/abc matches the /v1/tenants prefix
        allowed, _ = self.rbac.check_permission("GET", "/v1/tenants/my-tenant", "admin")
        self.assertTrue(allowed)


# ---------------------------------------------------------------------------
# Multi-Tenancy Tests
# ---------------------------------------------------------------------------


class MultiTenancyTests(unittest.TestCase):
    def setUp(self):
        self.db, self.db_path = _temp_db()
        self.tm = TenantManager(self.db)

    def tearDown(self):
        self.db.close()
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_create_tenant(self):
        tenant = self.tm.create_tenant("t1", "Test Tenant")
        self.assertEqual(tenant["tenant_id"], "t1")
        self.assertEqual(tenant["name"], "Test Tenant")
        self.assertEqual(tenant["budget_daily_usd"], 10.0)
        self.assertEqual(tenant["budget_monthly_usd"], 300.0)

    def test_create_tenant_with_custom_budgets(self):
        tenant = self.tm.create_tenant(
            "t2", "Big Budget", budget_daily_usd=50.0, budget_monthly_usd=1500.0
        )
        self.assertEqual(tenant["budget_daily_usd"], 50.0)
        self.assertEqual(tenant["budget_monthly_usd"], 1500.0)

    def test_get_tenant(self):
        self.tm.create_tenant("t1", "Test")
        t = self.tm.get_tenant("t1")
        self.assertIsNotNone(t)
        self.assertEqual(t["name"], "Test")

    def test_get_nonexistent_tenant(self):
        self.assertIsNone(self.tm.get_tenant("nonexistent"))

    def test_list_tenants(self):
        self.tm.create_tenant("t1", "One")
        self.tm.create_tenant("t2", "Two")
        tenants = self.tm.list_tenants()
        self.assertEqual(len(tenants), 2)

    def test_update_tenant(self):
        self.tm.create_tenant("t1", "Original")
        updated = self.tm.update_tenant("t1", name="Updated", budget_daily_usd=20.0)
        self.assertEqual(updated["name"], "Updated")
        self.assertEqual(updated["budget_daily_usd"], 20.0)

    def test_update_nonexistent_tenant(self):
        result = self.tm.update_tenant("nonexistent", name="X")
        self.assertIsNone(result)

    def test_delete_tenant(self):
        self.tm.create_tenant("t1", "Delete Me")
        self.assertTrue(self.tm.delete_tenant("t1"))
        self.assertIsNone(self.tm.get_tenant("t1"))

    def test_delete_nonexistent_tenant(self):
        self.assertFalse(self.tm.delete_tenant("nonexistent"))

    def test_check_budget_within_limits(self):
        self.tm.create_tenant("t1", "Test", budget_daily_usd=10.0)
        budget = self.tm.check_budget("t1")
        self.assertTrue(budget["within_budget"])
        self.assertEqual(budget["daily_spend"], 0.0)
        self.assertEqual(budget["daily_budget"], 10.0)

    def test_check_budget_nonexistent_tenant(self):
        budget = self.tm.check_budget("nonexistent")
        self.assertTrue(budget["within_budget"])

    def test_record_spend_updates_budget(self):
        self.tm.create_tenant("t1", "Test", budget_daily_usd=10.0)
        self.tm.record_spend("t1", 5.0)
        budget = self.tm.check_budget("t1")
        self.assertEqual(budget["daily_spend"], 5.0)
        self.assertTrue(budget["within_budget"])

    def test_record_spend_exceeds_daily_budget(self):
        self.tm.create_tenant("t1", "Test", budget_daily_usd=10.0)
        self.tm.record_spend("t1", 12.0)
        budget = self.tm.check_budget("t1")
        self.assertFalse(budget["within_budget"])
        self.assertEqual(budget["remaining_daily"], 0.0)


# ---------------------------------------------------------------------------
# Audit Trail Tests
# ---------------------------------------------------------------------------


class AuditTrailTests(unittest.TestCase):
    def setUp(self):
        self.db, self.db_path = _temp_db()
        self.audit = AuditLogger(self.db)

    def tearDown(self):
        self.db.close()
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_log_api_call(self):
        audit_id = self.audit.log_api_call(
            "user-1", "read", "model", "gpt-4", "127.0.0.1",
            {"detail": "test"}, success=True,
        )
        self.assertIsNotNone(audit_id)
        self.assertTrue(audit_id.startswith("audit-"))

    def test_log_api_call_failure(self):
        audit_id = self.audit.log_api_call(
            "user-2", "create", "api_key", "key-123", "10.0.0.1",
            success=False,
        )
        self.assertIsNotNone(audit_id)

    def test_query_audit_log(self):
        self.audit.log_api_call("user-1", "read", "model", "m1", "127.0.0.1")
        self.audit.log_api_call("user-2", "write", "model", "m2", "127.0.0.1")
        entries = self.audit.query(actor_id="user-1")
        self.assertGreaterEqual(len(entries), 1)
        for e in entries:
            self.assertEqual(e["actor_id"], "user-1")

    def test_query_by_action(self):
        self.audit.log_api_call("user-1", "read", "model", "m1", "127.0.0.1")
        self.audit.log_api_call("user-1", "write", "model", "m2", "127.0.0.1")
        entries = self.audit.query(action="write")
        self.assertGreaterEqual(len(entries), 1)
        for e in entries:
            self.assertEqual(e["action"], "write")

    def test_get_actor_activity(self):
        self.audit.log_api_call("user-1", "read", "model", "m1", "127.0.0.1")
        self.audit.log_api_call("user-1", "write", "model", "m2", "127.0.0.1")
        entries = self.audit.get_actor_activity("user-1")
        self.assertGreaterEqual(len(entries), 1)

    def test_get_resource_history(self):
        self.audit.log_api_call("user-1", "read", "model", "m1", "127.0.0.1")
        entries = self.audit.get_resource_history("model", "m1")
        self.assertGreaterEqual(len(entries), 0)

    def test_detect_anomalies_empty(self):
        anomalies = self.audit.detect_anomalies()
        self.assertEqual(len(anomalies), 0)

    def test_detect_anomalies_high_volume(self):
        for i in range(60):
            self.audit.log_api_call("user-1", "read", "model", f"m{i}", "127.0.0.1")
        anomalies = self.audit.detect_anomalies()
        self.assertGreaterEqual(len(anomalies), 1)
        self.assertTrue(any(a["type"] == "high_volume" for a in anomalies))

    def test_detect_anomalies_high_failure(self):
        for i in range(20):
            self.audit.log_api_call("user-1", "read", "model", f"m{i}", "127.0.0.1", success=False)
        anomalies = self.audit.detect_anomalies()
        self.assertGreaterEqual(len(anomalies), 1)
        self.assertTrue(any(a["type"] == "high_failure_rate" for a in anomalies))


# ---------------------------------------------------------------------------
# Cost Control Tests
# ---------------------------------------------------------------------------


class CostControlTests(unittest.TestCase):
    def setUp(self):
        self.db, self.db_path = _temp_db()
        self.tm = TenantManager(self.db)
        self.cc = CostController(self.db, self.tm)

    def tearDown(self):
        self.db.close()
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_add_alert_rule(self):
        rule = self.cc.add_alert_rule("test", "daily_spend_exceeds", 5.0)
        self.assertEqual(rule["name"], "test")
        self.assertEqual(rule["condition"], "daily_spend_exceeds")
        self.assertEqual(rule["threshold"], 5.0)

    def test_check_alerts_no_trigger(self):
        self.tm.create_tenant("t1", "Test", budget_daily_usd=10.0)
        self.cc.add_alert_rule("test", "daily_spend_exceeds", 5.0, tenant_id="t1")
        triggered = self.cc.check_alerts()
        self.assertEqual(len(triggered), 0)

    def test_check_alerts_daily_spend_exceeds(self):
        self.tm.create_tenant("t1", "Test", budget_daily_usd=10.0)
        self.tm.record_spend("t1", 8.0)
        self.cc.add_alert_rule("test", "daily_spend_exceeds", 5.0, tenant_id="t1")
        triggered = self.cc.check_alerts()
        self.assertEqual(len(triggered), 1)

    def test_check_alerts_monthly_spend_exceeds(self):
        self.tm.create_tenant("t1", "Test", budget_monthly_usd=100.0)
        self.tm.record_spend("t1", 120.0)
        self.cc.add_alert_rule("test", "monthly_spend_exceeds", 100.0, tenant_id="t1")
        triggered = self.cc.check_alerts()
        self.assertEqual(len(triggered), 1)

    def test_get_cost_summary(self):
        self.tm.create_tenant("t1", "Test", budget_daily_usd=10.0)
        self.tm.record_spend("t1", 3.0)
        summary = self.cc.get_cost_summary("t1", "daily")
        self.assertEqual(summary["tenant_id"], "t1")
        self.assertEqual(summary["spend"], 3.0)
        self.assertTrue(summary["within_budget"])

    def test_get_cost_summary_all_tenants(self):
        self.tm.create_tenant("t1", "One")
        self.tm.create_tenant("t2", "Two")
        summary = self.cc.get_cost_summary(period="daily")
        self.assertIn("tenants", summary)
        self.assertEqual(len(summary["tenants"]), 2)

    def test_get_cost_trend(self):
        self.tm.create_tenant("t1", "Test")
        self.tm.record_spend("t1", 5.0)
        self.tm.record_spend("t1", 3.0)
        trend = self.cc.get_cost_trend("t1", days=30)
        self.assertGreaterEqual(len(trend), 1)

    def test_forecast_cost(self):
        self.tm.create_tenant("t1", "Test")
        for _ in range(10):
            self.tm.record_spend("t1", 2.0)
        forecast = self.cc.forecast_cost("t1", days=7)
        self.assertIn("forecast_daily_cost", forecast)
        self.assertIn("forecast_total_cost", forecast)

    def test_forecast_cost_no_data(self):
        self.tm.create_tenant("t1", "Test")
        forecast = self.cc.forecast_cost("t1")
        self.assertEqual(forecast["forecast_daily_cost"], 0.0)
        self.assertEqual(forecast["confidence"], "low")

    def test_detect_anomalies_no_data(self):
        anomalies = self.cc.detect_anomalies()
        self.assertEqual(len(anomalies), 0)


# ---------------------------------------------------------------------------
# Alerting Tests
# ---------------------------------------------------------------------------


class AlertingTests(unittest.TestCase):
    def setUp(self):
        self.am = AlertManager()

    def test_add_rule(self):
        rule = self.am.add_rule("test rule", "cost_overrun", {"threshold": 100})
        self.assertEqual(rule["name"], "test rule")
        self.assertEqual(rule["alert_type"], "cost_overrun")
        self.assertEqual(rule["severity"], "warning")

    def test_add_rule_with_custom_severity(self):
        rule = self.am.add_rule("critical", "ledger_tamper", {}, severity="critical")
        self.assertEqual(rule["severity"], "critical")

    def test_trigger_alert(self):
        alert = self.am.trigger_alert("cost_overrun", "warning", "Budget exceeded")
        self.assertEqual(alert["alert_type"], "cost_overrun")
        self.assertEqual(alert["severity"], "warning")
        self.assertEqual(alert["message"], "Budget exceeded")
        self.assertFalse(alert["acknowledged"])

    def test_trigger_alert_with_details(self):
        alert = self.am.trigger_alert(
            "high_hir", "critical", "HIR too high",
            details={"hir": 0.5, "threshold": 0.3},
        )
        self.assertEqual(alert["details"]["hir"], 0.5)

    def test_get_alerts_all(self):
        self.am.trigger_alert("cost_overrun", "warning", "msg1")
        self.am.trigger_alert("high_hir", "critical", "msg2")
        alerts = self.am.get_alerts()
        self.assertEqual(len(alerts), 2)

    def test_get_alerts_by_severity(self):
        self.am.trigger_alert("cost_overrun", "warning", "msg1")
        self.am.trigger_alert("high_hir", "critical", "msg2")
        alerts = self.am.get_alerts(severity="critical")
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["severity"], "critical")

    def test_get_alerts_by_acknowledged(self):
        self.am.trigger_alert("cost_overrun", "warning", "msg1")
        alerts = self.am.get_alerts(acknowledged=False)
        self.assertEqual(len(alerts), 1)

    def test_acknowledge_alert(self):
        alert = self.am.trigger_alert("cost_overrun", "warning", "test")
        self.assertTrue(self.am.acknowledge_alert(alert["alert_id"], "admin"))
        alerts = self.am.get_alerts(acknowledged=True)
        self.assertEqual(len(alerts), 1)

    def test_acknowledge_nonexistent_alert(self):
        self.assertFalse(self.am.acknowledge_alert("nonexistent", "admin"))

    def test_register_webhook(self):
        wh = self.am.register_webhook("http://example.com/hook", ["cost_overrun"])
        self.assertEqual(wh["url"], "http://example.com/hook")
        self.assertIn("cost_overrun", wh["events"])


# ---------------------------------------------------------------------------
# Webhook Tests
# ---------------------------------------------------------------------------


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.db, self.db_path = _temp_db()
        self.wm = WebhookManager(self.db, allow_private_networks=True)

    def tearDown(self):
        self.db.close()
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_register_webhook(self):
        wh = self.wm.register("http://example.com/hook", ["run.completed", "cost.alert"])
        self.assertEqual(wh["url"], "http://example.com/hook")
        self.assertIn("run.completed", wh["events"])
        self.assertIn("cost.alert", wh["events"])
        self.assertTrue(wh["active"])

    def test_register_webhook_with_secret(self):
        wh = self.wm.register("http://example.com/hook", ["run.completed"], secret="my-secret")
        self.assertNotIn("secret", wh)
        self.assertTrue(wh["has_secret"])

    def test_private_webhook_is_blocked_by_default(self):
        manager = WebhookManager(self.db)
        with self.assertRaises(ValueError):
            manager.register("http://127.0.0.1/hook", ["run.completed"])

    def test_register_webhook_with_tenant(self):
        wh = self.wm.register("http://example.com/hook", ["run.completed"], tenant_id="t1")
        self.assertEqual(wh["tenant_id"], "t1")

    def test_list_webhooks(self):
        self.wm.register("http://a.com", ["run.completed"])
        self.wm.register("http://b.com", ["cost.alert"])
        webhooks = self.wm.list_webhooks()
        self.assertEqual(len(webhooks), 2)

    def test_list_webhooks_by_tenant(self):
        self.wm.register("http://a.com", ["run.completed"], tenant_id="t1")
        self.wm.register("http://b.com", ["run.completed"], tenant_id="t2")
        t1_hooks = self.wm.list_webhooks(tenant_id="t1")
        self.assertEqual(len(t1_hooks), 1)

    def test_delete_webhook(self):
        wh = self.wm.register("http://example.com/hook", ["run.completed"])
        self.assertTrue(self.wm.delete_webhook(wh["webhook_id"]))
        self.assertEqual(len(self.wm.list_webhooks()), 0)

    def test_delete_nonexistent_webhook(self):
        self.assertFalse(self.wm.delete_webhook("nonexistent"))

    def test_deliver_no_matching_webhooks(self):
        results = self.wm.deliver("run.completed", {"run_id": "r1"})
        self.assertEqual(len(results), 0)

    @patch("gateway.webhooks.open_webhook_request")
    def test_deliver_matching_webhook(self, mock_urlopen):
        """Webhook delivery succeeds when the target responds."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status": "ok"}'
        mock_resp.getcode.return_value = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=None)
        mock_urlopen.return_value = mock_resp

        self.wm.register("http://localhost:9999/webhook", ["run.completed"], "secret")
        results = self.wm.deliver("run.completed", {"run_id": "r1"})
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["success"])

    @patch("gateway.webhooks.open_webhook_request")
    def test_deliver_matching_webhook_failure(self, mock_urlopen):
        """Webhook delivery records failure when target is unreachable."""
        mock_urlopen.side_effect = OSError("Connection refused")

        self.wm.register("http://localhost:9999/nonexistent", ["run.completed"])
        results = self.wm.deliver("run.completed", {"run_id": "r1"})
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["success"])
        self.assertIsNotNone(results[0]["error"])

    @patch("gateway.webhooks.open_webhook_request")
    def test_deliver_includes_signature_header(self, mock_urlopen):
        """Webhook delivery includes HMAC signature when secret is set."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status": "ok"}'
        mock_resp.getcode.return_value = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=None)
        mock_urlopen.return_value = mock_resp

        self.wm.register("http://localhost:9999/webhook", ["run.completed"], "my-secret")
        self.wm.deliver("run.completed", {"run_id": "r1"})

        # Verify the signature header was added
        request = mock_urlopen.call_args[0][0]
        # Case-insensitive header lookup
        sig = None
        for key, value in request.headers.items():
            if key.lower() == "x-noerelay-signature":
                sig = value
                break
        self.assertIsNotNone(sig)
        self.assertEqual(len(sig), 64)

    @patch("gateway.webhooks.open_webhook_request")
    def test_deliver_event_header(self, mock_urlopen):
        """Webhook delivery includes X-NoeRelay-Event header."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status": "ok"}'
        mock_resp.getcode.return_value = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=None)
        mock_urlopen.return_value = mock_resp

        self.wm.register("http://localhost:9999/webhook", ["run.completed"])
        self.wm.deliver("run.completed", {"run_id": "r1"})

        request = mock_urlopen.call_args[0][0]
        event_header = None
        for key, value in request.headers.items():
            if key.lower() == "x-noerelay-event":
                event_header = value
                break
        self.assertEqual(event_header, "run.completed")

    def test_sign_payload(self):
        sig = self.wm._sign_payload("test payload", "secret")
        self.assertIsInstance(sig, str)
        self.assertEqual(len(sig), 64)  # SHA-256 hex digest

    @patch("gateway.webhooks.socket.getaddrinfo")
    def test_resolved_private_webhook_destination_is_blocked(self, getaddrinfo):
        getaddrinfo.return_value = [
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ]
        with self.assertRaisesRegex(ValueError, "private or local"):
            validate_webhook_destination("https://public-name.example/hook")

    @patch("gateway.webhooks.socket.getaddrinfo")
    def test_resolved_public_webhook_destination_is_allowed(self, getaddrinfo):
        getaddrinfo.return_value = [
            (2, 1, 6, "", ("93.184.216.34", 443)),
        ]
        self.assertEqual(
            validate_webhook_destination("https://example.com/hook"),
            "https://example.com/hook",
        )


# ---------------------------------------------------------------------------
# Config Manager Tests
# ---------------------------------------------------------------------------


class ConfigManagerTests(unittest.TestCase):
    def setUp(self):
        self.db, self.db_path = _temp_db()
        self.cm = ConfigManager(self.db)

    def tearDown(self):
        self.db.close()
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_set_and_get(self):
        self.cm.set("test_key", "test_value")
        self.assertEqual(self.cm.get("test_key"), "test_value")

    def test_get_with_default(self):
        self.assertEqual(self.cm.get("nonexistent", "default"), "default")

    def test_get_all(self):
        self.cm.set("key1", "val1")
        self.cm.set("key2", "val2")
        config = self.cm.get_all()
        self.assertEqual(config["key1"], "val1")
        self.assertEqual(config["key2"], "val2")

    def test_set_with_updated_by(self):
        self.cm.set("key", "val", updated_by="tester")
        self.assertEqual(self.cm.get("key"), "val")

    def test_listener_notification(self):
        notified = []

        def listener(key, value):
            notified.append((key, value))

        self.cm.register_listener(listener)
        self.cm.set("watch_key", "new_value")
        self.assertEqual(len(notified), 1)
        self.assertEqual(notified[0], ("watch_key", "new_value"))

    def test_listener_failure_does_not_crash(self):
        def bad_listener(key, value):
            raise RuntimeError("oops")

        self.cm.register_listener(bad_listener)
        # Should not raise
        self.cm.set("key", "value")
        self.assertEqual(self.cm.get("key"), "value")

    def test_hot_reload(self):
        self.cm.set("key1", "val1")
        self.cm.set("key2", "val2")
        config = self.cm.hot_reload()
        self.assertEqual(config["key1"], "val1")
        self.assertEqual(config["key2"], "val2")

    def test_hot_reload_notifies_changes(self):
        notified = []

        def listener(key, value):
            notified.append(key)

        self.cm.register_listener(listener)
        self.cm.set("key1", "val1")
        notified.clear()
        self.cm.hot_reload()
        # No changes, so no notifications
        self.assertEqual(len(notified), 0)


# ---------------------------------------------------------------------------
# Secret Manager Tests
# ---------------------------------------------------------------------------


class SecretManagerTests(unittest.TestCase):
    def setUp(self):
        self.db, self.db_path = _temp_db()
        self.sm = SecretManager(self.db, master_key="unit-test-master-key")

    def tearDown(self):
        self.db.close()
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_store_and_get_secret(self):
        self.sm.store_secret("api_key", "my-secret-value")
        self.assertEqual(self.sm.get_secret("api_key"), "my-secret-value")

    def test_get_nonexistent_secret(self):
        self.assertIsNone(self.sm.get_secret("nonexistent"))

    def test_store_secret_updates_existing(self):
        self.sm.store_secret("api_key", "value1")
        self.sm.store_secret("api_key", "value2")
        self.assertEqual(self.sm.get_secret("api_key"), "value2")

    def test_list_secrets(self):
        self.sm.store_secret("key1", "val1", description="First key")
        self.sm.store_secret("key2", "val2", description="Second key")
        secrets = self.sm.list_secrets()
        self.assertEqual(len(secrets), 2)
        # Values should not be in the listing
        self.assertNotIn("encrypted_value", secrets[0])
        self.assertNotIn("value", secrets[0])

    def test_list_secrets_by_tenant(self):
        self.sm.store_secret("key1", "val1", tenant_id="t1")
        self.sm.store_secret("key2", "val2", tenant_id="t2")
        t1_secrets = self.sm.list_secrets(tenant_id="t1")
        self.assertEqual(len(t1_secrets), 1)
        self.assertEqual(t1_secrets[0]["name"], "key1")

    def test_delete_secret(self):
        self.sm.store_secret("key1", "val1")
        self.assertTrue(self.sm.delete_secret("key1"))
        self.assertIsNone(self.sm.get_secret("key1"))

    def test_delete_nonexistent_secret(self):
        self.assertFalse(self.sm.delete_secret("nonexistent"))

    def test_encryption_is_not_plaintext(self):
        self.sm.store_secret("key1", "my-secret")
        conn = self.db._get_conn()
        row = conn.execute(
            "SELECT encrypted_value FROM secrets WHERE name = ? AND tenant_id = ?",
            ("key1", "default"),
        ).fetchone()
        encrypted = row["encrypted_value"]
        self.assertNotEqual(encrypted, "my-secret")
        self.assertNotIn("my-secret", encrypted)

    def test_rotate_master_key(self):
        self.sm.store_secret("key1", "secret-value")
        self.sm.rotate_master_key("new-master-key-12345")
        # After rotation, the secret should still be retrievable
        self.assertEqual(self.sm.get_secret("key1"), "secret-value")

    def test_secret_with_description(self):
        self.sm.store_secret("key1", "val1", description="My API key")
        secrets = self.sm.list_secrets()
        self.assertEqual(secrets[0]["description"], "My API key")

    def test_encrypt_decrypt_roundtrip(self):
        encrypted = self.sm._encrypt("hello world")
        self.assertNotEqual(encrypted, "hello world")
        decrypted = self.sm._decrypt(encrypted)
        self.assertEqual(decrypted, "hello world")

    def test_tampered_ciphertext_is_rejected(self):
        from gateway.secrets import SecretIntegrityError

        encrypted = self.sm._encrypt("hello world")
        replacement = "A" if encrypted[-1] != "A" else "B"
        with self.assertRaises(SecretIntegrityError):
            self.sm._decrypt(encrypted[:-1] + replacement)


if __name__ == "__main__":
    unittest.main()
