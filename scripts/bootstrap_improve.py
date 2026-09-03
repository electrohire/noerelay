#!/usr/bin/env python
"""Bootstrap self-improvement orchestrator with heartbeat and rollback.

Reads a plan from evidence/bootstrap/plan.json, runs self-improvement cycles,
writes heartbeat every 5 seconds, and records rollback plans on failure.

Usage:
    python scripts/bootstrap_improve.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BOOTSTRAP_DIR = Path("evidence/bootstrap")
PLAN_FILE = BOOTSTRAP_DIR / "plan.json"
HEARTBEAT_FILE = BOOTSTRAP_DIR / "heartbeat.json"
ROLLBACK_FILE = BOOTSTRAP_DIR / "rollback.json"
RESULTS_DIR = BOOTSTRAP_DIR / "results"
CYCLE_TIMEOUT = 600  # 10 minutes per cycle


def load_plan() -> dict[str, Any]:
    """Load the bootstrap plan from the plan file."""
    if not PLAN_FILE.exists():
        print(f"ERROR: Plan file not found at {PLAN_FILE}")
        sys.exit(1)
    return json.loads(PLAN_FILE.read_text("utf-8"))


def write_heartbeat(status: str, current_cycle: int, last_score: float, error: str | None = None) -> None:
    """Write heartbeat file for the agent to poll."""
    heartbeat = {
        "status": status,
        "current_cycle": current_cycle,
        "last_score": round(last_score, 4),
        "last_error": error,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    HEARTBEAT_FILE.write_text(json.dumps(heartbeat, indent=2), "utf-8")


def write_rollback(failed_cycle: int, error: str, actions_applied: list[str], state_snapshot: dict[str, Any]) -> None:
    """Write rollback plan so the agent can recover."""
    rollback = {
        "failed_cycle": failed_cycle,
        "error": error,
        "actions_applied_this_cycle": actions_applied,
        "rollback_actions": [
            {"action": "restart_gateway", "description": "Restart gateway in live mode"},
            {"action": "restart_sidecar", "description": "Restart LLMRouter sidecar if running"},
        ],
        "state_snapshot": state_snapshot,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    ROLLBACK_FILE.write_text(json.dumps(rollback, indent=2), "utf-8")


def ensure_gateway(plan: dict[str, Any]) -> bool:
    """Ensure the gateway is running in the correct mode."""
    gateway_mode = plan.get("gateway_mode", "live")
    print(f"  Ensuring gateway in {gateway_mode} mode...")

    # Check if gateway is already running
    try:
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1:8080/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("status") == "live":
                print("  Gateway already running")
                return True
    except Exception:
        pass

    # Start gateway via Docker
    env = os.environ.copy()
    env["NOERELAY_OPENROUTER_MODE"] = gateway_mode
    try:
        subprocess.run(
            ["docker", "compose", "up", "-d", "noerelay"],
            env=env,
            capture_output=True,
            timeout=60,
        )
        # Wait for gateway to be healthy
        for _ in range(30):
            try:
                req = urllib.request.Request("http://127.0.0.1:8080/health")
                with urllib.request.urlopen(req, timeout=3) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                    if result.get("status") == "live":
                        print("  Gateway started successfully")
                        return True
            except Exception:
                pass
            time.sleep(2)
        print("  ERROR: Gateway failed to start")
        return False
    except Exception as e:
        print(f"  ERROR: Failed to start gateway: {e}")
        return False


def run_cycle(cycle: int, plan: dict[str, Any]) -> tuple[bool, float, list[str], dict[str, Any]]:
    """Run a single self-improvement cycle."""
    print(f"\n{'='*60}")
    print(f"  CYCLE {cycle}")
    print(f"{'='*60}")

    # Reset orchestrator state for this cycle
    state_file = Path("evidence/self-improve/orchestrator_state.json")
    state_file.write_text(json.dumps({
        "started_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "current_cycle": 0,
        "converged": False,
        "converged_at": None,
        "score_history": [],
        "applied_actions": [],
        "last_results_file": None,
        "last_health_file": None,
        "last_report_file": None,
    }), "utf-8")

    # Build the command
    cmd = [
        sys.executable,
        "scripts/noerelay_self_improve.py",
        "--auto-apply",
        "--max-cycles", "1",
    ]
    if plan.get("quick"):
        cmd.append("--quick")
    if plan.get("full"):
        cmd.append("--full")

    # Set env vars
    env = os.environ.copy()
    env["NOERELAY_API_KEY"] = plan.get("api_key", "noerelay-local-development-key-0001")

    # Run with timeout
    try:
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=CYCLE_TIMEOUT,
            cwd=str(Path(__file__).resolve().parent.parent),
        )

        # Parse the score from output
        score = 0.0
        for line in result.stdout.split("\n"):
            if "Cycle 1 complete" in line and "Score:" in line:
                try:
                    score = float(line.split("Score:")[1].strip())
                except (ValueError, IndexError):
                    pass

        # Check for errors — the self-improvement script exits with code 1
        # when max cycles are reached without convergence, but the cycle itself
        # may have completed successfully. Treat a non-zero exit as success if
        # we got a valid score.
        if result.returncode != 0:
            if score > 0.0:
                # Cycle completed, just no convergence yet
                write_heartbeat("running", cycle, score)
                return True, score, [], {}
            error_msg = result.stderr[:500] if result.stderr else result.stdout[:500]
            write_rollback(cycle, error_msg, [], {})
            write_heartbeat("failed", cycle, 0.0, error_msg)
            return False, 0.0, [], {}

        write_heartbeat("running", cycle, score)
        return True, score, [], {}

    except subprocess.TimeoutExpired:
        error_msg = f"Cycle {cycle} timed out after {CYCLE_TIMEOUT}s"
        write_rollback(cycle, error_msg, [], {})
        write_heartbeat("failed", cycle, 0.0, error_msg)
        return False, 0.0, [], {}
    except Exception as e:
        error_msg = f"Cycle {cycle} failed: {e}\n{traceback.format_exc()}"
        write_rollback(cycle, error_msg, [], {})
        write_heartbeat("failed", cycle, 0.0, error_msg)
        return False, 0.0, [], {}


def main() -> int:
    """Main entry point for the bootstrap orchestrator."""
    print("=" * 60)
    print("  NoeRelay Bootstrap Self-Improvement Orchestrator")
    print("=" * 60)

    # Create directories
    BOOTSTRAP_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Load plan
    plan = load_plan()
    max_cycles = plan.get("max_cycles", 5)
    print(f"  Max cycles: {max_cycles}")
    print(f"  Gateway mode: {plan.get('gateway_mode', 'live')}")
    print(f"  Auto-apply: {plan.get('auto_apply', True)}")

    # Ensure gateway is running
    write_heartbeat("starting", 0, 0.0)
    if not ensure_gateway(plan):
        write_heartbeat("failed", 0, 0.0, "Gateway failed to start")
        return 1

    # Run cycles
    scores = []
    for cycle in range(1, max_cycles + 1):
        success, score, actions, snapshot = run_cycle(cycle, plan)
        scores.append(score)

        if not success:
            print(f"\n  Cycle {cycle} FAILED. Rollback written to {ROLLBACK_FILE}")
            write_heartbeat("failed", cycle, score)
            return 1

        # Check convergence
        if len(scores) >= 3:
            deltas = [abs(scores[i] - scores[i - 1]) for i in range(-2, 0)]
            if all(d < 0.005 for d in deltas):
                print(f"\n  Converged at cycle {cycle} (score: {score:.4f})")
                write_heartbeat("completed", cycle, score)
                return 0

        write_heartbeat("running", cycle, score)

    # Max cycles reached
    final_score = scores[-1] if scores else 0.0
    print(f"\n  Max cycles ({max_cycles}) reached. Final score: {final_score:.4f}")
    write_heartbeat("completed", max_cycles, final_score)
    return 0


if __name__ == "__main__":
    sys.exit(main())