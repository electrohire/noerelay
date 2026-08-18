"""Local model inference client (dependency-free, stdlib only).

Supports OpenAI-compatible local model servers such as Ollama, llama.cpp, and
vLLM.  Local models have zero per-call API cost and lower latency but may have
lower quality; the escalation policy decides when to fall back to cloud.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from typing import Any


class LocalModelError(RuntimeError):
    """Raised when a local model transport fails."""


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    """Extract the final user text from an OpenAI-shaped message list."""
    texts: list[str] = []
    for message in messages:
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(str(block.get("text", "")))
    return texts[-1] if texts else ""


class LocalModelClient:
    """Client for local model inference via HTTP endpoint (e.g., Ollama, vLLM)."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        model_id: str = "qwen3:8b",
        timeout: int = 120,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model_id = model_id
        self._timeout = timeout

    def create_chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send a chat completion request to the local model server.

        Supports Ollama (``/v1/chat/completions``) and other OpenAI-compatible
        local servers.
        """
        url = f"{self._base_url}/v1/chat/completions"
        # Use the model from the payload (set by the pipeline from the selected
        # plan), falling back to the configured local model ID.
        model_id = payload.get("model", self._model_id)
        local_payload = {**payload, "model": model_id}
        # Remove provider routing — local models do not need it.
        local_payload.pop("provider", None)

        body = json.dumps(local_payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8")
            except Exception:
                detail = "(unable to read error body)"
            raise LocalModelError(
                f"local model HTTP {exc.code} from {url}: {detail[:500]}"
            ) from None
        except urllib.error.URLError as exc:
            raise LocalModelError(
                f"local model transport error for {url}: {exc.reason}"
            ) from None
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise LocalModelError(
                f"local model unparseable response from {url}: {exc}"
            ) from None
        except OSError as exc:
            raise LocalModelError(
                f"local model OS error for {url}: {exc}"
            ) from None

    def is_available(self) -> bool:
        """Check if the local model server is running with the configured model."""
        try:
            url = f"{self._base_url}/v1/models"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return any(
                    m.get("id") == self._model_id for m in data.get("data", [])
                )
        except Exception:
            return False


class StubLocalModelClient:
    """Deterministic stub for local model inference (no network)."""

    def __init__(self, model_id: str = "qwen3:8b") -> None:
        self._model_id = model_id

    def create_chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return a deterministic OpenAI-shaped stub response."""
        last_user_text = _last_user_text(payload.get("messages", []))
        content = f"[local stub] {last_user_text[:200]}"
        return {
            "id": f"local-gen-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": self._model_id,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
            },
        }

    def is_available(self) -> bool:
        return True