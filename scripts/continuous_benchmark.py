#!/usr/bin/env python
"""Continuous benchmark pipeline for NoeRelay self-improvement.

Maps cohort names to dataset files, runs them through ``BenchmarkRunner``,
computes composite scores, and manages baseline-tracking for delta analysis
across improvement cycles.

Architecture:
  Cohort registry -> Dataset loading -> BenchmarkRunner.run_and_report ->
  Composite scoring -> Baseline comparison -> Results dict

This is the engine imported by ``noerelay_self_improve.py`` as
``ContinuousBenchmarkPipeline`` and by ``improvement_analyzer.py`` for
result interpretation.

Usage:
    # Run all standard cohorts once
    python scripts/continuous_benchmark.py --once

    # Run specific cohorts
    python scripts/continuous_benchmark.py --once --cohorts quick-test,coding-tasks

    # Run continuously (watch mode, re-run on changes)
    python scripts/continuous_benchmark.py --watch

    # With LiveBench and HF datasets
    python scripts/continuous_benchmark.py --once --livebench --hf
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure reference/ is importable
_scripts_dir = Path(__file__).resolve().parent
_reference_dir = _scripts_dir.parent / "reference"
if str(_reference_dir) not in sys.path:
    sys.path.insert(0, str(_reference_dir))

from benchmark.datasets import DatasetLoader, JsonlDatasetLoader
from benchmark.evaluator_compose import compose_results
from benchmark.evaluator_contract import EvaluatorResult
from benchmark.hf_datasets import HuggingFaceDatasetLoader
from benchmark.metrics import format_report
from benchmark.runner import BenchmarkRunner

# ---------------------------------------------------------------------------
# Cohort registry - maps cohort name -> (dataset_path, evaluator, harness)
# ---------------------------------------------------------------------------

BENCHMARKS_DIR = _scripts_dir.parent / "benchmarks"

COHORT_REGISTRY: dict[str, dict[str, Any]] = {
    "quick-test": {
        "dataset": str(BENCHMARKS_DIR / "quick-test.jsonl"),
        "evaluator": "contains",
        "harness": None,
        "weight": 0.10,  # contribution to composite score
        "description": "Fast sanity-check benchmark (math, geography, science)",
    },
    "coding-tasks": {
        "dataset": str(BENCHMARKS_DIR / "coding-tasks.jsonl"),
        "evaluator": "contains",
        "harness": None,
        "weight": 0.30,
        "description": "Code generation and editing tasks",
    },
    "reasoning-tasks": {
        "dataset": str(BENCHMARKS_DIR / "reasoning-tasks.jsonl"),
        "evaluator": "contains",
        "harness": None,
        "weight": 0.30,
        "description": "Multi-step reasoning and logic problems",
    },
    "multi-turn-tasks": {
        "dataset": str(BENCHMARKS_DIR / "multi-turn-tasks.jsonl"),
        "evaluator": "contains",
        "harness": None,
        "weight": 0.15,
        "description": "Multi-turn conversation and context retention",
    },
    "safety-tasks": {
        "dataset": str(BENCHMARKS_DIR / "safety-tasks.jsonl"),
        "evaluator": "contains",
        "harness": None,
        "weight": 0.10,
        "description": "Safety and refusal boundary tasks",
    },
    "tool-use-tasks": {
        "dataset": str(BENCHMARKS_DIR / "tool-use-tasks.jsonl"),
        "evaluator": "contains",
        "harness": None,
        "weight": 0.10,
        "description": "Tool calling and structured output tasks",
    },
    "vision-tasks": {
        "dataset": str(BENCHMARKS_DIR / "quick-test.jsonl"),
        "evaluator": "contains",
        "harness": None,
        "weight": 0.00,
        "description": "Vision/multimodal tasks (stub - extend with vision dataset)",
    },
}

HF_COHORTS: dict[str, dict[str, Any]] = {}

LIVEBENCH_COHORTS: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Composite scoring
# ---------------------------------------------------------------------------

def compute_composite_score(
    cohort_results: dict[str, dict[str, Any]],
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Compute a weighted composite score across all cohort results.

    The composite score is a weighted average of per-cohort accuracy,
    penalised for high latency, rework, escalation, and safety failures.
    """
    if not cohort_results:
        return {
            "overall_score": 0.0,
            "quality_score": 0.0,
            "cost_efficiency": 0.0,
            "safety_score": 1.0,
            "per_cohort": {},
        }

    default_weight = 1.0 / max(len(cohort_results), 1)
    total_weight = 0.0
    weighted_quality = 0.0
    weighted_cost = 0.0
    weighted_safety = 0.0

    per_cohort: dict[str, float] = {}

    for name, report in cohort_results.items():
        if isinstance(report, dict) and "error" in report:
            per_cohort[name] = 0.0
            continue

        weight = (weights or {}).get(name, default_weight)

        accuracy = float(report.get("accuracy", 0.0))
        rework = float(report.get("rework_rate", 0.0))
        escalation = float(report.get("escalation_rate", 0.0))
        safety_unsafe = float(report.get("unsafe_accept_rate", 0.0))

        # Quality: accuracy (0-1)
        quality = accuracy

        # Cost efficiency: penalised by rework and escalation
        cost_penalty = min(1.0, rework * 2.0 + escalation * 1.5)
        cost_efficiency = max(0.0, 1.0 - cost_penalty)

        # Safety: 1 - unsafe_accept_rate with heavy penalty
        safety = max(0.0, 1.0 - safety_unsafe * 5.0)

        weighted_quality += quality * weight
        weighted_cost += cost_efficiency * weight
        weighted_safety += safety * weight
        total_weight += weight

        per_cohort[name] = quality

    if total_weight == 0:
        return {
            "overall_score": 0.0,
            "quality_score": 0.0,
            "cost_efficiency": 0.0,
            "safety_score": 1.0,
            "per_cohort": per_cohort,
        }

    quality_score = weighted_quality / total_weight
    cost_score = weighted_cost / total_weight
    safety_score = weighted_safety / total_weight

    # Overall: 50% quality, 25% cost efficiency, 25% safety
    overall = quality_score * 0.50 + cost_score * 0.25 + safety_score * 0.25

    return {
        "overall_score": round(overall, 4),
        "quality_score": round(quality_score, 4),
        "cost_efficiency": round(cost_score, 4),
        "safety_score": round(safety_score, 4),
        "per_cohort": {k: round(v, 4) for k, v in per_cohort.items()},
    }


# ---------------------------------------------------------------------------
# ContinuousBenchmarkPipeline
# ---------------------------------------------------------------------------

class ContinuousBenchmarkPipeline:
    """Runs benchmarks across cohorts and manages baseline history."""

    def __init__(
        self,
        gateway_url: str = "http://127.0.0.1:8080",
        output_dir: Path | str | None = None,
        baseline_dir: Path | str | None = None,
        model: str = "axiovex-agni",
        include_livebench: bool = False,
        include_hf: bool = False,
    ) -> None:
        self.gateway_url = gateway_url.rstrip("/")
        self.model = model
        self.output_dir = Path(output_dir) if output_dir else Path("evidence/benchmarks")
        self.baseline_dir = Path(baseline_dir) if baseline_dir else Path("evidence/baselines")
        self.include_livebench = include_livebench
        self.include_hf = include_hf

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.baseline_dir.mkdir(parents=True, exist_ok=True)

        self._runner = BenchmarkRunner(gateway_url=gateway_url, model=model)

    # ----------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------

    def run_once(
        self,
        cohorts: list[str] | None = None,
        prefer_local: bool = False,
    ) -> dict[str, Any]:
        """Run all (or specified) cohorts once and return results dict."""
        runner = self._runner
        if prefer_local:
            runner.prefer_local = True

        cohort_names = cohorts or list(COHORT_REGISTRY.keys())
        cohort_results: dict[str, dict[str, Any]] = {}
        weights: dict[str, float] = {}

        for name in cohort_names:
            cfg = COHORT_REGISTRY.get(name)
            if cfg:
                weights[name] = cfg["weight"]
                cohort_results[name] = self._run_cohort_from_cfg(name, cfg, runner)
                continue

            hf_cfg = HF_COHORTS.get(name)
            if hf_cfg and self.include_hf:
                weights[name] = 0.15
                cohort_results[name] = self._run_hf_cohort(name, hf_cfg, runner)
                continue

            lb_cfg = LIVEBENCH_COHORTS.get(name)
            if lb_cfg and self.include_livebench:
                weights[name] = 0.15
                cohort_results[name] = self._run_livebench_cohort(name, lb_cfg, runner)
                continue

            cohort_results[name] = {
                "error": "Unknown cohort: %s" % name,
                "accuracy": 0.0,
            }

        # Normalise weights
        total_w = sum(weights.values())
        if total_w > 0:
            weights = {k: v / total_w for k, v in weights.items()}

        composite = compute_composite_score(cohort_results, weights)

        # Collect evaluator-contract results from each cohort
        evaluator_results: list[EvaluatorResult] = []
        for name in cohort_names:
            er = cohort_results.get(name, {})
            if isinstance(er, dict) and "evaluator_result" in er:
                er_data = er["evaluator_result"]
                if isinstance(er_data, dict):
                    from benchmark.evaluator_contract import _from_dict
                    evaluator_results.append(_from_dict(er_data))

        # Compose evaluator results if we have any
        composed_evaluator = None
        if evaluator_results:
            composed_evaluator = compose_results(
                evaluator_results, "after_implement", "strict"
            )

        timestamp = datetime.now(timezone.utc).isoformat()
        results: dict[str, Any] = {
            "timestamp": timestamp,
            "composite_score": composite,
            "cohorts": cohort_results,
            "metadata": {
                "gateway_url": self.gateway_url,
                "model": self.model,
                "cohorts_run": cohort_names,
                "include_livebench": self.include_livebench,
                "include_hf": self.include_hf,
            },
        }

        if composed_evaluator is not None:
            results["evaluator_result"] = composed_evaluator.to_dict()

        # Save results
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        results_file = self.output_dir / ("run-%s.json" % run_id)
        results_file.write_text(
            json.dumps(results, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

        self._update_baseline(results)

        return results

    def get_baseline(self) -> dict[str, Any] | None:
        """Load the current best baseline results."""
        baseline_file = self.baseline_dir / "baseline.json"
        if baseline_file.exists():
            return json.loads(baseline_file.read_text("utf-8"))
        return None

    def list_runs(self) -> list[dict[str, Any]]:
        """List all saved benchmark runs."""
        runs: list[dict[str, Any]] = []
        for f in sorted(self.output_dir.glob("run-*.json")):
            try:
                data = json.loads(f.read_text("utf-8"))
                runs.append({
                    "file": f.name,
                    "timestamp": data.get("timestamp", ""),
                    "score": data.get("composite_score", {}).get("overall_score", 0.0),
                })
            except (json.JSONDecodeError, OSError):
                continue
        return runs

    # ----------------------------------------------------------------
    # Internal runners
    # ----------------------------------------------------------------

    def _run_cohort_from_cfg(
        self, name: str, cfg: dict[str, Any], runner: BenchmarkRunner
    ) -> dict[str, Any]:
        """Run a single local JSONL cohort."""
        dataset_path = cfg["dataset"]
        evaluator = cfg.get("evaluator", "exact_match")
        harness = cfg.get("harness")

        if not Path(dataset_path).exists():
            return {"error": "Dataset not found: %s" % dataset_path, "accuracy": 0.0}

        try:
            dataset = JsonlDatasetLoader(dataset_path)
            report = runner.run_and_report(name, dataset, evaluator, harness)
            return report
        except Exception as exc:
            return {"error": str(exc), "accuracy": 0.0}

    def _run_hf_cohort(
        self, name: str, cfg: dict[str, Any], runner: BenchmarkRunner
    ) -> dict[str, Any]:
        """Run a HuggingFace dataset cohort."""
        try:
            dataset = HuggingFaceDatasetLoader(
                dataset_id=cfg["dataset_id"],
                revision=cfg.get("revision"),
                split=cfg.get("split", "test"),
                hf_token=os.environ.get("HF_TOKEN"),
                config=cfg.get("config"),
            )
            evaluator = cfg.get("evaluator", "exact_match")
            harness = cfg.get("harness")
            report = runner.run_and_report(name, dataset, evaluator, harness)
            return report
        except Exception as exc:
            return {"error": str(exc), "accuracy": 0.0}

    def _run_livebench_cohort(
        self, name: str, cfg: dict[str, Any], runner: BenchmarkRunner
    ) -> dict[str, Any]:
        """Run a LiveBench cohort (stub)."""
        return {"error": "LiveBench integration not yet implemented", "accuracy": 0.0}

    # ----------------------------------------------------------------
    # Baseline management
    # ----------------------------------------------------------------

    def _update_baseline(self, results: dict[str, Any]) -> None:
        """Update the baseline if this run has the best composite score."""
        current_score = results.get("composite_score", {}).get("overall_score", 0.0)
        existing = self.get_baseline()
        if existing is None:
            self._save_baseline(results)
            return

        existing_score = existing.get("composite_score", {}).get("overall_score", 0.0)
        if current_score > existing_score:
            archive_name = "baseline-%s.json" % existing.get("timestamp", "unknown").replace(":", "-")
            (self.baseline_dir / archive_name).write_text(
                json.dumps(existing, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            self._save_baseline(results)

    def _save_baseline(self, results: dict[str, Any]) -> None:
        """Save results as the current baseline."""
        baseline_file = self.baseline_dir / "baseline.json"
        baseline_file.write_text(
            json.dumps(results, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# Watch mode
# ---------------------------------------------------------------------------

def watch_loop(
    pipeline: ContinuousBenchmarkPipeline,
    cohorts: list[str] | None = None,
    interval_seconds: float = 300.0,
) -> None:
    """Continuously run benchmarks at a fixed interval."""
    print("Watch mode: running every %.0fs. Press Ctrl+C to stop." % interval_seconds)
    iteration = 0
    while True:
        iteration += 1
        print("\n" + "=" * 60)
        print("  Watch iteration %d - %s" % (iteration, datetime.now(timezone.utc).isoformat()))
        print("=" * 60)
        try:
            results = pipeline.run_once(cohorts=cohorts)
            score = results.get("composite_score", {}).get("overall_score", 0.0)
            print("  Composite score: %.4f" % score)
        except Exception as exc:
            print("  ERROR: %s" % exc)
        print("  Sleeping %.0fs..." % interval_seconds)
        time.sleep(interval_seconds)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="NoeRelay Continuous Benchmark Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--gateway", default=os.environ.get("NOERELAY_GATEWAY_URL", "http://127.0.0.1:8080"),
        help="Gateway base URL",
    )
    parser.add_argument(
        "--model", default="axiovex-agni", help="Model ID to benchmark",
    )
    parser.add_argument(
        "--once", action="store_true", help="Run once and exit",
    )
    parser.add_argument(
        "--watch", action="store_true", help="Run continuously (watch mode)",
    )
    parser.add_argument(
        "--interval", type=float, default=300.0,
        help="Watch mode interval in seconds (default: 300)",
    )
    parser.add_argument(
        "--cohorts", type=str, default=None,
        help="Comma-separated cohort names (default: all registered)",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output directory for results",
    )
    parser.add_argument(
        "--baseline-dir", type=str, default=None,
        help="Directory for baseline storage",
    )
    parser.add_argument(
        "--livebench", action="store_true", help="Include LiveBench cohorts",
    )
    parser.add_argument(
        "--hf", action="store_true", help="Include HuggingFace dataset cohorts",
    )
    parser.add_argument(
        "--prefer-local", action="store_true",
        help="Prefer local models over cloud",
    )
    parser.add_argument(
        "--list", action="store_true", help="List available cohorts and exit",
    )
    parser.add_argument(
        "--list-runs", action="store_true", help="List saved benchmark runs and exit",
    )
    args = parser.parse_args()

    pipeline = ContinuousBenchmarkPipeline(
        gateway_url=args.gateway,
        model=args.model,
        output_dir=args.output,
        baseline_dir=args.baseline_dir,
        include_livebench=args.livebench,
        include_hf=args.hf,
    )

    if args.list:
        print("Registered cohorts:")
        for name, cfg in sorted(COHORT_REGISTRY.items()):
            print("  %-20s - %s (weight=%.2f)" % (name, cfg["description"], cfg["weight"]))
        if HF_COHORTS:
            print("\nHF dataset cohorts:")
            for name in sorted(HF_COHORTS):
                print("  %s" % name)
        return 0

    if args.list_runs:
        runs = pipeline.list_runs()
        if not runs:
            print("No saved runs found.")
            return 0
        print("%-22s %-30s %8s" % ("Timestamp", "File", "Score"))
        print("-" * 62)
        for r in runs:
            print("%-22s %-30s %8.4f" % (r["timestamp"], r["file"], r["score"]))
        return 0

    cohort_list: list[str] | None = None
    if args.cohorts:
        cohort_list = [c.strip() for c in args.cohorts.split(",") if c.strip()]

    if args.watch:
        watch_loop(pipeline, cohorts=cohort_list, interval_seconds=args.interval)
    elif args.once:
        results = pipeline.run_once(cohorts=cohort_list, prefer_local=args.prefer_local)
        score = results.get("composite_score", {}).get("overall_score", 0.0)
        print("\nComposite score: %.4f" % score)
        print("Quality:        %.4f" % results["composite_score"]["quality_score"])
        print("Cost efficiency: %.4f" % results["composite_score"]["cost_efficiency"])
        print("Safety:          %.4f" % results["composite_score"]["safety_score"])
        print("\nPer-cohort:")
        for name, s in results["composite_score"]["per_cohort"].items():
            print("  %-20s: %.4f" % (name, s))
        print("\nResults saved to: %s" % pipeline.output_dir)
    else:
        parser.print_help()
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
