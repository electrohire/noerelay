"""Compression profiler — tracks per-strategy performance over time.

Records timing, token savings, and quality scores for each compression
operation.  Aggregates stats by strategy and can recommend the best
strategy based on historical performance.

Dependency-free (stdlib only).  Thread-safe via a threading.Lock.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProfileEntry:
    """A single compression profiling record."""

    strategy: str
    duration_ms: float
    original_tokens: int
    compressed_tokens: int
    compression_ratio: float
    quality_score: float | None = None
    timestamp: float = field(default_factory=time.time)


class CompressionProfiler:
    """Tracks per-strategy compression performance.

    Records timing, token savings, and quality scores.  Provides
    aggregated statistics and strategy recommendations.
    """

    def __init__(self, max_entries: int = 1000) -> None:
        self._max_entries = max_entries
        self._entries: list[ProfileEntry] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(self, entry: ProfileEntry) -> None:
        """Record a compression profiling entry."""
        with self._lock:
            self._entries.append(entry)
            # Trim oldest entries if over max
            while len(self._entries) > self._max_entries:
                self._entries.pop(0)

    def get_stats(self) -> dict[str, Any]:
        """Return aggregated stats grouped by strategy.

        Returns a dict with keys matching strategy names (``dedup``,
        ``prune``, ``auto``, ``summarize``) each containing:
        count, avg_duration_ms, avg_tokens_saved, avg_ratio, avg_quality.
        Also includes an ``overall`` key with global stats.
        """
        with self._lock:
            entries = list(self._entries)

        if not entries:
            return {"overall": {"count": 0}}

        by_strategy: dict[str, list[ProfileEntry]] = {}
        for e in entries:
            by_strategy.setdefault(e.strategy, []).append(e)

        result: dict[str, Any] = {}
        for strategy, group in by_strategy.items():
            count = len(group)
            avg_duration = sum(e.duration_ms for e in group) / count
            avg_saved = sum(e.original_tokens - e.compressed_tokens for e in group) / count
            avg_ratio = sum(e.compression_ratio for e in group) / count
            quality_scores = [e.quality_score for e in group if e.quality_score is not None]
            avg_quality = sum(quality_scores) / len(quality_scores) if quality_scores else None

            result[strategy] = {
                "count": count,
                "avg_duration_ms": round(avg_duration, 3),
                "avg_tokens_saved": round(avg_saved, 1),
                "avg_ratio": round(avg_ratio, 4),
                "avg_quality": round(avg_quality, 4) if avg_quality is not None else None,
            }

        # Overall stats
        total = len(entries)
        total_tokens_saved = sum(e.original_tokens - e.compressed_tokens for e in entries)
        result["overall"] = {
            "count": total,
            "avg_duration_ms": round(sum(e.duration_ms for e in entries) / total, 3),
            "avg_tokens_saved": round(total_tokens_saved / total, 1),
            "avg_ratio": round(sum(e.compression_ratio for e in entries) / total, 4),
            "total_entries": total,
            "total_tokens_saved": int(total_tokens_saved),
        }

        return result

    def get_recommendations(self) -> dict[str, Any]:
        """Return strategy recommendations based on historical performance.

        Ranks strategies by a composite score that weights token savings
        (60%), quality (30%), and speed (10%).  The strategy with the
        highest composite score is recommended.
        """
        stats = self.get_stats()

        # Filter out "overall"
        strategies = {k: v for k, v in stats.items() if k != "overall" and v.get("count", 0) > 0}

        if not strategies:
            return {
                "recommended_strategy": "auto",
                "reason": "insufficient_data",
                "rankings": [],
            }

        rankings: list[dict[str, Any]] = []
        for name, s in strategies.items():
            # Composite score: 60% token savings, 30% quality, 10% speed
            savings_score = min(s["avg_tokens_saved"] / max(s["avg_tokens_saved"], 1), 1.0) if s["avg_tokens_saved"] > 0 else 0
            quality_score = s["avg_quality"] if s["avg_quality"] is not None else 0.7
            speed_score = 1.0 / max(s["avg_duration_ms"], 0.1)  # faster = higher

            # Normalize speed relative to fastest
            max_speed = max(
                1.0 / max(s2["avg_duration_ms"], 0.1)
                for s2 in strategies.values()
            )
            speed_score = speed_score / max(max_speed, 0.001)

            composite = (savings_score * 0.6) + (quality_score * 0.3) + (speed_score * 0.1)

            rankings.append({
                "strategy": name,
                "composite_score": round(composite, 4),
                "savings_score": round(savings_score, 4),
                "quality_score": round(quality_score, 4),
                "speed_score": round(speed_score, 4),
                "sample_count": s["count"],
            })

        rankings.sort(key=lambda r: r["composite_score"], reverse=True)

        return {
            "recommended_strategy": rankings[0]["strategy"],
            "reason": "composite_score" if rankings else "insufficient_data",
            "rankings": rankings,
        }

    def export(self) -> dict[str, Any]:
        """Export all profiling data as a dict."""
        with self._lock:
            entries_data = [
                {
                    "strategy": e.strategy,
                    "duration_ms": e.duration_ms,
                    "original_tokens": e.original_tokens,
                    "compressed_tokens": e.compressed_tokens,
                    "compression_ratio": e.compression_ratio,
                    "quality_score": e.quality_score,
                    "timestamp": e.timestamp,
                }
                for e in self._entries
            ]
        return {
            "stats": self.get_stats(),
            "recommendations": self.get_recommendations(),
            "entries": entries_data,
        }

    def clear(self) -> None:
        """Clear all profiling data."""
        with self._lock:
            self._entries.clear()