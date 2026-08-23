"""API-key authentication middleware for the gateway."""

from __future__ import annotations

import hashlib
import hmac
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
        require_auth: bool | None = None,
        default_rate: float = 10.0,
        default_burst: int = 20,
    ) -> None:
        self._api_keys = api_keys or set()
        self._key_manager = api_key_manager
        self._rate_limiter = rate_limiter
        self._require_auth = (
            bool(self._api_keys) or api_key_manager is not None
            if require_auth is None
            else require_auth
        )
        self._default_rate = default_rate
        self._default_burst = default_burst

    @classmethod
    def from_csv(cls, raw: str | None) -> "AuthMiddleware":
        """Build an instance from a comma-separated API key string."""
        if not raw:
            return cls()
        return cls({key.strip() for key in raw.split(",") if key.strip()})

    @staticmethod
    def _bearer_token(headers: dict[str, str]) -> str | None:
        """Parse a Bearer credential without accepting empty or ambiguous values."""
        auth = headers.get("Authorization", "")
        scheme, separator, token = auth.partition(" ")
        if not separator or scheme.casefold() != "bearer" or not token.strip():
            return None
        return token.strip()

    def authenticate_with_metadata(
        self, headers: dict[str, str]
    ) -> tuple[bool, dict[str, Any] | None, dict[str, str]]:
        """Authenticate and return identity plus rate-limit response headers."""
        raw_key = self._bearer_token(headers)
        if raw_key is None:
            return (not self._require_auth, None, {})

        if not self._require_auth and not self._api_keys and self._key_manager is None:
            return True, None, {}

        key_info: dict[str, Any] | None = None
        if self._key_manager is not None:
            key_info = self._key_manager.authenticate(raw_key)

        if key_info is None and self._api_keys:
            # Evaluate every configured key so the match position does not leak
            # through early-return timing differences.
            matched = False
            for configured_key in self._api_keys:
                matched = hmac.compare_digest(raw_key, configured_key) or matched
            if matched:
                key_info = {
                    "key_id": "env-" + hashlib.sha256(raw_key.encode()).hexdigest()[:16],
                    "role": "admin",
                    "tenant_id": "default",
                    "rate_limit_rate": self._default_rate,
                    "rate_limit_burst": self._default_burst,
                }

        if key_info is None:
            return False, None, {}

        rate_headers: dict[str, str] = {}
        if self._rate_limiter is not None:
            allowed, rate_headers = self._rate_limiter.allow_with_metadata(
                key_info["key_id"],
                key_info.get("rate_limit_rate", self._default_rate),
                key_info.get("rate_limit_burst", self._default_burst),
            )
            if not allowed:
                return False, {"rate_limited": True, **key_info}, rate_headers
        return True, key_info, rate_headers

    def authenticate(self, headers: dict[str, str]) -> bool | tuple[bool, dict[str, Any] | None]:
        """Check if the request has a valid API key.

        Returns:
            - bool (legacy mode): True if authenticated, False otherwise.
            - tuple[bool, dict|None] (manager mode): (success, key_info).
        """
        # Preserve the historical return shape for direct callers and tests.
        if self._key_manager is not None:
            allowed, key_info, _ = self.authenticate_with_metadata(headers)
            return allowed, key_info

        # Legacy mode: simple key set
        allowed, _, _ = self.authenticate_with_metadata(headers)
        return allowed
