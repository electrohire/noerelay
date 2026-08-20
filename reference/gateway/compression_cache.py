"""Compression cache — LRU cache with TTL-based expiry for CompressionResult.

Stores compression results keyed by a hash of the messages array. This avoids
re-compressing identical or very similar message sets on repeated calls.

Thread-safe via a threading.Lock.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any


def _hash_messages(messages: list[dict[str, Any]]) -> str:
    """Compute a stable SHA-256 hash of a messages array."""
    canonical = json.dumps(messages, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CompressionCache:
    """LRU cache for :class:`CompressionResult` objects.

    Parameters
    ----------
    max_size:
        Maximum number of entries before evicting least-recently-used.
    ttl_seconds:
        Time-to-live in seconds. Entries older than this are considered
        expired and will not be returned by :meth:`get`.

    Thread-safe.
    """

    def __init__(self, max_size: int = 100, ttl_seconds: int = 300) -> None:
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, Any]] = {}  # key -> (timestamp, result)
        self._access_order: list[str] = []  # LRU tracking
        self._hits: int = 0
        self._misses: int = 0
        self._evictions: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, messages: list[dict[str, Any]]) -> Any | None:
        """Look up a cached compression result by messages hash.

        Returns ``None`` on cache miss, expired entry, or if the messages
        produce a hash not present in the cache.
        """
        key = _hash_messages(messages)
        with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None

            timestamp, result = self._cache[key]

            # TTL check (>= so ttl=0 means immediate expiry)
            if (time.monotonic() - timestamp) >= self._ttl_seconds:
                self._cache.pop(key, None)
                self._access_order.remove(key)
                self._misses += 1
                self._evictions += 1
                return None

            # Update LRU order (move to end = most-recently-used)
            self._access_order.remove(key)
            self._access_order.append(key)
            self._hits += 1
            return result

    def put(self, messages: list[dict[str, Any]], result: Any) -> None:
        """Store a compression result, keyed by messages hash.

        If the cache is at capacity, the least-recently-used entry is evicted.
        """
        key = _hash_messages(messages)
        with self._lock:
            # If key already exists, update it and bump LRU
            if key in self._cache:
                self._access_order.remove(key)

            # Evict if at capacity and key is new
            elif len(self._cache) >= self._max_size:
                self._evict_one()

            self._cache[key] = (time.monotonic(), result)
            self._access_order.append(key)

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = self._hits / max(total, 1)
            return {
                "size": len(self._cache),
                "max_size": self._max_size,
                "ttl_seconds": self._ttl_seconds,
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "hit_rate": round(hit_rate, 4),
            }

    def clear(self) -> None:
        """Remove all cached entries and reset statistics."""
        with self._lock:
            self._cache.clear()
            self._access_order.clear()
            self._hits = 0
            self._misses = 0
            self._evictions = 0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _evict_one(self) -> None:
        """Evict the least-recently-used entry. Must hold ``_lock``."""
        if self._access_order:
            oldest_key = self._access_order.pop(0)
            self._cache.pop(oldest_key, None)
            self._evictions += 1