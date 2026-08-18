#!/usr/bin/env python
"""Run a benchmark against the NoeRelay gateway.

Usage:
    python scripts/run_benchmark.py --dataset benchmarks/quick-test.jsonl --cohort "test-cohort"
    python scripts/run_benchmark.py --inline --cohort "quick-test"
    python scripts/run_benchmark.py --hf-dataset "SWE-bench/SWE-bench_Verified" --cohort "governed-software-v-model" --harness swe_bench
    python scripts/run_benchmark.py --inline --cohort "stub" --promote --canary-version 1.1.0
"""

import argparse
import json
import os
import sys

# Add reference/ to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "reference"))

from benchmark.datasets import InlineDatasetLoader, JsonlDatasetLoader
from benchmark.hf_datasets import HuggingFaceDatasetLoader
from benchmark.runner import BenchmarkRunner
from gateway.online_learning import PolicyVersionManager


def main() -> int:
    parser = argparse.ArgumentParser(description="Run NoeRelay benchmark")
    parser.add_argument("--gateway-url", default="http://127.0.0.1:8080")
    parser.add_argument("--dataset", help="Path to JSONL dataset file")
    parser.add_argument("--hf-dataset", help="HuggingFace dataset id")
    parser.add_argument("--inline", action="store_true", help="Use inline test cases")
    parser.add_argument("--cohort", default="default", help="Cohort name")
    parser.add_argument("--evaluator", default="exact_match", help="Evaluator name")
    parser.add_argument("--harness", default=None, help="Official harness name")
    parser.add_argument("--model", default="noerelay/epr-1", help="Model ID")
    parser.add_argument(
        "--prefer-local",
        action="store_true",
        help="Prefer local models over cloud models",
    )
    parser.add_argument("--hf-token", default=None, help="HuggingFace token")
    parser.add_argument("--split", default="test", help="HuggingFace dataset split")
    parser.add_argument(
        "--revision", default=None, help="HuggingFace dataset revision"
    )
    parser.add_argument(
        "--promote", action="store_true", help="Attempt canary promotion after benchmark"
    )
    parser.add_argument(
        "--canary-version", default=None, help="Canary version to promote"
    )
    args = parser.parse_args()

    if args.inline:
        # Built-in quick test cases
        cases = [
            {
                "id": "q1",
                "input": {
                    "messages": [
                        {
                            "role": "user",
                            "content": "What is 2+2? Answer with just the number.",
                        }
                    ]
                },
                "expected_output": "4",
                "evaluator": "contains",
            },
            {
                "id": "q2",
                "input": {
                    "messages": [
                        {
                            "role": "user",
                            "content": "What is the capital of France? Answer with just the city name.",
                        }
                    ]
                },
                "expected_output": "Paris",
                "evaluator": "contains",
            },
            {
                "id": "q3",
                "input": {
                    "messages": [
                        {
                            "role": "user",
                            "content": "What is 10 minus 3? Answer with just the number.",
                        }
                    ]
                },
                "expected_output": "7",
                "evaluator": "contains",
            },
        ]
        dataset = InlineDatasetLoader(cases)
    elif args.hf_dataset:
        dataset = HuggingFaceDatasetLoader(
            args.hf_dataset,
            revision=args.revision,
            split=args.split,
            hf_token=args.hf_token,
        )
    elif args.dataset:
        dataset = JsonlDatasetLoader(args.dataset)
    else:
        parser.error("Specify --dataset, --inline, or --hf-dataset")

    runner = BenchmarkRunner(args.gateway_url, args.model, prefer_local=args.prefer_local)
    report = runner.run_and_report(args.cohort, dataset, args.evaluator, args.harness)
    print(json.dumps(report, indent=2))

    if args.promote:
        if not args.canary_version:
            parser.error("--promote requires --canary-version")
        manager = PolicyVersionManager()
        manager.register_canary(args.canary_version)
        payload = runner.build_promotion_payload(args.cohort, report)
        ok, reason = manager.promote_canary(args.canary_version, payload)
        print(json.dumps({"promotion": {"ok": ok, "reason": reason}}, indent=2))
        if not ok:
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
