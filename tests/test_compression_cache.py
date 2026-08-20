"""Dedicated tests for the compression cache module (reference/gateway/compression_cache.py).

Covers:
- Basic get/put
- LRU eviction
- TTL expiry
- Thread safety
- Stats tracking
- Clear/reset
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reference"))

from gateway.compression_cache import CompressionCache, _hash_messages


class CompressionCacheBasicTests(unittest.TestCase):
    """Basic get/put and key hashing."""

    def setUp(self):
        self.cache = CompressionCache(max_size=10, ttl_seconds=60)

    def test_hash_consistency(self):
        """Same messages produce the same hash."""
        msgs1 = [{"role": "user", "content": "hello"}]
        msgs2 = [{"role": "user", "content": "hello"}]
        self.assertEqual(_hash_messages(msgs1), _hash_messages(msgs2))

    def test_hash_different_content(self):
        """Different messages produce different hashes."""
        msgs1 = [{"role": "user", "content": "hello"}]
        msgs2 = [{"role": "user", "content": "world"}]
        self.assertNotEqual(_hash_messages(msgs1), _hash_messages(msgs2))

    def test_put_and_get(self):
        """Store and retrieve a compression result."""
        msgs = [{"role": "user", "content": "hello"}]
        result = {"compressed": "hi", "tokens": 5}
        self.cache.put(msgs, result)
        cached = self.cache.get(msgs)
        self.assertEqual(cached, result)

    def test_get_miss(self):
        """Cache miss returns None."""
        msgs = [{"role": "user", "content": "hello"}]
        self.assertIsNone(self.cache.get(msgs))

    def test_put_duplicate_key_updates(self):
        """Putting the same key updates the value."""
        msgs = [{"role": "user", "content": "hello"}]
        self.cache.put(msgs, "first")
        self.cache.put(msgs, "second")
        self.assertEqual(self.cache.get(msgs), "second")

    def test_stats_initial(self):
        """Initial stats reflect empty cache."""
        stats = self.cache.stats()
        self.assertEqual(stats["size"], 0)
        self.assertEqual(stats["hits"], 0)
        self.assertEqual(stats["misses"], 0)

    def test_stats_after_operations(self):
        """Stats update after hits and misses."""
        msgs = [{"role": "user", "content": "hello"}]
        self.cache.get(msgs)  # miss
        self.cache.put(msgs, "result")
        self.cache.get(msgs)  # hit
        self.cache.get([{"role": "assistant", "content": "bye"}])  # miss

        stats = self.cache.stats()
        self.assertEqual(stats["size"], 1)
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 2)

    def test_clear(self):
        """Clear removes all entries and resets stats."""
        msgs = [{"role": "user", "content": "hello"}]
        self.cache.put(msgs, "result")
        self.cache.clear()
        # After clear, get is a fresh miss
        self.assertIsNone(self.cache.get(msgs))
        stats = self.cache.stats()
        self.assertEqual(stats["size"], 0)
        self.assertEqual(stats["hits"], 0)


class CompressionCacheEvictionTests(unittest.TestCase):
    """LRU eviction behavior."""

    def test_lru_eviction_at_capacity(self):
        """When cache is full, least-recently-used is evicted."""
        cache = CompressionCache(max_size=3, ttl_seconds=300)

        # Fill cache
        for i in range(3):
            msgs = [{"role": "user", "content": f"msg{i}"}]
            cache.put(msgs, f"result{i}")

        # Access msg0 to make it most recently used
        cache.get([{"role": "user", "content": "msg0"}])

        # Add one more --- should evict msg1 (now LRU)
        msgs3 = [{"role": "user", "content": "msg3"}]
        cache.put(msgs3, "result3")

        # msg0 and msg2 should still be there
        self.assertIsNotNone(cache.get([{"role": "user", "content": "msg0"}]))
        self.assertIsNotNone(cache.get([{"role": "user", "content": "msg2"}]))

        # msg1 should be evicted
        self.assertIsNone(cache.get([{"role": "user", "content": "msg1"}]))

        # msg3 should be in cache
        self.assertIsNotNone(cache.get([{"role": "user", "content": "msg3"}]))

    def test_lru_order_with_get_updates(self):
        """get() updates LRU order."""
        cache = CompressionCache(max_size=2, ttl_seconds=300)

        cache.put([{"role": "user", "content": "a"}], "A")
        cache.put([{"role": "user", "content": "b"}], "B")

        # Access 'a' --- now 'b' is LRU
        cache.get([{"role": "user", "content": "a"}])

        # Add 'c' --- should evict 'b'
        cache.put([{"role": "user", "content": "c"}], "C")

        self.assertIsNotNone(cache.get([{"role": "user", "content": "a"}]))
        self.assertIsNone(cache.get([{"role": "user", "content": "b"}]))
        self.assertIsNotNone(cache.get([{"role": "user", "content": "c"}]))


class CompressionCacheTTLTests(unittest.TestCase):
    """TTL-based expiry."""

    def test_ttl_zero_immediate_expiry(self):
        """Entries expire immediately with ttl=0."""
        cache = CompressionCache(max_size=10, ttl_seconds=0)
        msgs = [{"role": "user", "content": "hello"}]
        cache.put(msgs, "result")
        self.assertIsNone(cache.get(msgs))

    def test_entries_do_not_expire_within_ttl(self):
        """Entry is available within TTL window."""
        cache = CompressionCache(max_size=10, ttl_seconds=300)
        msgs = [{"role": "user", "content": "hello"}]
        cache.put(msgs, "result")
        self.assertEqual(cache.get(msgs), "result")


class CompressionCacheThreadSafetyTests(unittest.TestCase):
    """Concurrent access tests."""

    def test_concurrent_put_and_get(self):
        """Multiple threads can put and get without errors."""
        cache = CompressionCache(max_size=100, ttl_seconds=300)
        errors: list[Exception] = []

        def worker(worker_id: int):
            try:
                for i in range(50):
                    msgs = [{"role": "user", "content": f"w{worker_id}-m{i}"}]
                    cache.put(msgs, f"result-{worker_id}-{i}")
                    _ = cache.get(msgs)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        stats = cache.stats()
        # 4 workers x 50 puts each = 200 entries, but max is 100
        self.assertLessEqual(stats["size"], 100)

    def test_concurrent_clear_and_get(self):
        """clear() is safe with concurrent gets."""
        cache = CompressionCache(max_size=50, ttl_seconds=300)
        for i in range(20):
            cache.put([{"role": "user", "content": f"m{i}"}], f"r{i}")

        errors: list[Exception] = []

        def getter():
            try:
                for _ in range(100):
                    cache.get([{"role": "user", "content": f"m{_ % 20}"}])
            except Exception as e:
                errors.append(e)

        def clearer():
            try:
                for _ in range(5):
                    cache.clear()
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=getter) for _ in range(3)
        ] + [threading.Thread(target=clearer)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)


class CompressionCacheHitRateTests(unittest.TestCase):
    """Hit rate calculation."""

    def test_hit_rate_perfect(self):
        """All gets are hits -> hit_rate = 1.0."""
        cache = CompressionCache(max_size=10, ttl_seconds=300)
        msgs = [{"role": "user", "content": "hello"}]
        cache.put(msgs, "result")
        for _ in range(5):
            cache.get(msgs)
        stats = cache.stats()
        self.assertEqual(stats["hit_rate"], 1.0)

    def test_hit_rate_mixed(self):
        """Mixed hits and misses -> hit_rate between 0 and 1."""
        cache = CompressionCache(max_size=10, ttl_seconds=300)
        msgs1 = [{"role": "user", "content": "hello"}]
        cache.put(msgs1, "result")
        cache.get(msgs1)  # hit
        cache.get(msgs1)  # hit
        cache.get([{"role": "user", "content": "missing"}])  # miss

        stats = cache.stats()
        self.assertEqual(stats["hits"], 2)
        self.assertEqual(stats["misses"], 1)
        self.assertAlmostEqual(stats["hit_rate"], 2 / 3, places=4)


if __name__ == "__main__":
    unittest.main()