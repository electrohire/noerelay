"""Validate protected remote credentials without performing paid inference."""

from __future__ import annotations

import json
import os
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class SmokeCheckError(RuntimeError):
    """A sanitized remote smoke-check failure safe to print in CI."""


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SmokeCheckError(f"required environment value {name} is missing")
    return value


def _request_json(label: str, url: str, headers: dict[str, str]) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json", **headers})
    try:
        with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed HTTPS hosts/config.
            payload = json.load(response)
    except HTTPError as exc:
        raise SmokeCheckError(f"{label} returned HTTP {exc.code}") from None
    except URLError as exc:
        reason = type(exc.reason).__name__
        raise SmokeCheckError(f"{label} could not be reached ({reason})") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise SmokeCheckError(f"{label} returned an invalid JSON response") from None

    if not isinstance(payload, dict):
        raise SmokeCheckError(f"{label} returned an unexpected response shape")
    return payload


def main() -> int:
    openrouter_key = _required_env("OPENROUTER_API_KEY")
    hf_token = _required_env("HF_TOKEN")
    openrouter_base = _required_env("OPENROUTER_BASE_URL").rstrip("/")
    referer = _required_env("OPENROUTER_HTTP_REFERER")
    app_title = _required_env("OPENROUTER_APP_TITLE")
    live_tests = _required_env("NOERELAY_LIVE_TESTS")
    if live_tests not in {"0", "1"}:
        raise SmokeCheckError("NOERELAY_LIVE_TESTS must be 0 or 1")
    if openrouter_base != "https://openrouter.ai/api/v1":
        raise SmokeCheckError("OPENROUTER_BASE_URL is not the approved endpoint")

    openrouter = _request_json(
        "OpenRouter credential check",
        f"{openrouter_base}/key",
        {
            "Authorization": f"Bearer {openrouter_key}",
            "HTTP-Referer": referer,
            "X-Title": app_title,
        },
    )
    if not isinstance(openrouter.get("data"), dict):
        raise SmokeCheckError("OpenRouter credential response is missing key metadata")
    print("OpenRouter credential authenticated; no inference request was made.")

    hugging_face = _request_json(
        "Hugging Face credential check",
        "https://huggingface.co/api/whoami-v2",
        {"Authorization": f"Bearer {hf_token}"},
    )
    if not hugging_face.get("name"):
        raise SmokeCheckError("Hugging Face credential response is missing identity metadata")
    print("Hugging Face credential authenticated.")

    dataset_ids = (
        "SWE-bench/SWE-bench_Verified",
        "gorilla-llm/Berkeley-Function-Calling-Leaderboard",
    )
    for dataset_id in dataset_ids:
        metadata = _request_json(
            f"Hugging Face dataset {dataset_id}",
            f"https://huggingface.co/api/datasets/{quote(dataset_id, safe='/')}",
            {"Authorization": f"Bearer {hf_token}"},
        )
        revision = metadata.get("sha")
        if not isinstance(revision, str) or not revision:
            raise SmokeCheckError(f"dataset {dataset_id} did not resolve to an immutable revision")
        print(f"Benchmark accessible: {dataset_id} at {revision[:12]}.")

    state = "enabled" if live_tests == "1" else "disabled"
    print(f"Paid live-generation tests are {state}; this workflow never performs generation.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeCheckError as exc:
        print(f"::error::Remote smoke check failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
