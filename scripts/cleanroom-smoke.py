#!/usr/bin/env python3
"""Bootstrap and smoke-test NoeRelay with fresh, disposable Docker state."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROJECT = "noerelay-cleanroom"
PRIMARY = "axiovex-agni"
RECOVERY = "axiovex-agni-recovery"


def run(*args: str, capture: bool = False, timeout: int = 900) -> str:
    completed = subprocess.run(
        args,
        cwd=ROOT,
        env={
            **os.environ,
            "NOERELAY_PORT": "18080",
            "NOERELAY_WEBUI_PORT": "13001",
            "NOERELAY_A2A_PORT": "18090",
        },
        check=True,
        text=True,
        capture_output=capture,
        timeout=timeout,
    )
    return completed.stdout.strip() if capture else ""


def request(path: str, key: str, payload: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:18080{path}",
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    with urllib.request.urlopen(req, timeout=660) as response:
        return json.load(response)


def request_text(path: str, key: str, payload: dict) -> str:
    req = urllib.request.Request(
        f"http://127.0.0.1:18080{path}",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=660) as response:
        return response.read().decode()


def webui_request(path: str, token: str | None = None, payload: dict | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"http://127.0.0.1:13001{path}",
        data=None if payload is None else json.dumps(payload).encode(),
        headers=headers,
        method="GET" if payload is None else "POST",
    )
    with urllib.request.urlopen(req, timeout=660) as response:
        return json.load(response)


def chat_sse_text(body: str) -> str:
    """Reassemble assistant text split across OpenAI SSE delta events."""
    parts: list[str] = []
    for line in body.splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        try:
            event = json.loads(line[6:])
            content = event["choices"][0]["delta"].get("content")
            if content:
                parts.append(content)
        except (KeyError, IndexError, json.JSONDecodeError):
            continue
    return "".join(parts)


def wait_for(url: str, timeout: int = 600) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(2)
    raise TimeoutError(f"timed out waiting for {url}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="keep the clean-room stack")
    args = parser.parse_args()
    if not (ROOT / ".env.docker").is_file():
        raise SystemExit(".env.docker is required")

    compose = ("docker", "compose", "-p", PROJECT, "--env-file", ".env.docker")
    try:
        run(*compose, "up", "-d", "--build")
        wait_for("http://127.0.0.1:18080/health")
        wait_for("http://127.0.0.1:13001/health")
        run("docker", "wait", f"{PROJECT}-ollama-init-1", capture=True)
        run("docker", "wait", f"{PROJECT}-ollama-recovery-init-1", capture=True)
        run(*compose, "run", "--rm", "open-webui-init")

        key = run(
            "docker", "exec", f"{PROJECT}-noerelay-1", "printenv", "NOERELAY_API_KEY",
            capture=True,
        )
        models = request("/v1/models", key)
        ids = [model["id"] for model in models["data"]]
        assert ids == [PRIMARY], ids

        knowledge_tool = {
            "type": "function",
            "function": {
                "name": "list_knowledge_bases",
                "description": "List accessible knowledge bases",
                "parameters": {"type": "object", "properties": {"count": {"type": "integer"}}},
            },
        }
        greeting = request_text(
            "/v1/chat/completions",
            key,
            {
                "model": PRIMARY,
                "messages": [{
                    "role": "user",
                    "content": (
                        "Be specific. What are your models capable of? How do you route "
                        "to different models? What does your stack look like?"
                    ),
                }],
                "tools": [knowledge_tool],
                "stream": True,
                "max_tokens": 240,
            },
        )
        assert "data: " in greeting, greeting
        assert "list_knowledge_bases" not in greeting, greeting
        assert "tool_calls" not in greeting, greeting
        assert "noerelay" in chat_sse_text(greeting).lower(), greeting

        native_tool = request_text(
            "/v1/chat/completions",
            key,
            {
                "model": PRIMARY,
                "messages": [{"role": "user", "content": "List my accessible knowledge bases."}],
                "tools": [knowledge_tool],
                "stream": True,
                "max_tokens": 120,
            },
        )
        assert "tool_calls" in native_tool, native_tool
        assert "list_knowledge_bases" in native_tool, native_tool

        loop_broken = request_text(
            "/v1/chat/completions",
            key,
            {
                "model": PRIMARY,
                "messages": [
                    {"role": "user", "content": "Find relevant internal material."},
                    {"role": "assistant", "content": None, "tool_calls": [{
                        "id": "call_1", "type": "function",
                        "function": {"name": "list_knowledge_bases", "arguments": "{\"count\":5}"},
                    }]},
                    {"role": "tool", "tool_call_id": "call_1", "content": "[]"},
                    {"role": "assistant", "content": None, "tool_calls": [{
                        "id": "call_2", "type": "function",
                        "function": {"name": "list_knowledge_bases", "arguments": "{\"count\":5}"},
                    }]},
                    {"role": "tool", "tool_call_id": "call_2", "content": "[]"},
                ],
                "tools": [knowledge_tool],
                "stream": True,
                "max_tokens": 120,
            },
        )
        assert "tool_calls" not in loop_broken, loop_broken

        admin_email = run(
            "docker", "exec", f"{PROJECT}-open-webui-1", "printenv", "WEBUI_ADMIN_EMAIL",
            capture=True,
        )
        admin_password = run(
            "docker", "exec", f"{PROJECT}-open-webui-1", "printenv", "WEBUI_ADMIN_PASSWORD",
            capture=True,
        )
        webui_token = webui_request(
            "/api/v1/auths/signin",
            payload={"email": admin_email, "password": admin_password},
        )["token"]
        webui_models = webui_request("/api/models", webui_token)
        webui_ids = [model["id"] for model in webui_models["data"]]
        assert len(webui_ids) == 2 and set(webui_ids) == {PRIMARY, RECOVERY}, webui_ids
        recovery = webui_request(
            "/api/chat/completions",
            webui_token,
            {
                "model": RECOVERY,
                "messages": [{"role": "user", "content": "Reply exactly: recovery-ready"}],
                "max_tokens": 20,
            },
        )
        assert recovery["model"] == RECOVERY, recovery
        print(json.dumps({
            "status": "passed",
            "models": ids,
            "sentinel_models": webui_ids,
            "local_recovery": True,
            "capabilities_rendered": True,
            "native_streaming_tools": True,
            "repeated_tool_circuit_breaker": True,
        }))
    finally:
        if not args.keep:
            run(*compose, "down", "--volumes", "--remove-orphans")


if __name__ == "__main__":
    main()
