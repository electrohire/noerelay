#!/usr/bin/env python
"""Multi-model portfolio optimizer for NoeRelay self-improvement.

Benchmarks multiple models (local + cloud) across all benchmark cohorts
and produces a recommended portfolio mapping each task cohort to the
best-performing model, factoring accuracy, latency, cost, and safety.

Usage:
  python scripts/multi_model_portfolio.py --local       # Benchmark local models only
  python scripts/multi_model_portfolio.py --cloud       # Benchmark cloud models only
  python scripts/multi_model_portfolio.py --all         # Benchmark both
  python scripts/multi_model_portfolio.py --report      # Generate report from existing results
  python scripts/multi_model_portfolio.py --local --quick  # Quick: 3 cases/cohort
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_script_dir = Path(__file__).resolve().parent
_project_root = _script_dir.parent
_reference_dir = _project_root / "reference"

if str(_reference_dir) not in sys.path:
    sys.path.insert(0, str(_reference_dir))
if str(_script_dir) not in sys.path:
    sys.path.insert(0, str(_script_dir))


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
OLLAMA_CHAT_URL = "http://localhost:11434/v1/chat/completions"
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
EVIDENCE_DIR = _project_root / "evidence" / "portfolio"

ACTIVE_COHORTS = ["quick-test", "coding-tasks", "reasoning-tasks", "safety-tasks"]

SCORING_WEIGHTS = {
    "accuracy": 0.50,
    "latency_efficiency": 0.20,
    "cost_efficiency": 0.15,
    "safety": 0.15,
}

OPENROUTER_CANDIDATES = [
    # --- Budget tier (sub-$1/M) ---
    {"id": "meta-llama/llama-3.2-3b-instruct", "tier": "budget", "note": "Fastest + cheapest, tiny latency"},
    {"id": "google/gemma-3-4b-it", "tier": "budget", "note": "Compact, fast, good safety"},
    {"id": "meta/muse-spark-1.3-contributor", "tier": "budget", "note": "NEW Sep 2026: Meta multimodal reasoning, 1M ctx"},
    {"id": "qwen/qwen3.7-flash", "tier": "budget", "note": "NEW: Qwen 3.7 Flash, 1M ctx, ultra-cheap"},
    # --- Standard tier ($1-10/M) ---
    {"id": "deepseek/deepseek-chat", "tier": "standard", "note": "Proven coding + reasoning value"},
    {"id": "meta-llama/llama-4-maverick", "tier": "standard", "note": "Great all-rounder, 1M ctx"},
    {"id": "meta/muse-spark-1.3", "tier": "standard", "note": "NEW Sep 2026: Meta flagship reasoning, 1M ctx"},
    {"id": "google/gemini-3.8-flash", "tier": "standard", "note": "NEW Sep 2026: Google latest Flash, 1M ctx"},
    {"id": "qwen/qwen3-235b-a22b-2507", "tier": "standard", "note": "Qwen 3 MoE 235B, strong reasoning"},
    {"id": "qwen/qwen3-coder-30b-a3b-instruct", "tier": "standard", "note": "Qwen 3 Coder MoE, coding specialist"},
    # --- Premium tier ($10-100/M) ---
    {"id": "openai/gpt-5.2", "tier": "premium", "note": "GPT-5.2: latest OpenAI, 400K ctx"},
    {"id": "anthropic/claude-opus-4.1", "tier": "premium", "note": "Claude Opus 4.1: top safety + reasoning"},
    {"id": "anthropic/claude-fable-5.1", "tier": "premium", "note": "NEW: Claude Fable 5.1, 1M ctx, frontier reasoning"},
    {"id": "openai/o3-pro", "tier": "premium", "note": "O3 Pro: ultimate reasoning, 200K ctx"},
    {"id": "openai/gpt-5.5-pro", "tier": "premium", "note": "GPT-5.5 Pro: cutting-edge, 1M ctx"},
]

COHORT_CONFIG = {
    "quick-test": {"dataset": "benchmarks/quick-test.jsonl", "evaluator": "exact_match", "weight": 0.20},
    "coding-tasks": {"dataset": "benchmarks/coding-tasks.jsonl", "evaluator": "exact_match", "weight": 0.30},
    "reasoning-tasks": {"dataset": "benchmarks/reasoning-tasks.jsonl", "evaluator": "exact_match", "weight": 0.30},
    "safety-tasks": {"dataset": "benchmarks/safety-tasks.jsonl", "evaluator": "regex_match", "harness": "safety", "weight": 0.20},
}

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ModelBenchmark:
    model_id: str
    model_name: str
    provider: str
    tier: str
    cohort: str
    accuracy: float
    avg_latency_ms: float
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: float
    rework_rate: float
    escalation_rate: float
    unsafe_accept_rate: float
    num_cases: int
    num_correct: int
    error_count: int
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id, "model_name": self.model_name,
            "provider": self.provider, "tier": self.tier, "cohort": self.cohort,
            "accuracy": self.accuracy, "avg_latency_ms": self.avg_latency_ms,
            "total_tokens": self.total_tokens, "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "rework_rate": self.rework_rate, "escalation_rate": self.escalation_rate,
            "unsafe_accept_rate": self.unsafe_accept_rate,
            "num_cases": self.num_cases, "num_correct": self.num_correct,
            "error_count": self.error_count, "errors": self.errors,
        }


@dataclass
class PortfolioEntry:
    cohort: str
    best_model_id: str
    best_model_name: str
    provider: str
    tier: str
    accuracy: float
    avg_latency_ms: float
    estimated_cost_usd: float
    composite_score: float
    runner_up_id: str
    runner_up_score: float
    all_scores: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cohort": self.cohort, "best_model_id": self.best_model_id,
            "best_model_name": self.best_model_name, "provider": self.provider,
            "tier": self.tier, "accuracy": self.accuracy,
            "avg_latency_ms": self.avg_latency_ms,
            "estimated_cost_usd": self.estimated_cost_usd,
            "composite_score": self.composite_score,
            "runner_up_id": self.runner_up_id, "runner_up_score": self.runner_up_score,
            "all_scores": self.all_scores,
        }


@dataclass
class PortfolioReport:
    timestamp: str
    local_models_tested: list[str]
    cloud_models_tested: list[str]
    cohorts_benchmarked: list[str]
    local_portfolio: list[PortfolioEntry]
    cloud_portfolio: list[PortfolioEntry]
    hybrid_portfolio: list[PortfolioEntry]
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "local_models_tested": self.local_models_tested,
            "cloud_models_tested": self.cloud_models_tested,
            "cohorts_benchmarked": self.cohorts_benchmarked,
            "local_portfolio": [e.to_dict() for e in self.local_portfolio],
            "cloud_portfolio": [e.to_dict() for e in self.cloud_portfolio],
            "hybrid_portfolio": [e.to_dict() for e in self.hybrid_portfolio],
            "summary": self.summary,
        }


# ---------------------------------------------------------------------------
# Model discovery
# ---------------------------------------------------------------------------

def discover_local_models() -> list[dict[str, Any]]:
    """Discover installed models from Ollama."""
    try:
        req = urllib.request.Request(
            "http://localhost:11434/api/tags",
            headers={"Accept": "application/json"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        models = data.get("models", [])
        result: list[dict[str, Any]] = []
        for m in models:
            name = m.get("name", "")
            result.append({
                "id": name, "name": name,
                "size_bytes": m.get("size", 0),
                "size_gb": round(m.get("size", 0) / (1024 ** 3), 2),
                "provider": "ollama", "tier": "local",
            })
        return result
    except Exception as exc:
        print(f"  WARNING: Cannot discover local models: {exc}")
        return []


# ---------------------------------------------------------------------------
# Benchmark engine
# ---------------------------------------------------------------------------

def _send_chat_request(
    url: str, model: str, messages: list[dict[str, Any]],
    api_key: str = "", timeout: int = 120,
) -> dict[str, Any]:
    """Send a chat completion request to an OpenAI-compatible endpoint."""
    body = json.dumps({"model": model, "messages": messages}).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"_error": str(exc), "choices": [], "usage": {}}


def _load_cohort_cases(cohort_name: str, quick: bool = False) -> list[dict[str, Any]]:
    """Load benchmark cases for a cohort from JSONL file."""
    cfg = COHORT_CONFIG.get(cohort_name)
    if not cfg:
        return []
    dataset_path = _project_root / cfg["dataset"]
    if not dataset_path.exists():
        print(f"  WARNING: Dataset not found: {dataset_path}")
        return []
    cases: list[dict[str, Any]] = []
    with open(dataset_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                cases.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if quick:
        cases = cases[:3]
    return cases


def _evaluate_case(
    case: dict[str, Any], content: str,
    evaluator_name: str, harness_name: str | None = None,
) -> tuple[bool, str]:
    """Evaluate a single case. Returns (is_correct, severity)."""
    from benchmark.evaluators import get_evaluator
    from benchmark.harnesses import get_harness

    expected = case.get("expected_output", "")
    if harness_name:
        finding = get_harness(harness_name).evaluate(case, {"content": content})
    else:
        evaluator = get_evaluator(case.get("evaluator", evaluator_name))
        finding = evaluator.evaluate(content, expected, case)
    is_correct = finding.recommended_action == "none"
    return is_correct, finding.severity


def _estimate_openrouter_cost(
    model_id: str, prompt_tokens: int, completion_tokens: int,
) -> float:
    """Estimate cost for an OpenRouter model (USD per million tokens)."""
    # Pricing in USD per MILLION tokens (prompt, completion)
    # Sourced from OpenRouter API Sep 2026 (per-token * 1e6)
    pricing: dict[str, tuple[float, float]] = {
        # Budget tier
        "meta-llama/llama-3.2-3b-instruct": (0.06, 0.08),
        "google/gemma-3-4b-it": (0.05, 0.15),
        "meta/muse-spark-1.3-contributor": (0.10, 0.20),  # NEW Sep 2026
        "qwen/qwen3.7-flash": (0.10, 0.40),               # NEW
        # Standard tier
        "deepseek/deepseek-chat": (0.27, 1.10),
        "meta-llama/llama-4-maverick": (0.75, 1.50),
        "meta/muse-spark-1.3": (1.25, 4.25),               # NEW Sep 2026
        "google/gemini-3.8-flash": (0.75, 3.75),           # NEW Sep 2026
        "qwen/qwen3-235b-a22b-2507": (0.90, 0.90),
        "qwen/qwen3-coder-30b-a3b-instruct": (0.35, 0.40),
        # Premium tier
        "openai/gpt-5.2": (3.75, 15.00),                   # NEW
        "anthropic/claude-opus-4.1": (15.00, 75.00),
        "anthropic/claude-fable-5.1": (8.00, 40.00),       # NEW Sep 2026
        "openai/o3-pro": (10.00, 40.00),                   # NEW
        "openai/gpt-5.5-pro": (5.00, 25.00),               # NEW
    }
    p_per_m, c_per_m = pricing.get(model_id, (1.0, 5.0))
    cost_prompt = (prompt_tokens / 1_000_000) * p_per_m
    cost_completion = (completion_tokens / 1_000_000) * c_per_m
    return cost_prompt + cost_completion


def benchmark_model_on_cohort(
    model_id: str, model_name: str, provider: str, tier: str,
    cohort_name: str, api_url: str, api_key: str = "", quick: bool = False,
) -> ModelBenchmark:
    """Benchmark a single model on a single cohort."""
    cfg = COHORT_CONFIG.get(cohort_name, {})
    evaluator_name = cfg.get("evaluator", "exact_match")
    harness_name = cfg.get("harness")
    cases = _load_cohort_cases(cohort_name, quick=quick)

    if not cases:
        return ModelBenchmark(
            model_id=model_id, model_name=model_name, provider=provider,
            tier=tier, cohort=cohort_name,
            accuracy=0.0, avg_latency_ms=0.0, total_tokens=0,
            prompt_tokens=0, completion_tokens=0, estimated_cost_usd=0.0,
            rework_rate=0.0, escalation_rate=0.0, unsafe_accept_rate=0.0,
            num_cases=0, num_correct=0, error_count=0,
            errors=["No cases loaded"],
        )

    num_correct = 0
    total_latency_ms = 0.0
    total_prompt_tokens = 0
    total_completion_tokens = 0
    rework_count = 0
    escalation_count = 0
    unsafe_accept_count = 0
    error_count = 0
    errors: list[str] = []
    safety_cohort = (cohort_name == "safety-tasks")

    for case in cases:
        input_data = case.get("input", {})
        messages = input_data.get(
            "messages", [{"role": "user", "content": str(input_data)}]
        )
        start = time.perf_counter()
        resp = _send_chat_request(api_url, model_id, messages, api_key=api_key)
        latency_ms = (time.perf_counter() - start) * 1000

        if "_error" in resp:
            error_count += 1
            errors.append(f"Case {case.get('id', '?')}: {resp['_error'][:120]}")
            continue

        content = ""
        if resp.get("choices"):
            content = resp["choices"][0]["message"].get("content", "") or ""

        usage = resp.get("usage", {})
        p_tokens = usage.get("prompt_tokens", 0)
        c_tokens = usage.get("completion_tokens", 0)
        total_prompt_tokens += p_tokens
        total_completion_tokens += c_tokens
        total_latency_ms += latency_ms

        is_correct, severity = _evaluate_case(
            case, content, evaluator_name, harness_name,
        )
        if is_correct:
            num_correct += 1
        else:
            if severity in ("critical", "high"):
                rework_count += 1
            if severity == "critical":
                escalation_count += 1
            if safety_cohort:
                unsafe_accept_count += 1

    total_valid = len(cases) - error_count
    accuracy = num_correct / total_valid if total_valid > 0 else 0.0
    avg_latency = total_latency_ms / total_valid if total_valid > 0 else 0.0

    estimated_cost = 0.0
    if provider == "openrouter":
        estimated_cost = _estimate_openrouter_cost(
            model_id, total_prompt_tokens, total_completion_tokens,
        )
    else:
        estimated_cost = (total_latency_ms / 1000 / 3600) * 0.10 * 0.12

    rework_rate = rework_count / total_valid if total_valid > 0 else 0.0
    escalation_rate = escalation_count / total_valid if total_valid > 0 else 0.0
    unsafe_accept_rate = unsafe_accept_count / total_valid if total_valid > 0 else 0.0

    return ModelBenchmark(
        model_id=model_id, model_name=model_name, provider=provider,
        tier=tier, cohort=cohort_name,
        accuracy=round(accuracy, 4),
        avg_latency_ms=round(avg_latency, 1),
        total_tokens=total_prompt_tokens + total_completion_tokens,
        prompt_tokens=total_prompt_tokens,
        completion_tokens=total_completion_tokens,
        estimated_cost_usd=round(estimated_cost, 6),
        rework_rate=round(rework_rate, 4),
        escalation_rate=round(escalation_rate, 4),
        unsafe_accept_rate=round(unsafe_accept_rate, 4),
        num_cases=len(cases), num_correct=num_correct,
        error_count=error_count, errors=errors[:5],
    )


# ---------------------------------------------------------------------------
# Portfolio scoring
# ---------------------------------------------------------------------------

def _compute_composite_score(bench: ModelBenchmark, max_latency: float, max_cost: float) -> float:
    """Compute a composite score (0-1) for a model on a cohort."""
    accuracy = bench.accuracy
    # Latency efficiency: 1.0 = fastest, 0.0 = slowest
    latency_eff = max(0.0, 1.0 - (bench.avg_latency_ms / max_latency)) if max_latency > 0 else 1.0
    # Cost efficiency: 1.0 = cheapest, 0.0 = most expensive
    cost_eff = max(0.0, 1.0 - (bench.estimated_cost_usd / max_cost)) if max_cost > 0 else 1.0
    # Safety: 1.0 - unsafe_accept_rate
    safety = max(0.0, 1.0 - bench.unsafe_accept_rate * 10)

    w = SCORING_WEIGHTS
    score = (
        accuracy * w["accuracy"] +
        latency_eff * w["latency_efficiency"] +
        cost_eff * w["cost_efficiency"] +
        safety * w["safety"]
    )
    return round(score, 4)


def _build_portfolio(
    benchmarks: list[ModelBenchmark], cohorts: list[str],
) -> list[PortfolioEntry]:
    """Build a portfolio: best model per cohort."""
    entries: list[PortfolioEntry] = []

    for cohort in cohorts:
        cohort_benches = [b for b in benchmarks if b.cohort == cohort]
        if not cohort_benches:
            continue

        # Compute normalization factors
        max_latency = max(b.avg_latency_ms for b in cohort_benches)
        max_cost = max(b.estimated_cost_usd for b in cohort_benches)

        # Score each model
        scored: list[tuple[float, ModelBenchmark]] = []
        for b in cohort_benches:
            score = _compute_composite_score(b, max_latency, max_cost)
            scored.append((score, b))
        scored.sort(key=lambda x: x[0], reverse=True)

        best_score, best = scored[0]
        runner_up_score, runner_up = scored[1] if len(scored) > 1 else (0.0, best)

        all_scores = [
            {
                "model_id": b.model_id, "model_name": b.model_name,
                "provider": b.provider, "tier": b.tier,
                "composite_score": s,
                "accuracy": b.accuracy,
                "avg_latency_ms": b.avg_latency_ms,
                "estimated_cost_usd": b.estimated_cost_usd,
                "unsafe_accept_rate": b.unsafe_accept_rate,
            }
            for s, b in scored
        ]

        entries.append(PortfolioEntry(
            cohort=cohort,
            best_model_id=best.model_id,
            best_model_name=best.model_name,
            provider=best.provider,
            tier=best.tier,
            accuracy=best.accuracy,
            avg_latency_ms=best.avg_latency_ms,
            estimated_cost_usd=best.estimated_cost_usd,
            composite_score=best_score,
            runner_up_id=runner_up.model_id,
            runner_up_score=runner_up_score,
            all_scores=all_scores,
        ))

    return entries


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_local_benchmarks(quick: bool = False) -> tuple[list[ModelBenchmark], list[str]]:
    """Benchmark all local Ollama models."""
    local_models = discover_local_models()
    if not local_models:
        print("No local models found. Is Ollama running?")
        return [], []

    print(f"\nDiscovered {len(local_models)} local models:")
    for m in local_models:
        print(f"  - {m['name']} ({m['size_gb']:.1f} GB)")

    results: list[ModelBenchmark] = []
    model_ids: list[str] = []

    for model in local_models:
        model_ids.append(model["id"])
        print(f"\n{'='*60}")
        print(f"Benchmarking: {model['name']}")
        print(f"{'='*60}")

        for cohort in ACTIVE_COHORTS:
            print(f"  Cohort: {cohort}...", end=" ", flush=True)
            bench = benchmark_model_on_cohort(
                model_id=model["id"],
                model_name=model["name"],
                provider="ollama",
                tier="local",
                cohort_name=cohort,
                api_url=OLLAMA_CHAT_URL,
                quick=quick,
            )
            results.append(bench)
            status = f"acc={bench.accuracy:.2f}" if bench.num_cases > 0 else "NO DATA"
            print(f"{status} ({bench.num_correct}/{bench.num_cases})")

    return results, model_ids


def run_cloud_benchmarks(quick: bool = False) -> tuple[list[ModelBenchmark], list[str]]:
    """Benchmark OpenRouter cloud candidates."""
    if not OPENROUTER_API_KEY:
        print("OPENROUTER_API_KEY not set. Skipping cloud benchmarks.")
        return [], []

    print(f"\nBenchmarking {len(OPENROUTER_CANDIDATES)} cloud models...")

    results: list[ModelBenchmark] = []
    model_ids: list[str] = []

    for candidate in OPENROUTER_CANDIDATES:
        model_id = candidate["id"]
        model_ids.append(model_id)
        print(f"\n{'='*60}")
        print(f"Benchmarking: {model_id} [{candidate['tier']}]")
        print(f"  {candidate['note']}")
        print(f"{'='*60}")

        for cohort in ACTIVE_COHORTS:
            print(f"  Cohort: {cohort}...", end=" ", flush=True)
            bench = benchmark_model_on_cohort(
                model_id=model_id,
                model_name=model_id,
                provider="openrouter",
                tier=candidate["tier"],
                cohort_name=cohort,
                api_url=OPENROUTER_CHAT_URL,
                api_key=OPENROUTER_API_KEY,
                quick=quick,
            )
            results.append(bench)
            if bench.num_cases > 0:
                print(f"acc={bench.accuracy:.2f} cost=${bench.estimated_cost_usd:.4f} ({bench.num_correct}/{bench.num_cases})")
            else:
                print("NO DATA")

    return results, model_ids


def save_results(
    benchmarks: list[ModelBenchmark], local_ids: list[str], cloud_ids: list[str],
    local_portfolio: list[PortfolioEntry], cloud_portfolio: list[PortfolioEntry],
    hybrid_portfolio: list[PortfolioEntry],
) -> str:
    """Save all results to evidence/portfolio/."""
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).isoformat()
    report = PortfolioReport(
        timestamp=timestamp,
        local_models_tested=local_ids,
        cloud_models_tested=cloud_ids,
        cohorts_benchmarked=ACTIVE_COHORTS,
        local_portfolio=local_portfolio,
        cloud_portfolio=cloud_portfolio,
        hybrid_portfolio=hybrid_portfolio,
        summary={
            "total_benchmarks": len(benchmarks),
            "local_models": len(local_ids),
            "cloud_models": len(cloud_ids),
            "cohorts": len(ACTIVE_COHORTS),
        },
    )

    # Save full report
    report_file = EVIDENCE_DIR / "portfolio_report.json"
    report_file.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Save raw benchmarks
    bench_file = EVIDENCE_DIR / "all_benchmarks.json"
    bench_file.write_text(
        json.dumps([b.to_dict() for b in benchmarks], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"\nResults saved to: {EVIDENCE_DIR}")
    print(f"  - {report_file.name}")
    print(f"  - {bench_file.name}")

    return str(EVIDENCE_DIR)


def print_portfolio(title: str, entries: list[PortfolioEntry]) -> None:
    """Pretty-print a portfolio recommendation."""
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")

    if not entries:
        print("  No recommendations available.")
        return

    for entry in entries:
        print(f"\n  [{entry.cohort}]")
        print(f"    Best:  {entry.best_model_name} ({entry.provider}/{entry.tier})")
        print(f"    Score: {entry.composite_score:.4f}  |  Accuracy: {entry.accuracy:.2%}")
        print(f"    Latency: {entry.avg_latency_ms:.0f}ms  |  Cost: ${entry.estimated_cost_usd:.6f}")
        if entry.runner_up_id != entry.best_model_id:
            print(f"    Runner-up: {entry.runner_up_id} (score: {entry.runner_up_score:.4f})")

    # Summary line
    avg_acc = sum(e.accuracy for e in entries) / len(entries) if entries else 0
    avg_score = sum(e.composite_score for e in entries) / len(entries) if entries else 0
    print(f"\n  Portfolio avg accuracy: {avg_acc:.2%}  |  avg score: {avg_score:.4f}")


def generate_report_from_disk() -> None:
    """Generate a portfolio report from existing benchmark files."""
    bench_file = EVIDENCE_DIR / "all_benchmarks.json"
    if not bench_file.exists():
        print(f"No benchmark data found at {bench_file}")
        print("Run benchmarks first with --local, --cloud, or --all")
        return

    data = json.loads(bench_file.read_text("utf-8"))
    benchmarks = [
        ModelBenchmark(
            model_id=d["model_id"], model_name=d["model_name"],
            provider=d["provider"], tier=d["tier"], cohort=d["cohort"],
            accuracy=d["accuracy"], avg_latency_ms=d["avg_latency_ms"],
            total_tokens=d["total_tokens"], prompt_tokens=d["prompt_tokens"],
            completion_tokens=d["completion_tokens"],
            estimated_cost_usd=d["estimated_cost_usd"],
            rework_rate=d["rework_rate"], escalation_rate=d["escalation_rate"],
            unsafe_accept_rate=d["unsafe_accept_rate"],
            num_cases=d["num_cases"], num_correct=d["num_correct"],
            error_count=d["error_count"], errors=d.get("errors", []),
        )
        for d in data
    ]

    local_benches = [b for b in benchmarks if b.provider == "ollama"]
    cloud_benches = [b for b in benchmarks if b.provider == "openrouter"]

    local_ids = sorted(set(b.model_id for b in local_benches))
    cloud_ids = sorted(set(b.model_id for b in cloud_benches))

    local_portfolio = _build_portfolio(local_benches, ACTIVE_COHORTS)
    cloud_portfolio = _build_portfolio(cloud_benches, ACTIVE_COHORTS)
    hybrid_portfolio = _build_portfolio(benchmarks, ACTIVE_COHORTS)

    print_portfolio("LOCAL PORTFOLIO (best model per cohort on this machine)", local_portfolio)
    print_portfolio("CLOUD PORTFOLIO (best OpenRouter model per cohort)", cloud_portfolio)
    print_portfolio("HYBRID PORTFOLIO (best across local + cloud)", hybrid_portfolio)

    save_results(benchmarks, local_ids, cloud_ids, local_portfolio, cloud_portfolio, hybrid_portfolio)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Multi-model portfolio optimizer for NoeRelay",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/multi_model_portfolio.py --local --quick   # Fast local test
  python scripts/multi_model_portfolio.py --cloud           # Cloud models only
  python scripts/multi_model_portfolio.py --all             # Full benchmark
  python scripts/multi_model_portfolio.py --report          # Report from saved data
""",
    )
    parser.add_argument("--local", action="store_true", help="Benchmark local models")
    parser.add_argument("--cloud", action="store_true", help="Benchmark cloud models")
    parser.add_argument("--all", action="store_true", help="Benchmark both local and cloud")
    parser.add_argument("--quick", action="store_true", help="Quick mode: 3 cases per cohort")
    parser.add_argument("--report", action="store_true", help="Generate report from existing results")
    args = parser.parse_args()

    if args.report:
        generate_report_from_disk()
        return 0

    if not (args.local or args.cloud or args.all):
        parser.print_help()
        print("\nSpecify --local, --cloud, --all, or --report")
        return 1

    run_local = args.local or args.all
    run_cloud = args.cloud or args.all

    all_benchmarks: list[ModelBenchmark] = []
    local_ids: list[str] = []
    cloud_ids: list[str] = []

    if run_local:
        benches, ids = run_local_benchmarks(quick=args.quick)
        all_benchmarks.extend(benches)
        local_ids = ids

    if run_cloud:
        benches, ids = run_cloud_benchmarks(quick=args.quick)
        all_benchmarks.extend(benches)
        cloud_ids = ids

    if not all_benchmarks:
        print("No benchmark results collected.")
        return 1

    local_benches = [b for b in all_benchmarks if b.provider == "ollama"]
    cloud_benches = [b for b in all_benchmarks if b.provider == "openrouter"]

    local_portfolio = _build_portfolio(local_benches, ACTIVE_COHORTS)
    cloud_portfolio = _build_portfolio(cloud_benches, ACTIVE_COHORTS)
    hybrid_portfolio = _build_portfolio(all_benchmarks, ACTIVE_COHORTS)

    if local_portfolio:
        print_portfolio("LOCAL PORTFOLIO (best model per cohort on this machine)", local_portfolio)
    if cloud_portfolio:
        print_portfolio("CLOUD PORTFOLIO (best OpenRouter model per cohort)", cloud_portfolio)
    if hybrid_portfolio:
        print_portfolio("HYBRID PORTFOLIO (best across local + cloud)", hybrid_portfolio)

    save_results(all_benchmarks, local_ids, cloud_ids,
                 local_portfolio, cloud_portfolio, hybrid_portfolio)

    return 0


if __name__ == "__main__":
    sys.exit(main())