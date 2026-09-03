#!/usr/bin/env python
"""Agent-facing wrapper for the bootstrap self-improvement orchestrator.

Commands:
    --start     Write plan file and spawn bootstrap_improve.py
    --status    Read heartbeat file and print current state
    --recover   Read rollback plan and print recovery instructions

Usage:
    python scripts/agent_bootstrap.py --start --mode live --max-cycles 5 --auto-apply
    python scripts/agent_bootstrap.py --status
    python scripts/agent_bootstrap.py --recover
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

BOOTSTRAP_DIR = Path("evidence/bootstrap")
PLAN_FILE = BOOTSTRAP_DIR / "plan.json"
HEARTBEAT_FILE = BOOTSTRAP_DIR / "heartbeat.json"
ROLLBACK_FILE = BOOTSTRAP_DIR / "rollback.json"


def cmd_start(args: argparse.Namespace) -> int:
    """Write plan file and spawn the bootstrap orchestrator."""
    BOOTSTRAP_DIR.mkdir(parents=True, exist_ok=True)

    plan = {
        "gateway_mode": args.mode,
        "max_cycles": args.max_cycles,
        "auto_apply": args.auto_apply,
        "quick": args.quick,
        "full": args.full,
        "api_key": args.api_key or os.environ.get("NOERELAY_API_KEY", "noerelay-local-development-key-0001"),
        "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }
    PLAN_FILE.write_text(json.dumps(plan, indent=2), "utf-8")
    print(f"Plan written to {PLAN_FILE}")

    # Spawn bootstrap orchestrator
    cmd = [sys.executable, "scripts/bootstrap_improve.py"]
    env = os.environ.copy()
    if args.api_key:
        env["NOERELAY_API_KEY"] = args.api_key

    if sys.platform == "win32":
        # Use PowerShell to start in background
        ps_cmd = f"Start-Process -NoNewWindow -FilePath '{sys.executable}' -ArgumentList 'scripts/bootstrap_improve.py'"
        subprocess.Popen(["pwsh", "-NoProfile", "-Command", ps_cmd], env=env)
    else:
        subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print(f"Bootstrap orchestrator started (PID: check heartbeat)")
    print(f"Run 'python scripts/agent_bootstrap.py --status' to check progress")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Read heartbeat file and print current state."""
    if not HEARTBEAT_FILE.exists():
        print("No self-improvement run in progress.")
        return 0

    try:
        heartbeat = json.loads(HEARTBEAT_FILE.read_text("utf-8"))
        print(f"Status: {heartbeat.get('status', 'unknown')}")
        print(f"Cycle: {heartbeat.get('current_cycle', '?')}")
        print(f"Last score: {heartbeat.get('last_score', '?')}")
        if heartbeat.get("last_error"):
            print(f"Error: {heartbeat['last_error']}")
        print(f"Updated: {heartbeat.get('timestamp', '?')}")
    except (json.JSONDecodeError, OSError) as e:
        print(f"Could not read heartbeat: {e}")

    return 0


def cmd_recover(args: argparse.Namespace) -> int:
    """Read rollback plan and print recovery instructions."""
    if not ROLLBACK_FILE.exists():
        print("No rollback plan found. Check if a run failed.")
        return 0

    try:
        rollback = json.loads(ROLLBACK_FILE.read_text("utf-8"))
        print(f"Rollback plan for cycle {rollback.get('failed_cycle', '?')}:")
        print(f"  Error: {rollback.get('error', 'unknown')}")
        print(f"\nRecommended recovery steps:")
        for action in rollback.get("rollback_actions", []):
            print(f"  - {action['description']}")
        print(f"\nTo resume, fix the root cause and run:")
        print(f"  python scripts/agent_bootstrap.py --start --mode live --max-cycles 5 --auto-apply")
    except (json.JSONDecodeError, OSError) as e:
        print(f"Could not read rollback plan: {e}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent-facing bootstrap wrapper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="Start self-improvement loop")
    start_parser.add_argument("--mode", choices=["live", "stub"], default="live")
    start_parser.add_argument("--max-cycles", type=int, default=5)
    start_parser.add_argument("--auto-apply", action="store_true", default=True)
    start_parser.add_argument("--quick", action="store_true")
    start_parser.add_argument("--full", action="store_true")
    start_parser.add_argument("--api-key", help="NoeRelay API key")

    subparsers.add_parser("status", help="Check current status")
    subparsers.add_parser("recover", help="Read rollback plan and recovery instructions")

    args = parser.parse_args()

    if args.command == "start":
        return cmd_start(args)
    elif args.command == "status":
        return cmd_status(args)
    elif args.command == "recover":
        return cmd_recover(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())