"""API-key authentication middleware for the gateway."""

from __future__ import annotations

from typing import Any


class AuthMiddleware:
    """API key authentication via the ``Authorization`` header.

    Supports both legacy simple key sets and the new APIKeyManager with
    per-key rate limiting.
    """

    def __init__(
        self,
        api_keys: set[str] | None = None,
        api_key_manager: Any | None = None,
        rate_limiter: Any | None = None,
    ) -> None:
        self._api_keys = api_keys or set()
        self._key_manager = api_key_manager
        self._rate_limiter = rate_limiter

    @classmethod
    def from_csv(cls, raw: str | None) -> "AuthMiddleware":
        """Build an instance from a comma-separated API key string."""
        if not raw:
            return cls()
        return cls({key.strip() for key in raw.split(",") if key.strip()})

    def authenticate(self, headers: dict[str, str]) -> bool | tuple[bool, dict[str, Any] | None]:
        """Check if the request has a valid API key.

        Returns:
            - bool (legacy mode): True if authenticated, False otherwise.
            - tuple[bool, dict|None] (manager mode): (success, key_info).
        """
        # If APIKeyManager is available, use it
        if self._key_manager is not None:
            auth = headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return False, None
            raw_key = auth[7:]
            key_info = self._key_manager.authenticate(raw_key)
            if key_info is None:
                return False, None
            # Check rate limit
            if self._rate_limiter:
                allowed = self._rate_limiter.allow(
                    key_info["key_id"],
                    key_info.get("rate_limit_rate", 10.0),
                    key_info.get("rate_limit_burst", 20),
                )
                if not allowed:
                    return False, {"rate_limited": True}
            return True, key_info

        # Legacy mode: simple key set
        if not self._api_keys:
            return True  # No keys configured = open access (for dev)
        auth = headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:] in self._api_keys
        return False
