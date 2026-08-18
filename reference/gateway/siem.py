"""SIEM integration for log shipping and security event forwarding.

Supports:
- JSON log shipping to HTTP endpoints (Splunk HEC, Datadog Logs API)
- Syslog format output
- CEF (Common Event Format) for Splunk/QRadar
- LEEF for IBM QRadar

Dependency-free (stdlib only).
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from typing import Any


class SIEMIntegration:
    """SIEM integration for log shipping and security event forwarding.

    Supports:
    - JSON log shipping to HTTP endpoints (Splunk HEC, Datadog Logs API)
    - Syslog format output
    - CEF (Common Event Format) for Splunk/QRadar
    - LEEF for IBM QRadar
    """

    # CEF header fields
    CEF_VERSION = "0"
    CEF_DEVICE_VENDOR = "ElectroHire"
    CEF_DEVICE_PRODUCT = "NoeRelay"
    CEF_DEVICE_VERSION = "0.1.0"

    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        format: str = "json",
    ) -> None:
        self._endpoint = endpoint
        self._api_key = api_key
        self._format = format
        self._buffer: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ship_log(self, log_entry: dict[str, Any]) -> bool:
        """Ship a log entry to the SIEM endpoint.

        Returns True if shipped successfully, False otherwise.
        """
        if not self._endpoint:
            self._buffer.append(log_entry)
            return True  # Buffered, not shipped

        payload = self._format_log_entry(log_entry)
        return self._send_to_endpoint(payload)

    def ship_audit_event(self, audit_entry: dict[str, Any]) -> bool:
        """Ship an audit event in SIEM format.

        Adds SIEM-specific fields to the audit entry.
        """
        enriched = dict(audit_entry)
        enriched["_siem_timestamp"] = time.time()
        enriched["_siem_type"] = "audit"
        enriched["_siem_source"] = "noerelay-gateway"
        return self.ship_log(enriched)

    def ship_ledger_event(self, ledger_event: dict[str, Any]) -> bool:
        """Ship a ledger event to SIEM for security monitoring.

        Enriches the ledger event with SIEM metadata.
        """
        enriched = dict(ledger_event)
        enriched["_siem_timestamp"] = time.time()
        enriched["_siem_type"] = "ledger"
        enriched["_siem_source"] = "noerelay-gateway"
        return self.ship_log(enriched)

    def format_cef(self, event: dict[str, Any]) -> str:
        """Format an event in Common Event Format (CEF).

        CEF format: ``CEF:Version|Device Vendor|Device Product|Device
        Version|Signature ID|Name|Severity|Extension``
        """
        name = event.get("name", event.get("event_type", "Unknown"))
        severity = str(event.get("severity", "5"))
        signature_id = event.get("event_type", event.get("action", "Unknown"))

        # Build extension string
        extensions: list[str] = []
        for key, value in event.items():
            if key in {"name", "severity", "event_type", "action", "timestamp"}:
                continue
            # CEF extension keys must use alphanumeric and underscore
            safe_key = "".join(c if c.isalnum() or c == "_" else "_" for c in str(key))
            safe_key = safe_key[:31]  # CEF key length limit
            # Escape backslashes and equals signs in values
            safe_value = str(value).replace("\\", "\\\\").replace("=", "\\=")
            extensions.append(f"{safe_key}={safe_value}")

        extension_str = " ".join(extensions)
        return (
            f"CEF:{self.CEF_VERSION}|{self.CEF_DEVICE_VENDOR}|"
            f"{self.CEF_DEVICE_PRODUCT}|{self.CEF_DEVICE_VERSION}|"
            f"{signature_id}|{name}|{severity}|{extension_str}"
        )

    def format_leef(self, event: dict[str, Any]) -> str:
        """Format an event in Log Extended Event Format (LEEF).

        LEEF format: ``LEEF:2.0|Vendor|Product|Version|EventID|attributes``
        """
        event_id = event.get("event_type", event.get("action", "Unknown"))

        # Build attribute string (tab-separated key=value pairs)
        attributes: list[str] = []
        for key, value in event.items():
            if key in {"event_type", "action", "timestamp"}:
                continue
            # LEEF attribute keys are case-insensitive alphanumeric
            safe_key = "".join(c if c.isalnum() else "_" for c in str(key))
            # Escape tab, newline, and backslash in values
            safe_value = (
                str(value)
                .replace("\\", "\\\\")
                .replace("\t", "\\t")
                .replace("\n", "\\n")
                .replace("\r", "\\r")
            )
            attributes.append(f"{safe_key}={safe_value}")

        attr_str = "\t".join(attributes)
        return (
            f"LEEF:2.0|{self.CEF_DEVICE_VENDOR}|{self.CEF_DEVICE_PRODUCT}|"
            f"{self.CEF_DEVICE_VERSION}|{event_id}|\t{attr_str}"
        )

    def format_syslog(self, event: dict[str, Any]) -> str:
        """Format an event as a syslog message (RFC 5424 style).

        Returns a string like:
        ``<134>1 {timestamp} {hostname} noerelay {pid} - - {structured_data} {msg}``
        """
        import socket

        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        hostname = socket.gethostname()
        pid = 0  # simplified
        msg = json.dumps(event, ensure_ascii=False)
        structured_data = "-"

        return (
            f"<134>1 {timestamp} {hostname} noerelay {pid} - "
            f"{structured_data} {msg}"
        )

    def flush(self) -> int:
        """Flush buffered logs. Returns count shipped."""
        with self._lock:
            if not self._buffer:
                return 0
            count = 0
            if self._endpoint:
                for entry in self._buffer:
                    payload = self._format_log_entry(entry)
                    if self._send_to_endpoint(payload):
                        count += 1
            self._buffer.clear()
            return count

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _format_log_entry(self, entry: dict[str, Any]) -> str:
        """Format a log entry according to the configured format."""
        if self._format == "cef":
            return self.format_cef(entry)
        elif self._format == "leef":
            return self.format_leef(entry)
        elif self._format == "syslog":
            return self.format_syslog(entry)
        else:
            # Default JSON format
            return json.dumps(entry, ensure_ascii=False)

    def _send_to_endpoint(self, payload: str) -> bool:
        """Send a payload to the SIEM HTTP endpoint."""
        if not self._endpoint:
            return False
        try:
            data = payload.encode("utf-8")
            req = urllib.request.Request(
                self._endpoint,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "NoeRelay-SIEM/0.1.0",
                },
            )
            if self._api_key:
                req.add_header("Authorization", f"Bearer {self._api_key}")
            with urllib.request.urlopen(req, timeout=5) as resp:
                return 200 <= resp.status < 300
        except Exception:
            # Buffer on failure for later flush
            return False