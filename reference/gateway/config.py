"""Dependency-free environment configuration for the NoeRelay gateway.

Phase 1 scope: load and validate environment variables. Policy, portfolio,
and state-machine *files* are loaded by their respective modules in later
phases; this module only resolves their paths and validates scalar settings.

Startup is fail-closed: any invalid value raises :class:`ConfigError`, and the
caller must not bind the server when configuration fails.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class ConfigError(RuntimeError):
    """A sanitized configuration error safe to surface at startup."""


# Repository root. This module lives at reference/gateway/config.py, so the
# root is two directories above the module's own directory.
ROOT = Path(__file__).resolve().parents[2]

_DEFAULT_EXTERNAL_BASE_URL = "http://127.0.0.1:8080"
_DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _value(environ: Mapping[str, str], name: str, default: str) -> str:
    value = environ.get(name, default)
    return default if value is None else value.strip()


def _parse_int(
    environ: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw = _value(environ, name, str(default))
    try:
        parsed = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer; got {raw!r}") from None
    if minimum is not None and parsed < minimum:
        raise ConfigError(f"{name} must be >= {minimum}; got {parsed}")
    if maximum is not None and parsed > maximum:
        raise ConfigError(f"{name} must be <= {maximum}; got {parsed}")
    return parsed


def _parse_float(
    environ: Mapping[str, str],
    name: str,
    default: float,
    *,
    minimum: float | None = None,
    exclusive: bool = False,
) -> float:
    raw = _value(environ, name, str(default))
    try:
        parsed = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number; got {raw!r}") from None
    if minimum is not None:
        if exclusive and parsed <= minimum:
            raise ConfigError(f"{name} must be > {minimum}; got {parsed}")
        if not exclusive and parsed < minimum:
            raise ConfigError(f"{name} must be >= {minimum}; got {parsed}")
    return parsed


def _resolve_path(environ: Mapping[str, str], name: str, default: str) -> Path:
    raw = _value(environ, name, default)
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def _parse_bool(environ: Mapping[str, str], name: str, default: bool) -> bool:
    raw = _value(environ, name, "1" if default else "0")
    if raw not in {"0", "1"}:
        raise ConfigError(f"{name} must be '0' or '1'; got {raw!r}")
    return raw == "1"


@dataclass(frozen=True)
class GatewayConfig:
    """Validated, dependency-free gateway configuration."""

    host: str
    port: int
    openrouter_mode: str
    policy_path: Path
    state_machine_path: Path
    portfolio_path: Path
    default_max_cost_usd: float
    default_max_latency_ms: int
    external_base_url: str
    openrouter_base_url: str
    openrouter_api_key: str | None
    openrouter_http_referer: str
    openrouter_app_title: str
    live_tests: bool
    auth_api_keys: str | None
    rate_limit_rate: float
    rate_limit_burst: int
    persistence_dir: str | None
    enable_health_endpoint: bool
    enable_metrics_endpoint: bool
    local_model_url: str
    local_model_enabled: bool
    escalation_hir_threshold: float
    escalation_rr_threshold: float
    cache_enabled: bool
    cache_max_size: int
    cache_ttl_seconds: int
    database_path: str
    database_enabled: bool
    log_level: str
    log_output: str
    log_file_path: str
    tls_enabled: bool
    tls_cert_path: str | None
    tls_key_path: str | None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "GatewayConfig":
        """Build a validated config from an environment mapping.

        When ``environ`` is ``None``, the process environment is read. Passing
        an explicit mapping keeps tests hermetic without global state.
        """
        env: Mapping[str, str] = os.environ if environ is None else environ

        host = _value(env, "NOERELAY_GATEWAY_HOST", "127.0.0.1")
        port = _parse_int(
            env, "NOERELAY_GATEWAY_PORT", 8080, minimum=0, maximum=65535
        )

        openrouter_mode = _value(env, "NOERELAY_OPENROUTER_MODE", "stub")
        if openrouter_mode not in {"stub", "live"}:
            raise ConfigError(
                "NOERELAY_OPENROUTER_MODE must be 'stub' or 'live'; "
                f"got {openrouter_mode!r}"
            )

        live_tests_raw = _value(env, "NOERELAY_LIVE_TESTS", "0")
        if live_tests_raw not in {"0", "1"}:
            raise ConfigError(
                "NOERELAY_LIVE_TESTS must be '0' or '1'; "
                f"got {live_tests_raw!r}"
            )
        live_tests = live_tests_raw == "1"

        openrouter_api_key = _value(env, "OPENROUTER_API_KEY", "") or None
        if openrouter_mode == "live" and not openrouter_api_key:
            raise ConfigError(
                "NOERELAY_OPENROUTER_MODE=live requires OPENROUTER_API_KEY "
                "to be set"
            )

        policy_path = _resolve_path(
            env, "NOERELAY_POLICY_PATH", "spec/routing-policy.json"
        )
        state_machine_path = _resolve_path(
            env, "NOERELAY_STATE_MACHINE_PATH", "spec/verification-state-machine.json"
        )
        portfolio_path = _resolve_path(
            env, "NOERELAY_PORTFOLIO_PATH", "examples/candidate-actions.json"
        )

        default_max_cost_usd = _parse_float(
            env,
            "NOERELAY_DEFAULT_MAX_COST_USD",
            0.25,
            minimum=0.0,
            exclusive=True,
        )
        default_max_latency_ms = _parse_int(
            env, "NOERELAY_DEFAULT_MAX_LATENCY_MS", 60000, minimum=1
        )

        external_base_url = _value(
            env, "NOERELAY_EXTERNAL_BASE_URL", _DEFAULT_EXTERNAL_BASE_URL
        ).rstrip("/")
        openrouter_base_url = _value(
            env, "OPENROUTER_BASE_URL", _DEFAULT_OPENROUTER_BASE_URL
        ).rstrip("/")
        openrouter_http_referer = _value(
            env, "OPENROUTER_HTTP_REFERER", "https://github.com/electrohire/noerelay"
        )
        openrouter_app_title = _value(env, "OPENROUTER_APP_TITLE", "NoeRelay")

        auth_api_keys = _value(env, "NOERELAY_AUTH_API_KEYS", "") or None
        rate_limit_rate = _parse_float(
            env, "NOERELAY_RATE_LIMIT_RATE", 10.0, minimum=0.0
        )
        rate_limit_burst = _parse_int(
            env, "NOERELAY_RATE_LIMIT_BURST", 20, minimum=1
        )
        persistence_dir_raw = _value(env, "NOERELAY_PERSISTENCE_DIR", "") or None
        persistence_dir: str | None = None
        if persistence_dir_raw:
            persistence_path = Path(persistence_dir_raw)
            persistence_dir = str(
                persistence_path
                if persistence_path.is_absolute()
                else ROOT / persistence_path
            )
        enable_health_endpoint = _parse_bool(
            env, "NOERELAY_ENABLE_HEALTH_ENDPOINT", True
        )
        enable_metrics_endpoint = _parse_bool(
            env, "NOERELAY_ENABLE_METRICS_ENDPOINT", True
        )

        local_model_url = _value(
            env, "NOERELAY_LOCAL_MODEL_URL", "http://127.0.0.1:11434"
        ).rstrip("/")
        local_model_enabled = _parse_bool(
            env, "NOERELAY_LOCAL_MODEL_ENABLED", True
        )
        escalation_hir_threshold = _parse_float(
            env, "NOERELAY_ESCALATION_HIR_THRESHOLD", 0.15, minimum=0.0
        )
        escalation_rr_threshold = _parse_float(
            env, "NOERELAY_ESCALATION_RR_THRESHOLD", 0.25, minimum=0.0
        )

        cache_enabled = _parse_bool(env, "NOERELAY_CACHE_ENABLED", False)
        cache_max_size = _parse_int(
            env, "NOERELAY_CACHE_MAX_SIZE", 100, minimum=1
        )
        cache_ttl_seconds = _parse_int(
            env, "NOERELAY_CACHE_TTL_SECONDS", 3600, minimum=1
        )

        database_path = _value(
            env, "NOERELAY_DATABASE_PATH", ".noerelay/noerelay.db"
        )
        database_enabled = _parse_bool(
            env, "NOERELAY_DATABASE_ENABLED", True
        )
        log_level = _value(env, "NOERELAY_LOG_LEVEL", "INFO")
        if log_level.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigError(
                f"NOERELAY_LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR, "
                f"CRITICAL; got {log_level!r}"
            )
        log_output = _value(env, "NOERELAY_LOG_OUTPUT", "stdout")
        if log_output not in {"stdout", "file"}:
            raise ConfigError(
                f"NOERELAY_LOG_OUTPUT must be 'stdout' or 'file'; got {log_output!r}"
            )
        log_file_path = _value(
            env, "NOERELAY_LOG_FILE_PATH", ".noerelay/noerelay.log"
        )
        tls_enabled = _parse_bool(env, "NOERELAY_TLS_ENABLED", False)
        tls_cert_path_raw = _value(env, "NOERELAY_TLS_CERT_PATH", "") or None
        tls_cert_path: str | None = None
        if tls_cert_path_raw:
            cert_path = Path(tls_cert_path_raw)
            tls_cert_path = str(
                cert_path if cert_path.is_absolute() else ROOT / cert_path
            )
        tls_key_path_raw = _value(env, "NOERELAY_TLS_KEY_PATH", "") or None
        tls_key_path: str | None = None
        if tls_key_path_raw:
            key_path = Path(tls_key_path_raw)
            tls_key_path = str(
                key_path if key_path.is_absolute() else ROOT / key_path
            )

        return cls(
            host=host,
            port=port,
            openrouter_mode=openrouter_mode,
            policy_path=policy_path,
            state_machine_path=state_machine_path,
            portfolio_path=portfolio_path,
            default_max_cost_usd=default_max_cost_usd,
            default_max_latency_ms=default_max_latency_ms,
            external_base_url=external_base_url,
            openrouter_base_url=openrouter_base_url,
            openrouter_api_key=openrouter_api_key,
            openrouter_http_referer=openrouter_http_referer,
            openrouter_app_title=openrouter_app_title,
            live_tests=live_tests,
            auth_api_keys=auth_api_keys,
            rate_limit_rate=rate_limit_rate,
            rate_limit_burst=rate_limit_burst,
            persistence_dir=persistence_dir,
            enable_health_endpoint=enable_health_endpoint,
            enable_metrics_endpoint=enable_metrics_endpoint,
            local_model_url=local_model_url,
            local_model_enabled=local_model_enabled,
            escalation_hir_threshold=escalation_hir_threshold,
            escalation_rr_threshold=escalation_rr_threshold,
            cache_enabled=cache_enabled,
            cache_max_size=cache_max_size,
            cache_ttl_seconds=cache_ttl_seconds,
            database_path=database_path,
            database_enabled=database_enabled,
            log_level=log_level,
            log_output=log_output,
            log_file_path=log_file_path,
            tls_enabled=tls_enabled,
            tls_cert_path=tls_cert_path,
            tls_key_path=tls_key_path,
        )
