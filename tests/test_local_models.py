"""Tests for local model support, escalation policy, and HIR/RR metrics."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reference"))

from gateway.config import GatewayConfig
from gateway.escalation_policy import EscalationPolicy
from gateway.local_models import StubLocalModelClient
from gateway.local_policy import extend_policy_with_local
from gateway.local_portfolio import (
    LOCAL_MODELS,
    get_local_model_by_id,
    get_local_models,
    local_candidates,
)
from gateway.openrouter import StubOpenRouterClient
from gateway.pipeline import PipelineContext, run_inference_pipeline
from gateway.policy import load_policy
from gateway.runs import RunRegistry
from gateway.statemachine import VerificationStateMachine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(
    *,
    prefer_local: bool = False,
    local_model_client=None,
    escalation_policy=None,
    policy=None,
    portfolio=None,
) -> PipelineContext:
    """Build a PipelineContext suitable for pipeline tests."""
    config = GatewayConfig.from_env(
        {"NOERELAY_GATEWAY_HOST": "127.0.0.1", "NOERELAY_GATEWAY_PORT": "0"}
    )
    if policy is None:
        policy = json.loads(
            (ROOT / "spec" / "routing-policy.json").read_text("utf-8")
        )
    if portfolio is None:
        portfolio = json.loads(
            (ROOT / "examples" / "candidate-actions.json").read_text("utf-8")
        )
    spec = json.loads(
        (ROOT / "spec" / "verification-state-machine.json").read_text("utf-8")
    )
    return PipelineContext(
        config=config,
        policy=policy,
        portfolio=portfolio,
        openrouter_client=StubOpenRouterClient(policy),
        state_machine=VerificationStateMachine(spec),
        registry=RunRegistry(),
        prefer_local=prefer_local,
        local_model_client=local_model_client,
        escalation_policy=escalation_policy,
    )


class FailingLocalModelClient:
    """A local model stub that returns empty content (fails schema check)."""

    def __init__(self, model_id: str = "qwen3:8b") -> None:
        self._model_id = model_id

    def create_chat_completion(self, payload: dict) -> dict:
        return {
            "id": "local-gen-fail",
            "object": "chat.completion",
            "created": 0,
            "model": self._model_id,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": ""},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 0,
                "total_tokens": 10,
            },
        }

    def is_available(self) -> bool:
        return True


class TransportErrorLocalModelClient:
    """A local model stub that raises a transport error (simulates Ollama not running)."""

    def __init__(self, model_id: str = "qwen3:8b") -> None:
        self._model_id = model_id

    def create_chat_completion(self, payload: dict) -> dict:
        from gateway.local_models import LocalModelError

        raise LocalModelError("local model transport error: connection refused")

    def is_available(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# EscalationPolicyTests
# ---------------------------------------------------------------------------


class EscalationPolicyTests(unittest.TestCase):
    def test_initial_hir_and_rr_are_zero(self):
        policy = EscalationPolicy(min_sample_size=3)
        self.assertEqual(policy.current_hir(), 0.0)
        self.assertEqual(policy.current_rr(), 0.0)

    def test_hir_computation(self):
        policy = EscalationPolicy(min_sample_size=3)
        for _ in range(5):
            policy.record_run({"required_human_intervention": True, "required_rework": False})
        self.assertAlmostEqual(policy.current_hir(), 1.0)
        self.assertAlmostEqual(policy.current_rr(), 0.0)

    def test_rr_computation(self):
        policy = EscalationPolicy(min_sample_size=3)
        for _ in range(4):
            policy.record_run({"required_human_intervention": False, "required_rework": True})
        self.assertAlmostEqual(policy.current_hir(), 0.0)
        self.assertAlmostEqual(policy.current_rr(), 1.0)

    def test_below_min_sample_returns_zero(self):
        policy = EscalationPolicy(min_sample_size=10)
        for _ in range(5):
            policy.record_run({"required_human_intervention": True, "required_rework": True})
        self.assertEqual(policy.current_hir(), 0.0)
        self.assertEqual(policy.current_rr(), 0.0)

    def test_rolling_window_capped_at_100(self):
        policy = EscalationPolicy(min_sample_size=1)
        for _ in range(150):
            policy.record_run({"required_human_intervention": True, "required_rework": False})
        self.assertLessEqual(policy.history_size, 100)

    def test_should_escalate_to_cloud_local_failed(self):
        policy = EscalationPolicy()
        should, reason = policy.should_escalate_to_cloud(True, "low")
        self.assertTrue(should)
        self.assertEqual(reason, "local_model_failed")

    def test_should_escalate_to_cloud_high_risk(self):
        policy = EscalationPolicy()
        should, reason = policy.should_escalate_to_cloud(False, "high")
        self.assertTrue(should)
        self.assertIn("high_risk_class", reason)

    def test_should_escalate_to_cloud_critical_risk(self):
        policy = EscalationPolicy()
        should, reason = policy.should_escalate_to_cloud(False, "critical")
        self.assertTrue(should)
        self.assertIn("high_risk_class", reason)

    def test_should_escalate_to_cloud_hir_exceeded(self):
        policy = EscalationPolicy(hir_threshold=0.1, min_sample_size=3)
        for _ in range(5):
            policy.record_run({"required_human_intervention": True, "required_rework": False})
        should, reason = policy.should_escalate_to_cloud(False, "low")
        self.assertTrue(should)
        self.assertIn("hir_exceeded_threshold", reason)

    def test_should_escalate_to_cloud_rr_exceeded(self):
        policy = EscalationPolicy(rr_threshold=0.1, min_sample_size=3)
        for _ in range(5):
            policy.record_run({"required_human_intervention": False, "required_rework": True})
        should, reason = policy.should_escalate_to_cloud(False, "low")
        self.assertTrue(should)
        self.assertIn("rr_exceeded_threshold", reason)

    def test_should_escalate_to_cloud_local_sufficient(self):
        policy = EscalationPolicy(min_sample_size=10)
        should, reason = policy.should_escalate_to_cloud(False, "low")
        self.assertFalse(should)
        self.assertEqual(reason, "local_model_sufficient")

    def test_should_request_human_review_critical(self):
        policy = EscalationPolicy()
        should, reason = policy.should_request_human_review(False, False, "critical")
        self.assertTrue(should)
        self.assertEqual(reason, "critical_risk_requires_human")

    def test_should_request_human_review_blocking_conflict(self):
        policy = EscalationPolicy()
        should, reason = policy.should_request_human_review(False, True, "low")
        self.assertTrue(should)
        self.assertEqual(reason, "blocking_epistemic_conflict")

    def test_should_request_human_review_high_rr(self):
        policy = EscalationPolicy(rr_threshold=0.1, min_sample_size=3)
        for _ in range(5):
            policy.record_run({"required_human_intervention": False, "required_rework": True})
        should, reason = policy.should_request_human_review(True, False, "low")
        self.assertTrue(should)
        self.assertIn("verification_failed_with_high_rr", reason)

    def test_should_request_human_review_no_review_needed(self):
        policy = EscalationPolicy(min_sample_size=10)
        should, reason = policy.should_request_human_review(False, False, "low")
        self.assertFalse(should)
        self.assertEqual(reason, "no_human_review_needed")


# ---------------------------------------------------------------------------
# LocalModelTests
# ---------------------------------------------------------------------------


class LocalModelTests(unittest.TestCase):
    def test_stub_returns_valid_response(self):
        client = StubLocalModelClient(model_id="qwen3:8b")
        payload = {
            "model": "qwen3:8b",
            "messages": [{"role": "user", "content": "Hello"}],
        }
        result = client.create_chat_completion(payload)
        self.assertIn("choices", result)
        self.assertEqual(len(result["choices"]), 1)
        self.assertIn("[local stub]", result["choices"][0]["message"]["content"])
        self.assertEqual(result["model"], "qwen3:8b")

    def test_stub_is_available(self):
        client = StubLocalModelClient()
        self.assertTrue(client.is_available())

    def test_local_portfolio_has_four_models(self):
        models = get_local_models()
        self.assertEqual(len(models), 4)

    def test_local_portfolio_models_are_local(self):
        for model in get_local_models():
            self.assertTrue(model.get("is_local"), f"{model['candidate_id']} should be local")

    def test_get_local_model_by_id_found(self):
        model = get_local_model_by_id("qwen3:8b")
        self.assertIsNotNone(model)
        self.assertEqual(model["candidate_id"], "qwen3-8b-local")

    def test_get_local_model_by_id_not_found(self):
        model = get_local_model_by_id("nonexistent-model")
        self.assertIsNone(model)

    def test_local_candidates_are_kernel_compatible(self):
        candidates = local_candidates()
        self.assertEqual(len(candidates), 4)
        for c in candidates:
            self.assertEqual(c["action_kind"], "model")
            self.assertIn("execute", c["roles"])
            self.assertIn("capabilities", c)
            self.assertIn("data_policies", c)
            self.assertIn("acceptance", c)
            self.assertIn("costs", c)
            self.assertIn("lower_bound", c["acceptance"])
            self.assertTrue(c["is_local"])

    def test_extend_policy_adds_local_gateway(self):
        policy = json.loads(
            (ROOT / "spec" / "routing-policy.json").read_text("utf-8")
        )
        extended = extend_policy_with_local(policy)
        self.assertIn("local", extended["inference"]["allowed_gateways"])
        self.assertIn("local", extended["inference"])
        self.assertEqual(
            extended["inference"]["local"]["default_base_url"],
            "http://127.0.0.1:11434",
        )

    def test_extend_policy_does_not_mutate_original(self):
        policy = json.loads(
            (ROOT / "spec" / "routing-policy.json").read_text("utf-8")
        )
        original_gateways = list(policy["inference"]["allowed_gateways"])
        extend_policy_with_local(policy)
        self.assertEqual(
            policy["inference"]["allowed_gateways"], original_gateways
        )


# ---------------------------------------------------------------------------
# HumanInterventionReworkTests
# ---------------------------------------------------------------------------


class HumanInterventionReworkTests(unittest.TestCase):
    def test_record_human_intervention(self):
        registry = RunRegistry()
        record = registry.begin("run-1", "trace-1")
        self.assertFalse(record.required_human_intervention)
        self.assertIsNone(record.human_intervention_reason)

        registry.record_human_intervention("run-1", "critical_risk")
        self.assertTrue(record.required_human_intervention)
        self.assertEqual(record.human_intervention_reason, "critical_risk")

    def test_record_rework(self):
        registry = RunRegistry()
        record = registry.begin("run-2", "trace-2")
        self.assertFalse(record.required_rework)
        self.assertIsNone(record.rework_reason)

        registry.record_rework("run-2", "verification_failed")
        self.assertTrue(record.required_rework)
        self.assertEqual(record.rework_reason, "verification_failed")

    def test_record_human_intervention_raises_for_unknown_run(self):
        registry = RunRegistry()
        with self.assertRaises(KeyError):
            registry.record_human_intervention("nonexistent", "reason")

    def test_record_rework_raises_for_unknown_run(self):
        registry = RunRegistry()
        with self.assertRaises(KeyError):
            registry.record_rework("nonexistent", "reason")

    def test_critical_risk_records_human_intervention(self):
        """Pipeline records human intervention for critical-risk tasks.

        Critical-risk tasks cannot route in the skeleton (no independent
        verifier with sufficient LCB), so the pipeline escalates with 424.
        The human-intervention flag is still recorded before routing fails.
        """
        from gateway.pipeline import PipelineError

        ctx = _make_ctx()
        request = {
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": "Hello"}],
            "governance": {"risk_class": "critical", "max_cost_usd": 2.0},
        }
        try:
            run_inference_pipeline(request, ctx)
        except PipelineError as exc:
            run_id = exc.body["epr"]["run_id"]
            record = ctx.registry.get(run_id)
            self.assertIsNotNone(record)
            self.assertTrue(
                record.required_human_intervention,
                "Critical risk should record human intervention",
            )
            self.assertIn(
                "critical_risk", record.human_intervention_reason or ""
            )
        else:
            self.fail("Expected PipelineError for critical risk")

    def test_low_risk_does_not_record_human_intervention(self):
        """Low-risk tasks should not record human intervention."""
        ctx = _make_ctx()
        request = {
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": "Hello"}],
            "governance": {"risk_class": "low"},
        }
        result = run_inference_pipeline(request, ctx)
        self.assertEqual(result["epr"]["status"], "accepted")
        run_id = result["epr"]["run_id"]
        record = ctx.registry.get(run_id)
        self.assertIsNotNone(record)
        self.assertFalse(record.required_human_intervention)

    def test_epr_metadata_includes_hir_rr_flags(self):
        """The epr metadata block carries HIR/RR flags."""
        ctx = _make_ctx()
        request = {
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": "Hello"}],
        }
        result = run_inference_pipeline(request, ctx)
        epr = result["epr"]
        self.assertIn("required_human_intervention", epr)
        self.assertIn("required_rework", epr)
        self.assertFalse(epr["required_human_intervention"])
        self.assertFalse(epr["required_rework"])


# ---------------------------------------------------------------------------
# LocalCloudEscalationTests
# ---------------------------------------------------------------------------


class LocalCloudEscalationTests(unittest.TestCase):
    def test_local_to_cloud_escalation(self):
        """When a local model fails verification, the pipeline escalates to cloud."""
        policy = json.loads(
            (ROOT / "spec" / "routing-policy.json").read_text("utf-8")
        )
        portfolio = json.loads(
            (ROOT / "examples" / "candidate-actions.json").read_text("utf-8")
        )
        ctx = _make_ctx(
            prefer_local=True,
            local_model_client=FailingLocalModelClient(model_id="qwen3:8b"),
            escalation_policy=EscalationPolicy(),
            policy=policy,
            portfolio=portfolio,
        )
        request = {
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": "Hello"}],
            "governance": {"risk_class": "low", "data_policy": "local_only"},
        }
        result = run_inference_pipeline(request, ctx)
        self.assertEqual(result["epr"]["status"], "accepted")

        run_id = result["epr"]["run_id"]
        record = ctx.registry.get(run_id)
        self.assertIsNotNone(record)

        # Rework should be recorded because the local attempt failed.
        self.assertTrue(record.required_rework)
        self.assertIn("local_to_cloud_escalation", record.rework_reason or "")

        # A semantic fallback should have been recorded.
        semantic_fallbacks = record.fallbacks.get_semantic_fallbacks()
        self.assertGreaterEqual(len(semantic_fallbacks), 1)
        self.assertEqual(
            semantic_fallbacks[0]["reason"], "local_to_cloud_escalation"
        )

    def test_local_model_succeeds_no_escalation(self):
        """When a local model passes verification, no escalation occurs."""
        policy = json.loads(
            (ROOT / "spec" / "routing-policy.json").read_text("utf-8")
        )
        portfolio = json.loads(
            (ROOT / "examples" / "candidate-actions.json").read_text("utf-8")
        )
        ctx = _make_ctx(
            prefer_local=True,
            local_model_client=StubLocalModelClient(model_id="qwen3:8b"),
            escalation_policy=EscalationPolicy(),
            policy=policy,
            portfolio=portfolio,
        )
        request = {
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": "Hello"}],
            "governance": {"risk_class": "low", "data_policy": "local_only"},
        }
        result = run_inference_pipeline(request, ctx)
        self.assertEqual(result["epr"]["status"], "accepted")

        run_id = result["epr"]["run_id"]
        record = ctx.registry.get(run_id)
        self.assertIsNotNone(record)
        self.assertFalse(record.required_rework)

    def test_no_local_client_no_escalation(self):
        """When prefer_local is True but no local client, cloud is used directly."""
        ctx = _make_ctx(prefer_local=True, local_model_client=None)
        request = {
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": "Hello"}],
        }
        result = run_inference_pipeline(request, ctx)
        self.assertEqual(result["epr"]["status"], "accepted")
        # No rework because cloud was used directly (no local attempt).
        run_id = result["epr"]["run_id"]
        record = ctx.registry.get(run_id)
        self.assertIsNotNone(record)
        self.assertFalse(record.required_rework)

    def test_local_to_cloud_escalation_on_transport_error(self):
        """When the local model server is unavailable, the pipeline falls back to cloud."""
        policy = json.loads(
            (ROOT / "spec" / "routing-policy.json").read_text("utf-8")
        )
        portfolio = json.loads(
            (ROOT / "examples" / "candidate-actions.json").read_text("utf-8")
        )
        ctx = _make_ctx(
            prefer_local=True,
            local_model_client=TransportErrorLocalModelClient(model_id="qwen3:8b"),
            escalation_policy=EscalationPolicy(),
            policy=policy,
            portfolio=portfolio,
        )
        request = {
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": "Hello"}],
            "governance": {"risk_class": "low", "data_policy": "local_only"},
        }
        result = run_inference_pipeline(request, ctx)
        self.assertEqual(result["epr"]["status"], "accepted")

        run_id = result["epr"]["run_id"]
        record = ctx.registry.get(run_id)
        self.assertIsNotNone(record)

        # A provider fallback should have been recorded (transport error triggers
        # provider fallback, not semantic fallback).
        provider_fallbacks = record.fallbacks.get_provider_fallbacks()
        self.assertGreaterEqual(len(provider_fallbacks), 1)


# ---------------------------------------------------------------------------
# CostEstimationTests
# ---------------------------------------------------------------------------


class CostEstimationTests(unittest.TestCase):
    def test_local_model_returns_zero_cost(self):
        """Local models should return $0.0 estimated cost."""
        from gateway.pipeline import _estimate_cost

        usage = {"prompt_tokens": 100, "completion_tokens": 50}
        plan = {"model_id": "qwen3:8b", "inference_gateway": "local"}
        cost = _estimate_cost(usage, plan)
        self.assertEqual(cost, 0.0)

    def test_local_model_zero_cost_regardless_of_tokens(self):
        """Local models return $0.0 even with high token counts."""
        from gateway.pipeline import _estimate_cost

        usage = {"prompt_tokens": 100000, "completion_tokens": 50000}
        plan = {"model_id": "qwen3-coder:30b", "inference_gateway": "local"}
        cost = _estimate_cost(usage, plan)
        self.assertEqual(cost, 0.0)

    def test_cloud_model_returns_nonzero_cost(self):
        """Cloud models should return non-zero estimated cost."""
        from gateway.pipeline import _estimate_cost

        usage = {"prompt_tokens": 1000, "completion_tokens": 500}
        plan = {"model_id": "qwen/qwen3.6-35b-a3b", "inference_gateway": "openrouter"}
        cost = _estimate_cost(usage, plan)
        self.assertGreater(cost, 0.0)

    def test_local_model_rate_entries_exist(self):
        """All local models have $0.0 rate entries."""
        from gateway.pipeline import _MODEL_RATES_USD_PER_1K

        local_ids = [
            "qwen3:8b",
            "qwen3-coder:30b",
            "qwen3-vl:8b-thinking",
            "qwen38-4b-distilled:latest",
        ]
        for model_id in local_ids:
            with self.subTest(model_id=model_id):
                self.assertIn(model_id, _MODEL_RATES_USD_PER_1K)
                prompt_rate, completion_rate = _MODEL_RATES_USD_PER_1K[model_id]
                self.assertEqual(prompt_rate, 0.0)
                self.assertEqual(completion_rate, 0.0)


# ---------------------------------------------------------------------------
# BenchmarkDatasetTests
# ---------------------------------------------------------------------------


class BenchmarkDatasetTests(unittest.TestCase):
    def _load_jsonl(self, path: Path) -> list[dict]:
        cases = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    cases.append(json.loads(line))
        return cases

    def test_coding_tasks_load(self):
        path = ROOT / "benchmarks" / "coding-tasks.jsonl"
        self.assertTrue(path.exists(), f"Missing {path}")
        cases = self._load_jsonl(path)
        self.assertEqual(len(cases), 15)
        for case in cases:
            self.assertIn("id", case)
            self.assertIn("input", case)
            self.assertIn("expected_output", case)
            self.assertIn("evaluator", case)
            self.assertTrue(case["id"].startswith("code-"))

    def test_reasoning_tasks_load(self):
        path = ROOT / "benchmarks" / "reasoning-tasks.jsonl"
        self.assertTrue(path.exists(), f"Missing {path}")
        cases = self._load_jsonl(path)
        self.assertEqual(len(cases), 15)
        for case in cases:
            self.assertIn("id", case)
            self.assertTrue(case["id"].startswith("reason-"))

    def test_multi_turn_tasks_load(self):
        path = ROOT / "benchmarks" / "multi-turn-tasks.jsonl"
        self.assertTrue(path.exists(), f"Missing {path}")
        cases = self._load_jsonl(path)
        self.assertEqual(len(cases), 10)
        for case in cases:
            self.assertIn("id", case)
            self.assertTrue(case["id"].startswith("mt-"))
            # Multi-turn tasks should have more than 1 message
            messages = case["input"]["messages"]
            self.assertGreater(len(messages), 1)


# ---------------------------------------------------------------------------
# ResponseCacheTests
# ---------------------------------------------------------------------------


class ResponseCacheTests(unittest.TestCase):
    def test_cache_hit_and_miss(self):
        from gateway.cache import ResponseCache

        cache = ResponseCache(max_size=10, ttl_seconds=3600)
        request = {
            "messages": [{"role": "user", "content": "Hello"}],
            "passthrough": {},
            "governance": {},
        }
        response = {"choices": [{"message": {"content": "Hi!"}}], "epr": {}}

        # Miss on first lookup.
        self.assertIsNone(cache.get(request))

        # Store and hit.
        cache.put(request, response)
        cached = cache.get(request)
        self.assertIsNotNone(cached)
        self.assertEqual(cached["choices"][0]["message"]["content"], "Hi!")

    def test_cache_ttl_expiry(self):
        from gateway.cache import ResponseCache

        cache = ResponseCache(max_size=10, ttl_seconds=0)  # Immediate expiry
        request = {
            "messages": [{"role": "user", "content": "Hello"}],
            "passthrough": {},
            "governance": {},
        }
        response = {"choices": [{"message": {"content": "Hi!"}}], "epr": {}}

        cache.put(request, response)
        # Should be expired immediately (TTL=0 means expires_at == put time,
        # and the >= check means it's already expired).
        self.assertIsNone(cache.get(request))

    def test_cache_lru_eviction(self):
        from gateway.cache import ResponseCache

        cache = ResponseCache(max_size=2, ttl_seconds=3600)

        req1 = {"messages": [{"role": "user", "content": "A"}], "passthrough": {}, "governance": {}}
        req2 = {"messages": [{"role": "user", "content": "B"}], "passthrough": {}, "governance": {}}
        req3 = {"messages": [{"role": "user", "content": "C"}], "passthrough": {}, "governance": {}}

        cache.put(req1, {"epr": {}, "data": "a"})
        cache.put(req2, {"epr": {}, "data": "b"})
        self.assertEqual(cache.size(), 2)

        # Adding third should evict oldest (req1).
        cache.put(req3, {"epr": {}, "data": "c"})
        self.assertEqual(cache.size(), 2)
        self.assertIsNone(cache.get(req1))
        self.assertIsNotNone(cache.get(req2))
        self.assertIsNotNone(cache.get(req3))

    def test_cache_clear(self):
        from gateway.cache import ResponseCache

        cache = ResponseCache(max_size=10, ttl_seconds=3600)
        request = {
            "messages": [{"role": "user", "content": "Hello"}],
            "passthrough": {},
            "governance": {},
        }
        cache.put(request, {"epr": {}, "data": "test"})
        self.assertEqual(cache.size(), 1)
        cache.clear()
        self.assertEqual(cache.size(), 0)

    def test_cache_stats(self):
        from gateway.cache import ResponseCache

        cache = ResponseCache(max_size=50, ttl_seconds=1800)
        stats = cache.stats()
        self.assertEqual(stats["size"], 0)
        self.assertEqual(stats["max_size"], 50)
        self.assertEqual(stats["ttl_seconds"], 1800)

    def test_cache_different_requests_different_keys(self):
        from gateway.cache import ResponseCache

        cache = ResponseCache(max_size=10, ttl_seconds=3600)
        req1 = {"messages": [{"role": "user", "content": "Hello"}], "passthrough": {}, "governance": {}}
        req2 = {"messages": [{"role": "user", "content": "World"}], "passthrough": {}, "governance": {}}

        cache.put(req1, {"epr": {}, "data": "response1"})
        self.assertIsNone(cache.get(req2))
        self.assertIsNotNone(cache.get(req1))

    def test_cache_hit_flag_in_epr_metadata(self):
        """cache_hit field is present in epr metadata."""
        from gateway.render import render_epr_metadata

        epr = render_epr_metadata(
            run_id="run-1",
            trace_id="trace-1",
            status="accepted",
            route_decision_id="dec-1",
            external_base_url="http://localhost:8080",
            ledger_head_hash="sha256:abc",
            total_cost_usd=0.0,
            cache_hit=True,
        )
        self.assertTrue(epr.get("cache_hit"))

        epr_no_cache = render_epr_metadata(
            run_id="run-2",
            trace_id="trace-2",
            status="accepted",
            route_decision_id="dec-2",
            external_base_url="http://localhost:8080",
            ledger_head_hash="sha256:def",
            total_cost_usd=0.0,
        )
        self.assertFalse(epr_no_cache.get("cache_hit"))

    def test_pipeline_cache_integration(self):
        """Pipeline with cache enabled returns cached response on second call."""
        from gateway.cache import ResponseCache

        cache = ResponseCache(max_size=10, ttl_seconds=3600)
        ctx = _make_ctx()
        ctx.response_cache = cache

        request = {
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": "Hello"}],
        }

        # First call — should go through pipeline.
        result1 = run_inference_pipeline(request, ctx)
        self.assertEqual(result1["epr"]["status"], "accepted")
        self.assertFalse(result1["epr"].get("cache_hit"))

        # Second call with same request — should hit cache.
        result2 = run_inference_pipeline(request, ctx)
        self.assertTrue(result2["epr"].get("cache_hit"))


# ---------------------------------------------------------------------------
# DynamicDiscoveryTests
# ---------------------------------------------------------------------------


class DynamicDiscoveryTests(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_discover_local_models_returns_list(self, mock_urlopen):
        from gateway.local_portfolio import discover_local_models

        # Mock a successful response with models.
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "data": [{"id": "qwen3:8b"}, {"id": "qwen3-coder:30b"}],
        }).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=None)
        mock_urlopen.return_value = mock_resp

        result = discover_local_models("http://127.0.0.1:11434", timeout=1)
        self.assertIsInstance(result, list)
        self.assertEqual(result, ["qwen3:8b", "qwen3-coder:30b"])

    @patch("urllib.request.urlopen")
    def test_discover_local_models_with_default_url(self, mock_urlopen):
        from gateway.local_portfolio import discover_local_models

        # Mock a successful response with models.
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "data": [{"id": "llama3:8b"}],
        }).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=None)
        mock_urlopen.return_value = mock_resp

        result = discover_local_models(timeout=1)
        self.assertIsInstance(result, list)
        self.assertEqual(result, ["llama3:8b"])

    @patch("urllib.request.urlopen")
    def test_discover_local_models_returns_empty_on_error(self, mock_urlopen):
        from gateway.local_portfolio import discover_local_models

        # Simulate connection error.
        mock_urlopen.side_effect = OSError("Connection refused")

        result = discover_local_models("http://127.0.0.1:19999", timeout=1)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 0)

    @patch("urllib.request.urlopen")
    def test_discover_local_models_returns_empty_on_timeout(self, mock_urlopen):
        from gateway.local_portfolio import discover_local_models

        import socket
        mock_urlopen.side_effect = socket.timeout("timed out")

        result = discover_local_models(timeout=1)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 0)

    def test_local_candidates_filtered_by_discovery(self):
        """local_candidates returns kernel-compatible entries."""
        from gateway.local_portfolio import local_candidates

        candidates = local_candidates(data_policy="zdr")
        self.assertIsInstance(candidates, list)
        for c in candidates:
            self.assertIn("model_id", c)
            self.assertIn("inference_gateway", c)
            self.assertEqual(c["inference_gateway"], "local")

    def test_handle_list_local_models(self):
        """handle_list_local_models returns a valid response."""
        from gateway.handlers import handle_list_local_models

        ctx = _make_ctx()
        status, body = handle_list_local_models(ctx)
        self.assertEqual(status, 200)
        self.assertEqual(body["object"], "list")
        self.assertIsInstance(body["data"], list)

    def test_handle_cache_stats_disabled(self):
        """handle_cache_stats returns disabled when no cache."""
        from gateway.handlers import handle_cache_stats

        ctx = _make_ctx()
        status, body = handle_cache_stats(ctx)
        self.assertEqual(status, 200)
        self.assertFalse(body.get("cache_enabled", True))

    def test_handle_cache_stats_enabled(self):
        """handle_cache_stats returns stats when cache is enabled."""
        from gateway.cache import ResponseCache
        from gateway.handlers import handle_cache_stats

        ctx = _make_ctx()
        ctx.response_cache = ResponseCache(max_size=50, ttl_seconds=1800)
        status, body = handle_cache_stats(ctx)
        self.assertEqual(status, 200)
        self.assertEqual(body["max_size"], 50)
        self.assertEqual(body["ttl_seconds"], 1800)
        self.assertEqual(body["size"], 0)


if __name__ == "__main__":
    unittest.main()
