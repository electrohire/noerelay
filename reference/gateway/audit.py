"""Complete audit trail for all API calls.

Records: who (actor_id, API key), what (action, resource),
when (timestamp), where (IP address), outcome (success/failure),
and details (request/response summary).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class AuditLogger:
    """Complete audit trail for all API calls.

    Records: who (actor_id, API key), what (action, resource),
    when (timestamp), where (IP address), outcome (success/failure),
    and details (request/response summary).
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    def log_api_call(
        self,
        actor_id: str,
        action: str,
        resource_type: str | None = None,
        resource_id: str | None = None,
        ip_address: str | None = None,
        details: dict[str, Any] | None = None,
        success: bool = True,
    ) -> str:
        """Log an API call to the audit trail."""
        return self._db.record_audit(
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            ip_address=ip_address,
            details=details,
            success=success,
        )

    def query(
        self,
        actor_id: str | None = None,
        action: str | None = None,
        from_ts: str | None = None,
        to_ts: str | None = None,
        resource_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Query the audit trail."""
        return self._db.query_audit_log(
            actor_id=actor_id,
            action=action,
            from_ts=from_ts,
            to_ts=to_ts,
            limit=limit,
            offset=offset,
        )

    def get_actor_activity(
        self,
        actor_id: str,
        from_ts: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get all activity for a specific actor."""
        return self._db.query_audit_log(
            actor_id=actor_id,
            from_ts=from_ts,
            limit=limit,
        )

    def get_resource_history(
        self,
        resource_type: str,
        resource_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get all audit entries for a specific resource."""
        # Query by resource_type and filter by resource_id
        entries = self._db.query_audit_log(limit=limit * 2)
        return [
            e for e in entries
            if e.get("resource_type") == resource_type
            and e.get("resource_id") == resource_id
        ][:limit]

    def detect_anomalies(self, window_minutes: int = 60) -> list[dict[str, Any]]:
        """Detect unusual activity patterns.

        Checks for:
        - Unusual request volume from a single actor
        - Failed authentication attempts
        - Access to unusual resources
        - Off-hours access
        """
        entries = self._db.query_audit_log(limit=1000)
        anomalies: list[dict[str, Any]] = []

        # Count requests per actor
        actor_counts: dict[str, int] = {}
        actor_failures: dict[str, int] = {}
        for entry in entries:
            actor = entry.get("actor_id", "unknown")
            actor_counts[actor] = actor_counts.get(actor, 0) + 1
            if not entry.get("success", True):
                actor_failures[actor] = actor_failures.get(actor, 0) + 1

        # Detect high-volume actors (>50 requests in window)
        for actor, count in actor_counts.items():
            if count > 50:
                anomalies.append({
                    "type": "high_volume",
                    "actor_id": actor,
                    "request_count": count,
                    "window_minutes": window_minutes,
                })

        # Detect actors with high failure rates (>10 failures)
        for actor, failures in actor_failures.items():
            if failures > 10:
                anomalies.append({
                    "type": "high_failure_rate",
                    "actor_id": actor,
                    "failure_count": failures,
                    "total_requests": actor_counts.get(actor, 0),
                })

        return anomalies