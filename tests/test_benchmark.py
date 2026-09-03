from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reference"))

from benchmark.datasets import DatasetLoader, InlineDatasetLoader, JsonlDatasetLoader
from benchmark.evaluators import (
    AcceptanceCriteriaEvaluator,
    CompositeEvaluator,
    ContainsEvaluator,
    ExactMatchEvaluator,
    RegexEvaluator,
    get_evaluator,
)
from benchmark.metrics import (
    BenchmarkResults,
    CaseResult,
    compute_results,
    format_report,
)
from benchmark.runner import BenchmarkRunner

from gateway.config import GatewayConfig
from gateway.openrouter import StubOpenRouterClient
from gateway.pipeline import PipelineContext
from gateway.policy import load_policy
from gateway.runs import RunRegistry
from gateway.server import create_server
from gateway.statemachine import VerificationStateMachine


def _case(
    case_id="c1",
    correct=True,
    tokens=10,
    cost=0.1,
    latency=50.0,
):
    return CaseResult(
        case_id=case_id,
        is_correct=correct,
        total_tokens=tokens,
        prompt_tokens=7,
        completion_tokens=3,
        actual_cost_usd=cost,
        latency_ms=latency,
        model="m",
        response="resp",
        expected="exp",
        metadata={},
    )


class DatasetLoaderTests(unittest.TestCase):
    def test_jsonl_loader_loads_cases(self):
        content = (
            '{"id": "c1", "input": {"messages": [{"role": "user", "content": "hi"}]}, '
            '"expected_output": "hi", "evaluator": "contains"}\n'
            "\n"
            "# a comment line\n"
            '{"id": "c2", "input": {"messages": [{"role": "user", "content": "2+2"}]}, '
            '"expected_output": "4", "evaluator": "exact_match"}\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cases.jsonl"
            path.write_text(content, encoding="utf-8")
            cases = JsonlDatasetLoader(str(path)).load()

        self.assertEqual(len(cases), 2)
        self.assertEqual(cases[0]["id"], "c1")
        self.assertEqual(cases[1]["expected_output"], "4")

    def test_inline_loader_returns_cases(self):
        cases = [{"id": "c1"}, {"id": "c2"}]
        self.assertEqual(InlineDatasetLoader(cases).load(), cases)

    def test_dataset_loader_is_abstract(self):
        with self.assertRaises(NotImplementedError):
            DatasetLoader().load()


class EvaluatorTests(unittest.TestCase):
    def _assert_passes(self, finding):
        """Assert a Finding represents a passing evaluation."""
        self.assertEqual(
            finding.recommended_action, "none",
            "Expected pass but got action=%s: %s" % (finding.recommended_action, finding.description),
        )

    def _assert_fails(self, finding):
        """Assert a Finding represents a failing evaluation."""
        self.assertNotEqual(
            finding.recommended_action, "none",
            "Expected failure but got action=none: %s" % finding.description,
        )

    def test_exact_match(self):
        evaluator = ExactMatchEvaluator()
        self._assert_passes(evaluator.evaluate("  Paris  ", "paris", {}))
        self._assert_fails(evaluator.evaluate("Paris", "London", {}))

    def test_contains(self):
        evaluator = ContainsEvaluator()
        self._assert_passes(evaluator.evaluate("The capital is Paris", "paris", {}))
        self._assert_fails(evaluator.evaluate("The capital is Paris", "London", {}))

    def test_regex(self):
        evaluator = RegexEvaluator()
        self._assert_passes(evaluator.evaluate("The answer is 42", r"\b42\b", {}))
        self._assert_fails(evaluator.evaluate("The answer is 43", r"\b42\b", {}))

    def test_acceptance(self):
        evaluator = AcceptanceCriteriaEvaluator()
        self._assert_passes(evaluator.evaluate("  some output  ", "", {}))
        self._assert_fails(evaluator.evaluate("", "", {}))
        self._assert_fails(evaluator.evaluate("   ", "", {}))

    def test_composite_all_must_pass(self):
        evaluator = CompositeEvaluator([ContainsEvaluator(), ContainsEvaluator()])
        self._assert_passes(evaluator.evaluate("Paris and France", "Paris", {}))
        self._assert_fails(evaluator.evaluate("Paris and France", "London", {}))

    def test_get_evaluator(self):
        self.assertIsInstance(get_evaluator("exact_match"), ExactMatchEvaluator)
        self.assertIsInstance(get_evaluator("contains"), ContainsEvaluator)
        self.assertIsInstance(get_evaluator("regex"), RegexEvaluator)
        self.assertIsInstance(get_evaluator("acceptance"), AcceptanceCriteriaEvaluator)
        self.assertIsInstance(get_evaluator("unknown"), ExactMatchEvaluator)


class MetricsTests(unittest.TestCase):
    def test_case_result_fields(self):
        result = _case()
        self.assertEqual(result.case_id, "c1")
        self.assertTrue(result.is_correct)
        self.assertEqual(result.total_tokens, 10)

    def test_benchmark_results_properties(self):
        results = BenchmarkResults("cohort", 2, 1, 20, 0.2, 50.0, 50.0, [])
        self.assertAlmostEqual(results.accuracy, 0.5)
        self.assertEqual(results.tokens_per_correct, 20.0)
        self.assertEqual(results.cost_per_correct, 0.2)
        self.assertEqual(results.mean_tokens, 10.0)

    def test_tokens_per_correct_inf_when_no_correct(self):
        results = BenchmarkResults("cohort", 2, 0, 20, 0.2, 50.0, 50.0, [])
        self.assertEqual(results.tokens_per_correct, float("inf"))
        self.assertEqual(results.cost_per_correct, float("inf"))

    def test_compute_results_aggregates(self):
        cases = [_case("a", True, 10, 0.1, 10.0), _case("b", False, 30, 0.3, 90.0)]
        results = compute_results("cohort", cases)
        self.assertEqual(results.total_cases, 2)
        self.assertEqual(results.correct_count, 1)
        self.assertEqual(results.total_tokens, 40)
        self.assertAlmostEqual(results.total_cost_usd, 0.4)
        self.assertAlmostEqual(results.mean_latency_ms, 50.0)
        self.assertEqual(results.p95_latency_ms, 90.0)

    def test_format_report(self):
        cases = [_case("a", True, 10, 0.1, 10.0), _case("b", True, 30, 0.3, 90.0)]
        report = format_report(compute_results("cohort", cases))
        self.assertEqual(report["cohort"], "cohort")
        self.assertEqual(report["total_cases"], 2)
        self.assertEqual(report["correct_count"], 2)
        self.assertAlmostEqual(report["accuracy"], 1.0)
        self.assertEqual(report["total_tokens"], 40)
        self.assertEqual(report["tokens_per_correct_answer"], 20.0)

    def test_format_report_handles_zero_correct(self):
        cases = [_case("a", False, 10, 0.1, 10.0)]
        report = format_report(compute_results("cohort", cases))
        self.assertIsNone(report["tokens_per_correct_answer"])
        self.assertIsNone(report["cost_per_correct_usd"])
        self.assertEqual(report["accuracy"], 0.0)


class BenchmarkRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config = GatewayConfig.from_env(
            {"NOERELAY_GATEWAY_HOST": "127.0.0.1", "NOERELAY_GATEWAY_PORT": "0"}
        )
        policy = load_policy(ROOT / "spec" / "routing-policy.json")
        portfolio = json.loads(
            (ROOT / "examples" / "candidate-actions.json").read_text("utf-8")
        )
        spec = json.loads(
            (ROOT / "spec" / "verification-state-machine.json").read_text("utf-8")
        )
        cls.ctx = PipelineContext(
            config=config,
            policy=policy,
            portfolio=portfolio,
            openrouter_client=StubOpenRouterClient(policy),
            state_machine=VerificationStateMachine(spec),
            registry=RunRegistry(),
        )
        cls.server = create_server(config, cls.ctx)
        cls.port = cls.server.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls._thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls._thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_run_cohort_against_stub_gateway(self):
        cases = [
            {
                "id": "c1",
                "input": {"messages": [{"role": "user", "content": "What is 2+2?"}]},
                "expected_output": "noerelay stub",
                "evaluator": "contains",
            },
            {
                "id": "c2",
                "input": {"messages": [{"role": "user", "content": "What is 2+2?"}]},
                "expected_output": "What is 2+2?",
                "evaluator": "contains",
            },
            {
                "id": "c3",
                "input": {"messages": [{"role": "user", "content": "What is 2+2?"}]},
                "expected_output": "4",
                "evaluator": "contains",
            },
        ]
        runner = BenchmarkRunner(self.base)
        case_results, findings = runner.run_cohort("stub", InlineDatasetLoader(cases), "contains")
        from benchmark.metrics import compute_results
        results = compute_results("stub", case_results)
        self.assertEqual(results.total_cases, 3)
        self.assertEqual(results.correct_count, 2)
        self.assertAlmostEqual(results.accuracy, 2 / 3, places=4)
        self.assertEqual(results.total_tokens, 0)
        # Verify findings are produced
        self.assertEqual(len(findings), 3)
        for f in findings:
            self.assertIsNotNone(f.id)
            self.assertIn(f.severity, ("critical", "high", "medium", "low", "info"))

    def test_run_and_report_returns_dict(self):
        cases = [
            {
                "id": "c1",
                "input": {"messages": [{"role": "user", "content": "What is 2+2?"}]},
                "expected_output": "noerelay stub",
                "evaluator": "contains",
            }
        ]
        report = BenchmarkRunner(self.base).run_and_report(
            "stub", InlineDatasetLoader(cases), "contains"
        )
        self.assertEqual(report["cohort"], "stub")
        self.assertEqual(report["total_cases"], 1)
        self.assertEqual(report["correct_count"], 1)

    def test_run_single_case_extracts_metrics(self):
        runner = BenchmarkRunner("http://127.0.0.1:1")

        def fake_send(request):
            return {
                "model": "noerelay/epr-1",
                "choices": [{"message": {"content": "Paris"}}],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 2,
                    "total_tokens": 7,
                },
                "epr": {"actual_cost_usd": 0.00123, "total_tokens": 7, "latency_ms": 12.5},
            }

        runner._send_request = fake_send
        case = {
            "id": "c1",
            "input": {"messages": [{"role": "user", "content": "capital of France?"}]},
            "expected_output": "Paris",
            "evaluator": "exact_match",
        }
        result, finding = runner._run_single_case(case, "exact_match")
        self.assertTrue(result.is_correct)
        self.assertEqual(result.total_tokens, 7)
        self.assertEqual(result.prompt_tokens, 5)
        self.assertEqual(result.completion_tokens, 2)
        self.assertEqual(result.actual_cost_usd, 0.00123)
        self.assertEqual(result.model, "noerelay/epr-1")
        self.assertEqual(result.response, "Paris")
        # Verify finding is produced
        self.assertIsNotNone(finding.id)
        self.assertEqual(finding.recommended_action, "none")


if __name__ == "__main__":
    unittest.main()
