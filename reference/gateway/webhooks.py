"""Webhook registration and delivery.

Events:
- run.completed: A run completed (accepted/rejected/escalated)
- run.escalated: A run was escalated
- cost.alert: A cost alert was triggered
- model.failure: A model failed verification
- benchmark.completed: A benchmark run completed
- ledger.tamper: Ledger chain verification failed
- api_key.created: An API key was created
- api_key.revoked: An API key was revoked
"""

from __future__ import annotations

import hashlib
import hmac
import json
import ipaddress
import socket
import threading
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_webhook_url(url: str, *, allow_private_networks: bool = False) -> str:
    """Validate a webhook target and reject common SSRF/header-injection forms."""
    if not isinstance(url, str) or not url or any(char in url for char in "\r\n"):
        raise ValueError("webhook URL must be a non-empty URL without control characters")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("webhook URL must use http or https and include a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("webhook URL must not contain embedded credentials")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("webhook URL contains an invalid port") from exc
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None and not allow_private_networks and not address.is_global:
        raise ValueError("webhook URL must not target a private or local address")
    return url


def validate_webhook_destination(
    url: str, *, allow_private_networks: bool = False
) -> str:
    """Resolve a webhook hostname and reject non-public destinations.

    Validation is repeated immediately before delivery and for every redirect.
    This closes hostname aliases and redirect-based SSRF paths in addition to
    the literal-address checks performed at registration time.
    """
    validate_webhook_url(url, allow_private_networks=allow_private_networks)
    if allow_private_networks:
        return url
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    if hostname.casefold() == "localhost" or hostname.casefold().endswith(".localhost"):
        raise ValueError("webhook URL must not target localhost")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = socket.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise ValueError("webhook hostname could not be resolved") from exc
    if not addresses:
        raise ValueError("webhook hostname did not resolve to an address")
    for address_info in addresses:
        raw_address = str(address_info[4][0]).split("%", 1)[0]
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError as exc:
            raise ValueError("webhook hostname resolved to an invalid address") from exc
        if not address.is_global:
            raise ValueError("webhook hostname resolved to a private or local address")
    return url


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Revalidate webhook destinations before following redirects."""

    def __init__(self, allow_private_networks: bool) -> None:
        super().__init__()
        self._allow_private_networks = allow_private_networks

    def redirect_request(
        self,
        req: Any,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Any:
        validate_webhook_destination(
            newurl,
            allow_private_networks=self._allow_private_networks,
        )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_webhook_request(
    request: urllib.request.Request,
    *,
    timeout: float,
    allow_private_networks: bool = False,
) -> Any:
    """Open a webhook request after destination and redirect validation."""
    validate_webhook_destination(
        request.full_url,
        allow_private_networks=allow_private_networks,
    )
    opener = urllib.request.build_opener(
        _SafeRedirectHandler(allow_private_networks)
    )
    return opener.open(request, timeout=timeout)


class WebhookManager:
    """Webhook registration and delivery."""

    def __init__(
        self,
        db: Any,
        *,
        allow_private_networks: bool = False,
        max_webhooks: int = 1000,
    ) -> None:
        if max_webhooks < 1:
            raise ValueError("max_webhooks must be at least 1")
        self._db = db
        self._webhooks: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._allow_private_networks = allow_private_networks
        self._max_webhooks = max_webhooks

    @staticmethod
    def _public_webhook(webhook: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in webhook.items()
            if key != "secret"
        } | {"has_secret": bool(webhook.get("secret"))}

    def register(
        self,
        url: str,
        events: list[str],
        secret: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        """Register a webhook."""
        validate_webhook_url(url, allow_private_networks=self._allow_private_networks)
        if not isinstance(events, list) or not events or not all(
            isinstance(event, str) and event for event in events
        ):
            raise ValueError("events must be a non-empty list of event names")
        webhook = {
            "webhook_id": f"wh-{uuid.uuid4().hex}",
            "url": url,
            "events": events,
            "secret": secret,
            "tenant_id": tenant_id,
            "created_at": _now(),
            "active": True,
        }
        with self._lock:
            self._webhooks.append(webhook)
            del self._webhooks[:-self._max_webhooks]
        return self._public_webhook(webhook)

    def list_webhooks(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        """List all webhooks."""
        with self._lock:
            if tenant_id:
                selected = [w for w in self._webhooks if w.get("tenant_id") == tenant_id]
            else:
                selected = list(self._webhooks)
            return [self._public_webhook(webhook) for webhook in selected]

    def delete_webhook(self, webhook_id: str) -> bool:
        """Delete a webhook."""
        with self._lock:
            for i, webhook in enumerate(self._webhooks):
                if webhook["webhook_id"] == webhook_id:
                    self._webhooks.pop(i)
                    return True
        return False

    def deliver(self, event: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Deliver an event to all matching webhooks.

        Returns list of delivery results (success/failure per webhook).
        Uses urllib.request to POST to each webhook URL.
        Includes HMAC-SHA256 signature if secret is provided.
        """
        results: list[dict[str, Any]] = []

        with self._lock:
            matching = [w for w in self._webhooks if event in w.get("events", [])]

        payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        payload_str = payload_bytes.decode("utf-8")

        for webhook in matching:
            result = {
                "webhook_id": webhook["webhook_id"],
                "url": webhook["url"],
                "event": event,
                "success": False,
                "error": None,
            }
            try:
                req = urllib.request.Request(
                    webhook["url"],
                    data=payload_bytes,
                    headers={
                        "Content-Type": "application/json",
                        "X-NoeRelay-Event": event,
                    },
                    method="POST",
                )
                if webhook.get("secret"):
                    signature = self._sign_payload(payload_str, webhook["secret"])
                    req.add_header("X-NoeRelay-Signature", signature)

                with open_webhook_request(
                    req,
                    timeout=10,
                    allow_private_networks=self._allow_private_networks,
                ):
                    pass
                result["success"] = True
            except (OSError, ValueError) as exc:
                result["error"] = f"delivery failed ({type(exc).__name__})"

            results.append(result)

        return results

    @staticmethod
    def _sign_payload(payload_str: str, secret: str) -> str:
        """Sign a payload with HMAC-SHA256."""
        return hmac.new(
            secret.encode(),
            payload_str.encode(),
            hashlib.sha256,
        ).hexdigest()
