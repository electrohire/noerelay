#!/usr/bin/env python
"""Probe the running gateway and dump the raw chat response to a file."""
import json
import urllib.request
import urllib.error
import sys

GATEWAY = "http://127.0.0.1:8080"
KEY = "noerelay-local-development-key-0001"


def probe():
    body = json.dumps({
        "model": "axiovex-agni",
        "messages": [{"role": "user", "content": "What is 2+2? Answer with just the number."}],
    }).encode()
    req = urllib.request.Request(
        GATEWAY + "/v1/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + KEY,
        },
        method="POST",
    )
    result = {"gateway": GATEWAY}
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result["status"] = resp.status
            result["headers"] = {k: v for k, v in resp.headers.items()}
            result["body"] = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        result["status"] = e.code
        result["body"] = e.read().decode()
    except Exception as e:
        result["error"] = "%s: %s" % (type(e).__name__, e)

    with open("evidence/gateway-probe.json", "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, default=str)
    print("WROTE evidence/gateway-probe.json")
    return 0


if __name__ == "__main__":
    sys.exit(probe())
