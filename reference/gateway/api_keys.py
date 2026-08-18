"""API key management for the NoeRelay gateway.

Provides create, revoke, rotate, and authenticate operations.
Keys are stored as SHA-256 hashes in the database (never plaintext).
Keys are returned in plaintext only at creation time (prefix 'noerelay-').
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from typing import Any


class APIKeyManager:
    """Manage API keys with create, revoke, rotate, and authenticate.

    Keys are stored as SHA-256 hashes in the database (never plaintext).
    Keys are returned in plaintext only at creation time (prefix 'noerelay-').
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    def create_key(
        self,
        name: str,
        role: str = "operator",
        rate_limit_rate: float = 10.0,
        rate_limit_burst: int = 20,
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        """Create a new API key. Returns {key_id, key (plaintext, shown once), name, role}."""
        raw_key = "noerelay-" + secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        key_id = self._db.create_api_key(
            key_hash, name, role, rate_limit_rate, rate_limit_burst, tenant_id
        )
        return {
            "key_id": key_id,
            "key": raw_key,
            "name": name,
            "role": role,
        }

    def authenticate(self, raw_key: str) -> dict[str, Any] | None:
        """Authenticate a raw API key. Returns key info or None."""
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        key_info = self._db.get_api_key_by_hash(key_hash)
        if key_info and not key_info.get("revoked_at"):
            self._db.update_last_used(key_info["key_id"])
            return key_info
        return None

    def list_keys(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        """List all API keys (without hashes)."""
        return self._db.list_api_keys(tenant_id)

    def revoke_key(self, key_id: str) -> bool:
        """Revoke an API key."""
        return self._db.revoke_api_key(key_id)

    def rotate_key(self, key_id: str) -> dict[str, Any]:
        """Rotate an API key. Revokes old key, creates new one."""
        keys = self._db.list_api_keys()
        old_key = next((k for k in keys if k["key_id"] == key_id), None)
        if not old_key:
            raise ValueError(f"key {key_id} not found")
        self.revoke_key(key_id)
        return self.create_key(
            name=old_key["name"],
            role=old_key["role"],
            rate_limit_rate=old_key.get("rate_limit_rate", 10.0),
            rate_limit_burst=old_key.get("rate_limit_burst", 20),
            tenant_id=old_key.get("tenant_id", "default"),
        )