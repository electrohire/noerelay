"""Model lifecycle management CLI.

Usage:
    python scripts/model_lifecycle.py discover --task text-generation --max-size-gb 20
    python scripts/model_lifecycle.py recommend-downloads
    python scripts/model_lifecycle.py recommend-removals
    python scripts/model_lifecycle.py list-local
    python scripts/model_lifecycle.py disk-usage
    python scripts/model_lifecycle.py pull qwen3:8b
    python scripts/model_lifecycle.py remove qwen3:8b
    python scripts/model_lifecycle.py cloud-models
    python scripts/model_lifecycle.py recommend-cloud
    python scripts/model_lifecycle.py rank
    python scripts/model_lifecycle.py cost-explain --model-a qwen3:8b --model-b llama3.2:3b
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "reference"))

from gateway.cost_model import TrueCostModel
from gateway.model_lifecycle import (
    HuggingFaceModelDiscovery,
    ModelPerformanceAnalyzer,
    OllamaModelManager,
    OpenRouterModelDiscovery,
)


def _load_env() -> dict[str, str]:
    """Load .env file into a dict."""
    env: dict[str, str] = {}
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return env
    with env_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def _get_hf_token() -> str | None:
    """Get HF token from env or .env."""
    return os.environ.get("HF_TOKEN") or _load_env().get("HF_TOKEN")


def _get_openrouter_api_key() -> str | None:
    """Get OpenRouter API key from env or .env."""
    return os.environ.get("OPENROUTER_API_KEY") or _load_env().get("OPENROUTER_API_KEY")


def _print_json(obj: object) -> None:
    """Pretty-print a JSON-serializable object."""
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def cmd_discover(args: argparse.Namespace) -> int:
    """Discover models on HuggingFace."""
    hf = HuggingFaceModelDiscovery(hf_token=_get_hf_token())
    results = hf.search_models(
        task=args.task, max_size_gb=args.max_size_gb, limit=args.limit
    )
    _print_json(results)
    print(f"\nFound {len(results)} models matching criteria.")
    return 0


def cmd_recommend_downloads(args: argparse.Namespace) -> int:
    """Recommend models to download from HuggingFace."""
    hf = HuggingFaceModelDiscovery(hf_token=_get_hf_token())
    ollama = OllamaModelManager()

    local_models = [m["name"] for m in ollama.list_models()]

    print("=== Currently installed models ===")
    for lm in local_models:
        print(f"  - {lm}")

    analyzer = ModelPerformanceAnalyzer()
    recommendations = analyzer.recommend_downloads(
        available_local=local_models,
        hf_discovery=hf,
        task=args.task,
        max_size_gb=args.max_size_gb,
    )

    print("\n=== Recommended downloads ===")
    if not recommendations:
        print("  No new models recommended. You're up to date!")
        return 0

    _print_json(recommendations)
    print(f"\n{len(recommendations)} models recommended for download.")
    return 0


def cmd_recommend_removals(args: argparse.Namespace) -> int:
    """Recommend models to remove based on performance."""
    ollama = OllamaModelManager()
    models = ollama.list_models()
    model_ids = [m["name"] for m in models]

    print("=== Currently installed models ===")
    for m in models:
        size_gb = m.get("size", 0) / (1024**3)
        print(f"  - {m['name']} ({size_gb:.2f} GB)")

    analyzer = ModelPerformanceAnalyzer()
    # Note: In a real scenario, benchmark data would be loaded from history.
    # For now, we analyze with whatever data is available.
    removals = analyzer.recommend_removals(model_ids)

    print("\n=== Recommended removals ===")
    if not removals:
        print("  No models recommended for removal. All models are performing well!")
        return 0

    _print_json(removals)
    print(f"\n{len(removals)} models recommended for removal.")
    return 0


def cmd_list_local(args: argparse.Namespace) -> int:
    """List installed local models."""
    ollama = OllamaModelManager()
    models = ollama.list_models()

    print("=== Installed local models ===")
    if not models:
        print("  No models installed.")
        return 0

    for m in models:
        size_gb = m.get("size", 0) / (1024**3)
        print(f"  - {m['name']}")
        print(f"      Size: {size_gb:.2f} GB")
        if m.get("parameter_size"):
            print(f"      Parameters: {m['parameter_size']}")
        if m.get("quantization_level"):
            print(f"      Quantization: {m['quantization_level']}")
        if m.get("family"):
            print(f"      Family: {m['family']}")
        print()
    return 0


def cmd_disk_usage(args: argparse.Namespace) -> int:
    """Show disk usage of local models."""
    ollama = OllamaModelManager()
    usage = ollama.get_disk_usage()

    print("=== Disk usage ===")
    print(f"Total models: {usage['model_count']}")
    print(f"Total size:   {usage['total_size_gb']:.2f} GB")
    print()
    for m in usage.get("models", []):
        print(f"  {m['name']}: {m['size_gb']:.2f} GB")
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    """Download a model."""
    ollama = OllamaModelManager()
    print(f"Pulling model: {args.model_name}...")
    result = ollama.pull_model(args.model_name)
    _print_json(result)
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    """Remove a model."""
    ollama = OllamaModelManager()
    print(f"Removing model: {args.model_name}...")
    result = ollama.delete_model(args.model_name)
    _print_json(result)
    return 0


def cmd_cloud_models(args: argparse.Namespace) -> int:
    """List available OpenRouter models."""
    api_key = _get_openrouter_api_key()
    if not api_key:
        print("Error: OPENROUTER_API_KEY not set in .env or environment.", file=sys.stderr)
        return 1

    or_discovery = OpenRouterModelDiscovery(api_key=api_key)
    try:
        models = or_discovery.list_models()
    except RuntimeError as exc:
        print(f"Error fetching OpenRouter models: {exc}", file=sys.stderr)
        return 1

    print(f"=== OpenRouter models ({len(models)} total) ===")
    for m in models[:20]:  # Show first 20
        pricing = m.get("pricing", {})
        prompt_cost = float(pricing.get("prompt", 0)) * 1000
        completion_cost = float(pricing.get("completion", 0)) * 1000
        print(f"  - {m['id']}")
        print(f"      Context: {m.get('context_length', 0)} tokens")
        print(f"      Cost: ${prompt_cost:.6f}/1K prompt, ${completion_cost:.6f}/1K completion")
        print()

    if len(models) > 20:
        print(f"  ... and {len(models) - 20} more models.")
    return 0


def cmd_recommend_cloud(args: argparse.Namespace) -> int:
    """Recommend cloud model changes."""
    api_key = _get_openrouter_api_key()
    if not api_key:
        print("Error: OPENROUTER_API_KEY not set in .env or environment.", file=sys.stderr)
        return 1

    or_discovery = OpenRouterModelDiscovery(api_key=api_key)
    try:
        recommendations = or_discovery.recommend_cloud_models(
            current_portfolio=[],
            task=args.task,
            max_cost_per_1k=args.max_cost_per_1k,
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print("=== Cloud model recommendations ===")
    if not recommendations:
        print("  No recommendations available.")
        return 0

    _print_json(recommendations)
    return 0


def cmd_rank(args: argparse.Namespace) -> int:
    """Rank models by true total cost of ownership."""
    cost_params = _parse_cost_parameters(args)
    cost_model = TrueCostModel(**cost_params)
    analyzer = ModelPerformanceAnalyzer(cost_model=cost_model)
    ranked = analyzer.rank_models()

    print("=== Model ranking (by True Cost of Ownership) ===")
    if not ranked:
        print("  No benchmark data available. Run benchmarks first.")
        print("  Use: python scripts/run_benchmark.py")
        return 0

    for i, r in enumerate(ranked):
        print(f"  {i + 1}. {r['model_id']}")
        print(f"      True Cost/Correct: ${r.get('true_cost_per_correct', 'N/A')}")
        print(f"      Legacy Score: {r['score']:.4f}")
        print(f"      Accuracy: {r['mean_accuracy']:.2%}")
        cost_breakdown = r.get("cost_breakdown", {})
        if cost_breakdown:
            print(f"      Cost Breakdown:")
            print(f"        Direct:       ${cost_breakdown.get('direct', 0):.8f}")
            print(f"        Rework:       ${cost_breakdown.get('rework', 0):.8f}")
            print(f"        Human:        ${cost_breakdown.get('human', 0):.8f}")
            print(f"        Escalation:   ${cost_breakdown.get('escalation', 0):.8f}")
            print(f"        Latency:      ${cost_breakdown.get('latency', 0):.8f}")
            print(f"        Infrastructure: ${cost_breakdown.get('infrastructure', 0):.8f}")
            print(f"        Total/Case:   ${cost_breakdown.get('total_per_case', 0):.8f}")
        print(f"      Runs: {r['runs']}")
        print(f"      Tokens/Correct: {r['mean_tokens_per_correct']:.1f}")
        print(f"      Latency: {r['mean_latency_ms']:.1f}ms")
        print()
    return 0


def cmd_cost_explain(args: argparse.Namespace) -> int:
    """Explain the cost difference between two models."""
    cost_params = _parse_cost_parameters(args)
    cost_model = TrueCostModel(**cost_params)
    analyzer = ModelPerformanceAnalyzer(cost_model=cost_model)

    model_a = args.model_a
    model_b = args.model_b

    stats_a = analyzer.get_model_stats(model_a)
    stats_b = analyzer.get_model_stats(model_b)

    if stats_a.get("runs", 0) == 0:
        print(f"Error: No benchmark data for model '{model_a}'.", file=sys.stderr)
        return 1
    if stats_b.get("runs", 0) == 0:
        print(f"Error: No benchmark data for model '{model_b}'.", file=sys.stderr)
        return 1

    explanation = cost_model.explain_cost_difference(stats_a, stats_b)

    print(f"=== Cost Comparison: {model_a} vs {model_b} ===")
    print(f"  Per-token cheaper: {explanation['per_token_cheaper']}")
    print(f"  True cost cheaper: {explanation['true_cost_cheaper']}")
    print()
    print("  Cost Differences (B - A, positive means B is more expensive):")
    diff = explanation["cost_difference"]
    print(f"    Direct:        ${diff['direct']:+.8f}")
    print(f"    Rework:        ${diff['rework']:+.8f}")
    print(f"    Human:         ${diff['human']:+.8f}")
    print(f"    Escalation:    ${diff['escalation']:+.8f}")
    print(f"    Latency:       ${diff['latency']:+.8f}")
    print(f"    Infrastructure: ${diff['infrastructure']:+.8f}")
    print()
    print(f"  Explanation: {explanation['explanation']}")
    return 0


def _parse_cost_parameters(args: argparse.Namespace) -> dict[str, float]:
    """Parse cost parameter overrides from CLI args."""
    params: dict[str, float] = {}
    if hasattr(args, "cost_parameters") and args.cost_parameters:
        for param in args.cost_parameters:
            if "=" not in param:
                print(f"Warning: Invalid cost parameter '{param}'. Use key=value format.", file=sys.stderr)
                continue
            key, _, value = param.partition("=")
            try:
                params[key.strip()] = float(value.strip())
            except ValueError:
                print(f"Warning: Invalid float value '{value}' for parameter '{key}'.", file=sys.stderr)
    return params


def main() -> int:
    parser = argparse.ArgumentParser(
        description="NoeRelay Model Lifecycle Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command")

    # discover
    p_discover = subparsers.add_parser("discover", help="Discover models on HuggingFace")
    p_discover.add_argument("--task", default="text-generation")
    p_discover.add_argument("--max-size-gb", type=float, default=20.0)
    p_discover.add_argument("--limit", type=int, default=10)

    # recommend-downloads
    p_rec_dl = subparsers.add_parser("recommend-downloads", help="Recommend models to download")
    p_rec_dl.add_argument("--task", default="text-generation")
    p_rec_dl.add_argument("--max-size-gb", type=float, default=20.0)

    # recommend-removals
    subparsers.add_parser("recommend-removals", help="Recommend models to remove")

    # list-local
    subparsers.add_parser("list-local", help="List installed local models")

    # disk-usage
    subparsers.add_parser("disk-usage", help="Show disk usage of local models")

    # pull
    p_pull = subparsers.add_parser("pull", help="Download a model")
    p_pull.add_argument("model_name")

    # remove
    p_remove = subparsers.add_parser("remove", help="Remove a model")
    p_remove.add_argument("model_name")

    # cloud-models
    subparsers.add_parser("cloud-models", help="List available OpenRouter models")

    # recommend-cloud
    p_rec_cloud = subparsers.add_parser("recommend-cloud", help="Recommend cloud model changes")
    p_rec_cloud.add_argument("--task", default="text-generation")
    p_rec_cloud.add_argument("--max-cost-per-1k", type=float, default=0.01)

    # rank
    p_rank = subparsers.add_parser("rank", help="Rank models by true total cost of ownership")
    p_rank.add_argument(
        "--cost-parameters",
        nargs="*",
        default=[],
        metavar="KEY=VALUE",
        help="Override default cost parameters (e.g., --cost-parameters human_hourly_rate_usd=100)",
    )

    # cost-explain
    p_cost_explain = subparsers.add_parser(
        "cost-explain", help="Explain cost difference between two models"
    )
    p_cost_explain.add_argument("--model-a", required=True, help="First model ID")
    p_cost_explain.add_argument("--model-b", required=True, help="Second model ID")
    p_cost_explain.add_argument(
        "--cost-parameters",
        nargs="*",
        default=[],
        metavar="KEY=VALUE",
        help="Override default cost parameters (e.g., --cost-parameters human_hourly_rate_usd=100)",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    commands = {
        "discover": cmd_discover,
        "recommend-downloads": cmd_recommend_downloads,
        "recommend-removals": cmd_recommend_removals,
        "list-local": cmd_list_local,
        "disk-usage": cmd_disk_usage,
        "pull": cmd_pull,
        "remove": cmd_remove,
        "cloud-models": cmd_cloud_models,
        "recommend-cloud": cmd_recommend_cloud,
        "rank": cmd_rank,
        "cost-explain": cmd_cost_explain,
    }

    handler = commands.get(args.command)
    if handler:
        return handler(args)

    print(f"Unknown command: {args.command}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())