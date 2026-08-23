"""API key management for the NoeRelay gateway.

Provides create, revoke, rotate, and authenticate operations.
Keys are stored as SHA-256 hashes in the database (never plaintext).
Keys are returned in plaintext only at creation time (prefix 'noerelay-').
"""

from __future__ import annotations

import hashlib
import math
import re
import secrets
from typing import Any

from .rbac import Role


_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


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
        if not isinstance(name, str) or not name.strip() or len(name) > 128:
            raise ValueError("name must be a non-empty string of at most 128 characters")
        try:
            Role(role)
        except (TypeError, ValueError):
            raise ValueError("role must be one of admin, operator, auditor, developer, viewer") from None
        if (
            isinstance(rate_limit_rate, bool)
            or not isinstance(rate_limit_rate, (int, float))
            or not math.isfinite(float(rate_limit_rate))
            or float(rate_limit_rate) < 0
        ):
            raise ValueError("rate_limit_rate must be a finite non-negative number")
        if (
            isinstance(rate_limit_burst, bool)
            or not isinstance(rate_limit_burst, int)
            or not 1 <= rate_limit_burst <= 1_000_000
        ):
            raise ValueError("rate_limit_burst must be an integer from 1 to 1000000")
        if not isinstance(tenant_id, str) or not _SAFE_IDENTIFIER.fullmatch(tenant_id):
            raise ValueError("tenant_id contains unsupported characters")
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
        """Rotate an API key atomically and return the new plaintext once."""
        raw_key = "noerelay-" + secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        rotated = self._db.rotate_api_key(key_id, key_hash)
        if rotated is None:
            raise ValueError(f"key {key_id} not found")
        return {**rotated, "key": raw_key}
