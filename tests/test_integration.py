"""Integration tests for Phase 4: Prometheus metrics, SIEM, Docker, Kubernetes.

Tests:
- PrometheusMetricsTests: counter, gauge, histogram, format, record_run
- SIEMIntegrationTests: log shipping (mocked), CEF/LEEF format
- DockerfileTests: Dockerfile exists and is valid
- KubernetesManifestTests: K8s manifests exist and are valid YAML
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reference"))

from gateway.prometheus import (
    PrometheusMetrics,
    _compute_bucket_counts,
    _format_float,
    _format_labels_escaped,
    _parse_labels_string,
    _split_labels,
)
from gateway.siem import SIEMIntegration


# ---------------------------------------------------------------------------
# PrometheusMetricsTests
# ---------------------------------------------------------------------------


class PrometheusMetricsTests(unittest.TestCase):
    """Tests for the PrometheusMetrics collector."""

    def setUp(self):
        self.pm = PrometheusMetrics()

    # -- Counter tests --

    def test_counter_increment(self):
        self.pm.inc_counter("noerelay_runs_total")
        self.assertEqual(self.pm.get_counter("noerelay_runs_total"), 1.0)

        self.pm.inc_counter("noerelay_runs_total", value=5.0)
        self.assertEqual(self.pm.get_counter("noerelay_runs_total"), 6.0)

    def test_counter_with_labels(self):
        self.pm.inc_counter("noerelay_model_requests_total", {"model_id": "qwen3:8b"})
        self.pm.inc_counter("noerelay_model_requests_total", {"model_id": "qwen3:8b"})
        self.pm.inc_counter("noerelay_model_requests_total", {"model_id": "llama3:8b"})

        self.assertEqual(
            self.pm.get_counter("noerelay_model_requests_total", {"model_id": "qwen3:8b"}),
            2.0,
        )
        self.assertEqual(
            self.pm.get_counter("noerelay_model_requests_total", {"model_id": "llama3:8b"}),
            1.0,
        )

    def test_counter_unlabelled(self):
        self.pm.inc_counter("noerelay_runs_total")
        self.pm.inc_counter("noerelay_runs_total")
        self.assertEqual(self.pm.get_counter("noerelay_runs_total"), 2.0)

    # -- Gauge tests --

    def test_gauge_set(self):
        self.pm.set_gauge("noerelay_cache_size", 42.0)
        self.assertEqual(self.pm.get_gauge("noerelay_cache_size"), 42.0)

        self.pm.set_gauge("noerelay_cache_size", 100.0)
        self.assertEqual(self.pm.get_gauge("noerelay_cache_size"), 100.0)

    def test_gauge_unset_returns_zero(self):
        self.assertEqual(self.pm.get_gauge("noerelay_cache_size"), 0.0)

    def test_gauge_with_labels(self):
        self.pm.set_gauge("noerelay_active_runs", 5.0, {"tenant_id": "team-a"})
        self.assertEqual(
            self.pm.get_gauge("noerelay_active_runs", {"tenant_id": "team-a"}), 5.0
        )

    # -- Histogram tests --

    def test_histogram_observe(self):
        self.pm.observe_histogram("noerelay_request_duration_seconds", 0.5)
        self.pm.observe_histogram("noerelay_request_duration_seconds", 1.2)
        self.pm.observe_histogram("noerelay_request_duration_seconds", 0.05)

        values = self.pm.get_histogram_values("noerelay_request_duration_seconds")
        self.assertEqual(len(values), 3)
        self.assertIn(0.5, values)
        self.assertIn(1.2, values)
        self.assertIn(0.05, values)

    def test_histogram_empty(self):
        values = self.pm.get_histogram_values("noerelay_request_duration_seconds")
        self.assertEqual(values, [])

    # -- Format tests --

    def test_format_float(self):
        # Prometheus accepts both "1" and "1.0" for integer-valued floats
        self.assertIn(_format_float(1.0), ("1.0", "1"))
        self.assertIn(_format_float(0.0), ("0.0", "0"))
        self.assertEqual(_format_float(3.14159), "3.14159")
        self.assertEqual(_format_float(float("inf")), "+Inf")
        self.assertEqual(_format_float(float("-inf")), "-Inf")
        self.assertEqual(_format_float(float("nan")), "Nan")

    def test_format_labels_escaped(self):
        labels = {"model_id": "qwen3:8b"}
        result = _format_labels_escaped(labels)
        self.assertEqual(result, '{model_id="qwen3:8b"}')

    def test_format_labels_escaped_empty(self):
        self.assertEqual(_format_labels_escaped(None), "")
        self.assertEqual(_format_labels_escaped({}), "")

    def test_format_labels_escaped_special_chars(self):
        labels = {"key": 'value"with"quotes'}
        result = _format_labels_escaped(labels)
        self.assertIn('\\"', result)

    def test_parse_labels_string(self):
        result = _parse_labels_string('{model_id="qwen3:8b"}')
        self.assertEqual(result, {"model_id": "qwen3:8b"})

    def test_parse_labels_string_empty(self):
        self.assertEqual(_parse_labels_string("{}"), {})
        self.assertEqual(_parse_labels_string(""), {})

    def test_split_labels(self):
        result = _split_labels('model_id="qwen3:8b",tenant_id="default"')
        self.assertEqual(len(result), 2)
        self.assertIn('model_id="qwen3:8b"', result)
        self.assertIn('tenant_id="default"', result)

    def test_split_labels_single(self):
        result = _split_labels('model_id="qwen3:8b"')
        self.assertEqual(len(result), 1)

    def test_compute_bucket_counts(self):
        values = [0.05, 0.5, 1.2, 5.0]
        buckets = [0.1, 1.0, 5.0, 10.0]
        counts = _compute_bucket_counts(values, buckets)
        # <= 0.1: [0.05] -> 1
        # <= 1.0: [0.05, 0.5] -> 2
        # <= 5.0: [0.05, 0.5, 1.2, 5.0] -> 4
        # <= 10.0: all -> 4
        self.assertEqual(counts, [1, 2, 4, 4])

    def test_compute_bucket_counts_empty(self):
        counts = _compute_bucket_counts([], [0.1, 1.0])
        self.assertEqual(counts, [0, 0])

    # -- Format output tests --

    def test_format_empty(self):
        text = self.pm.format()
        self.assertIsInstance(text, str)
        # Should end with a newline
        self.assertTrue(text.endswith("\n"))

    def test_format_with_counter(self):
        self.pm.inc_counter("noerelay_runs_total")
        text = self.pm.format()
        self.assertIn("# HELP noerelay_runs_total", text)
        self.assertIn("# TYPE noerelay_runs_total counter", text)
        self.assertIn("noerelay_runs_total 1", text)

    def test_format_with_labelled_counter(self):
        self.pm.inc_counter("noerelay_model_requests_total", {"model_id": "qwen3:8b"})
        text = self.pm.format()
        self.assertIn("noerelay_model_requests_total{model_id=", text)

    def test_format_with_gauge(self):
        self.pm.set_gauge("noerelay_cache_size", 42.0)
        text = self.pm.format()
        self.assertIn("# HELP noerelay_cache_size", text)
        self.assertIn("# TYPE noerelay_cache_size gauge", text)
        self.assertIn("noerelay_cache_size 42", text)

    def test_format_with_histogram(self):
        self.pm.observe_histogram("noerelay_request_duration_seconds", 0.5)
        self.pm.observe_histogram("noerelay_request_duration_seconds", 1.2)
        text = self.pm.format()
        self.assertIn("# HELP noerelay_request_duration_seconds", text)
        self.assertIn("# TYPE noerelay_request_duration_seconds histogram", text)
        self.assertIn("_bucket{", text)
        self.assertIn("_sum", text)
        self.assertIn("_count", text)

    def test_get_metrics_text(self):
        self.pm.inc_counter("noerelay_runs_total")
        text = self.pm.get_metrics_text()
        self.assertIn("noerelay_runs_total", text)

    # -- record_run tests --

    def test_record_run_accepted(self):
        self.pm.record_run({
            "status": "accepted",
            "model_id": "qwen3:8b",
            "risk_class": "low",
            "tokens": 500,
            "cost_usd": 0.05,
            "duration_ms": 1200,
            "tenant_id": "default",
        })
        self.assertEqual(self.pm.get_counter("noerelay_runs_total"), 1.0)
        self.assertEqual(self.pm.get_counter("noerelay_runs_accepted_total"), 1.0)
        self.assertEqual(
            self.pm.get_counter("noerelay_model_requests_total", {"model_id": "qwen3:8b"}),
            1.0,
        )
        self.assertEqual(
            self.pm.get_counter("noerelay_model_tokens_total", {"model_id": "qwen3:8b"}),
            500.0,
        )
        self.assertEqual(
            self.pm.get_counter("noerelay_model_cost_total", {"model_id": "qwen3:8b"}),
            0.05,
        )
        self.assertEqual(
            self.pm.get_counter("noerelay_tenant_spend_total", {"tenant_id": "default"}),
            0.05,
        )
        self.assertEqual(
            self.pm.get_counter("noerelay_risk_class_runs_total", {"risk_class": "low"}),
            1.0,
        )
        self.assertEqual(
            len(self.pm.get_histogram_values("noerelay_request_duration_seconds")), 1
        )
        self.assertEqual(
            len(self.pm.get_histogram_values("noerelay_tokens_per_request")), 1
        )
        self.assertEqual(
            len(self.pm.get_histogram_values("noerelay_cost_per_request_usd")), 1
        )

    def test_record_run_escalated(self):
        self.pm.record_run({"status": "escalated", "model_id": "llama3:8b"})
        self.assertEqual(self.pm.get_counter("noerelay_runs_escalated_total"), 1.0)

    def test_record_run_rejected(self):
        self.pm.record_run({"status": "rejected"})
        self.assertEqual(self.pm.get_counter("noerelay_runs_rejected_total"), 1.0)

    def test_record_run_minimal(self):
        """record_run should handle a minimal dict without errors."""
        self.pm.record_run({"status": "accepted"})
        self.assertEqual(self.pm.get_counter("noerelay_runs_total"), 1.0)
        self.assertEqual(self.pm.get_counter("noerelay_runs_accepted_total"), 1.0)


# ---------------------------------------------------------------------------
# SIEMIntegrationTests
# ---------------------------------------------------------------------------


class SIEMIntegrationTests(unittest.TestCase):
    """Tests for the SIEM integration module."""

    def setUp(self):
        self.siem = SIEMIntegration(format="json")

    # -- CEF format --

    def test_format_cef_basic(self):
        event = {
            "event_type": "run.completed",
            "name": "Run Completed",
            "severity": "3",
            "run_id": "run-123",
            "model_id": "qwen3:8b",
        }
        result = self.siem.format_cef(event)
        self.assertIn("CEF:0|ElectroHire|NoeRelay|0.1.0", result)
        self.assertIn("run.completed", result)
        self.assertIn("Run Completed", result)
        self.assertIn("run_id=run-123", result)

    def test_format_cef_default_severity(self):
        event = {"event_type": "test", "name": "Test"}
        result = self.siem.format_cef(event)
        # Should use default severity 5
        parts = result.split("|")
        self.assertIn("5", parts[6])  # severity is the 7th field (0-indexed 6)

    def test_format_cef_escapes(self):
        event = {
            "event_type": "test",
            "name": "Test",
            "special": "value=with\\backslash",
        }
        result = self.siem.format_cef(event)
        self.assertIn("special=value\\=with\\\\backslash", result)

    # -- LEEF format --

    def test_format_leef_basic(self):
        event = {
            "event_type": "run.escalated",
            "run_id": "run-456",
            "model_id": "llama3:8b",
        }
        result = self.siem.format_leef(event)
        self.assertIn("LEEF:2.0|ElectroHire|NoeRelay|0.1.0", result)
        self.assertIn("run.escalated", result)
        self.assertIn("run_id=run-456", result)

    def test_format_leef_escapes(self):
        event = {
            "event_type": "test",
            "value": "line1\nline2\tend",
        }
        result = self.siem.format_leef(event)
        self.assertIn("\\n", result)
        self.assertIn("\\t", result)

    # -- Syslog format --

    def test_format_syslog(self):
        siem = SIEMIntegration(format="syslog")
        event = {"event_type": "test", "message": "hello"}
        result = siem.format_syslog(event)
        self.assertTrue(result.startswith("<134>1 "))
        self.assertIn("noerelay", result)

    # -- JSON format (default) --

    def test_format_json(self):
        siem = SIEMIntegration(format="json")
        event = {"event_type": "test", "message": "hello"}
        result = siem._format_log_entry(event)
        self.assertIsInstance(result, str)
        parsed = json.loads(result)
        self.assertEqual(parsed["event_type"], "test")

    # -- Buffering (no endpoint) --

    def test_ship_log_buffers_when_no_endpoint(self):
        siem = SIEMIntegration()  # No endpoint
        result = siem.ship_log({"event_type": "test"})
        self.assertTrue(result)
        self.assertEqual(len(siem._buffer), 1)

    def test_flush_returns_count(self):
        siem = SIEMIntegration()  # No endpoint
        siem.ship_log({"event_type": "test"})
        siem.ship_log({"event_type": "test2"})
        count = siem.flush()
        self.assertEqual(count, 0)  # No endpoint, so nothing shipped
        self.assertEqual(len(siem._buffer), 0)  # Buffer cleared

    # -- Audit event shipping --

    def test_ship_audit_event(self):
        siem = SIEMIntegration()
        audit = {"action": "api_call", "actor_id": "admin"}
        result = siem.ship_audit_event(audit)
        self.assertTrue(result)
        self.assertEqual(len(siem._buffer), 1)
        buffered = siem._buffer[0]
        self.assertEqual(buffered["_siem_type"], "audit")
        self.assertEqual(buffered["_siem_source"], "noerelay-gateway")

    # -- Ledger event shipping --

    def test_ship_ledger_event(self):
        siem = SIEMIntegration()
        ledger = {"event_type": "run.started", "run_id": "r1"}
        result = siem.ship_ledger_event(ledger)
        self.assertTrue(result)
        self.assertEqual(len(siem._buffer), 1)
        buffered = siem._buffer[0]
        self.assertEqual(buffered["_siem_type"], "ledger")
        self.assertEqual(buffered["_siem_source"], "noerelay-gateway")


# ---------------------------------------------------------------------------
# DockerfileTests
# ---------------------------------------------------------------------------


class DockerfileTests(unittest.TestCase):
    """Tests that Docker support files exist and have valid content."""

    def test_dockerfile_exists(self):
        dockerfile = ROOT / "Dockerfile"
        self.assertTrue(
            dockerfile.exists(),
            f"Dockerfile not found at {dockerfile}",
        )

    def test_dockerfile_has_from(self):
        dockerfile = ROOT / "Dockerfile"
        content = dockerfile.read_text()
        self.assertIn("FROM python", content)
        self.assertIn("EXPOSE", content)
        self.assertIn("CMD", content)

    def test_dockerignore_exists(self):
        dockerignore = ROOT / ".dockerignore"
        self.assertTrue(
            dockerignore.exists(),
            f".dockerignore not found at {dockerignore}",
        )

    def test_docker_compose_exists(self):
        compose = ROOT / "docker-compose.yml"
        self.assertTrue(
            compose.exists(),
            f"docker-compose.yml not found at {compose}",
        )

    def test_docker_compose_has_services(self):
        compose = ROOT / "docker-compose.yml"
        content = compose.read_text()
        self.assertIn("noerelay:", content)
        self.assertIn("8080", content)


# ---------------------------------------------------------------------------
# KubernetesManifestTests
# ---------------------------------------------------------------------------


class KubernetesManifestTests(unittest.TestCase):
    """Tests that Kubernetes manifests exist and are valid YAML."""

    def test_deployment_exists(self):
        path = ROOT / "deploy" / "kubernetes" / "deployment.yaml"
        self.assertTrue(path.exists(), f"deployment.yaml not found at {path}")

    def test_deployment_valid(self):
        path = ROOT / "deploy" / "kubernetes" / "deployment.yaml"
        content = path.read_text()
        self.assertIn("kind: Deployment", content)
        self.assertIn("name: noerelay", content)
        self.assertIn("containerPort: 8080", content)
        self.assertIn("readinessProbe", content)
        self.assertIn("livenessProbe", content)

    def test_service_exists(self):
        path = ROOT / "deploy" / "kubernetes" / "service.yaml"
        self.assertTrue(path.exists(), f"service.yaml not found at {path}")

    def test_service_valid(self):
        path = ROOT / "deploy" / "kubernetes" / "service.yaml"
        content = path.read_text()
        self.assertIn("kind: Service", content)
        self.assertIn("name: noerelay", content)
        self.assertIn("targetPort: 8080", content)

    def test_pvc_exists(self):
        path = ROOT / "deploy" / "kubernetes" / "pvc.yaml"
        self.assertTrue(path.exists(), f"pvc.yaml not found at {path}")

    def test_pvc_valid(self):
        path = ROOT / "deploy" / "kubernetes" / "pvc.yaml"
        content = path.read_text()
        self.assertIn("kind: PersistentVolumeClaim", content)
        self.assertIn("name: noerelay-data", content)

    def test_secret_exists(self):
        path = ROOT / "deploy" / "kubernetes" / "secret.yaml"
        self.assertTrue(path.exists(), f"secret.yaml not found at {path}")

    def test_secret_valid(self):
        path = ROOT / "deploy" / "kubernetes" / "secret.yaml"
        content = path.read_text()
        self.assertIn("kind: Secret", content)
        self.assertIn("name: noerelay-secrets", content)


# ---------------------------------------------------------------------------
# GitHubActionsTests
# ---------------------------------------------------------------------------


class GitHubActionsTests(unittest.TestCase):
    """Tests that CI/CD workflow files exist and are valid."""

    def test_ci_workflow_exists(self):
        path = ROOT / ".github" / "workflows" / "ci.yml"
        self.assertTrue(path.exists(), f"ci.yml not found at {path}")

    def test_ci_workflow_valid(self):
        path = ROOT / ".github" / "workflows" / "ci.yml"
        content = path.read_text()
        self.assertIn("name: CI", content)
        self.assertIn("pytest", content)
        self.assertIn("docker build", content)

    def test_benchmark_workflow_exists(self):
        path = ROOT / ".github" / "workflows" / "benchmark.yml"
        self.assertTrue(path.exists(), f"benchmark.yml not found at {path}")

    def test_benchmark_workflow_valid(self):
        path = ROOT / ".github" / "workflows" / "benchmark.yml"
        content = path.read_text()
        self.assertIn("name: Benchmark", content)
        self.assertIn("run_benchmark", content)


# ---------------------------------------------------------------------------
# MetricsEndpointIntegrationTests
# ---------------------------------------------------------------------------


class MetricsEndpointIntegrationTests(unittest.TestCase):
    """Integration tests for the /metrics endpoint content negotiation."""

    @classmethod
    def _make_config(cls):
        from gateway.compression import CompressionConfig
        from gateway.config import GatewayConfig
        return GatewayConfig(
            host="127.0.0.1",
            port=8080,
            openrouter_mode="stub",
            policy_path=ROOT / "spec" / "routing-policy.json",
            state_machine_path=ROOT / "spec" / "verification-state-machine.json",
            portfolio_path=ROOT / "examples" / "candidate-actions.json",
            default_max_cost_usd=0.25,
            default_max_latency_ms=60000,
            external_base_url="http://127.0.0.1:8080",
            openrouter_base_url="https://openrouter.ai/api/v1",
            openrouter_api_key=None,
            openrouter_http_referer="",
            openrouter_app_title="",
            live_tests=False,
            auth_api_keys=None,
            rate_limit_rate=10.0,
            rate_limit_burst=20,
            persistence_dir=None,
            enable_health_endpoint=True,
            enable_metrics_endpoint=True,
            local_model_url="http://127.0.0.1:11434",
            local_model_enabled=False,
            escalation_hir_threshold=0.15,
            escalation_rr_threshold=0.25,
            cache_enabled=False,
            cache_max_size=100,
            cache_ttl_seconds=3600,
            database_path=":memory:",
            database_enabled=False,
            log_level="INFO",
            log_output="stdout",
            log_file_path="",
            tls_enabled=False,
            tls_cert_path=None,
            tls_key_path=None,
            compression=CompressionConfig(),
        )

    def test_handle_metrics_json_format(self):
        import json
        from gateway.handlers import handle_metrics
        from gateway.openrouter import StubOpenRouterClient
        from gateway.pipeline import PipelineContext
        from gateway.runs import RunRegistry
        from gateway.statemachine import VerificationStateMachine

        config = self._make_config()
        spec = json.loads(config.state_machine_path.read_text())
        sm = VerificationStateMachine(spec)
        registry = RunRegistry()
        client = StubOpenRouterClient({})

        ctx = PipelineContext(
            config=config,
            policy={},
            portfolio=[],
            openrouter_client=client,
            state_machine=sm,
            registry=registry,
        )

        # Request JSON format
        status, body, content_type = handle_metrics(ctx, accept_header="application/json")
        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json")
        self.assertIsInstance(body, dict)
        self.assertIn("runs_total", body)

    def test_handle_metrics_prometheus_format(self):
        import json
        from gateway.handlers import handle_metrics
        from gateway.openrouter import StubOpenRouterClient
        from gateway.pipeline import PipelineContext
        from gateway.runs import RunRegistry
        from gateway.statemachine import VerificationStateMachine

        config = self._make_config()
        spec = json.loads(config.state_machine_path.read_text())
        sm = VerificationStateMachine(spec)
        registry = RunRegistry()
        client = StubOpenRouterClient({})

        ctx = PipelineContext(
            config=config,
            policy={},
            portfolio=[],
            openrouter_client=client,
            state_machine=sm,
            registry=registry,
        )

        # Request Prometheus format (default, no Accept header)
        status, body, content_type = handle_metrics(ctx, accept_header="")
        self.assertEqual(status, 200)
        self.assertEqual(content_type, "text/plain")
        self.assertIsInstance(body, str)
        self.assertIn("# HELP", body)

    def test_handle_metrics_prometheus_with_instance(self):
        import json
        from gateway.handlers import handle_metrics
        from gateway.openrouter import StubOpenRouterClient
        from gateway.pipeline import PipelineContext
        from gateway.prometheus import PrometheusMetrics
        from gateway.runs import RunRegistry
        from gateway.statemachine import VerificationStateMachine

        config = self._make_config()
        spec = json.loads(config.state_machine_path.read_text())
        sm = VerificationStateMachine(spec)
        registry = RunRegistry()
        client = StubOpenRouterClient({})
        pm = PrometheusMetrics()
        pm.inc_counter("noerelay_runs_total", value=42)

        ctx = PipelineContext(
            config=config,
            policy={},
            portfolio=[],
            openrouter_client=client,
            state_machine=sm,
            registry=registry,
        )
        ctx.prometheus_metrics = pm

        status, body, content_type = handle_metrics(ctx, accept_header="")
        self.assertEqual(status, 200)
        self.assertEqual(content_type, "text/plain")
        self.assertIsInstance(body, str)
        self.assertIn("noerelay_runs_total 42", body)


if __name__ == "__main__":
    unittest.main()