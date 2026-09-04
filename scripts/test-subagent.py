#!/usr/bin/env python
"""NoeRelay test subagent script.

Verifies that the NoeRelay gateway is running and responding correctly.
Can be used by a Zoo-Code subagent to test the system.

Usage:
    python scripts/test-subagent.py
    python scripts/test-subagent.py --gateway-url http://127.0.0.1:8080
    python scripts/test-subagent.py --start  # Start gateway if not running
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

def check_gateway(url: str) -> dict:
    """Check if the gateway is running and healthy."""
    results = {}
    
    # Health check
    try:
        req = urllib.request.Request(f"{url}/health", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            results["health"] = json.loads(resp.read())
            results["health_status"] = "pass"
    except Exception as e:
        results["health"] = str(e)
        results["health_status"] = "fail"
    
    # Models check
    try:
        req = urllib.request.Request(f"{url}/v1/models", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            results["models"] = json.loads(resp.read())
            results["models_status"] = "pass"
    except Exception as e:
        results["models"] = str(e)
        results["models_status"] = "fail"
    
    # Chat completion test
    try:
        data = json.dumps({
            "model": "axiovex-agni",
            "messages": [{"role": "user", "content": "Say hello in one word."}],
            "max_tokens": 50
        }).encode()
        req = urllib.request.Request(
            f"{url}/v1/chat/completions",
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            results["chat_completion"] = {
                "status": "pass",
                "model": result.get("model"),
                "has_choices": bool(result.get("choices")),
                "has_epr": bool(result.get("epr")),
                "epr_run_id": result.get("epr", {}).get("run_id"),
                "epr_ledger_hash": result.get("epr", {}).get("ledger_head_hash"),
            }
    except Exception as e:
        results["chat_completion"] = {"status": "fail", "error": str(e)}
    
    # Metrics check
    try:
        req = urllib.request.Request(f"{url}/metrics", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            results["metrics"] = "pass"
    except Exception as e:
        results["metrics"] = f"fail: {e}"
    
    # Dashboard check
    try:
        req = urllib.request.Request(f"{url}/dashboard", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            content = resp.read().decode()
            results["dashboard"] = "pass" if "NoeRelay" in content else "fail: no title"
    except Exception as e:
        results["dashboard"] = f"fail: {e}"
    
    return results

def start_gateway():
    """Start the gateway if not running."""
    try:
        req = urllib.request.Request("http://127.0.0.1:8080/health", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            print("Gateway already running")
            return True
    except Exception:
        pass
    
    print("Starting gateway...")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(script_dir)
    reference_dir = os.path.join(project_dir, "reference")
    
    env = os.environ.copy()
    env["NOERELAY_OPENROUTER_MODE"] = "stub"
    
    proc = subprocess.Popen(
        [sys.executable, "-m", "gateway"],
        cwd=reference_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    # Wait for gateway to start
    for _ in range(10):
        time.sleep(1)
        try:
            req = urllib.request.Request("http://127.0.0.1:8080/health", method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                print("Gateway started successfully")
                return True
        except Exception:
            continue
    
    print("Gateway failed to start")
    return False

def main():
    parser = argparse.ArgumentParser(description="NoeRelay test subagent")
    parser.add_argument("--gateway-url", default="http://127.0.0.1:8080")
    parser.add_argument("--start", action="store_true", help="Start gateway if not running")
    args = parser.parse_args()
    
    if args.start:
        if not start_gateway():
            print(json.dumps({"status": "fail", "error": "Gateway failed to start"}))
            return 1
    
    results = check_gateway(args.gateway_url)
    
    # Print results
    print(json.dumps(results, indent=2))
    
    # Return exit code based on results
    all_pass = all(
        v.get("status") == "pass" if isinstance(v, dict) and "status" in v
        else v == "pass" if isinstance(v, str)
        else True
        for v in results.values()
    )
    
    return 0 if all_pass else 1

if __name__ == "__main__":
    sys.exit(main())
