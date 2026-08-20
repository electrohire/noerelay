"""Dedicated tests for the compression profiler module (reference/gateway/compression_profiler.py).

Covers:
- Recording profiling entries
- Aggregated stats by strategy
- Recommendations
- Export
- Thread safety
"""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reference"))

from gateway.compression_profiler import CompressionProfiler, ProfileEntry


class CompressionProfilerBasicTests(unittest.TestCase):
    """Basic recording and stats."""

    def setUp(self):
        self.profiler = CompressionProfiler(max_entries=100)

    def test_empty_stats(self):
        """Empty profiler returns count=0 overall."""
        stats = self.profiler.get_stats()
        self.assertEqual(stats["overall"]["count"], 0)

    def test_record_single_entry(self):
        """Record one entry and verify stats."""
        entry = ProfileEntry(
            strategy="dedup",
            duration_ms=2.5,
            original_tokens=500,
            compressed_tokens=300,
            compression_ratio=0.4,
            quality_score=0.85,
        )
        self.profiler.record(entry)
        stats = self.profiler.get_stats()
        self.assertEqual(stats["overall"]["count"], 1)
        self.assertEqual(stats["dedup"]["count"], 1)
        self.assertEqual(stats["dedup"]["avg_ratio"], 0.4)
        self.assertEqual(stats["dedup"]["avg_quality"], 0.85)

    def test_record_multiple_entries_same_strategy(self):
        """Multiple entries for the same strategy are aggregated."""
        for i in range(5):
            self.profiler.record(ProfileEntry(
                strategy="dedup",
                duration_ms=float(i + 1),
                original_tokens=100,
                compressed_tokens=50,
                compression_ratio=0.5,
                quality_score=0.8,
            ))
        stats = self.profiler.get_stats()
        self.assertEqual(stats["dedup"]["count"], 5)
        self.assertEqual(stats["dedup"]["avg_duration_ms"], 3.0)  # (1+2+3+4+5)/5
        self.assertEqual(stats["dedup"]["avg_ratio"], 0.5)

    def test_record_multiple_strategies(self):
        """Entries for different strategies are grouped separately."""
        self.profiler.record(ProfileEntry(
            strategy="dedup", duration_ms=1.0, original_tokens=200,
            compressed_tokens=100, compression_ratio=0.5, quality_score=0.9,
        ))
        self.profiler.record(ProfileEntry(
            strategy="prune", duration_ms=5.0, original_tokens=400,
            compressed_tokens=200, compression_ratio=0.5, quality_score=0.7,
        ))
        stats = self.profiler.get_stats()
        self.assertIn("dedup", stats)
        self.assertIn("prune", stats)
        self.assertEqual(stats["dedup"]["count"], 1)
        self.assertEqual(stats["prune"]["count"], 1)
        self.assertEqual(stats["overall"]["count"], 2)

    def test_max_entries_trimming(self):
        """Old entries are trimmed when max_entries is exceeded."""
        profiler = CompressionProfiler(max_entries=5)
        for i in range(10):
            profiler.record(ProfileEntry(
                strategy="dedup", duration_ms=1.0, original_tokens=100,
                compressed_tokens=50, compression_ratio=0.5, quality_score=0.8,
            ))
        stats = profiler.get_stats()
        self.assertLessEqual(stats["overall"]["count"], 5)


class CompressionProfilerQualityTests(unittest.TestCase):
    """Quality score handling."""

    def test_quality_none_handled(self):
        """Entries with quality_score=None are handled gracefully."""
        profiler = CompressionProfiler()
        profiler.record(ProfileEntry(
            strategy="dedup", duration_ms=1.0, original_tokens=100,
            compressed_tokens=50, compression_ratio=0.5, quality_score=None,
        ))
        stats = profiler.get_stats()
        self.assertIsNone(stats["dedup"]["avg_quality"])


class CompressionProfilerRecommendationTests(unittest.TestCase):
    """Strategy recommendations."""

    def test_insufficient_data(self):
        """No entries -> default auto recommendation."""
        profiler = CompressionProfiler()
        recs = profiler.get_recommendations()
        self.assertEqual(recs["recommended_strategy"], "auto")
        self.assertEqual(recs["reason"], "insufficient_data")

    def test_single_strategy_recommended(self):
        """One strategy -> it is recommended."""
        profiler = CompressionProfiler()
        profiler.record(ProfileEntry(
            strategy="dedup", duration_ms=1.0, original_tokens=500,
            compressed_tokens=200, compression_ratio=0.6, quality_score=0.9,
        ))
        recs = profiler.get_recommendations()
        self.assertEqual(recs["recommended_strategy"], "dedup")
        self.assertEqual(len(recs["rankings"]), 1)

    def test_best_strategy_wins(self):
        """The strategy with the best overall composite score is recommended."""
        profiler = CompressionProfiler()
        # dedup: slightly better savings, same quality
        for _ in range(10):
            profiler.record(ProfileEntry(
                strategy="dedup", duration_ms=1.0, original_tokens=400,
                compressed_tokens=200, compression_ratio=0.5, quality_score=0.95,
            ))
        # prune: moderate savings, lower quality
        for _ in range(10):
            profiler.record(ProfileEntry(
                strategy="prune", duration_ms=1.0, original_tokens=500,
                compressed_tokens=400, compression_ratio=0.2, quality_score=0.6,
            ))
        recs = profiler.get_recommendations()
        rankings = recs["rankings"]
        dedup_rank = next(r for r in rankings if r["strategy"] == "dedup")
        prune_rank = next(r for r in rankings if r["strategy"] == "prune")
        # dedup should rank higher because of better quality and savings
        self.assertGreater(dedup_rank["composite_score"], prune_rank["composite_score"])


class CompressionProfilerExportTests(unittest.TestCase):
    """Data export."""

    def test_export_structure(self):
        """Export returns stats, recommendations, and entries."""
        profiler = CompressionProfiler()
        profiler.record(ProfileEntry(
            strategy="dedup", duration_ms=1.0, original_tokens=100,
            compressed_tokens=50, compression_ratio=0.5, quality_score=0.8,
        ))
        data = profiler.export()
        self.assertIn("stats", data)
        self.assertIn("recommendations", data)
        self.assertIn("entries", data)
        self.assertEqual(len(data["entries"]), 1)

    def test_export_entry_fields(self):
        """Exported entries have all expected fields."""
        profiler = CompressionProfiler()
        profiler.record(ProfileEntry(
            strategy="auto", duration_ms=3.5, original_tokens=1000,
            compressed_tokens=600, compression_ratio=0.4, quality_score=0.75,
        ))
        data = profiler.export()
        entry = data["entries"][0]
        self.assertEqual(entry["strategy"], "auto")
        self.assertEqual(entry["duration_ms"], 3.5)
        self.assertEqual(entry["original_tokens"], 1000)
        self.assertEqual(entry["compressed_tokens"], 600)
        self.assertEqual(entry["compression_ratio"], 0.4)
        self.assertEqual(entry["quality_score"], 0.75)
        self.assertIn("timestamp", entry)

    def test_clear(self):
        """Clear removes all entries."""
        profiler = CompressionProfiler()
        profiler.record(ProfileEntry(
            strategy="dedup", duration_ms=1.0, original_tokens=100,
            compressed_tokens=50, compression_ratio=0.5, quality_score=0.8,
        ))
        profiler.clear()
        stats = profiler.get_stats()
        self.assertEqual(stats["overall"]["count"], 0)


class CompressionProfilerThreadSafetyTests(unittest.TestCase):
    """Concurrent recording tests."""

    def test_concurrent_recording(self):
        """Multiple threads can record entries without errors."""
        profiler = CompressionProfiler(max_entries=500)
        errors: list[Exception] = []

        def worker(worker_id: int, strategy: str):
            try:
                for i in range(50):
                    profiler.record(ProfileEntry(
                        strategy=strategy,
                        duration_ms=float(i),
                        original_tokens=100,
                        compressed_tokens=50,
                        compression_ratio=0.5,
                        quality_score=0.8,
                    ))
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=worker, args=(i, "dedup" if i % 2 == 0 else "prune"))
            for i in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        stats = profiler.get_stats()
        # 4 workers x 50 = 200, but limited by max_entries=500
        self.assertGreater(stats["overall"]["count"], 0)


if __name__ == "__main__":
    unittest.main()