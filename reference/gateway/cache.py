"""LRU-style response cache for identical prompts.

Caches responses by prompt hash to avoid redundant local or cloud calls.
Cache entries expire after TTL seconds (default 3600 = 1 hour).
Thread-safe.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any


class ResponseCache:
    """LRU-style response cache for identical prompts.

    Caches responses by prompt hash to avoid redundant local or cloud calls.
    Cache entries expire after TTL seconds (default 3600 = 1 hour).
    Thread-safe.
    """

    def __init__(self, max_size: int = 100, ttl_seconds: int = 3600) -> None:
        self._cache: dict[str, dict[str, Any]] = {}
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._lock = threading.Lock()

    @staticmethod
    def _hash_request(request: dict[str, Any]) -> str:
        """Compute a deterministic hash of the request (excluding timestamps/UUIDs)."""
        material = json.dumps(
            {
                "messages": request.get("messages", []),
                "passthrough": request.get("passthrough", {}),
                "governance": request.get("governance", {}),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def get(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Get a cached response for the request, or None if not cached/expired."""
        key = self._hash_request(request)
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if time.monotonic() >= entry["expires_at"]:
                del self._cache[key]
                return None
            return entry["response"]

    def put(self, request: dict[str, Any], response: dict[str, Any]) -> None:
        """Cache a response for the request."""
        key = self._hash_request(request)
        with self._lock:
            if len(self._cache) >= self._max_size:
                # Evict oldest entry (simple LRU by insertion order)
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]
            self._cache[key] = {
                "response": response,
                "expires_at": time.monotonic() + self._ttl,
            }

    def clear(self) -> None:
        """Clear all cached responses."""
        with self._lock:
            self._cache.clear()

    def size(self) -> int:
        """Return the number of cached entries."""
        with self._lock:
            return len(self._cache)

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        with self._lock:
            return {
                "size": len(self._cache),
                "max_size": self._max_size,
                "ttl_seconds": self._ttl,
            }