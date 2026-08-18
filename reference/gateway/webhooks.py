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
import threading
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class WebhookManager:
    """Webhook registration and delivery."""

    def __init__(self, db: Any) -> None:
        self._db = db
        self._webhooks: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def register(
        self,
        url: str,
        events: list[str],
        secret: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        """Register a webhook."""
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
        return webhook

    def list_webhooks(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        """List all webhooks."""
        with self._lock:
            if tenant_id:
                return [w for w in self._webhooks if w.get("tenant_id") == tenant_id]
            return list(self._webhooks)

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

                urllib.request.urlopen(req, timeout=10)
                result["success"] = True
            except Exception as exc:
                result["error"] = str(exc)

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