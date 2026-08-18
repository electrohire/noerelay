"""Token-bucket rate limiting for the gateway."""

from __future__ import annotations

import threading
import time


class TokenBucketRateLimiter:
    """Token bucket rate limiter."""

    def __init__(self, rate: float = 10.0, burst: int = 20) -> None:
        self._rate = float(rate)
        self._burst = burst
        self._tokens = float(burst)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        """Check if a request is allowed under the rate limit."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(
                float(self._burst), self._tokens + elapsed * self._rate
            )
            self._last_refill = now
            if self._tokens >= 1:
                self._tokens -= 1
                return True
            return False


class PerKeyRateLimiter:
    """Per-API-key rate limiting with configurable tiers."""

    def __init__(self) -> None:
        self._limiters: dict[str, TokenBucketRateLimiter] = {}
        self._lock = threading.Lock()

    def get_limiter(
        self, key_id: str, rate: float = 10.0, burst: int = 20
    ) -> TokenBucketRateLimiter:
        """Get or create a rate limiter for a specific key."""
        with self._lock:
            if key_id not in self._limiters:
                self._limiters[key_id] = TokenBucketRateLimiter(
                    rate=rate, burst=burst
                )
            return self._limiters[key_id]

    def allow(
        self, key_id: str, rate: float = 10.0, burst: int = 20
    ) -> bool:
        """Check if a request is allowed for a specific key."""
        return self.get_limiter(key_id, rate, burst).allow()

    def reset(self, key_id: str | None = None) -> None:
        """Reset rate limiters for a key or all keys."""
        with self._lock:
            if key_id:
                self._limiters.pop(key_id, None)
            else:
                self._limiters.clear()
