"""Service health probe for all NoeRelay machines and endpoints.

Probes every service in the NoeRelay deployment topology:
- Local: noerelay-gateway (8080), postgres (5432), LiteLLM proxy (4000), Ollama (11434)
- Remote GPU: inference through an SSH tunnel and a private-network endpoint
- Docker: container health checks

Returns a structured health matrix with per-service status, latency, and diagnostics.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ServiceStatus:
    """Health status for a single service."""

    name: str
    host: str
    port: int
    kind: str  # "gateway", "database", "proxy", "inference", "docker", "remote"
    machine: str  # "localhost", "remote-gpu", "docker"
    reachable: bool = False
    latency_ms: float = 0.0
    status_code: int = 0
    detail: str = ""
    models: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def healthy(self) -> bool:
        return self.reachable and self.status_code in (0, 200)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "kind": self.kind,
            "machine": self.machine,
            "reachable": self.reachable,
            "latency_ms": round(self.latency_ms, 2),
            "status_code": self.status_code,
            "healthy": self.healthy,
            "detail": self.detail,
            "models": self.models,
            "error": self.error,
        }


@dataclass
class HealthMatrix:
    """Aggregate health across all probed services."""

    timestamp: str
    services: list[ServiceStatus]
    all_healthy: bool = True
    healthy_count: int = 0
    total_count: int = 0
    machines_online: list[str] = field(default_factory=list)
    machines_offline: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "all_healthy": self.all_healthy,
            "healthy_count": self.healthy_count,
            "total_count": self.total_count,
            "machines_online": self.machines_online,
            "machines_offline": self.machines_offline,
            "services": [s.to_dict() for s in self.services],
        }


# ---------------------------------------------------------------------------
# Service definitions
# ---------------------------------------------------------------------------

SERVICE_DEFINITIONS: list[dict[str, Any]] = [
    # --- Localhost services ---
    {
        "name": "NoeRelay Gateway",
        "host": "127.0.0.1",
        "port": 8080,
        "kind": "gateway",
        "machine": "localhost",
        "health_path": "/health",
        "models_path": "/v1/models",
    },
    {
        "name": "LiteLLM Proxy",
        "host": "127.0.0.1",
        "port": 4000,
        "kind": "proxy",
        "machine": "localhost",
        "health_path": "/health",
        "models_path": "/v1/models",
    },
    {
        "name": "Ollama",
        "host": "127.0.0.1",
        "port": 11434,
        "kind": "inference",
        "machine": "localhost",
        "health_path": "/",
        "models_path": "/api/tags",
    },
    {
        "name": "PostgreSQL",
        "host": "127.0.0.1",
        "port": 5432,
        "kind": "database",
        "machine": "localhost",
        "health_path": None,
        "models_path": None,
    },
    # --- Remote GPU inference via SSH tunnel on localhost:4000 ---
    {
        "name": "Remote GPU inference endpoint",
        "host": "127.0.0.1",
        "port": 4000,
        "kind": "inference",
        "machine": "remote-gpu",
        "health_path": "/health",
        "models_path": "/v1/models",
        "alt_port": 4000,  # Same port as LiteLLM — distinguished by kind
    },
    # --- Remote GPU private-network endpoint ---
    {
        "name": "Remote GPU network endpoint",
        "host": os.environ.get(
            "REMOTE_GPU_HOST", "remote-gpu.example.internal"
        ),
        "port": 4000,
        "kind": "inference",
        "machine": "remote-gpu",
        "health_path": "/health",
        "models_path": "/v1/models",
    },
    # --- Docker containers ---
    {
        "name": "Docker: noerelay-gateway",
        "host": "127.0.0.1",
        "port": 8080,
        "kind": "docker",
        "machine": "docker",
        "health_path": "/health",
        "models_path": "/v1/models",
        "container_name": "noerelay-noerelay-1",
    },
    {
        "name": "Docker: postgres",
        "host": "127.0.0.1",
        "port": 5432,
        "kind": "docker",
        "machine": "docker",
        "health_path": None,
        "models_path": None,
        "container_name": "noerelay-postgres-1",
    },
]


# ---------------------------------------------------------------------------
# Probe functions
# ---------------------------------------------------------------------------

def _tcp_probe(host: str, port: int, timeout: float = 3.0) -> tuple[bool, float]:
    """Check if a TCP port is open. Returns (reachable, latency_ms)."""
    start = time.perf_counter()
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        latency = (time.perf_counter() - start) * 1000
        return True, latency
    except (socket.timeout, ConnectionRefusedError, OSError):
        latency = (time.perf_counter() - start) * 1000
        return False, latency


def _http_probe(
    url: str, timeout: float = 5.0
) -> tuple[bool, int, str, float]:
    """HTTP GET probe. Returns (reachable, status_code, body_preview, latency_ms)."""
    start = time.perf_counter()
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")[:500]
            latency = (time.perf_counter() - start) * 1000
            return True, resp.status, body, latency
    except urllib.error.HTTPError as exc:
        latency = (time.perf_counter() - start) * 1000
        return True, exc.code, str(exc)[:200], latency
    except Exception as exc:
        latency = (time.perf_counter() - start) * 1000
        return False, 0, str(exc)[:200], latency


def _fetch_models(url: str, timeout: float = 5.0) -> list[str]:
    """Fetch model list from an OpenAI-compatible /v1/models or Ollama /api/tags."""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []

    # OpenAI shape: {"data": [{"id": "..."}, ...]}
    if isinstance(data.get("data"), list):
        return [m.get("id", "") for m in data["data"] if m.get("id")]

    # Ollama shape: {"models": [{"name": "..."}, ...]}
    if isinstance(data.get("models"), list):
        return [m.get("name", "") for m in data["models"] if m.get("name")]

    return []


def _docker_container_running(container_name: str) -> tuple[bool, str]:
    """Check if a Docker container is running."""
    try:
        result = subprocess.run(
            [
                "docker", "inspect", "-f", "{{.State.Status}}",
                container_name,
            ],
            capture_output=True, text=True, timeout=10,
        )
        status = result.stdout.strip()
        if status == "running":
            return True, "running"
        return False, status or "not found"
    except Exception as exc:
        return False, str(exc)


def _ssh_tunnel_active(host: str, port: int) -> bool:
    """Check if an SSH tunnel is active by looking for the SSH process."""
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["powershell", "-Command",
                 f"Get-Process ssh -ErrorAction SilentlyContinue | "
                 f"Select-Object -ExpandProperty Id"],
                capture_output=True, text=True, timeout=10,
            )
        else:
            result = subprocess.run(
                ["pgrep", "-f", f"ssh.*-L.*{port}:"],
                capture_output=True, text=True, timeout=10,
            )
        return bool(result.stdout.strip())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Main probe entry point
# ---------------------------------------------------------------------------

def probe_all_services(
    timeout: float = 5.0,
    include_docker: bool = True,
    include_remote: bool = True,
) -> HealthMatrix:
    """Probe all defined services and return a HealthMatrix.

    Args:
        timeout: Per-probe timeout in seconds.
        include_docker: Whether to check Docker container status.
        include_remote: Whether to probe remote machines (remote-gpu).

    Returns:
        HealthMatrix with status of every service.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    services: list[ServiceStatus] = []
    machines_seen: set[str] = set()
    machines_healthy: set[str] = set()

    for svc_def in SERVICE_DEFINITIONS:
        # Skip remote if not requested
        if not include_remote and svc_def["machine"] == "remote-gpu":
            continue
        # Skip docker if not requested
        if not include_docker and svc_def["kind"] == "docker":
            continue

        name = svc_def["name"]
        host = svc_def["host"]
        port = svc_def["port"]
        kind = svc_def["kind"]
        machine = svc_def["machine"]
        machines_seen.add(machine)

        svc = ServiceStatus(name=name, host=host, port=port, kind=kind, machine=machine)

        # --- Docker probe ---
        if kind == "docker" and "container_name" in svc_def:
            running, detail = _docker_container_running(svc_def["container_name"])
            svc.reachable = running
            svc.detail = detail
            if running:
                machines_healthy.add(machine)
            services.append(svc)
            continue

        # --- Database probe (TCP only) ---
        if kind == "database":
            reachable, latency = _tcp_probe(host, port, timeout)
            svc.reachable = reachable
            svc.latency_ms = latency
            svc.detail = "TCP port open" if reachable else "TCP port closed"
            if reachable:
                machines_healthy.add(machine)
            services.append(svc)
            continue

        # --- HTTP probe ---
        health_path = svc_def.get("health_path")
        models_path = svc_def.get("models_path")

        if health_path:
            url = f"http://{host}:{port}{health_path}"
            reachable, status, body, latency = _http_probe(url, timeout)
            svc.reachable = reachable
            svc.status_code = status
            svc.latency_ms = latency
            svc.detail = body[:200] if body else ""
        else:
            # Fallback to TCP
            reachable, latency = _tcp_probe(host, port, timeout)
            svc.reachable = reachable
            svc.latency_ms = latency

        # Fetch models if available
        if svc.reachable and models_path:
            models_url = f"http://{host}:{port}{models_path}"
            svc.models = _fetch_models(models_url, timeout)

        if svc.reachable:
            machines_healthy.add(machine)

        # Special: check SSH tunnel for remote-gpu
        if machine == "remote-gpu" and host == "127.0.0.1":
            if _ssh_tunnel_active(host, port):
                svc.detail = f"SSH tunnel active. {svc.detail}"

        services.append(svc)

    # Compute aggregate
    healthy_count = sum(1 for s in services if s.healthy)
    total_count = len(services)
    all_healthy = healthy_count == total_count
    machines_online = sorted(machines_healthy)
    machines_offline = sorted(machines_seen - machines_healthy)

    return HealthMatrix(
        timestamp=timestamp,
        services=services,
        all_healthy=all_healthy,
        healthy_count=healthy_count,
        total_count=total_count,
        machines_online=machines_online,
        machines_offline=machines_offline,
    )


def print_health_report(matrix: HealthMatrix) -> None:
    """Print a human-readable health report to stdout."""
    print(f"\n{'='*60}")
    print(f"  NoeRelay Service Health Report")
    print(f"  {matrix.timestamp}")
    print(f"{'='*60}")
    print(f"  Services: {matrix.healthy_count}/{matrix.total_count} healthy")
    print(f"  Machines online:  {', '.join(matrix.machines_online) if matrix.machines_online else '(none)'}")
    print(f"  Machines offline: {', '.join(matrix.machines_offline) if matrix.machines_offline else '(none)'}")
    print(f"  All healthy: {'YES' if matrix.all_healthy else 'NO'}")
    print()

    for svc in matrix.services:
        icon = "[OK]" if svc.healthy else "[FAIL]"
        print(f"  {icon} {svc.name} ({svc.machine})")
        print(f"       {svc.host}:{svc.port}  latency={svc.latency_ms:.1f}ms")
        if svc.error:
            print(f"       ERROR: {svc.error}")
        if svc.detail and not svc.healthy:
            print(f"       detail: {svc.detail}")
        if svc.models:
            model_list = ", ".join(svc.models[:5])
            suffix = f" +{len(svc.models)-5} more" if len(svc.models) > 5 else ""
            print(f"       models: {model_list}{suffix}")
        print()

    print(f"{'='*60}")
    if not matrix.all_healthy:
        print("  WARNING: Not all services are healthy!")
        print("  Run: .\\scripts\\ensure-services.ps1 to attempt recovery.")
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Probe all NoeRelay services across all machines",
    )
    parser.add_argument(
        "--json", action="store_true", help="Output as JSON"
    )
    parser.add_argument(
        "--timeout", type=float, default=5.0, help="Per-probe timeout (seconds)"
    )
    parser.add_argument(
        "--no-docker", action="store_true", help="Skip Docker container checks"
    )
    parser.add_argument(
        "--no-remote", action="store_true", help="Skip remote machine probes"
    )
    args = parser.parse_args()

    matrix = probe_all_services(
        timeout=args.timeout,
        include_docker=not args.no_docker,
        include_remote=not args.no_remote,
    )

    if args.json:
        print(json.dumps(matrix.to_dict(), indent=2))
    else:
        print_health_report(matrix)

    return 0 if matrix.all_healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
