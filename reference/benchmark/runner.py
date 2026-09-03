"""HTTP benchmark runner for the NoeRelay gateway.

Produces :class:`EvaluatorResult` objects conforming to the spec-kit-evaluator
contract, with evidence-classified findings from evaluators and harnesses.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from .advanced_metrics import compute_all_metrics
from .datasets import DatasetLoader
from .evaluator_contract import (
    EvaluatorInfo,
    EvaluatorMetadata,
    EvaluatorResult,
    Finding,
    make_finding_id,
    make_result,
    make_timestamp,
)
from .evaluators import get_evaluator
from .harnesses import get_harness
from .metrics import CaseResult, compute_results, format_report


class BenchmarkRunner:
    """Runs benchmarks against the NoeRelay gateway.

    Produces :class:`EvaluatorResult` objects with evidence-classified
    findings from evaluators and harnesses.
    """

    def __init__(
        self,
        gateway_url: str,
        model: str = "noerelay/epr-1",
        prefer_local: bool = False,
        api_key: str | None = None,
    ) -> None:
        self.gateway_url = gateway_url.rstrip("/")
        self.model = model
        self.prefer_local = prefer_local
        # Allow an explicit API key, or fall back to the environment.
        self.api_key = api_key or os.environ.get("NOERELAY_API_KEY", "")

    def run_cohort(
        self,
        cohort_name: str,
        dataset: DatasetLoader,
        evaluator_name: str = "exact_match",
        harness_name: str | None = None,
    ) -> tuple[list[CaseResult], list[Finding]]:
        """Run a benchmark cohort and return case results with evaluator findings.

        Returns:
            A tuple of (case_results, findings) where findings are
            evidence-classified per the evaluator contract.
        """
        cases = dataset.load()
        case_results: list[CaseResult] = []
        findings: list[Finding] = []
        for case in cases:
            result, finding = self._run_single_case(
                case, evaluator_name, harness_name
            )
            case_results.append(result)
            findings.append(finding)
        return case_results, findings

    def _run_single_case(
        self,
        case: dict[str, Any],
        evaluator_name: str,
        harness_name: str | None = None,
    ) -> tuple[CaseResult, Finding]:
        """Send a single case through the gateway and evaluate it.

        Returns both a CaseResult (for backward-compatible metrics) and a
        Finding (for the evaluator contract).
        """
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
        if "tools" in input_data:
            request["tools"] = input_data["tools"]
        if self.prefer_local:
            request["passthrough"] = {
                **request.get("passthrough", {}),
                "prefer_local": True,
            }

        start = time.perf_counter()
        response = self._send_request(request)
        latency_ms = (time.perf_counter() - start) * 1000

        content = ""
        if response.get("choices"):
            content = response["choices"][0]["message"].get("content", "") or ""
        usage = response.get("usage", {})
        epr = response.get("epr", {})

        expected = case.get("expected_output", "")

        # Produce evaluator-contract Finding
        if harness_name:
            finding = get_harness(harness_name).evaluate(
                case, {"content": content}
            )
        else:
            evaluator = get_evaluator(case.get("evaluator", evaluator_name))
            finding = evaluator.evaluate(content, expected, case)

        # Derive is_correct from finding for backward compatibility
        is_correct = finding.recommended_action == "none"

        metadata = dict(case.get("metadata", {}))
        metadata["required_human_intervention"] = epr.get(
            "required_human_intervention", False
        )
        metadata["required_rework"] = epr.get("required_rework", False)
        metadata["was_escalated"] = epr.get("was_escalated", False)
        metadata["finding_id"] = finding.id
        metadata["finding_severity"] = finding.severity

        case_result = CaseResult(
            case_id=case.get("id", ""),
            is_correct=is_correct,
            total_tokens=epr.get("total_tokens", usage.get("total_tokens", 0)),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            actual_cost_usd=epr.get(
                "actual_cost_usd", epr.get("total_cost_usd", 0.0)
            ),
            latency_ms=latency_ms,
            model=response.get("model", ""),
            response=content,
            expected=expected,
            metadata=metadata,
        )
        return case_result, finding

    def _send_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Send a chat completion request to the gateway.

        Transport/HTTP errors are converted into an empty OpenAI-shaped
        response so a single failed case does not abort the whole cohort.
        """
        url = "%s/v1/chat/completions" % self.gateway_url
        body = json.dumps(request).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = "Bearer %s" % self.api_key
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers=headers,
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
        """Run a cohort and return a formatted JSON report with metrics.

        Returns a dict with both the legacy metrics report and the
        evaluator-contract result.
        """
        case_results, findings = self.run_cohort(
            cohort_name, dataset, evaluator_name, harness_name
        )
        results = compute_results(cohort_name, case_results)
        report = format_report(results)
        report.update(compute_all_metrics(case_results))

        # Build evaluator-contract result
        eval_result = make_result(
            evaluator_id="noerelay-benchmark",
            evaluator_version="1.0.0",
            phase="after_implement",
            findings=findings,
            evaluator_name="NoeRelay Benchmark Runner",
            deterministic=True,
            duration_ms=int(
                sum(r.latency_ms for r in case_results)
            ),
        )
        report["evaluator_result"] = eval_result.to_dict()

        return report

    def run_and_report_contract(
        self,
        cohort_name: str,
        dataset: DatasetLoader,
        evaluator_name: str = "exact_match",
        harness_name: str | None = None,
    ) -> EvaluatorResult:
        """Run a cohort and return an :class:`EvaluatorResult` directly.

        This is the primary method for the evaluator-contract pipeline.
        """
        case_results, findings = self.run_cohort(
            cohort_name, dataset, evaluator_name, harness_name
        )
        return make_result(
            evaluator_id="noerelay-benchmark",
            evaluator_version="1.0.0",
            phase="after_implement",
            findings=findings,
            evaluator_name="NoeRelay Benchmark Runner",
            deterministic=True,
            duration_ms=int(
                sum(r.latency_ms for r in case_results)
            ),
        )

    def build_promotion_payload(
        self, cohort_name: str, report: dict[str, Any]
    ) -> dict[str, Any]:
        """Build a signed promotion payload compatible with ``promote_canary``."""
        return {
            "signed": True,
            "cohorts": {
                cohort_name: {
                    "accepted_outcome_rate": report.get("accuracy", 0.0),
                    "unsafe_accept_rate": report.get(
                        "unsafe_accept_rate", 0.0
                    ),
                    "calibration_ece": report.get("calibration_ece", 0.0),
                    "brier_score": report.get("brier_score", 0.0),
                    "selective_risk": report.get("selective_risk", 0.0),
                    "tool_call_success_rate": report.get(
                        "tool_call_success_rate", 1.0
                    ),
                    "replay_success_rate": report.get(
                        "replay_success_rate", 1.0
                    ),
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