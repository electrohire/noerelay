from __future__ import annotations

import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reference"))

from benchmark.advanced_metrics import (  # noqa: E402
    compute_all_metrics,
    compute_brier_score,
    compute_context_evidence_recall,
    compute_human_intervention_rate,
    compute_requirement_trace_coverage,
    compute_replay_success_rate,
    compute_rework_rate,
    compute_route_regret,
    compute_selective_risk,
    compute_tool_call_success_rate,
    compute_unsafe_accept_rate,
)
from benchmark.harnesses import (  # noqa: E402
    BFCLHarnessAdapter,
    ContractComplianceAdapter,
    HarnessAdapter,
    SWEBenchHarnessAdapter,
    get_harness,
)
from benchmark.hf_datasets import (  # noqa: E402
    DATASET_REGISTRY,
    HuggingFaceDatasetLoader,
    build_registry_from_manifest,
    get_cohort_loaders,
)
from benchmark.metrics import CaseResult  # noqa: E402

from gateway.auth import AuthMiddleware  # noqa: E402
from gateway.config import GatewayConfig  # noqa: E402
from gateway.handlers import handle_health, handle_metrics  # noqa: E402
from gateway.online_learning import PolicyVersionManager  # noqa: E402
from gateway.openrouter import StubOpenRouterClient  # noqa: E402
from gateway.persistence import FileRunRegistry  # noqa: E402
from gateway.pipeline import PipelineContext  # noqa: E402
from gateway.policy import load_policy  # noqa: E402
from gateway.rate_limit import TokenBucketRateLimiter  # noqa: E402
from gateway.runs import RunRegistry  # noqa: E402
from gateway.server import create_server  # noqa: E402
from gateway.statemachine import VerificationStateMachine  # noqa: E402


def _case(
    case_id="c1",
    correct=True,
    tokens=10,
    cost=0.1,
    latency=50.0,
    metadata=None,
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
        metadata=metadata or {},
    )


class HFDatasetLoaderTests(unittest.TestCase):
    def test_resolve_revision_returns_provided_revision(self):
        loader = HuggingFaceDatasetLoader("owner/ds", revision="abc123")
        self.assertEqual(loader.resolve_revision(), "abc123")

    def test_resolve_revision_from_api(self):
        loader = HuggingFaceDatasetLoader("owner/ds")
        fake = io.BytesIO(json.dumps({"sha": "deadbeef"}).encode("utf-8"))
        with patch("benchmark.hf_datasets.urllib.request.urlopen", return_value=fake):
            self.assertEqual(loader.resolve_revision(), "deadbeef")

    def test_resolve_revision_falls_back_to_main(self):
        loader = HuggingFaceDatasetLoader("owner/ds")
        fake = io.BytesIO(json.dumps({}).encode("utf-8"))
        with patch("benchmark.hf_datasets.urllib.request.urlopen", return_value=fake):
            self.assertEqual(loader.resolve_revision(), "main")

    def test_load_jsonl(self):
        loader = HuggingFaceDatasetLoader("owner/ds", revision="abc")

        def fake_download(revision, filename):
            if filename.endswith(".jsonl"):
                return (
                    b'{"id": "c1", "input": {"messages": [{"role": "user", '
                    b'"content": "hi"}]}, "expected_output": "hi"}\n'
                )
            return None

        loader._download_file = fake_download
        cases = loader.load()
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["id"], "c1")
        self.assertEqual(cases[0]["expected_output"], "hi")

    def test_load_json(self):
        loader = HuggingFaceDatasetLoader("owner/ds", revision="abc")
        payload = [
            {
                "id": "a",
                "input": {"messages": [{"role": "user", "content": "x"}]},
                "output": "y",
            }
        ]

        def fake_download(revision, filename):
            if filename.endswith(".json") and not filename.endswith(".jsonl"):
                return json.dumps(payload).encode("utf-8")
            return None

        loader._download_file = fake_download
        cases = loader.load()
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["expected_output"], "y")

    def test_registry_has_known_cohorts(self):
        self.assertIn("governed-software-v-model", DATASET_REGISTRY)
        self.assertIn("agentic-tool-calling", DATASET_REGISTRY)

    def test_get_cohort_loaders(self):
        loaders = get_cohort_loaders("governed-software-v-model")
        self.assertEqual(len(loaders), 1)
        self.assertEqual(loaders[0].dataset_id, "SWE-bench/SWE-bench_Verified")

    def test_build_registry_from_manifest(self):
        registry = build_registry_from_manifest(
            {
                "cohorts": [
                    {
                        "id": "c1",
                        "datasets": [
                            {
                                "name": "x",
                                "source": "s",
                                "split": "test",
                                "registry": "huggingface",
                                "dataset_id": "owner/swe-ds",
                            }
                        ],
                    }
                ]
            }
        )
        self.assertIn("c1", registry)
        self.assertEqual(registry["c1"]["datasets"][0]["id"], "owner/swe-ds")
        self.assertEqual(registry["c1"]["datasets"][0]["format"], "swe_bench")


class HarnessAdapterTests(unittest.TestCase):
    def test_harness_adapter_is_abstract(self):
        with self.assertRaises(NotImplementedError):
            HarnessAdapter().evaluate({}, {"content": ""})

    def test_swebench_detects_patch(self):
        adapter = SWEBenchHarnessAdapter()
        self.assertTrue(
            adapter.evaluate({}, {"content": "```diff\n--- a/x\n+++ b/x\n```"})
        )
        self.assertTrue(adapter.evaluate({}, {"content": "diff --git a/x b/x"}))
        self.assertFalse(adapter.evaluate({}, {"content": "just a plain answer"}))

    def test_bfcl_json_match(self):
        adapter = BFCLHarnessAdapter()
        case = {"expected_output": '{"name": "foo", "args": {}}'}
        self.assertTrue(
            adapter.evaluate(case, {"content": '{"name": "foo", "args": {}}'})
        )
        self.assertFalse(adapter.evaluate(case, {"content": '{"name": "bar"}'}))

    def test_bfcl_plain_match(self):
        adapter = BFCLHarnessAdapter()
        case = {"expected_output": "lookup"}
        self.assertTrue(adapter.evaluate(case, {"content": "  LOOKUP  "}))
        self.assertFalse(adapter.evaluate(case, {"content": "other"}))

    def test_contract_adapter(self):
        adapter = ContractComplianceAdapter()
        self.assertTrue(adapter.evaluate({}, {"content": "some output"}))
        self.assertFalse(adapter.evaluate({}, {"content": ""}))

    def test_get_harness(self):
        self.assertIsInstance(get_harness("swe_bench"), SWEBenchHarnessAdapter)
        self.assertIsInstance(get_harness("bfcl"), BFCLHarnessAdapter)
        self.assertIsInstance(get_harness("unknown"), ContractComplianceAdapter)


class AdvancedMetricsTests(unittest.TestCase):
    def test_brier_score(self):
        self.assertAlmostEqual(compute_brier_score([0.8, 0.2], [True, False]), 0.04)
        self.assertEqual(compute_brier_score([], []), 0.0)

    def test_selective_risk(self):
        results = [
            _case("a", True, metadata={"confidence": 0.9}),
            _case("b", False, metadata={"confidence": 0.9}),
            _case("c", False, metadata={"confidence": 0.1}),
        ]
        self.assertAlmostEqual(compute_selective_risk(results, 0.5), 0.5)

    def test_route_regret(self):
        results = [_case("a", cost=0.3), _case("b", cost=0.2)]
        self.assertAlmostEqual(compute_route_regret(results, [0.1, 0.1]), 0.15)

    def test_context_evidence_recall(self):
        self.assertAlmostEqual(
            compute_context_evidence_recall(["a", "b"], ["a", "c"]), 0.5
        )
        self.assertEqual(compute_context_evidence_recall([], ["a"]), 0.0)
        self.assertEqual(compute_context_evidence_recall(["a"], []), 1.0)

    def test_replay_success_rate(self):
        original = [_case("a", True), _case("b", False), _case("c", True)]
        replay = [_case("a", True), _case("b", True), _case("c", True)]
        self.assertAlmostEqual(compute_replay_success_rate(original, replay), 2 / 3)

    def test_unsafe_accept_rate(self):
        results = [
            _case("a", True, metadata={"verification_passed": True}),
            _case("b", False, metadata={"verification_passed": True}),
            _case("c", False, metadata={"verification_passed": False}),
        ]
        self.assertAlmostEqual(compute_unsafe_accept_rate(results), 0.5)

    def test_requirement_trace_coverage(self):
        traces = [{"artifact_ids": ["x"]}, {"artifact_ids": []}, {}]
        self.assertAlmostEqual(compute_requirement_trace_coverage(traces), 1 / 3)
        self.assertEqual(compute_requirement_trace_coverage([]), 0.0)

    def test_tool_call_success_rate(self):
        results = [
            _case("a", True, metadata={"has_tool_calls": True}),
            _case("b", False, metadata={"has_tool_calls": True}),
            _case("c", True, metadata={"has_tool_calls": False}),
        ]
        self.assertAlmostEqual(compute_tool_call_success_rate(results), 0.5)
        no_tools = [_case("x", True, metadata={"has_tool_calls": False})]
        self.assertEqual(compute_tool_call_success_rate(no_tools), 1.0)

    def test_compute_all_metrics(self):
        results = [
            _case("a", True, cost=0.1, tokens=10),
            _case("b", False, cost=0.2, tokens=20),
        ]
        metrics = compute_all_metrics(results)
        self.assertAlmostEqual(metrics["accuracy"], 0.5)
        self.assertEqual(metrics["total_tokens"], 30)
        self.assertAlmostEqual(metrics["total_cost_usd"], 0.3)
        self.assertIn("brier_score", metrics)
        self.assertIn("tool_call_success_rate", metrics)


class HIRRMetricsTests(unittest.TestCase):
    def test_human_intervention_rate(self):
        results = [
            _case("a", metadata={"required_human_intervention": True}),
            _case("b", metadata={"required_human_intervention": False}),
            _case("c", metadata={"required_human_intervention": True}),
        ]
        self.assertAlmostEqual(compute_human_intervention_rate(results), 2 / 3)

    def test_human_intervention_rate_empty(self):
        self.assertEqual(compute_human_intervention_rate([]), 0.0)

    def test_rework_rate(self):
        results = [
            _case("a", metadata={"required_rework": True}),
            _case("b", metadata={"required_rework": False}),
            _case("c", metadata={"required_rework": False}),
        ]
        self.assertAlmostEqual(compute_rework_rate(results), 1 / 3)

    def test_rework_rate_empty(self):
        self.assertEqual(compute_rework_rate([]), 0.0)

    def test_compute_all_metrics_includes_hir_rr(self):
        results = [
            _case(
                "a",
                metadata={
                    "required_human_intervention": True,
                    "required_rework": True,
                },
            ),
            _case("b", metadata={}),
        ]
        metrics = compute_all_metrics(results)
        self.assertIn("human_intervention_rate", metrics)
        self.assertIn("rework_rate", metrics)
        self.assertAlmostEqual(metrics["human_intervention_rate"], 0.5)
        self.assertAlmostEqual(metrics["rework_rate"], 0.5)


class PromotionGateTests(unittest.TestCase):
    def _cohort_results(self, **overrides):
        base = {
            "accepted_outcome_rate": 0.95,
            "unsafe_accept_rate": 0.0,
            "tool_call_success_rate": 0.98,
            "calibration_ece": 0.02,
            "replay_success_rate": 0.995,
            "context_evidence_recall": 0.995,
            "route_regret_usd": 0.01,
        }
        base.update(overrides)
        return base

    def test_promote_with_manifest_cohorts(self):
        manager = PolicyVersionManager("1.0.0")
        manager.register_canary("1.1.0")
        ok, reason = manager.promote_canary(
            "1.1.0",
            {
                "signed": True,
                "cohorts": {
                    "agentic-tool-calling": self._cohort_results(),
                },
            },
        )
        self.assertTrue(ok, reason)
        self.assertEqual(manager.get_production_policy_version(), "1.1.0")

    def test_promote_fails_cohort_gate(self):
        manager = PolicyVersionManager("1.0.0")
        manager.register_canary("1.1.0")
        ok, reason = manager.promote_canary(
            "1.1.0",
            {
                "signed": True,
                "cohorts": {
                    "agentic-tool-calling": self._cohort_results(
                        accepted_outcome_rate=0.85
                    ),
                },
            },
        )
        self.assertFalse(ok)
        self.assertIn("agentic-tool-calling", reason)

    def test_promote_fails_missing_cohort_gate(self):
        manager = PolicyVersionManager("1.0.0")
        manager.register_canary("1.1.0")
        ok, reason = manager.promote_canary(
            "1.1.0",
            {
                "signed": True,
                "cohorts": {
                    "agentic-tool-calling": {"accepted_outcome_rate": 0.95},
                },
            },
        )
        self.assertFalse(ok)
        self.assertIn("missing gate", reason)

    def test_promote_requires_signed(self):
        manager = PolicyVersionManager("1.0.0")
        manager.register_canary("1.1.0")
        ok, reason = manager.promote_canary("1.1.0", {"cohorts": {}})
        self.assertFalse(ok)
        self.assertIn("signed", reason)

    def test_legacy_flat_gates_still_supported(self):
        manager = PolicyVersionManager("1.0.0")
        manager.register_canary("1.1.0")
        ok, reason = manager.promote_canary(
            "1.1.0",
            {
                "signed": True,
                "gates": {
                    "maximum_unsafe_accept_rate": 0,
                    "maximum_calibration_ece": 0.03,
                    "minimum_replay_success_rate": 0.99,
                    "minimum_context_evidence_recall": 0.99,
                    "maximum_route_regret_usd": 0.05,
                },
            },
        )
        self.assertTrue(ok, reason)


class AuthTests(unittest.TestCase):
    def test_open_access_when_no_keys(self):
        self.assertTrue(
            AuthMiddleware().authenticate({"Authorization": "Bearer anything"})
        )

    def test_valid_key_accepted(self):
        middleware = AuthMiddleware({"secret"})
        self.assertTrue(
            middleware.authenticate({"Authorization": "Bearer secret"})
        )
        self.assertFalse(middleware.authenticate({"Authorization": "Bearer wrong"}))

    def test_missing_bearer_rejected(self):
        middleware = AuthMiddleware({"secret"})
        self.assertFalse(middleware.authenticate({}))
        self.assertFalse(middleware.authenticate({"Authorization": "secret"}))
        self.assertFalse(middleware.authenticate({"Authorization": "Basic secret"}))

    def test_from_csv(self):
        middleware = AuthMiddleware.from_csv("a, b ,c")
        self.assertTrue(middleware.authenticate({"Authorization": "Bearer a"}))
        self.assertTrue(middleware.authenticate({"Authorization": "Bearer b"}))
        self.assertTrue(middleware.authenticate({"Authorization": "Bearer c"}))
        self.assertFalse(middleware.authenticate({"Authorization": "Bearer d"}))


class RateLimitTests(unittest.TestCase):
    def test_burst_capacity(self):
        limiter = TokenBucketRateLimiter(rate=0.0, burst=2)
        self.assertTrue(limiter.allow())
        self.assertTrue(limiter.allow())
        self.assertFalse(limiter.allow())

    def test_default_allows(self):
        limiter = TokenBucketRateLimiter()
        self.assertTrue(limiter.allow())


class PersistenceTests(unittest.TestCase):
    def test_persist_and_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = FileRunRegistry(tmp)
            registry.begin("run-1", "trace-1")
            registry.ledger(
                "run-1",
                "request_received",
                {"id": "gw", "kind": "service"},
                "task-1",
                {"hash": "abc"},
            )
            registry.issue_receipt("run-1", "accepted", [], 0.0)

            registry2 = FileRunRegistry(tmp)
            reloaded = registry2.get("run-1")
            self.assertIsNotNone(reloaded)
            self.assertEqual(reloaded.trace_id, "trace-1")
            self.assertEqual(reloaded.receipt["status"], "accepted")
            self.assertEqual(len(reloaded.events), 1)

    def test_storage_dir_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp) / "nested" / "runs"
            FileRunRegistry(str(storage))
            self.assertTrue(storage.exists())


class HealthMetricsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = GatewayConfig.from_env(
            {"NOERELAY_GATEWAY_HOST": "127.0.0.1", "NOERELAY_GATEWAY_PORT": "0"}
        )
        cls.policy = load_policy(ROOT / "spec" / "routing-policy.json")
        cls.portfolio = json.loads(
            (ROOT / "examples" / "candidate-actions.json").read_text("utf-8")
        )
        cls.spec = json.loads(
            (ROOT / "spec" / "verification-state-machine.json").read_text("utf-8")
        )
        cls.ctx = PipelineContext(
            config=cls.config,
            policy=cls.policy,
            portfolio=cls.portfolio,
            openrouter_client=StubOpenRouterClient(cls.policy),
            state_machine=VerificationStateMachine(cls.spec),
            registry=RunRegistry(),
        )
        cls.server = create_server(cls.config, cls.ctx)
        cls.port = cls.server.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls._thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls._thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _make_ctx(self):
        return PipelineContext(
            config=GatewayConfig.from_env(
                {"NOERELAY_GATEWAY_HOST": "127.0.0.1", "NOERELAY_GATEWAY_PORT": "0"}
            ),
            policy=self.policy,
            portfolio=self.portfolio,
            openrouter_client=StubOpenRouterClient(self.policy),
            state_machine=VerificationStateMachine(self.spec),
            registry=RunRegistry(),
        )

    def _get(self, path):
        req = urllib.request.Request(f"{self.base}{path}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())

    def test_health_endpoint(self):
        status, body = self._get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "healthy")
        self.assertEqual(body["version"], "0.1.0")

    def test_metrics_endpoint(self):
        req = urllib.request.Request(
            f"{self.base}/metrics",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            status = resp.status
            body = json.loads(resp.read())
        self.assertEqual(status, 200)
        self.assertEqual(body["runs_total"], 0)
        self.assertEqual(body["runs_accepted"], 0)
        self.assertEqual(body["runs_escalated"], 0)

    def test_handle_health_direct(self):
        status, body = handle_health(self.ctx)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "healthy")

    def test_handle_metrics_direct(self):
        ctx = self._make_ctx()
        ctx.registry.begin("run-m", "trace-m")
        ctx.registry.issue_receipt("run-m", "accepted", [], 0.0)
        status, body, content_type = handle_metrics(ctx, accept_header="application/json")
        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(body["runs_total"], 1)
        self.assertEqual(body["runs_accepted"], 1)
        self.assertEqual(body["runs_escalated"], 0)


if __name__ == "__main__":
    unittest.main()
