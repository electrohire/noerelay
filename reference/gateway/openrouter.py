"""OpenRouter integration boundary for the NoeRelay gateway.

Phase 6 scope: the client interface, the payload builder, a deterministic,
network-free stub, and a live HTTP client via ``urllib.request``.

EPR-API-007 is enforced at the payload boundary: ``model`` is always an
explicit, non-forbidden model id, and the policy's provider-routing block is
injected verbatim (``data_collection: "deny"``, ``zdr: true``,
``ignore: ["openai"]``). Automatic selection is structurally impossible.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Protocol

from .config import ConfigError, GatewayConfig

_PASSTHROUGH_FIELDS = (
    "temperature",
    "max_tokens",
    "tools",
    "tool_choice",
    "response_format",
)


class OpenRouterError(RuntimeError):
    """Raised when the live OpenRouter transport fails.

    Error messages are sanitized — the API key is never included.
    """


class OpenRouterClient(Protocol):
    """Interface for any OpenRouter chat-completions transport."""

    def create_chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send a chat-completions payload; return the OpenAI-shaped response."""
        ...


def build_chat_payload(
    selected_plan: dict[str, Any],
    inference_request: dict[str, Any],
    policy: dict[str, Any],
    config: GatewayConfig | None = None,
) -> dict[str, Any]:
    """Build the OpenRouter request body from a selected plan.

    ``model`` is always the explicit selected ``model_id``; a missing or
    forbidden model id raises :class:`ConfigError` (fail-closed). ``config`` is
    reserved for future use (e.g., base-URL normalization) and is currently
    unused.
    """
    del config  # reserved for future use

    model_id = selected_plan.get("model_id")
    if not model_id:
        raise ConfigError("selected_plan is missing an explicit model_id")

    inference = policy.get("inference", {})
    forbidden_ids = {
        str(item).lower() for item in inference.get("forbidden_model_ids", [])
    }
    forbidden_prefixes = tuple(
        str(item).lower() for item in inference.get("forbidden_model_prefixes", [])
    )
    model_lower = str(model_id).lower()
    if model_lower in forbidden_ids or model_lower.startswith(forbidden_prefixes):
        raise ConfigError(f"model_id {model_id!r} is denied by policy")

    passthrough = inference_request.get("passthrough") or {}
    payload: dict[str, Any] = {
        "model": model_id,
        "messages": inference_request["messages"],
    }
    for field in _PASSTHROUGH_FIELDS:
        if field in passthrough:
            payload[field] = passthrough[field]

    payload["provider"] = inference["openrouter"]["provider_routing"]
    return payload


def _last_user_text(messages: list[dict[str, Any]]) -> str:
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


class StubOpenRouterClient:
    """Deterministic, network-free OpenRouter client for the skeleton."""

    def __init__(self, policy: dict[str, Any]) -> None:
        self._policy = policy

    def create_chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return a deterministic OpenAI-shaped stub response.

        The payload is validated first so the stub remains honest about the
        interface contract (explicit non-forbidden model id, deny-data-collection
        provider block that ignores ``openai``).
        """
        self._validate_payload(payload)
        last_user_text = _last_user_text(payload.get("messages", []))
        content = f"[noerelay stub] {last_user_text[:200]}"
        return {
            "id": f"gen-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": payload["model"],
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }

    def _validate_payload(self, payload: dict[str, Any]) -> None:
        inference = self._policy.get("inference", {})
        forbidden_ids = {
            str(item).lower() for item in inference.get("forbidden_model_ids", [])
        }
        forbidden_prefixes = tuple(
            str(item).lower() for item in inference.get("forbidden_model_prefixes", [])
        )

        model = str(payload.get("model", "")).lower()
        if not model or model in forbidden_ids or model.startswith(forbidden_prefixes):
            raise ConfigError("payload model is missing or denied by policy")

        provider = payload.get("provider") or {}
        if provider.get("data_collection") != "deny":
            raise ConfigError("provider.data_collection must be 'deny'")
        if "openai" not in provider.get("ignore", []):
            raise ConfigError("provider.ignore must include 'openai'")


class HttpOpenRouterClient:
    """Live OpenRouter transport via ``urllib.request``.

    Gated on ``NOERELAY_OPENROUTER_MODE=live`` and a non-empty
    ``OPENROUTER_API_KEY``.  Error messages are sanitized so the API key is
    never logged.
    """

    def __init__(self, config: GatewayConfig) -> None:
        self._config = config
        if not config.openrouter_api_key:
            raise ConfigError("OPENROUTER_API_KEY is required for live mode")

    def create_chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST to ``{base_url}/chat/completions`` and return the parsed body.

        Raises :class:`OpenRouterError` on transport, HTTP, or parse failures.
        The error message is sanitized — the API key is never included.
        """
        url = f"{self._config.openrouter_base_url}/chat/completions"
        body_bytes = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self._config.openrouter_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "HTTP-Referer": self._config.openrouter_http_referer,
            "X-Title": self._config.openrouter_app_title,
        }
        request = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            # Sanitize: never include the Authorization header in error messages.
            try:
                body = exc.read().decode("utf-8")
            except Exception:
                body = "(unable to read error body)"
            raise OpenRouterError(
                f"OpenRouter HTTP {exc.code} from {url}: {body[:500]}"
            ) from None
        except urllib.error.URLError as exc:
            raise OpenRouterError(
                f"OpenRouter transport error for {url}: {exc.reason}"
            ) from None
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise OpenRouterError(
                f"OpenRouter unparseable response from {url}: {exc}"
            ) from None
        except OSError as exc:
            raise OpenRouterError(
                f"OpenRouter OS error for {url}: {exc}"
            ) from None