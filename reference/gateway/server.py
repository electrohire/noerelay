"""ThreadingHTTPServer adapter — no business logic.

Routes are parsed here and delegated to pure functions in ``handlers.py``.
The layering rule is ``server.py`` -> ``handlers.py`` -> ``pipeline.py`` -> ``epr``.
"""

from __future__ import annotations

import json
import re
import signal
import ssl
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import GatewayConfig
from .handlers import (
    handle_acknowledge_alert,
    handle_add_alert_rule,
    handle_add_candidate,
    handle_audit_analytics,
    handle_benchmark_analytics,
    handle_cache_stats,
    handle_chat_completions,
    handle_compare_models,
    handle_cost_analytics,
    handle_create_api_key,
    handle_create_tenant,
    handle_dashboard_data,
    handle_dashboard_html,
    handle_delete_secret,
    handle_delete_tenant,
    handle_delete_webhook,
    handle_escalation_analytics,
    handle_export_data,
    handle_get_config,
    handle_get_policy,
    handle_get_portfolio,
    handle_get_run,
    handle_get_risk_classes,
    handle_get_trace,
    handle_health,
    handle_import_data,
    handle_ledger_chain,
    handle_ledger_events,
    handle_ledger_export,
    handle_ledger_verify,
    handle_list_alerts,
    handle_list_api_keys,
    handle_list_benchmark_results,
    handle_list_local_models,
    handle_list_models,
    handle_list_secrets,
    handle_list_tenants,
    handle_list_webhooks,
    handle_metrics,
    handle_model_cloud,
    handle_model_ranking,
    handle_model_recommendations,
    handle_performance_analytics,
    handle_pull_model,
    handle_readiness,
    handle_register_model,
    handle_register_webhook,
    handle_remove_candidate,
    handle_remove_model,
    handle_responses,
    handle_revoke_api_key,
    handle_rotate_api_key,
    handle_run_benchmark,
    handle_set_config,
    handle_store_secret,
    handle_tenant_budget,
    handle_update_candidate,
    handle_update_policy,
    handle_update_risk_class,
    handle_update_tenant,
    handle_usage_analytics,
)
from .pipeline import PipelineContext
from .render import error_envelope
from .streaming import SSEStreamer, StreamResponse

_RUN_PATH = re.compile(r"^/v1/epr/runs/([A-Za-z0-9][A-Za-z0-9._:/-]{0,127})$")
_TRACE_PATH = re.compile(r"^/v1/epr/runs/([A-Za-z0-9][A-Za-z0-9._:/-]{0,127})/trace$")
_LEDGER_CHAIN_PATH = re.compile(r"^/v1/epr/ledger/chain/([A-Za-z0-9][A-Za-z0-9._:/-]{0,127})$")
_LEDGER_EXPORT_PATH = re.compile(r"^/v1/epr/ledger/export/([A-Za-z0-9][A-Za-z0-9._:/-]{0,127})$")
_LEDGER_VERIFY_PATH = re.compile(r"^/v1/epr/ledger/verify/([A-Za-z0-9][A-Za-z0-9._:/-]{0,127})$")
_MODEL_DELETE_PATH = re.compile(r"^/v1/models/([A-Za-z0-9][A-Za-z0-9._:/-]{0,127})$")
_CANDIDATE_PATH = re.compile(r"^/v1/routing/candidates/([A-Za-z0-9][A-Za-z0-9._:/-]{0,127})$")
_API_KEY_PATH = re.compile(r"^/v1/api-keys/([A-Za-z0-9][A-Za-z0-9._:/-]{0,127})$")
_API_KEY_ROTATE_PATH = re.compile(r"^/v1/api-keys/([A-Za-z0-9][A-Za-z0-9._:/-]{0,127})/rotate$")
_RISK_CLASS_PATH = re.compile(r"^/v1/governance/risk-class/([A-Za-z0-9][A-Za-z0-9._:/-]{0,127})$")
# Governance paths
_TENANT_PATH = re.compile(r"^/v1/tenants/([A-Za-z0-9][A-Za-z0-9._:/-]{0,127})$")
_TENANT_BUDGET_PATH = re.compile(r"^/v1/tenants/([A-Za-z0-9][A-Za-z0-9._:/-]{0,127})/budget$")
_ALERT_ACK_PATH = re.compile(r"^/v1/alerts/([A-Za-z0-9][A-Za-z0-9._:/-]{0,127})/acknowledge$")
_WEBHOOK_PATH = re.compile(r"^/v1/webhooks/([A-Za-z0-9][A-Za-z0-9._:/-]{0,127})$")
_CONFIG_KEY_PATH = re.compile(r"^/v1/config/([A-Za-z0-9][A-Za-z0-9._:/-]{0,127})$")
_SECRET_PATH = re.compile(r"^/v1/secrets/([A-Za-z0-9][A-Za-z0-9._:/-]{0,127})$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class _NoeRelayHTTPServer(ThreadingHTTPServer):
    """Thread-per-request server with bounded shutdown-safe workers."""

    daemon_threads = True
    block_on_close = True
    allow_reuse_address = True
    request_queue_size = 128


class _ShutdownState:
    """Shared state for graceful shutdown with request draining."""

    def __init__(self) -> None:
        self.shutdown_requested = False
        self.active_requests = 0
        self.lock = threading.Lock()


def _setup_graceful_shutdown(
    server: ThreadingHTTPServer,
    shutdown_state: _ShutdownState | None = None,
) -> None:
    """Install SIGTERM/SIGINT handlers that gracefully stop the server.

    When *shutdown_state* is provided, the handler waits for active requests
    to drain (up to 30 seconds) before shutting down the server.
    """

    def shutdown(signum: int, frame: Any) -> None:
        if shutdown_state is not None:
            with shutdown_state.lock:
                shutdown_state.shutdown_requested = True

            # Wait for active requests to complete (max 30 seconds)
            timeout = 30
            start = time.monotonic()
            while (time.monotonic() - start) < timeout:
                with shutdown_state.lock:
                    active_requests = shutdown_state.active_requests
                if active_requests == 0:
                    break
                time.sleep(0.1)

        # BaseServer.shutdown() must run on a different thread from
        # serve_forever(); signal handlers execute on the main thread.
        threading.Thread(target=server.shutdown, daemon=True).start()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, shutdown)
        except (ValueError, OSError):
            # signal.signal may only be installed from the main thread.
            pass


def create_server(
    config: GatewayConfig, ctx: PipelineContext
) -> ThreadingHTTPServer:
    shutdown_state = _ShutdownState()
    request_slots = threading.BoundedSemaphore(config.max_concurrent_requests)

    class Handler(BaseHTTPRequestHandler):
        server_version = "NoeRelayGateway/0.1.0"

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(config.request_timeout_seconds)

        def _get_request_id(self) -> str:
            current = getattr(self, "_noerelay_request_id", None)
            if current is not None:
                return str(current)
            supplied = self.headers.get("X-Request-ID", "")
            request_id = supplied if _REQUEST_ID.fullmatch(supplied) else uuid.uuid4().hex
            self._noerelay_request_id = request_id
            return request_id

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _cors_origin(self) -> str | None:
            """Return a configured origin when this request is CORS-eligible."""
            origin = self.headers.get("Origin", "").rstrip("/")
            if origin and origin in config.cors_allowed_origins:
                return origin
            return None

        def _send_common_headers(self, extra_headers: dict[str, str] | None = None) -> None:
            origin = self._cors_origin()
            if origin is not None:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("X-Request-ID", self._get_request_id())
            if config.tls_enabled or config.trusted_proxy_tls:
                self.send_header(
                    "Strict-Transport-Security",
                    "max-age=31536000; includeSubDomains",
                )
            response_headers = dict(getattr(self, "_response_extra_headers", {}))
            response_headers.update(extra_headers or {})
            for name, value in response_headers.items():
                self.send_header(name, value)

        def _send_json(
            self,
            status: int,
            body: dict,
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self._send_common_headers(extra_headers)
            self.end_headers()
            self.wfile.write(data)

        def _send_text(self, status: int, body: str, content_type: str = "text/plain") -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self._send_common_headers()
            self.end_headers()
            self.wfile.write(data)

        def _send_stream(self, stream: StreamResponse) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self._send_common_headers()
            self.end_headers()
            for chunk in stream.chunks:
                self.wfile.write(SSEStreamer.format_chunk(chunk).encode("utf-8"))
                self.wfile.flush()
            self.wfile.write(SSEStreamer.format_done().encode("utf-8"))
            self.wfile.flush()
            # SSE uses a body terminated by EOF rather than Content-Length;
            # close the socket so clients stop reading after ``[DONE]``.
            self.close_connection = True

        def _path(self) -> str:
            return urlparse(self.path).path

        def _scoped_tenant(self, requested: str | None) -> str | None:
            """Prevent non-admin identities from selecting another tenant."""
            role = getattr(ctx.request_local, "role", None)
            tenant_id = getattr(ctx.request_local, "tenant_id", "default")
            return requested if role in (None, "admin") else tenant_id

        def _check_rbac_and_audit(
            self, method: str, path: str, role: str | None, action: str
        ) -> bool:
            """Check RBAC permissions and record audit trail.

            Returns True if the request is allowed, False if it was denied
            (response already sent).
            """
            if ctx.rbac is not None:
                allowed, reason = ctx.rbac.check_permission(method, path, role)
                if not allowed:
                    if ctx.audit_logger is not None:
                        ctx.audit_logger.log_api_call(
                            actor_id=role or "anonymous",
                            action=action,
                            resource_type="api",
                            resource_id=path,
                            ip_address=self.client_address[0],
                            details={"method": method, "reason": reason},
                            success=False,
                        )
                    self._send_json(
                        403,
                        error_envelope(
                            reason or "Access denied",
                            "forbidden",
                            None,
                            "insufficient_permissions",
                        ),
                    )
                    return False
            if ctx.audit_logger is not None:
                ctx.audit_logger.log_api_call(
                    actor_id=role or "anonymous",
                    action=action,
                    resource_type="api",
                    resource_id=path,
                    ip_address=self.client_address[0],
                    details={"method": method},
                    success=True,
                )
            return True

        def _query_int(
            self,
            query: dict[str, list[str]],
            name: str,
            default: int,
            *,
            minimum: int = 0,
            maximum: int = 1000,
        ) -> int | None:
            """Parse one bounded integer query parameter or send a 400."""
            raw = query.get(name, [str(default)])[0]
            try:
                value = int(raw)
            except (TypeError, ValueError):
                value = minimum - 1
            if not minimum <= value <= maximum:
                self._send_json(
                    400,
                    error_envelope(
                        f"{name} must be an integer from {minimum} to {maximum}",
                        "invalid_request_error",
                        name,
                        "invalid_query_parameter",
                    ),
                )
                return None
            return value

        def _handle_get(self) -> None:
            path = self._path()
            qs = parse_qs(urlparse(self.path).query)

            # Dashboard
            if path == "/dashboard":
                status, body, content_type = handle_dashboard_html()
                return self._send_text(status, body, content_type)

            # OpenAI-compatible
            if path == "/v1/models":
                status, body = handle_list_models(ctx)
                return self._send_json(status, body)

            # Model discovery
            if path == "/models/local":
                status, body = handle_list_local_models(ctx)
                return self._send_json(status, body)
            if path == "/models/recommendations":
                status, body = handle_model_recommendations(ctx)
                return self._send_json(status, body)
            if path == "/models/cloud":
                status, body = handle_model_cloud(ctx)
                return self._send_json(status, body)
            if path == "/models/ranking":
                status, body = handle_model_ranking(ctx)
                return self._send_json(status, body)

            # System
            if path == "/cache/stats":
                status, body = handle_cache_stats(ctx)
                return self._send_json(status, body)
            if path == "/health" and config.enable_health_endpoint:
                status, body = handle_health(ctx)
                return self._send_json(status, body)
            if path == "/ready" and config.enable_health_endpoint:
                status, body = handle_readiness(ctx)
                return self._send_json(status, body)
            if path == "/metrics" and config.enable_metrics_endpoint:
                accept = self.headers.get("Accept", "")
                status, body, content_type = handle_metrics(ctx, accept_header=accept)
                if content_type == "text/plain":
                    return self._send_text(status, body, content_type)
                return self._send_json(status, body)

            # Admin
            if path == "/v1/admin/export":
                status, body = _handle_admin_export(ctx)
                return self._send_json(status, body)

            # EPR
            match = _TRACE_PATH.match(path)
            if match:
                status, body = handle_get_trace(match.group(1), ctx)
                return self._send_json(status, body)
            match = _LEDGER_CHAIN_PATH.match(path)
            if match:
                status, body = handle_ledger_chain(match.group(1), ctx)
                return self._send_json(status, body)
            match = _LEDGER_EXPORT_PATH.match(path)
            if match:
                status, body = handle_ledger_export(match.group(1), ctx)
                return self._send_json(status, body)
            if path == "/v1/epr/ledger/events":
                status, body = handle_ledger_events(
                    ctx,
                    run_id=qs.get("run_id", [None])[0],
                    event_type=qs.get("event_type", [None])[0],
                    actor=qs.get("actor", [None])[0],
                    from_ts=qs.get("from", [None])[0],
                    to_ts=qs.get("to", [None])[0],
                )
                return self._send_json(status, body)
            match = _RUN_PATH.match(path)
            if match:
                status, body = handle_get_run(match.group(1), ctx)
                return self._send_json(status, body)

            # Benchmark
            if path == "/v1/benchmarks/results":
                limit = self._query_int(qs, "limit", 50, minimum=1, maximum=1000)
                offset = self._query_int(qs, "offset", 0, maximum=1_000_000)
                if limit is None or offset is None:
                    return
                status, body = handle_list_benchmark_results(
                    ctx,
                    cohort=qs.get("cohort", [None])[0],
                    model_id=qs.get("model_id", [None])[0],
                    limit=limit,
                    offset=offset,
                )
                return self._send_json(status, body)
            if path == "/v1/benchmarks/compare":
                query_params: dict[str, Any] = {}
                for key in qs:
                    query_params[key] = qs[key][0] if len(qs[key]) == 1 else qs[key]
                status, body = handle_compare_models(query_params, ctx)
                return self._send_json(status, body)

            # Governance
            if path == "/v1/governance/policy":
                status, body = handle_get_policy(ctx)
                return self._send_json(status, body)
            if path == "/v1/governance/risk-classes":
                status, body = handle_get_risk_classes(ctx)
                return self._send_json(status, body)

            # Routing
            if path == "/v1/routing/portfolio":
                status, body = handle_get_portfolio(ctx)
                return self._send_json(status, body)

            # API Keys
            if path == "/v1/api-keys":
                status, body = handle_list_api_keys(
                    ctx,
                    tenant_id=self._scoped_tenant(qs.get("tenant_id", [None])[0]),
                )
                return self._send_json(status, body)

            # Analytics
            if path == "/v1/analytics/cost":
                query_params = {}
                for key in qs:
                    query_params[key] = qs[key][0] if len(qs[key]) == 1 else qs[key]
                status, body = handle_cost_analytics(query_params, ctx)
                return self._send_json(status, body)
            if path == "/v1/analytics/performance":
                query_params = {}
                for key in qs:
                    query_params[key] = qs[key][0] if len(qs[key]) == 1 else qs[key]
                status, body = handle_performance_analytics(query_params, ctx)
                return self._send_json(status, body)
            if path == "/v1/analytics/usage":
                query_params = {}
                for key in qs:
                    query_params[key] = qs[key][0] if len(qs[key]) == 1 else qs[key]
                status, body = handle_usage_analytics(query_params, ctx)
                return self._send_json(status, body)
            if path == "/v1/analytics/escalations":
                query_params = {}
                for key in qs:
                    query_params[key] = qs[key][0] if len(qs[key]) == 1 else qs[key]
                status, body = handle_escalation_analytics(query_params, ctx)
                return self._send_json(status, body)
            if path == "/v1/analytics/audit":
                query_params = {}
                for key in qs:
                    query_params[key] = qs[key][0] if len(qs[key]) == 1 else qs[key]
                status, body = handle_audit_analytics(query_params, ctx)
                return self._send_json(status, body)
            if path == "/v1/analytics/benchmarks":
                query_params = {}
                for key in qs:
                    query_params[key] = qs[key][0] if len(qs[key]) == 1 else qs[key]
                status, body = handle_benchmark_analytics(query_params, ctx)
                return self._send_json(status, body)
            if path == "/v1/analytics/dashboard":
                status, body = handle_dashboard_data(ctx)
                return self._send_json(status, body)

            # Export
            if path == "/v1/export":
                query_params = {}
                for key in qs:
                    query_params[key] = qs[key][0] if len(qs[key]) == 1 else qs[key]
                status, body = handle_export_data(query_params, ctx)
                return self._send_json(status, body)

            # Tenants
            if path == "/v1/tenants":
                status, body = handle_list_tenants(
                    ctx,
                    ctx.tenant_manager,
                    tenant_id=self._scoped_tenant(None),
                )
                return self._send_json(status, body)
            match = _TENANT_BUDGET_PATH.match(path)
            if match:
                status, body = handle_tenant_budget(
                    self._scoped_tenant(match.group(1)) or "default",
                    ctx.tenant_manager,
                )
                return self._send_json(status, body)

            # Alerts
            if path == "/v1/alerts":
                ack_raw = qs.get("acknowledged", [None])[0]
                ack_val = None
                if ack_raw is not None:
                    ack_val = ack_raw.lower() == "true"
                limit = self._query_int(qs, "limit", 50, minimum=1, maximum=1000)
                if limit is None:
                    return
                status, body = handle_list_alerts(
                    ctx.alert_manager,
                    severity=qs.get("severity", [None])[0],
                    acknowledged=ack_val,
                    limit=limit,
                )
                return self._send_json(status, body)

            # Webhooks
            if path == "/v1/webhooks":
                status, body = handle_list_webhooks(
                    ctx.webhook_manager,
                    tenant_id=self._scoped_tenant(qs.get("tenant_id", [None])[0]),
                )
                return self._send_json(status, body)

            # Config
            if path == "/v1/config":
                status, body = handle_get_config(ctx.config_manager)
                return self._send_json(status, body)

            # Secrets
            if path == "/v1/secrets":
                status, body = handle_list_secrets(
                    ctx.secret_manager,
                    tenant_id=self._scoped_tenant(
                        qs.get("tenant_id", ["default"])[0]
                    ) or "default",
                )
                return self._send_json(status, body)

            self._send_json(
                404, error_envelope("Not found", "invalid_request_error", None, "not_found")
            )

        def _read_json_body(self) -> dict[str, Any] | None:
            """Read and parse JSON body. Returns None on failure (response sent)."""
            if self.headers.get("Transfer-Encoding"):
                self._send_json(
                    400,
                    error_envelope(
                        "Transfer-Encoding is not supported",
                        "invalid_request_error",
                        None,
                        "unsupported_transfer_encoding",
                    ),
                )
                self.close_connection = True
                return None
            content_lengths = self.headers.get_all("Content-Length", failobj=[])
            if len(content_lengths) > 1:
                self._send_json(
                    400,
                    error_envelope(
                        "multiple Content-Length headers are not allowed",
                        "invalid_request_error",
                        None,
                        "invalid_content_length",
                    ),
                )
                self.close_connection = True
                return None
            raw_length = self.headers.get("Content-Length")
            try:
                length = int(raw_length or 0)
            except ValueError:
                self._send_json(
                    400,
                    error_envelope(
                        "invalid Content-Length header",
                        "invalid_request_error",
                        None,
                        "invalid_content_length",
                    ),
                )
                return None
            if length < 0:
                self._send_json(
                    400,
                    error_envelope(
                        "Content-Length must not be negative",
                        "invalid_request_error",
                        None,
                        "invalid_content_length",
                    ),
                )
                return None
            if length > config.max_request_body_bytes:
                self._send_json(
                    413,
                    error_envelope(
                        "request body exceeds the configured size limit",
                        "invalid_request_error",
                        None,
                        "request_too_large",
                    ),
                )
                self.close_connection = True
                return None
            raw = self.rfile.read(length) if length else b""
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                self._send_json(
                    400,
                    error_envelope(
                        "invalid JSON body",
                        "invalid_request_error",
                        None,
                        "invalid_json",
                    ),
                )
                return None
            if not isinstance(parsed, dict):
                self._send_json(
                    400,
                    error_envelope(
                        "request body must be a JSON object",
                        "invalid_request_error",
                        None,
                        "invalid_json",
                    ),
                )
                return None
            return parsed

        def _handle_post(self) -> None:
            path = self._path()

            # OpenAI-compatible
            if path == "/v1/chat/completions":
                parsed = self._read_json_body()
                if parsed is None:
                    return
                result = handle_chat_completions(parsed, ctx)
                if isinstance(result, StreamResponse):
                    return self._send_stream(result)
                status, body = result
                return self._send_json(status, body)
            if path == "/v1/responses":
                parsed = self._read_json_body()
                if parsed is None:
                    return
                result = handle_responses(parsed, ctx)
                if isinstance(result, StreamResponse):
                    return self._send_stream(result)
                status, body = result
                return self._send_json(status, body)

            # EPR
            match = _LEDGER_VERIFY_PATH.match(path)
            if match:
                status, body = handle_ledger_verify(match.group(1), ctx)
                return self._send_json(status, body)

            # Admin
            if path == "/v1/admin/backup":
                status, body = _handle_admin_backup(ctx)
                return self._send_json(status, body)
            if path == "/v1/admin/restore":
                parsed = self._read_json_body()
                if parsed is None:
                    return
                status, body = _handle_admin_restore(ctx, parsed)
                return self._send_json(status, body)

            # Model Management
            if path == "/v1/models/pull":
                parsed = self._read_json_body()
                if parsed is None:
                    return
                status, body = handle_pull_model(parsed, ctx)
                return self._send_json(status, body)
            if path == "/v1/models/register":
                parsed = self._read_json_body()
                if parsed is None:
                    return
                status, body = handle_register_model(parsed, ctx)
                return self._send_json(status, body)

            # Benchmark
            if path == "/v1/benchmarks/run":
                parsed = self._read_json_body()
                if parsed is None:
                    return
                status, body = handle_run_benchmark(parsed, ctx)
                return self._send_json(status, body)

            # Governance
            if path == "/v1/governance/policy":
                parsed = self._read_json_body()
                if parsed is None:
                    return
                status, body = handle_update_policy(parsed, ctx)
                return self._send_json(status, body)

            # Routing
            if path == "/v1/routing/candidates":
                parsed = self._read_json_body()
                if parsed is None:
                    return
                status, body = handle_add_candidate(parsed, ctx)
                return self._send_json(status, body)

            # API Keys
            if path == "/v1/api-keys":
                parsed = self._read_json_body()
                if parsed is None:
                    return
                status, body = handle_create_api_key(parsed, ctx)
                return self._send_json(status, body)
            match = _API_KEY_ROTATE_PATH.match(path)
            if match:
                status, body = handle_rotate_api_key(match.group(1), ctx)
                return self._send_json(status, body)

            # Import
            if path == "/v1/import":
                parsed = self._read_json_body()
                if parsed is None:
                    return
                status, body = handle_import_data(parsed, ctx)
                return self._send_json(status, body)

            # Tenants
            if path == "/v1/tenants":
                parsed = self._read_json_body()
                if parsed is None:
                    return
                status, body = handle_create_tenant(parsed, ctx.tenant_manager)
                return self._send_json(status, body)

            # Alerts
            if path == "/v1/alerts/rules":
                parsed = self._read_json_body()
                if parsed is None:
                    return
                status, body = handle_add_alert_rule(parsed, ctx.alert_manager)
                return self._send_json(status, body)
            match = _ALERT_ACK_PATH.match(path)
            if match:
                parsed = self._read_json_body()
                if parsed is None:
                    return
                acknowledged_by = parsed.get("acknowledged_by", "api")
                status, body = handle_acknowledge_alert(
                    match.group(1), acknowledged_by, ctx.alert_manager
                )
                return self._send_json(status, body)

            # Webhooks
            if path == "/v1/webhooks":
                parsed = self._read_json_body()
                if parsed is None:
                    return
                status, body = handle_register_webhook(parsed, ctx.webhook_manager)
                return self._send_json(status, body)

            # Secrets
            if path == "/v1/secrets":
                parsed = self._read_json_body()
                if parsed is None:
                    return
                status, body = handle_store_secret(parsed, ctx.secret_manager)
                return self._send_json(status, body)

            self._send_json(
                404, error_envelope("Not found", "invalid_request_error", None, "not_found")
            )

        def _handle_other(self) -> None:
            path = self._path()

            # DELETE routes
            if self.command == "DELETE":
                match = _MODEL_DELETE_PATH.match(path)
                if match:
                    status, body = handle_remove_model(
                        {"model_name": match.group(1)}, ctx
                    )
                    return self._send_json(status, body)
                match = _CANDIDATE_PATH.match(path)
                if match:
                    status, body = handle_remove_candidate(match.group(1), ctx)
                    return self._send_json(status, body)
                match = _API_KEY_PATH.match(path)
                if match:
                    status, body = handle_revoke_api_key(match.group(1), ctx)
                    return self._send_json(status, body)
                # Governance DELETE routes
                match = _TENANT_PATH.match(path)
                if match:
                    status, body = handle_delete_tenant(match.group(1), ctx.tenant_manager)
                    return self._send_json(status, body)
                match = _WEBHOOK_PATH.match(path)
                if match:
                    status, body = handle_delete_webhook(match.group(1), ctx.webhook_manager)
                    return self._send_json(status, body)
                match = _SECRET_PATH.match(path)
                if match:
                    status, body = handle_delete_secret(
                        match.group(1), ctx.secret_manager
                    )
                    return self._send_json(status, body)

            # PUT routes
            if self.command == "PUT":
                match = _CANDIDATE_PATH.match(path)
                if match:
                    parsed = self._read_json_body()
                    if parsed is None:
                        return
                    status, body = handle_update_candidate(
                        match.group(1), parsed, ctx
                    )
                    return self._send_json(status, body)
                match = _RISK_CLASS_PATH.match(path)
                if match:
                    parsed = self._read_json_body()
                    if parsed is None:
                        return
                    parsed["risk_class"] = match.group(1)
                    status, body = handle_update_risk_class(parsed, ctx)
                    return self._send_json(status, body)
                # Governance PUT routes
                match = _TENANT_PATH.match(path)
                if match:
                    parsed = self._read_json_body()
                    if parsed is None:
                        return
                    status, body = handle_update_tenant(
                        match.group(1), parsed, ctx.tenant_manager
                    )
                    return self._send_json(status, body)
                match = _CONFIG_KEY_PATH.match(path)
                if match:
                    parsed = self._read_json_body()
                    if parsed is None:
                        return
                    status, body = handle_set_config(
                        match.group(1), parsed, ctx.config_manager
                    )
                    return self._send_json(status, body)

            # Known paths that don't support this method
            known = (
                path in {
                    "/v1/models", "/v1/chat/completions", "/v1/responses",
                    "/models/local", "/models/recommendations",
                    "/models/cloud", "/models/ranking", "/cache/stats",
                    "/v1/epr/ledger/events",
                    "/v1/admin/backup", "/v1/admin/restore",
                    "/v1/admin/export",
                    "/v1/models/pull", "/v1/models/register",
                    "/v1/benchmarks/run", "/v1/benchmarks/results",
                    "/v1/benchmarks/compare",
                    "/v1/governance/policy", "/v1/governance/risk-classes",
                    "/v1/routing/portfolio", "/v1/routing/candidates",
                    "/v1/api-keys",
                    "/v1/analytics/cost", "/v1/analytics/performance",
                    "/v1/analytics/usage", "/v1/analytics/escalations",
                    "/v1/analytics/audit", "/v1/analytics/benchmarks",
                    "/v1/analytics/dashboard",
                    "/v1/export", "/v1/import",
                    "/v1/tenants", "/v1/alerts", "/v1/alerts/rules",
                    "/v1/webhooks", "/v1/config", "/v1/secrets",
                    "/dashboard",
                }
                or bool(_RUN_PATH.match(path))
                or bool(_TRACE_PATH.match(path))
                or bool(_LEDGER_CHAIN_PATH.match(path))
                or bool(_LEDGER_EXPORT_PATH.match(path))
                or bool(_LEDGER_VERIFY_PATH.match(path))
                or bool(_MODEL_DELETE_PATH.match(path))
                or bool(_CANDIDATE_PATH.match(path))
                or bool(_API_KEY_PATH.match(path))
                or bool(_API_KEY_ROTATE_PATH.match(path))
                or bool(_RISK_CLASS_PATH.match(path))
                or bool(_TENANT_PATH.match(path))
                or bool(_TENANT_BUDGET_PATH.match(path))
                or bool(_ALERT_ACK_PATH.match(path))
                or bool(_WEBHOOK_PATH.match(path))
                or bool(_CONFIG_KEY_PATH.match(path))
                or bool(_SECRET_PATH.match(path))
                or (path == "/health" and config.enable_health_endpoint)
                or (path == "/ready" and config.enable_health_endpoint)
                or (path == "/metrics" and config.enable_metrics_endpoint)
            )
            if known:
                return self._send_json(
                    405,
                    error_envelope(
                        "Method not allowed",
                        "invalid_request_error",
                        None,
                        "method_not_allowed",
                    ),
                )
            self._send_json(
                404, error_envelope("Not found", "invalid_request_error", None, "not_found")
            )

        def _authenticate_and_authorize(self) -> tuple[bool, dict[str, str]]:
            """Enforce authentication, rate limits, RBAC, and audit logging."""
            path = self._path()
            if path in {"/health", "/ready"}:
                return True, {}

            role: str | None = None
            rate_headers: dict[str, str] = {}
            if ctx.auth is not None:
                headers = {name: value for name, value in self.headers.items()}
                allowed, identity, rate_headers = ctx.auth.authenticate_with_metadata(headers)
                self._response_extra_headers = rate_headers
                if not allowed:
                    if ctx.audit_logger is not None:
                        ctx.audit_logger.log_api_call(
                            actor_id=(identity or {}).get("key_id", "anonymous"),
                            action=f"{self.command.lower()}:{path}",
                            resource_type="api",
                            resource_id=path,
                            ip_address=self.client_address[0],
                            details={
                                "method": self.command,
                                "reason": "rate_limited" if identity else "authentication_failed",
                            },
                            success=False,
                        )
                    if identity and identity.get("rate_limited"):
                        self._send_json(
                            429,
                            error_envelope(
                                "rate limit exceeded",
                                "rate_limit_error",
                                None,
                                "rate_limit_exceeded",
                            ),
                            rate_headers,
                        )
                    else:
                        self._send_json(
                            401,
                            error_envelope(
                                "invalid or missing API key",
                                "authentication_error",
                                None,
                                "invalid_api_key",
                            ),
                        )
                    return False, rate_headers
                if identity is not None:
                    role = identity.get("role")
                    ctx.request_local.tenant_id = identity.get("tenant_id", "default")
                else:
                    ctx.request_local.tenant_id = "default"
                ctx.request_local.role = role

            action = f"{self.command.lower()}:{path}"
            if not self._check_rbac_and_audit(self.command, path, role, action):
                return False, rate_headers
            return True, rate_headers

        def _dispatch(self, handler: Any) -> None:
            """Run one request with shutdown accounting and sanitized failures."""
            if not request_slots.acquire(blocking=False):
                self._send_json(
                    503,
                    error_envelope(
                        "server request capacity is exhausted",
                        "server_error",
                        None,
                        "server_busy",
                    ),
                    {"Retry-After": "1"},
                )
                return
            with shutdown_state.lock:
                if shutdown_state.shutdown_requested:
                    self._send_json(
                        503,
                        error_envelope(
                            "gateway is shutting down",
                            "server_error",
                            None,
                            "shutting_down",
                        ),
                    )
                    request_slots.release()
                    return
                shutdown_state.active_requests += 1
            try:
                allowed, _ = self._authenticate_and_authorize()
                if allowed:
                    handler()
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True
            except Exception as exc:
                if ctx.logger is not None:
                    ctx.logger.error(
                        "unhandled HTTP request failure",
                        component="server",
                        exception_type=type(exc).__name__,
                    )
                try:
                    self._send_json(
                        500,
                        error_envelope(
                            "internal server error",
                            "server_error",
                            None,
                            "internal_error",
                        ),
                    )
                except (BrokenPipeError, ConnectionResetError):
                    self.close_connection = True
            finally:
                registry = getattr(ctx, "registry", None)
                db = getattr(registry, "_db", None)
                if db is not None and hasattr(db, "close_thread_connection"):
                    db.close_thread_connection()
                for attribute in ("tenant_id", "role"):
                    if hasattr(ctx.request_local, attribute):
                        delattr(ctx.request_local, attribute)
                with shutdown_state.lock:
                    shutdown_state.active_requests -= 1
                request_slots.release()

        def do_OPTIONS(self) -> None:
            """Handle browser CORS preflight without requiring credentials."""
            origin = self._cors_origin()
            if origin is None:
                return self._send_json(
                    403,
                    error_envelope(
                        "origin is not allowed",
                        "forbidden",
                        None,
                        "cors_origin_denied",
                    ),
                )
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Content-Length", "0")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()

        def do_GET(self) -> None:
            self._dispatch(self._handle_get)

        def do_POST(self) -> None:
            self._dispatch(self._handle_post)

        def do_DELETE(self) -> None:
            self._dispatch(self._handle_other)

        def do_PUT(self) -> None:
            self._dispatch(self._handle_other)

        def do_PATCH(self) -> None:
            self._dispatch(self._handle_other)

    server = _NoeRelayHTTPServer((config.host, config.port), Handler)

    # Apply TLS if enabled
    if config.tls_enabled and config.tls_cert_path and config.tls_key_path:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(
            certfile=config.tls_cert_path,
            keyfile=config.tls_key_path,
        )
        server.socket = context.wrap_socket(
            server.socket, server_side=True
        )

    _setup_graceful_shutdown(server, shutdown_state)
    return server


def _handle_admin_backup(ctx: PipelineContext) -> tuple[int, dict]:
    """Backup the database to a file."""
    if not hasattr(ctx, "registry") or not hasattr(ctx.registry, "_db"):
        return 501, error_envelope(
            "Database not enabled",
            "server_error",
            None,
            "database_not_enabled",
        )
    try:
        db = ctx.registry._db  # type: ignore[attr-defined]
        backup_path = db.backup(
            str(Path(db._db_path).resolve().with_name(  # type: ignore[attr-defined]
                f"{Path(db._db_path).stem}-backup.db"  # type: ignore[attr-defined]
            ))
        )
        return 200, {
            "status": "ok",
            "backup_path": backup_path,
        }
    except Exception as exc:
        return 500, error_envelope(
            "Backup failed",
            "server_error",
            None,
            "backup_failed",
        )


def _handle_admin_restore(
    ctx: PipelineContext, body: dict
) -> tuple[int, dict]:
    """Restore the database from a backup file."""
    if not hasattr(ctx, "registry") or not hasattr(ctx.registry, "_db"):
        return 501, error_envelope(
            "Database not enabled",
            "server_error",
            None,
            "database_not_enabled",
        )
    backup_path = body.get("backup_path")
    if not backup_path or not isinstance(backup_path, str):
        return 400, error_envelope(
            "backup_path is required",
            "invalid_request_error",
            "backup_path",
            "missing_field",
        )
    try:
        db = ctx.registry._db  # type: ignore[attr-defined]
        base = Path(db._db_path).resolve().parent  # type: ignore[attr-defined]
        candidate = Path(backup_path)
        if not candidate.is_absolute():
            candidate = (Path.cwd() / candidate).resolve()
        else:
            candidate = candidate.resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            return 400, error_envelope(
                "backup_path must remain inside the configured database directory",
                "invalid_request_error",
                "backup_path",
                "invalid_backup_path",
            )
        db.restore(str(candidate))
        return 200, {
            "status": "ok",
            "message": f"Restored from {backup_path}",
        }
    except FileNotFoundError:
        return 404, error_envelope(
            f"Backup file not found: {backup_path}",
            "invalid_request_error",
            "backup_path",
            "backup_not_found",
        )
    except Exception as exc:
        return 500, error_envelope(
            "Restore failed",
            "server_error",
            None,
            "restore_failed",
        )


def _handle_admin_export(ctx: PipelineContext) -> tuple[int, dict]:
    """Export all data as JSON."""
    if not hasattr(ctx, "registry") or not hasattr(ctx.registry, "_db"):
        return 501, error_envelope(
            "Database not enabled",
            "server_error",
            None,
            "database_not_enabled",
        )
    try:
        db = ctx.registry._db  # type: ignore[attr-defined]
        export_path = db.export_json(
            str(Path(db._db_path).resolve().with_name(  # type: ignore[attr-defined]
                f"{Path(db._db_path).stem}-export.json"  # type: ignore[attr-defined]
            ))
        )
        return 200, {
            "status": "ok",
            "export_path": export_path,
        }
    except Exception as exc:
        return 500, error_envelope(
            "Export failed",
            "server_error",
            None,
            "export_failed",
        )
