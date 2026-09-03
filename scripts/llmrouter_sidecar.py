#!/usr/bin/env python
"""LLMRouter sidecar for NoeRelay advisory ranking.

Implements the minimal endpoints required by the integration mission:
  - POST /rank   — produce RankingAdvice for an admissible candidate set
  - GET  /health — liveness check
  - GET  /version — sidecar version info

The sidecar receives sanitized features and an already-admissible candidate set.
It returns ranking advice only. It must not receive production credentials,
dispatch model calls, add candidates, alter policy, or release results.

Usage:
    python scripts/llmrouter_sidecar.py --port 9878

Environment:
    LLMROUTER_SIDECAR_PORT — port to listen on (default: 9878)
    LLMROUTER_MODEL — model to use for ranking (default: qwen3:8b)
    LLMROUTER_OLLAMA_URL — Ollama base URL (default: http://127.0.0.1:11434)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import URLError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "1.0.0"
SIDECAR_VERSION = "0.1.0"
MAX_REQUEST_SIZE = 1_048_576  # 1 MB
REQUEST_TIMEOUT = 30  # seconds
RANKER_TIMEOUT = 60  # seconds for LLM call

# ---------------------------------------------------------------------------
# Ranking logic
# ---------------------------------------------------------------------------


def compute_candidate_set_hash(candidates: list[dict[str, Any]]) -> str:
    """Compute SHA-256 hash over sorted candidate IDs."""
    ids = sorted(c["candidate_id"] for c in candidates)
    material = json.dumps(ids, sort_keys=True).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def compute_features_hash(features: dict[str, Any]) -> str:
    """Compute SHA-256 hash over features JSON."""
    material = json.dumps(features, sort_keys=True).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def build_ranking_prompt(candidates: list[dict[str, Any]], features: dict[str, Any]) -> str:
    """Build a prompt for the LLM to rank candidates."""
    candidate_lines = []
    for i, c in enumerate(candidates):
        candidate_lines.append(
            f"  {i+1}. {c['candidate_id']} — "
            f"provider={c.get('provider','?')}, "
            f"cost={c.get('cost_total_microusd','?')}µUSD, "
            f"latency={c.get('latency_p95_ms','?')}ms, "
            f"acceptance={c.get('acceptance_lcb_ppm','?')}/1M"
        )

    features_str = json.dumps(features, indent=2)

    return f"""You are a model router. Rank the following candidates by their suitability for the task.

Task features:
{features_str}

Admissible candidates:
{chr(10).join(candidate_lines)}

Return a JSON object with a "rankings" array. Each entry must have:
  - "candidate_id": string (must match exactly)
  - "score": integer 0-1000000 (higher = better)
  - "rationale": string (brief reason for the score)

Return ONLY valid JSON, no other text."""


def call_ollama(prompt: str, model: str, ollama_url: str) -> dict[str, Any]:
    """Call Ollama to rank candidates."""
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 1024},
        }
    ).encode("utf-8")

    req = Request(
        f"{ollama_url}/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    with urlopen(req, timeout=RANKER_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_json_from_response(content: str) -> dict[str, Any] | None:
    """Extract JSON from an LLM response that may contain markdown fences."""
    # Try direct parse first
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # Try extracting from ```json ... ``` fence
    import re

    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try finding first { ... } block
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return None


def rank_candidates(
    candidates: list[dict[str, Any]],
    features: dict[str, Any],
    cohort: str,
    model: str,
    ollama_url: str,
) -> dict[str, Any]:
    """Produce RankingAdvice for the given candidates."""
    prompt = build_ranking_prompt(candidates, features)
    response = call_ollama(prompt, model, ollama_url)

    content = ""
    if response.get("choices"):
        content = response["choices"][0]["message"].get("content", "")

    parsed = extract_json_from_response(content)

    candidate_scores = []
    if parsed and "rankings" in parsed:
        for entry in parsed["rankings"]:
            score = max(0, min(1_000_000, int(entry.get("score", 0))))
            candidate_scores.append(
                {
                    "candidate_id": entry["candidate_id"],
                    "score_ppm": score,
                    "rationale": entry.get("rationale"),
                }
            )
    else:
        # Fallback: score by acceptance_lcb_ppm
        for c in candidates:
            candidate_scores.append(
                {
                    "candidate_id": c["candidate_id"],
                    "score_ppm": c.get("acceptance_lcb_ppm", 500_000),
                    "rationale": "Fallback: LLM response could not be parsed",
                }
            )
        candidate_scores.sort(key=lambda x: x["score_ppm"], reverse=True)

    now_ms = int(time.time() * 1000)
    return {
        "schema_version": SCHEMA_VERSION,
        "ranker": {
            "ranker_id": "llmrouter-sidecar",
            "revision": SIDECAR_VERSION,
            "display_name": f"LLMRouter ({model})",
        },
        "run_id": str(uuid.uuid4()),
        "cohort": cohort,
        "features_hash": compute_features_hash(features),
        "candidate_set_hash": compute_candidate_set_hash(candidates),
        "candidate_scores": candidate_scores,
        "trained_through_unix_ms": None,
        "generated_at_unix_ms": now_ms,
        "expires_at_unix_ms": now_ms + 300_000,  # 5 minute TTL
        "advisory_only": True,
    }


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------


class RankingHandler(BaseHTTPRequestHandler):
    """HTTP handler for the LLMRouter sidecar."""

    model: str = "qwen3:8b"
    ollama_url: str = "http://127.0.0.1:11434"

    def log_message(self, format: str, *args: Any) -> None:
        """Log to stderr with timestamps."""
        ts = datetime.now(timezone.utc).isoformat()
        sys.stderr.write(f"[{ts}] {format % args}\n")

    def _send_json(self, status: int, data: dict[str, Any]) -> None:
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"status": "healthy", "timestamp": int(time.time() * 1000)})
        elif self.path == "/version":
            self._send_json(
                200,
                {
                    "sidecar": "llmrouter-sidecar",
                    "version": SIDECAR_VERSION,
                    "schema_version": SCHEMA_VERSION,
                    "model": self.model,
                },
            )
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/rank":
            self._send_json(404, {"error": "not found"})
            return

        # Read body with size limit
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > MAX_REQUEST_SIZE:
            self._send_json(413, {"error": "request too large"})
            return

        try:
            body = self.rfile.read(content_length)
            request = json.loads(body)
        except (json.JSONDecodeError, ValueError) as e:
            self._send_json(400, {"error": f"invalid JSON: {e}"})
            return

        # Validate required fields
        required = ["candidates", "features", "cohort"]
        for field in required:
            if field not in request:
                self._send_json(400, {"error": f"missing required field: {field}"})
                return

        candidates = request["candidates"]
        features = request["features"]
        cohort = request["cohort"]

        if not isinstance(candidates, list) or len(candidates) == 0:
            self._send_json(400, {"error": "candidates must be a non-empty list"})
            return

        try:
            advice = rank_candidates(
                candidates, features, cohort, self.model, self.ollama_url
            )
            self._send_json(200, advice)
        except Exception as e:
            self._send_json(500, {"error": f"ranking failed: {e}"})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="LLMRouter sidecar for NoeRelay")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("LLMROUTER_SIDECAR_PORT", "9878")),
        help="Port to listen on (default: 9878)",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("LLMROUTER_MODEL", "qwen3:8b"),
        help="Ollama model for ranking (default: qwen3:8b)",
    )
    parser.add_argument(
        "--ollama-url",
        default=os.environ.get("LLMROUTER_OLLAMA_URL", "http://127.0.0.1:11434"),
        help="Ollama base URL",
    )
    args = parser.parse_args()

    RankingHandler.model = args.model
    RankingHandler.ollama_url = args.ollama_url

    server = HTTPServer(("127.0.0.1", args.port), RankingHandler)
    print(f"LLMRouter sidecar v{SIDECAR_VERSION} listening on http://127.0.0.1:{args.port}")
    print(f"  Model: {args.model}")
    print(f"  Ollama: {args.ollama_url}")
    print(f"  Endpoints: GET /health, GET /version, POST /rank")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())