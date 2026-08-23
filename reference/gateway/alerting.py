"""Alerting system for proactive issue detection.

Alert types:
- cost_overrun: Daily/monthly budget exceeded
- high_hir: Human intervention rate exceeds threshold
- high_rr: Rework rate exceeds threshold
- model_failure: Model repeatedly fails verification
- rate_limit_breach: API key hitting rate limits frequently
- ledger_tamper: Ledger chain verification failed
- escalation_spike: Unusual number of escalations
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

from .webhooks import open_webhook_request, validate_webhook_url


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class AlertManager:
    """Alerting system for proactive issue detection."""

    def __init__(
        self,
        max_alerts: int = 10000,
        max_rules: int = 1000,
        max_webhooks: int = 1000,
    ) -> None:
        if min(max_alerts, max_rules, max_webhooks) < 1:
            raise ValueError("alert manager limits must be at least 1")
        self._alerts: list[dict[str, Any]] = []
        self._rules: list[dict[str, Any]] = []
        self._webhooks: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._max_alerts = max_alerts
        self._max_rules = max_rules
        self._max_webhooks = max_webhooks

    def add_rule(
        self,
        name: str,
        alert_type: str,
        condition: dict[str, Any],
        severity: str = "warning",
    ) -> dict[str, Any]:
        """Add an alert rule."""
        rule = {
            "rule_id": f"alert-rule-{uuid.uuid4().hex}",
            "name": name,
            "alert_type": alert_type,
            "condition": condition,
            "severity": severity,
            "created_at": _now(),
        }
        with self._lock:
            self._rules.append(rule)
            del self._rules[:-self._max_rules]
        return rule

    def trigger_alert(
        self,
        alert_type: str,
        severity: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Trigger an alert and notify webhooks."""
        alert = {
            "alert_id": f"alert-{uuid.uuid4().hex}",
            "alert_type": alert_type,
            "severity": severity,
            "message": message,
            "details": details or {},
            "timestamp": _now(),
            "acknowledged": False,
            "acknowledged_by": None,
        }
        with self._lock:
            self._alerts.append(alert)
            del self._alerts[:-self._max_alerts]
        self._notify_webhooks(alert)
        return alert

    def get_alerts(
        self,
        severity: str | None = None,
        acknowledged: bool | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get alerts with filtering."""
        with self._lock:
            alerts = list(self._alerts)

        if severity is not None:
            alerts = [a for a in alerts if a["severity"] == severity]
        if acknowledged is not None:
            alerts = [a for a in alerts if a["acknowledged"] == acknowledged]

        return sorted(alerts, key=lambda a: a["timestamp"], reverse=True)[:limit]

    def acknowledge_alert(self, alert_id: str, acknowledged_by: str) -> bool:
        """Acknowledge an alert."""
        with self._lock:
            for alert in self._alerts:
                if alert["alert_id"] == alert_id:
                    alert["acknowledged"] = True
                    alert["acknowledged_by"] = acknowledged_by
                    return True
        return False

    def register_webhook(
        self,
        url: str,
        events: list[str],
        secret: str | None = None,
    ) -> dict[str, Any]:
        """Register a webhook for alert notifications."""
        validate_webhook_url(url)
        webhook = {
            "webhook_id": f"webhook-{uuid.uuid4().hex}",
            "url": url,
            "events": events,
            "secret": secret,
            "created_at": _now(),
        }
        with self._lock:
            self._webhooks.append(webhook)
            del self._webhooks[:-self._max_webhooks]
        return {key: value for key, value in webhook.items() if key != "secret"} | {
            "has_secret": bool(secret)
        }

    def _notify_webhooks(self, alert: dict[str, Any]) -> None:
        """Notify all registered webhooks about an alert."""
        alert_type = alert.get("alert_type", "")
        payload = json.dumps(alert, ensure_ascii=False).encode("utf-8")

        with self._lock:
            webhooks = list(self._webhooks)

        for webhook in webhooks:
            if alert_type not in webhook.get("events", []):
                continue
            try:
                req = urllib.request.Request(
                    webhook["url"],
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                if webhook.get("secret"):
                    signature = self._sign_payload(
                        payload.decode("utf-8"), webhook["secret"]
                    )
                    req.add_header("X-NoeRelay-Signature", signature)
                with open_webhook_request(req, timeout=5):
                    pass
            except (OSError, ValueError):
                pass  # Webhook delivery failures are non-fatal

    @staticmethod
    def _sign_payload(payload_str: str, secret: str) -> str:
        """Sign a payload with HMAC-SHA256."""
        return hmac.new(
            secret.encode(),
            payload_str.encode(),
            hashlib.sha256,
        ).hexdigest()
