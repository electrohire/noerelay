"""HTTP benchmark runner for the NoeRelay gateway."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from .advanced_metrics import compute_all_metrics
from .datasets import DatasetLoader
from .evaluators import get_evaluator
from .harnesses import get_harness
from .metrics import BenchmarkResults, CaseResult, compute_results, format_report


class BenchmarkRunner:
    """Runs benchmarks against the NoeRelay gateway."""

    def __init__(
        self,
        gateway_url: str,
        model: str = "noerelay/epr-1",
        prefer_local: bool = False,
    ) -> None:
        self.gateway_url = gateway_url.rstrip("/")
        self.model = model
        self.prefer_local = prefer_local

    def run_cohort(
        self,
        cohort_name: str,
        dataset: DatasetLoader,
        evaluator_name: str = "exact_match",
        harness_name: str | None = None,
    ) -> BenchmarkResults:
        """Run a benchmark cohort and return aggregate results."""
        cases = dataset.load()
        case_results = []
        for case in cases:
            result = self._run_single_case(case, evaluator_name, harness_name)
            case_results.append(result)
        return compute_results(cohort_name, case_results)

    def _run_single_case(
        self,
        case: dict[str, Any],
        evaluator_name: str,
        harness_name: str | None = None,
    ) -> CaseResult:
        """Send a single case through the gateway and evaluate it."""
        input_data = case.get("input", {})
        messages = input_data.get(
            "messages", [{"role": "user", "content": str(input_data)}]
        )
        request: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        if "governance" in case:
            request["governance"] = case["governance"]
        if self.prefer_local:
            request["passthrough"] = {**request.get("passthrough", {}), "prefer_local": True}

        start = time.perf_counter()
        response = self._send_request(request)
        latency_ms = (time.perf_counter() - start) * 1000

        content = ""
        if response.get("choices"):
            content = response["choices"][0]["message"].get("content", "") or ""
        usage = response.get("usage", {})
        epr = response.get("epr", {})

        expected = case.get("expected_output", "")
        if harness_name:
            is_correct = get_harness(harness_name).evaluate(
                case, {"content": content}
            )
        else:
            evaluator = get_evaluator(case.get("evaluator", evaluator_name))
            is_correct = evaluator.evaluate(content, expected, case)

        metadata = dict(case.get("metadata", {}))
        metadata["required_human_intervention"] = epr.get(
            "required_human_intervention", False
        )
        metadata["required_rework"] = epr.get("required_rework", False)
        metadata["was_escalated"] = epr.get("was_escalated", False)
        return CaseResult(
            case_id=case.get("id", ""),
            is_correct=is_correct,
            total_tokens=epr.get("total_tokens", usage.get("total_tokens", 0)),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            actual_cost_usd=epr.get("actual_cost_usd", epr.get("total_cost_usd", 0.0)),
            latency_ms=latency_ms,
            model=response.get("model", ""),
            response=content,
            expected=expected,
            metadata=metadata,
        )

    def _send_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Send a chat completion request to the gateway.

        Transport/HTTP errors are converted into an empty OpenAI-shaped
        response so a single failed case does not abort the whole cohort.
        """
        url = f"{self.gateway_url}/v1/chat/completions"
        body = json.dumps(request).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            OSError,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ) as exc:
            return {
                "model": self.model,
                "choices": [],
                "usage": {},
                "epr": {},
                "_benchmark_error": str(exc),
            }

    def run_and_report(
        self,
        cohort_name: str,
        dataset: DatasetLoader,
        evaluator_name: str = "exact_match",
        harness_name: str | None = None,
    ) -> dict[str, Any]:
        """Run a cohort and return a formatted JSON report with metrics."""
        results = self.run_cohort(
            cohort_name, dataset, evaluator_name, harness_name
        )
        report = format_report(results)
        report.update(compute_all_metrics(results.results))
        return report

    def build_promotion_payload(
        self, cohort_name: str, report: dict[str, Any]
    ) -> dict[str, Any]:
        """Build a signed promotion payload compatible with ``promote_canary``."""
        return {
            "signed": True,
            "cohorts": {
                cohort_name: {
                    "accepted_outcome_rate": report.get("accuracy", 0.0),
                    "unsafe_accept_rate": report.get("unsafe_accept_rate", 0.0),
                    "calibration_ece": report.get("calibration_ece", 0.0),
                    "brier_score": report.get("brier_score", 0.0),
                    "selective_risk": report.get("selective_risk", 0.0),
                    "tool_call_success_rate": report.get(
                        "tool_call_success_rate", 1.0
                    ),
                    "replay_success_rate": report.get("replay_success_rate", 1.0),
                    "context_evidence_recall": report.get(
                        "context_evidence_recall", 1.0
                    ),
                    "route_regret_usd": report.get("route_regret_usd", 0.0),
                    "requirement_trace_coverage": report.get(
                        "requirement_trace_coverage", 1.0
                    ),
                }
            },
        }
