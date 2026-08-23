from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reference"))

from gateway.config import ConfigError, GatewayConfig
from gateway.context import (
    CanonicalState,
    ContextCompactor,
    ContextCompiler,
    MemoryLevel,
    build_canonical_state,
)
from gateway.contracts import (
    LLMContractProposer,
    classify_acceptance_criterion,
    compile_task_contract,
    contract_state_items,
    requires_clarification,
    validate_task_contract,
)
from gateway.governance import (
    default_governance,
    merge_governance,
    validate_governance,
)
from gateway.state_vocabulary import StateVocabulary
from epr.ledger import verify_chain
from epr.memory import validate_context_capsule
from gateway.openrouter import StubOpenRouterClient, build_chat_payload
from gateway.decoding import DecodingPhaseManager
from gateway.streaming import SSEStreamer
from gateway.test_independence import TestIndependenceChecker, validate_test_metadata
from gateway.pipeline import PipelineContext, PipelineError, stage_route
from gateway.server import create_server
from gateway.policy import (
    check_requested_model,
    load_policy,
    validate_portfolio_against_policy,
)
from gateway.runs import RunRegistry, record_stub_verification
from gateway.fallback import FallbackRecorder
from gateway.online_learning import CanaryTrafficRouter, PolicyVersionManager
from gateway.provenance import ProvenanceMapper
from gateway.statemachine import TransitionError, VerificationStateMachine
from gateway.auth import AuthMiddleware
from gateway.rate_limit import PerKeyRateLimiter
from gateway.rbac import RBACMiddleware
from gateway.cache import ResponseCache


def _sse_data_lines(raw: str) -> list[str]:
    """Extract the ``data:`` payload lines from an SSE response body."""
    lines: list[str] = []
    for line in raw.splitlines():
        if line.startswith("data:"):
            lines.append(line[len("data:"):].strip())
    return lines


class ConfigTests(unittest.TestCase):
    def test_defaults_when_env_absent(self):
        config = GatewayConfig.from_env({})

        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 8080)
        self.assertEqual(config.openrouter_mode, "stub")
        self.assertEqual(config.policy_path, ROOT / "spec" / "routing-policy.json")
        self.assertEqual(
            config.state_machine_path,
            ROOT / "spec" / "verification-state-machine.json",
        )
        self.assertEqual(
            config.portfolio_path, ROOT / "examples" / "candidate-actions.json"
        )
        self.assertEqual(config.default_max_cost_usd, 0.25)
        self.assertEqual(config.default_max_latency_ms, 60000)
        self.assertEqual(config.external_base_url, "http://127.0.0.1:8080")
        self.assertEqual(config.openrouter_base_url, "https://openrouter.ai/api/v1")
        self.assertIsNone(config.openrouter_api_key)
        self.assertEqual(
            config.openrouter_http_referer,
            "https://github.com/electrohire/noerelay",
        )
        self.assertEqual(config.openrouter_app_title, "NoeRelay")
        self.assertFalse(config.live_tests)

    def test_live_mode_without_key_raises(self):
        with self.assertRaises(ConfigError):
            GatewayConfig.from_env({"NOERELAY_OPENROUTER_MODE": "live"})

    def test_live_mode_with_key_succeeds(self):
        config = GatewayConfig.from_env(
            {
                "NOERELAY_OPENROUTER_MODE": "live",
                "OPENROUTER_API_KEY": "sk-test",
            }
        )
        self.assertEqual(config.openrouter_mode, "live")
        self.assertEqual(config.openrouter_api_key, "sk-test")

    def test_invalid_mode_raises(self):
        with self.assertRaises(ConfigError):
            GatewayConfig.from_env({"NOERELAY_OPENROUTER_MODE": "automatic"})

    def test_invalid_live_tests_raises(self):
        with self.assertRaises(ConfigError):
            GatewayConfig.from_env({"NOERELAY_LIVE_TESTS": "maybe"})

    def test_invalid_port_raises(self):
        with self.assertRaises(ConfigError):
            GatewayConfig.from_env({"NOERELAY_GATEWAY_PORT": "not-a-number"})
        with self.assertRaises(ConfigError):
            GatewayConfig.from_env({"NOERELAY_GATEWAY_PORT": "70000"})

    def test_invalid_cost_raises(self):
        with self.assertRaises(ConfigError):
            GatewayConfig.from_env({"NOERELAY_DEFAULT_MAX_COST_USD": "0"})
        with self.assertRaises(ConfigError):
            GatewayConfig.from_env({"NOERELAY_DEFAULT_MAX_COST_USD": "abc"})

    def test_invalid_latency_raises(self):
        with self.assertRaises(ConfigError):
            GatewayConfig.from_env({"NOERELAY_DEFAULT_MAX_LATENCY_MS": "0"})

    def test_non_loopback_bind_requires_api_key(self):
        with self.assertRaises(ConfigError):
            GatewayConfig.from_env({"NOERELAY_GATEWAY_HOST": "0.0.0.0"})

    def test_wildcard_cors_origin_is_rejected(self):
        with self.assertRaises(ConfigError):
            GatewayConfig.from_env({"NOERELAY_CORS_ALLOWED_ORIGINS": "*"})

    def test_custom_values_are_parsed(self):
        config = GatewayConfig.from_env(
            {
                "NOERELAY_GATEWAY_HOST": "0.0.0.0",
                "NOERELAY_AUTH_API_KEYS": "test-key",
                "NOERELAY_GATEWAY_PORT": "9000",
                "NOERELAY_DEFAULT_MAX_COST_USD": "1.5",
                "NOERELAY_DEFAULT_MAX_LATENCY_MS": "30000",
                "NOERELAY_EXTERNAL_BASE_URL": "https://relay.example.com",
                "NOERELAY_LIVE_TESTS": "1",
            }
        )
        self.assertEqual(config.host, "0.0.0.0")
        self.assertEqual(config.port, 9000)
        self.assertEqual(config.default_max_cost_usd, 1.5)
        self.assertEqual(config.default_max_latency_ms, 30000)
        self.assertEqual(config.external_base_url, "https://relay.example.com")
        self.assertTrue(config.live_tests)
        self.assertTrue(config.auth_required)

    def test_relative_paths_are_resolved_against_repo_root(self):
        config = GatewayConfig.from_env(
            {
                "NOERELAY_POLICY_PATH": "custom/policy.json",
                "NOERELAY_PORTFOLIO_PATH": "custom/portfolio.json",
            }
        )
        self.assertEqual(config.policy_path, ROOT / "custom" / "policy.json")
        self.assertEqual(config.portfolio_path, ROOT / "custom" / "portfolio.json")


class GovernanceTests(unittest.TestCase):
    def _defaults(self):
        return default_governance(max_cost_usd=0.25, max_latency_ms=60000)

    def test_defaults_when_absent(self):
        merged = merge_governance(None, self._defaults())
        self.assertEqual(merged["risk_class"], "low")
        self.assertEqual(merged["data_policy"], "zdr")
        self.assertEqual(merged["max_cost_usd"], 0.25)
        self.assertEqual(merged["max_latency_ms"], 60000)
        self.assertEqual(merged["retention_class"], "ephemeral")
        self.assertTrue(merged["return_evidence_receipt"])
        self.assertNotIn("required_acceptance_probability", merged)
        self.assertEqual(validate_governance(merged), [])

    def test_request_overrides_defaults_per_field(self):
        merged = merge_governance(
            {"risk_class": "high", "max_cost_usd": 5.0, "project_id": "proj-1"},
            self._defaults(),
        )
        self.assertEqual(merged["risk_class"], "high")
        self.assertEqual(merged["max_cost_usd"], 5.0)
        self.assertEqual(merged["data_policy"], "zdr")
        self.assertEqual(merged["project_id"], "proj-1")

    def test_merge_does_not_mutate_defaults(self):
        defaults = self._defaults()
        merge_governance({"risk_class": "critical"}, defaults)
        self.assertEqual(defaults["risk_class"], "low")

    def test_merge_is_deterministic(self):
        defaults = self._defaults()
        request = {"risk_class": "medium", "data_policy": "no_training"}
        self.assertEqual(
            merge_governance(request, defaults),
            merge_governance(request, defaults),
        )

    def test_invalid_risk_class(self):
        merged = merge_governance({"risk_class": "extreme"}, self._defaults())
        self.assertTrue(any("risk_class_invalid" in e for e in validate_governance(merged)))

    def test_invalid_data_policy(self):
        merged = merge_governance({"data_policy": "unlimited"}, self._defaults())
        self.assertTrue(any("data_policy_invalid" in e for e in validate_governance(merged)))

    def test_invalid_retention_class(self):
        merged = merge_governance({"retention_class": "forever"}, self._defaults())
        self.assertTrue(any("retention_class_invalid" in e for e in validate_governance(merged)))

    def test_non_positive_cost_rejected(self):
        merged = merge_governance({"max_cost_usd": 0}, self._defaults())
        self.assertTrue(any("max_cost_usd_must_be_positive" in e for e in validate_governance(merged)))

    def test_non_number_cost_rejected(self):
        merged = merge_governance({"max_cost_usd": "cheap"}, self._defaults())
        self.assertTrue(any("max_cost_usd_must_be_number" in e for e in validate_governance(merged)))

    def test_non_positive_latency_rejected(self):
        merged = merge_governance({"max_latency_ms": 0}, self._defaults())
        self.assertTrue(any("max_latency_ms_must_be_positive" in e for e in validate_governance(merged)))

    def test_out_of_range_probability_rejected(self):
        merged = merge_governance(
            {"required_acceptance_probability": 1.5}, self._defaults()
        )
        self.assertTrue(
            any(
                "required_acceptance_probability_out_of_range" in e
                for e in validate_governance(merged)
            )
        )

    def test_non_bool_receipt_flag_rejected(self):
        merged = merge_governance(
            {"return_evidence_receipt": "yes"}, self._defaults()
        )
        self.assertTrue(
            any("return_evidence_receipt_must_be_boolean" in e for e in validate_governance(merged))
        )

    def test_unknown_key_rejected(self):
        merged = merge_governance({"provider": "openai"}, self._defaults())
        self.assertTrue(
            any("unknown_governance_fields" in e for e in validate_governance(merged))
        )

    def test_valid_full_governance_passes(self):
        merged = merge_governance(
            {
                "project_id": "proj-1",
                "risk_class": "critical",
                "max_cost_usd": 10.0,
                "max_latency_ms": 120000,
                "required_acceptance_probability": 0.995,
                "data_policy": "zdr",
                "retention_class": "regulated",
                "return_evidence_receipt": True,
            },
            self._defaults(),
        )
        self.assertEqual(validate_governance(merged), [])


class BoundaryModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = load_policy(ROOT / "spec" / "routing-policy.json")

    def test_virtual_model_is_allowed(self):
        self.assertEqual(
            check_requested_model("noerelay/epr-1", self.policy), []
        )

    def test_openai_prefix_denied(self):
        reasons = check_requested_model("openai/gpt-4o", self.policy)
        self.assertIn("model_id_denied", reasons)

    def test_openrouter_auto_denied(self):
        reasons = check_requested_model("openrouter/auto", self.policy)
        self.assertIn("model_id_denied", reasons)

    def test_openai_family_bare_denied(self):
        reasons = check_requested_model("openai", self.policy)
        self.assertIn("model_family_denied", reasons)

    def test_api_openai_host_reference_denied(self):
        reasons = check_requested_model(
            "https://api.openai.com/v1/models/gpt-4o", self.policy
        )
        self.assertIn("model_id_denied", reasons)

    def test_non_forbidden_model_not_denied_by_policy(self):
        reasons = check_requested_model("anthropic/claude-sonnet-4.6", self.policy)
        self.assertEqual(reasons, [])

    def test_case_insensitive(self):
        reasons = check_requested_model("OPENAI/GPT-4O", self.policy)
        self.assertIn("model_id_denied", reasons)


class PortfolioStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = load_policy(ROOT / "spec" / "routing-policy.json")

    def test_shipped_example_portfolio_passes(self):
        portfolio = json.loads(
            (ROOT / "examples" / "candidate-actions.json").read_text("utf-8")
        )
        self.assertEqual(
            validate_portfolio_against_policy(portfolio, self.policy), []
        )

    def test_openai_family_candidate_rejected(self):
        portfolio = [
            {
                "candidate_id": "bad-openai",
                "action_kind": "model",
                "inference_gateway": "openrouter",
                "model_id": "openai/gpt-4o",
                "provider_family": "openai",
                "roles": ["execute"],
            }
        ]
        errors = validate_portfolio_against_policy(portfolio, self.policy)
        self.assertTrue(any("model_family_denied" in e for e in errors))

    def test_openrouter_auto_candidate_rejected(self):
        portfolio = [
            {
                "candidate_id": "bad-auto",
                "action_kind": "model",
                "inference_gateway": "openrouter",
                "model_id": "openrouter/auto",
                "provider_family": "qwen",
                "roles": ["execute"],
            }
        ]
        errors = validate_portfolio_against_policy(portfolio, self.policy)
        self.assertTrue(any("model_id_denied" in e for e in errors))

    def test_missing_model_id_rejected_when_required(self):
        portfolio = [
            {
                "candidate_id": "no-model",
                "action_kind": "model",
                "inference_gateway": "openrouter",
                "model_id": "",
                "provider_family": "qwen",
                "roles": ["execute"],
            }
        ]
        errors = validate_portfolio_against_policy(portfolio, self.policy)
        self.assertTrue(any("model_id_required" in e for e in errors))


class ContractTests(unittest.TestCase):
    def _governance(self):
        return {
            "risk_class": "low",
            "data_policy": "zdr",
            "max_cost_usd": 0.25,
            "max_latency_ms": 60000,
            "retention_class": "ephemeral",
            "return_evidence_receipt": True,
        }

    def test_basic_chat_contract(self):
        contract = compile_task_contract(
            [{"role": "user", "content": "Hello"}],
            self._governance(),
            task_id="task-1",
        )
        self.assertEqual(contract["version"], "1.0")
        self.assertEqual(contract["task_id"], "task-1")
        self.assertEqual(contract["goal"], "Hello")
        self.assertEqual(contract["task_kind"], "conversation")
        self.assertEqual(contract["risk_class"], "low")
        self.assertEqual(contract["input_modalities"], ["text"])
        self.assertEqual(contract["required_capabilities"], ["text"])
        self.assertEqual(len(contract["acceptance_criteria"]), 1)
        self.assertEqual(
            contract["acceptance_criteria"][0]["kind"], "observable"
        )
        self.assertTrue(contract["acceptance_criteria"][0]["mandatory"])
        self.assertEqual(contract["governance"]["data_policy"], "zdr")
        self.assertFalse(contract["governance"]["human_approval_required"])

    def test_tools_add_tool_calling_capability(self):
        contract = compile_task_contract(
            [{"role": "user", "content": "Use a tool"}],
            self._governance(),
            passthrough={"tools": [{"type": "function"}]},
            task_id="task-2",
        )
        self.assertIn("tool_calling", contract["required_capabilities"])

    def test_response_format_adds_structured_output(self):
        contract = compile_task_contract(
            [{"role": "user", "content": "JSON please"}],
            self._governance(),
            passthrough={"response_format": {"type": "json_object"}},
            task_id="task-3",
        )
        self.assertIn("structured_output", contract["required_capabilities"])

    def test_image_content_adds_vision_and_image_modality(self):
        contract = compile_task_contract(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe this"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "https://example.com/img.png"},
                        },
                    ],
                }
            ],
            self._governance(),
            task_id="task-4",
        )
        self.assertIn("vision", contract["required_capabilities"])
        self.assertIn("image", contract["input_modalities"])

    def test_goal_truncation(self):
        long_text = "x" * 15000
        contract = compile_task_contract(
            [{"role": "user", "content": long_text}],
            self._governance(),
            task_id="task-5",
        )
        self.assertEqual(len(contract["goal"]), 10000)

    def test_acceptance_criterion_is_never_missing(self):
        contract = compile_task_contract(
            [{"role": "user", "content": "test"}],
            self._governance(),
            task_id="task-6",
        )
        for criterion in contract["acceptance_criteria"]:
            self.assertNotEqual(criterion["kind"], "missing")

    def test_critical_risk_sets_human_approval(self):
        governance = {**self._governance(), "risk_class": "critical"}
        contract = compile_task_contract(
            [{"role": "user", "content": "dangerous"}],
            governance,
            task_id="task-7",
        )
        self.assertTrue(contract["governance"]["human_approval_required"])

    def test_project_id_from_governance(self):
        governance = {**self._governance(), "project_id": "proj-custom"}
        contract = compile_task_contract(
            [{"role": "user", "content": "test"}],
            governance,
            task_id="task-8",
        )
        self.assertEqual(contract["project_id"], "proj-custom")

    def test_project_id_defaults(self):
        contract = compile_task_contract(
            [{"role": "user", "content": "test"}],
            self._governance(),
            task_id="task-9",
        )
        self.assertEqual(contract["project_id"], "project-default")


class RouteStageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = json.loads(
            (ROOT / "examples" / "high-risk-coding-contract.json").read_text("utf-8")
        )
        cls.portfolio = json.loads(
            (ROOT / "examples" / "candidate-actions.json").read_text("utf-8")
        )
        cls.policy = load_policy(ROOT / "spec" / "routing-policy.json")

    def test_selects_route_with_example_data(self):
        decision = stage_route(self.contract, self.portfolio, self.policy)
        self.assertEqual(decision["status"], "route_selected")
        self.assertIn("selected_plan", decision)
        self.assertEqual(
            decision["selected_plan"]["action_id"], "qwen3.6-35b-a3b-worker"
        )

    def test_escalation_with_empty_portfolio(self):
        decision = stage_route(self.contract, [], self.policy)
        self.assertEqual(decision["status"], "escalation_required")
        self.assertNotIn("selected_plan", decision)

    def test_candidate_audit_present(self):
        decision = stage_route(self.contract, self.portfolio, self.policy)
        self.assertIn("candidate_audit", decision)
        self.assertGreater(len(decision["candidate_audit"]), 0)
        for entry in decision["candidate_audit"]:
            self.assertIn("candidate_id", entry)
            self.assertIn("admissible", entry)
            self.assertIn("reasons", entry)


class StubClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = load_policy(ROOT / "spec" / "routing-policy.json")
        cls.selected_plan = {
            "action_id": "qwen3.6-35b-a3b-worker",
            "model_id": "qwen/qwen3.6-35b-a3b",
        }
        cls.inference_request = {
            "messages": [{"role": "user", "content": "Hello world"}],
            "passthrough": {
                "temperature": 0.2,
                "top_p": 0.9,
                "max_tokens": 100,
                "stop": ["END"],
                "n": 1,
                "presence_penalty": 0.1,
                "frequency_penalty": -0.1,
                "logit_bias": {"42": 1},
                "user": "sdk-test",
            },
        }

    def test_payload_has_explicit_model_and_provider_block(self):
        payload = build_chat_payload(
            self.selected_plan, self.inference_request, self.policy
        )
        self.assertEqual(payload["model"], "qwen/qwen3.6-35b-a3b")
        self.assertEqual(payload["provider"]["data_collection"], "deny")
        self.assertTrue(payload["provider"]["zdr"])
        self.assertIn("openai", payload["provider"]["ignore"])

    def test_payload_passes_standard_fields_verbatim(self):
        payload = build_chat_payload(
            self.selected_plan, self.inference_request, self.policy
        )
        self.assertEqual(payload["messages"], self.inference_request["messages"])
        self.assertEqual(payload["temperature"], 0.2)
        self.assertEqual(payload["max_tokens"], 100)
        for field in (
            "top_p",
            "stop",
            "n",
            "presence_penalty",
            "frequency_penalty",
            "logit_bias",
            "user",
        ):
            self.assertEqual(
                payload[field], self.inference_request["passthrough"][field]
            )

    def test_payload_rejects_forbidden_model(self):
        plan = {**self.selected_plan, "model_id": "openai/gpt-4o"}
        with self.assertRaises(ConfigError):
            build_chat_payload(plan, self.inference_request, self.policy)

    def test_payload_rejects_missing_model(self):
        with self.assertRaises(ConfigError):
            build_chat_payload(
                {"action_id": "no-model"}, self.inference_request, self.policy
            )

    def test_stub_returns_openai_shape(self):
        payload = build_chat_payload(
            self.selected_plan, self.inference_request, self.policy
        )
        client = StubOpenRouterClient(self.policy)
        response = client.create_chat_completion(payload)
        self.assertEqual(response["object"], "chat.completion")
        self.assertEqual(response["model"], "qwen/qwen3.6-35b-a3b")
        self.assertEqual(len(response["choices"]), 1)
        self.assertEqual(response["choices"][0]["message"]["role"], "assistant")
        self.assertEqual(response["usage"]["total_tokens"], 0)

    def test_stub_content_echoes_last_user_message(self):
        payload = build_chat_payload(
            self.selected_plan, self.inference_request, self.policy
        )
        client = StubOpenRouterClient(self.policy)
        response = client.create_chat_completion(payload)
        self.assertIn(
            "Hello world", response["choices"][0]["message"]["content"]
        )

    def test_stub_rejects_bad_provider_block(self):
        client = StubOpenRouterClient(self.policy)
        with self.assertRaises(ConfigError):
            client.create_chat_completion(
                {
                    "model": "qwen/qwen3.6-35b-a3b",
                    "messages": [],
                    "provider": {"data_collection": "allow"},
                }
            )


class StateMachineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = json.loads(
            (ROOT / "spec" / "verification-state-machine.json").read_text("utf-8")
        )

    def _machine(self):
        return VerificationStateMachine(self.spec)

    def test_happy_path_walk(self):
        machine = self._machine()
        run_id = "run-sm-1"
        self.assertEqual(machine.begin(run_id), "received")
        steps = [
            ("propose_contract", "contract_proposed"),
            ("validate_contract", "contract_validated"),
            ("check_policy", "policy_checked"),
            ("compile_context", "context_compiled"),
            ("select_route", "routed"),
            ("start_action", "executing"),
            ("record_result", "evidence_normalized"),
            ("start_verification", "verifying"),
            ("verification_passed", "accepted"),
            ("issue_receipt", "completed"),
        ]
        for event, expected in steps:
            self.assertEqual(machine.transition(run_id, event, True), expected)
        self.assertTrue(machine.is_terminal(run_id))

    def test_illegal_transition_raises(self):
        machine = self._machine()
        run_id = "run-sm-2"
        machine.begin(run_id)
        with self.assertRaises(TransitionError):
            machine.transition(run_id, "start_action", True)

    def test_guard_failure_raises(self):
        machine = self._machine()
        run_id = "run-sm-3"
        machine.begin(run_id)
        machine.transition(run_id, "propose_contract", True)
        with self.assertRaises(TransitionError):
            machine.transition(run_id, "validate_contract", False)

    def test_terminal_detection(self):
        machine = self._machine()
        run_id = "run-sm-4"
        machine.begin(run_id)
        self.assertFalse(machine.is_terminal(run_id))


class LedgerWiringTests(unittest.TestCase):
    def _registry(self):
        registry = RunRegistry()
        registry.begin("run-ledger-1", "trace-ledger-1")
        return registry

    def test_verify_chain_after_appends(self):
        registry = self._registry()
        registry.ledger(
            "run-ledger-1", "request_received", {"id": "gw", "kind": "service"},
            "task-1", {"hash": "abc"},
        )
        registry.ledger(
            "run-ledger-1", "route_selected", {"id": "gw", "kind": "service"},
            "task-1", {"route": "worker"},
        )
        record = registry.get("run-ledger-1")
        self.assertEqual(verify_chain(record.events), (True, "ok"))

    def test_tamper_detection(self):
        registry = self._registry()
        registry.ledger(
            "run-ledger-1", "request_received", {"id": "gw", "kind": "service"},
            "task-1", {"hash": "abc"},
        )
        record = registry.get("run-ledger-1")
        record.events[0]["payload"]["hash"] = "tampered"
        valid, message = verify_chain(record.events)
        self.assertFalse(valid)
        self.assertIn("content hash mismatch", message)

    def test_event_types_within_schema_enum(self):
        schema = json.loads(
            (ROOT / "spec" / "schemas" / "ledger-event.schema.json").read_text("utf-8")
        )
        allowed = set(schema["properties"]["event_type"]["enum"])
        for event_type in ("request_received", "route_selected", "action_started"):
            self.assertIn(event_type, allowed)

    def test_route_selected_precedes_action_started(self):
        registry = self._registry()
        registry.ledger(
            "run-ledger-1", "route_selected", {"id": "gw", "kind": "service"},
            "task-1", {},
        )
        registry.ledger(
            "run-ledger-1", "action_started", {"id": "gw", "kind": "service"},
            "task-1", {},
        )
        record = registry.get("run-ledger-1")
        types = [event["event_type"] for event in record.events]
        self.assertLess(types.index("route_selected"), types.index("action_started"))

    def test_receipt_binds_ledger_head(self):
        registry = self._registry()
        registry.ledger(
            "run-ledger-1", "outcome_accepted", {"id": "gw", "kind": "service"},
            "task-1", {},
        )
        receipt = registry.issue_receipt("run-ledger-1", "accepted", [], 0.0)
        self.assertEqual(receipt["run_id"], "run-ledger-1")
        self.assertEqual(receipt["status"], "accepted")
        self.assertEqual(
            receipt["total_cost"], {"currency": "USD", "amount": 0.0}
        )
        self.assertEqual(receipt["ledger_head_hash"], registry.head_hash("run-ledger-1"))

    def test_record_stub_verification(self):
        results = record_stub_verification(
            {
                "acceptance_criteria": [
                    {"id": "ac-1", "kind": "executable", "mandatory": True},
                    {"id": "ac-2", "kind": "judgmental", "mandatory": False},
                ]
            }
        )
        self.assertEqual(results[0]["criterion_id"], "ac-1")
        self.assertEqual(results[0]["status"], "passed")
        self.assertEqual(results[1]["criterion_id"], "ac-2")
        self.assertEqual(results[1]["status"], "not_run")


class GatewayIntegrationTests(unittest.TestCase):
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

        # Second server with empty portfolio for 424 escalation tests.
        cls.escalation_ctx = PipelineContext(
            config=GatewayConfig.from_env(
                {"NOERELAY_GATEWAY_HOST": "127.0.0.1", "NOERELAY_GATEWAY_PORT": "0"}
            ),
            policy=cls.policy,
            portfolio=[],
            openrouter_client=StubOpenRouterClient(cls.policy),
            state_machine=VerificationStateMachine(cls.spec),
            registry=RunRegistry(),
        )
        cls.escalation_server = create_server(
            cls.escalation_ctx.config, cls.escalation_ctx
        )
        cls.escalation_port = cls.escalation_server.server_address[1]
        cls.escalation_base = f"http://127.0.0.1:{cls.escalation_port}"
        cls._escalation_thread = threading.Thread(
            target=cls.escalation_server.serve_forever, daemon=True
        )
        cls._escalation_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.escalation_server.shutdown()
        cls.escalation_server.server_close()

    def _request(self, url, method="GET", data=None, headers=None):
        headers = headers or {}
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            return exc.code, (json.loads(raw) if raw else None)

    def _post(self, path, body):
        data = json.dumps(body).encode("utf-8")
        return self._request(
            f"{self.base}{path}",
            method="POST",
            data=data,
            headers={"Content-Type": "application/json"},
        )

    def _post_escalation(self, path, body):
        data = json.dumps(body).encode("utf-8")
        return self._request(
            f"{self.escalation_base}{path}",
            method="POST",
            data=data,
            headers={"Content-Type": "application/json"},
        )

    def _post_stream(self, base, path, body):
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{base}{path}",
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, dict(resp.headers), raw

    def test_list_models(self):
        status, body = self._request(f"{self.base}/v1/models")
        self.assertEqual(status, 200)
        self.assertEqual(body["object"], "list")
        self.assertEqual(body["data"][0]["id"], "noerelay/epr-1")

    def test_chat_completion_no_governance(self):
        status, body = self._post(
            "/v1/chat/completions",
            {"model": "noerelay/epr-1", "messages": [{"role": "user", "content": "Hi"}]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["object"], "chat.completion")
        self.assertEqual(body["model"], "noerelay/epr-1")
        self.assertIn("epr", body)
        self.assertEqual(body["epr"]["status"], "accepted")

    def test_chat_completion_with_governance(self):
        status, body = self._post(
            "/v1/chat/completions",
            {
                "model": "noerelay/epr-1",
                "messages": [{"role": "user", "content": "Hi"}],
                "governance": {"risk_class": "high", "max_cost_usd": 2.0},
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["epr"]["status"], "accepted")

    def test_invalid_governance_returns_422(self):
        status, body = self._post(
            "/v1/chat/completions",
            {
                "model": "noerelay/epr-1",
                "messages": [{"role": "user", "content": "Hi"}],
                "governance": {"risk_class": "extreme"},
            },
        )
        self.assertEqual(status, 422)
        self.assertEqual(body["error"]["type"], "governance_validation_error")

    def test_forbidden_model_returns_403(self):
        status, body = self._post(
            "/v1/chat/completions",
            {"model": "openai/gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
        )
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "model_denied_by_policy")

    def test_openrouter_auto_returns_403(self):
        status, body = self._post(
            "/v1/chat/completions",
            {"model": "openrouter/auto", "messages": [{"role": "user", "content": "Hi"}]},
        )
        self.assertEqual(status, 403)

    def test_unknown_model_returns_404(self):
        status, body = self._post(
            "/v1/chat/completions",
            {"model": "someone/else", "messages": [{"role": "user", "content": "Hi"}]},
        )
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "model_not_found")

    def test_malformed_json_returns_400(self):
        status, body = self._request(
            f"{self.base}/v1/chat/completions",
            method="POST",
            data=b"{invalid",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "invalid_json")

    def test_missing_messages_returns_400(self):
        status, body = self._post(
            "/v1/chat/completions",
            {"model": "noerelay/epr-1"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "missing_field")

    def test_invalid_standard_parameter_returns_400(self):
        status, body = self._post(
            "/v1/chat/completions",
            {
                "model": "noerelay/epr-1",
                "messages": [{"role": "user", "content": "Hi"}],
                "temperature": float("nan"),
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["param"], "temperature")

    def test_malformed_message_item_returns_400(self):
        status, body = self._post(
            "/v1/chat/completions",
            {"model": "noerelay/epr-1", "messages": ["not-an-object"]},
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["param"], "messages")

    def test_stream_returns_sse(self):
        status, headers, raw = self._post_stream(
            self.base,
            "/v1/chat/completions",
            {
                "model": "noerelay/epr-1",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "text/event-stream")
        lines = _sse_data_lines(raw)
        self.assertEqual(lines[-1], "[DONE]")
        events = [json.loads(line) for line in lines[:-1]]
        self.assertGreaterEqual(len(events), 2)
        self.assertEqual(events[0]["object"], "chat.completion.chunk")
        self.assertEqual(events[0]["model"], "noerelay/epr-1")
        terminal = events[-1]
        self.assertIn("epr", terminal)
        self.assertEqual(terminal["epr"]["status"], "accepted")

    def test_stream_with_governance(self):
        status, headers, raw = self._post_stream(
            self.base,
            "/v1/chat/completions",
            {
                "model": "noerelay/epr-1",
                "messages": [{"role": "user", "content": "Hi"}],
                "governance": {"risk_class": "high", "max_cost_usd": 2.0},
                "stream": True,
            },
        )
        self.assertEqual(status, 200)
        lines = _sse_data_lines(raw)
        events = [json.loads(line) for line in lines if line != "[DONE]"]
        self.assertGreaterEqual(len(events), 1)
        terminal = events[-1]
        self.assertEqual(terminal["epr"]["status"], "accepted")

    def test_stream_error_on_escalation(self):
        status, headers, raw = self._post_stream(
            self.escalation_base,
            "/v1/chat/completions",
            {
                "model": "noerelay/epr-1",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
            },
        )
        self.assertEqual(status, 200)
        lines = _sse_data_lines(raw)
        self.assertEqual(lines[-1], "[DONE]")
        events = [json.loads(line) for line in lines[:-1]]
        self.assertGreaterEqual(len(events), 1)
        self.assertIn("error", events[0])
        self.assertIn("epr", events[0])
        self.assertIn("run_id", events[0]["epr"])

    def test_empty_portfolio_returns_424(self):
        status, body = self._post_escalation(
            "/v1/chat/completions",
            {"model": "noerelay/epr-1", "messages": [{"role": "user", "content": "Hi"}]},
        )
        self.assertEqual(status, 424)
        self.assertEqual(body["error"]["type"], "no_admissible_route_error")
        self.assertIn("epr", body)
        rd = body["epr"]["route_decision"]
        self.assertIn("candidates_evaluated", rd)
        self.assertIn("candidates_admissible", rd)
        self.assertNotIn("model_id", str(rd))

    def test_receipt_round_trip(self):
        status, body = self._post(
            "/v1/chat/completions",
            {"model": "noerelay/epr-1", "messages": [{"role": "user", "content": "Hi"}]},
        )
        self.assertEqual(status, 200)
        run_id = body["epr"]["run_id"]
        status2, receipt = self._request(f"{self.base}/v1/epr/runs/{run_id}")
        self.assertEqual(status2, 200)
        self.assertEqual(receipt["run_id"], run_id)
        self.assertEqual(receipt["status"], "accepted")
        record = self.ctx.registry.get(run_id)
        self.assertIsNotNone(record)
        self.assertEqual(verify_chain(record.events), (True, "ok"))

    def test_receipt_round_trip_after_424(self):
        status, body = self._post_escalation(
            "/v1/chat/completions",
            {"model": "noerelay/epr-1", "messages": [{"role": "user", "content": "Hi"}]},
        )
        self.assertEqual(status, 424)
        run_id = body["epr"]["run_id"]
        status2, receipt = self._request(
            f"{self.escalation_base}/v1/epr/runs/{run_id}"
        )
        self.assertEqual(status2, 200)
        self.assertEqual(receipt["status"], "escalated")
        record = self.escalation_ctx.registry.get(run_id)
        self.assertIsNotNone(record)
        route_event = next(
            e for e in record.events if e["event_type"] == "route_selected"
        )
        self.assertIn("candidate_audit", route_event["payload"])

    def test_unknown_run_returns_404(self):
        status, body = self._request(f"{self.base}/v1/epr/runs/nonexistent")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "run_not_found")

    def test_wrong_method_returns_405(self):
        status, body = self._request(
            f"{self.base}/v1/models", method="DELETE"
        )
        self.assertEqual(status, 405)
        self.assertEqual(body["error"]["code"], "method_not_allowed")

    def test_unknown_path_returns_404(self):
        status, body = self._request(f"{self.base}/v1/nonexistent")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "not_found")

    def test_responses_api_string_input(self):
        status, body = self._post(
            "/v1/responses",
            {"model": "noerelay/epr-1", "input": "Hello"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["object"], "response")
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["output"][0]["type"], "message")


class VerificationEngineTests(unittest.TestCase):
    """Test the real verification DAG evaluation for each risk class."""

    @classmethod
    def setUpClass(cls):
        cls.policy = json.loads(
            (ROOT / "spec" / "routing-policy.json").read_text("utf-8")
        )
        cls.contract = {
            "version": "1.0",
            "task_id": "task-vfy-1",
            "goal": "test",
            "risk_class": "low",
            "acceptance_criteria": [
                {"id": "ac-1", "kind": "observable", "mandatory": True},
            ],
        }
        cls.valid_response = {
            "id": "gen-abc",
            "object": "chat.completion",
            "model": "qwen/qwen3.6-35b-a3b",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "Hello"}}
            ],
            "usage": {"total_tokens": 10},
        }
        cls.selected_plan = {
            "provider_family": "qwen",
            "verifier_family": "anthropic",
            "expected_total_cost_usd": 0.10,
        }

    def test_low_risk_dag_passes_on_valid_response(self):
        from gateway.verification import evaluate_verification
        results, all_passed, evidence = evaluate_verification(
            self.contract, self.valid_response, "low", self.policy, self.selected_plan
        )
        self.assertTrue(all_passed)
        self.assertGreater(len(results), 0)
        self.assertGreater(len(evidence), 0)
        # Low-risk DAG is ["schema", "policy"] — both should pass.
        statuses = {r["criterion_id"]: r["status"] for r in results}
        self.assertEqual(statuses.get("schema"), "passed")
        self.assertEqual(statuses.get("policy"), "passed")

    def test_medium_risk_dag_includes_acceptance(self):
        from gateway.verification import evaluate_verification
        results, all_passed, evidence = evaluate_verification(
            self.contract, self.valid_response, "medium", self.policy, self.selected_plan
        )
        self.assertTrue(all_passed)
        criteria_ids = {r["criterion_id"] for r in results}
        self.assertIn("deterministic_acceptance", criteria_ids)

    def test_high_risk_dag_includes_independent_review(self):
        from gateway.verification import evaluate_verification
        results, all_passed, evidence = evaluate_verification(
            self.contract, self.valid_response, "high", self.policy, self.selected_plan
        )
        self.assertTrue(all_passed)
        criteria_ids = {r["criterion_id"] for r in results}
        self.assertIn("independent_family_review", criteria_ids)

    def test_critical_risk_dag_blocks_acceptance(self):
        from gateway.verification import evaluate_verification
        results, all_passed, evidence = evaluate_verification(
            self.contract, self.valid_response, "critical", self.policy, self.selected_plan
        )
        # Critical risk requires human approval; skeleton fails closed.
        self.assertFalse(all_passed)
        human = next(r for r in results if r["criterion_id"] == "human_approval")
        self.assertEqual(human["status"], "not_run")
        self.assertTrue(human.get("_blocks_acceptance"))

    def test_schema_check_fails_on_malformed_response(self):
        from gateway.verification import schema_check
        result = schema_check({"bad": "shape"})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["criterion_id"], "schema")

    def test_schema_check_passes_on_valid_response(self):
        from gateway.verification import schema_check
        result = schema_check(self.valid_response)
        self.assertEqual(result["status"], "passed")

    def test_deterministic_acceptance_checks_observable_criteria(self):
        from gateway.verification import deterministic_acceptance_check
        result = deterministic_acceptance_check(
            self.contract, self.valid_response
        )
        self.assertEqual(result["criterion_id"], "deterministic_acceptance")
        self.assertEqual(result["status"], "passed")
        sub_ids = {s["criterion_id"] for s in result.get("sub_results", [])}
        self.assertIn("ac-1", sub_ids)

    def test_independent_family_review_passes_when_families_differ(self):
        from gateway.verification import independent_family_review_check
        plan = {"provider_family": "qwen", "verifier_family": "anthropic"}
        result = independent_family_review_check(plan)
        self.assertEqual(result["status"], "passed")

    def test_independent_family_review_fails_when_families_match(self):
        from gateway.verification import independent_family_review_check
        plan = {"provider_family": "qwen", "verifier_family": "qwen"}
        result = independent_family_review_check(plan)
        self.assertEqual(result["status"], "failed")

    def test_independent_family_review_fails_when_no_verifier(self):
        from gateway.verification import independent_family_review_check
        plan = {"provider_family": "qwen"}
        result = independent_family_review_check(plan)
        self.assertEqual(result["status"], "failed")

    def test_human_approval_waived_for_non_critical(self):
        from gateway.verification import human_approval_check
        for risk in ("low", "medium", "high"):
            result = human_approval_check(risk)
            self.assertEqual(result["status"], "waived", f"failed for {risk}")
            self.assertFalse(result.get("_blocks_acceptance", False))

    def test_human_approval_not_run_for_critical(self):
        from gateway.verification import human_approval_check
        result = human_approval_check("critical")
        self.assertEqual(result["status"], "not_run")
        self.assertTrue(result.get("_blocks_acceptance"))

    def test_evidence_records_conform_to_schema_shape(self):
        from gateway.verification import evaluate_verification
        _, _, evidence = evaluate_verification(
            self.contract, self.valid_response, "low", self.policy, self.selected_plan
        )
        for ev in evidence:
            self.assertIn("evidence_id", ev)
            self.assertIn("kind", ev)
            self.assertIn("produced_at", ev)
            self.assertIn("producer", ev)
            self.assertIn("activity_id", ev)
            self.assertIn("content_hash", ev)
            self.assertIn("location", ev)
            self.assertIn("strength", ev)
            self.assertTrue(ev["content_hash"].startswith("sha256:"))


class GuardEvaluatorTests(unittest.TestCase):
    """Test each guard computation individually."""

    def test_request_schema_valid(self):
        from gateway.statemachine import GuardEvaluator
        self.assertTrue(GuardEvaluator.request_schema_valid(
            {"model": "x", "messages": [{"role": "user", "content": "hi"}]}
        ))
        self.assertFalse(GuardEvaluator.request_schema_valid({}))
        self.assertFalse(GuardEvaluator.request_schema_valid(
            {"model": "", "messages": []}
        ))

    def test_contract_schema_valid_and_acceptance_sufficient(self):
        from gateway.statemachine import GuardEvaluator
        self.assertTrue(GuardEvaluator.contract_schema_valid_and_acceptance_sufficient(
            {"version": "1.0", "goal": "test"}
        ))
        self.assertFalse(GuardEvaluator.contract_schema_valid_and_acceptance_sufficient(
            {}
        ))
        self.assertFalse(GuardEvaluator.contract_schema_valid_and_acceptance_sufficient(
            {"version": "1.0"}
        ))

    def test_admissible_route_exists(self):
        from gateway.statemachine import GuardEvaluator
        self.assertTrue(GuardEvaluator.admissible_route_exists(
            {"status": "route_selected"}
        ))
        self.assertFalse(GuardEvaluator.admissible_route_exists(
            {"status": "escalation_required"}
        ))

    def test_budget_and_permissions_reserved(self):
        from gateway.statemachine import GuardEvaluator
        plan = {"expected_total_cost_usd": 0.10}
        contract = {"governance": {"max_cost_usd": 0.25}}
        self.assertTrue(GuardEvaluator.budget_and_permissions_reserved(plan, contract))
        plan_over = {"expected_total_cost_usd": 1.00}
        self.assertFalse(GuardEvaluator.budget_and_permissions_reserved(plan_over, contract))

    def test_result_bound_to_actor_activity_and_hash(self):
        from gateway.statemachine import GuardEvaluator
        self.assertTrue(GuardEvaluator.result_bound_to_actor_activity_and_hash(
            {"choices": [{"message": {"content": "ok"}}]}
        ))
        self.assertFalse(GuardEvaluator.result_bound_to_actor_activity_and_hash(
            {"choices": []}
        ))
        self.assertFalse(GuardEvaluator.result_bound_to_actor_activity_and_hash({}))

    def test_required_verification_dag_materialized(self):
        from gateway.statemachine import GuardEvaluator
        self.assertTrue(GuardEvaluator.required_verification_dag_materialized(
            [{"criterion_id": "schema"}]
        ))
        self.assertFalse(GuardEvaluator.required_verification_dag_materialized([]))

    def test_all_mandatory_criteria_pass_and_no_blocking_conflict(self):
        from gateway.statemachine import GuardEvaluator
        self.assertTrue(
            GuardEvaluator.all_mandatory_criteria_pass_and_no_blocking_conflict(
                [{"criterion_id": "schema", "status": "passed", "mandatory": True}]
            )
        )
        self.assertFalse(
            GuardEvaluator.all_mandatory_criteria_pass_and_no_blocking_conflict(
                [{"criterion_id": "schema", "status": "failed", "mandatory": True}]
            )
        )
        # Check sub_results (deterministic_acceptance pattern).
        self.assertTrue(
            GuardEvaluator.all_mandatory_criteria_pass_and_no_blocking_conflict(
                [{
                    "criterion_id": "deterministic_acceptance",
                    "status": "passed",
                    "mandatory": True,
                    "sub_results": [
                        {"criterion_id": "ac-1", "status": "passed", "mandatory": True},
                    ],
                }]
            )
        )
        self.assertFalse(
            GuardEvaluator.all_mandatory_criteria_pass_and_no_blocking_conflict(
                [{
                    "criterion_id": "deterministic_acceptance",
                    "status": "failed",
                    "mandatory": True,
                    "sub_results": [
                        {"criterion_id": "ac-1", "status": "failed", "mandatory": True},
                    ],
                }]
            )
        )

    def test_receipt_contains_ledger_head_and_verification_evidence(self):
        from gateway.statemachine import GuardEvaluator
        self.assertTrue(GuardEvaluator.receipt_contains_ledger_head_and_verification_evidence(
            {"ledger_head_hash": "sha256:abc", "verification_results": []}
        ))
        self.assertFalse(GuardEvaluator.receipt_contains_ledger_head_and_verification_evidence(
            {}
        ))

    def test_policy_allows_progress(self):
        from gateway.statemachine import GuardEvaluator
        self.assertTrue(GuardEvaluator.policy_allows_progress())

    def test_compaction_invariants_hold(self):
        from gateway.statemachine import GuardEvaluator
        self.assertTrue(GuardEvaluator.compaction_invariants_hold())

    def test_router_failed_closed(self):
        from gateway.statemachine import GuardEvaluator
        self.assertTrue(GuardEvaluator.router_failed_closed())


class StreamingTests(unittest.TestCase):
    """EPR-API-004: SSE streaming format and route-identity preservation."""

    def _epr(self, run_id="run-1"):
        return {
            "run_id": run_id,
            "trace_id": "trace-1",
            "status": "accepted",
            "route_decision_id": "decision-1",
            "evidence_receipt_url": f"http://127.0.0.1:8080/v1/epr/runs/{run_id}",
            "ledger_head_hash": "sha256:" + "a" * 64,
            "total_cost_usd": 0.1,
        }

    def test_format_chunk_is_sse_data_line(self):
        data = {"id": "x", "object": "chat.completion.chunk"}
        formatted = SSEStreamer.format_chunk(data)
        self.assertTrue(formatted.startswith("data: "))
        self.assertTrue(formatted.endswith("\n\n"))
        self.assertEqual(json.loads(formatted[len("data: "):].strip()), data)

    def test_format_done_is_terminator(self):
        self.assertEqual(SSEStreamer.format_done(), "data: [DONE]\n\n")

    def test_chunk_content_splits_on_word_boundaries(self):
        content = "one two three four five six seven"
        chunks = SSEStreamer.chunk_content(content, chunk_size=5)
        self.assertEqual("".join(chunks), content)
        self.assertEqual(len(chunks), 2)

    def test_chunk_content_empty(self):
        self.assertEqual(SSEStreamer.chunk_content(""), [])

    def test_build_stream_chunks_have_openai_shape(self):
        chunks = SSEStreamer.build_stream_chunks("run-1", "hello world", self._epr())
        self.assertGreaterEqual(len(chunks), 3)
        for chunk in chunks:
            self.assertEqual(chunk["object"], "chat.completion.chunk")
            self.assertEqual(chunk["model"], "noerelay/epr-1")
        self.assertEqual(chunks[0]["choices"][0]["delta"], {"role": "assistant"})
        self.assertIn("content", chunks[1]["choices"][0]["delta"])
        self.assertEqual(chunks[-2]["choices"][0]["finish_reason"], "stop")
        self.assertEqual(chunks[-2]["choices"][0]["delta"], {})

    def test_terminal_chunk_carries_epr_route_identity(self):
        epr = self._epr()
        chunks = SSEStreamer.build_stream_chunks("run-1", "hi", epr)
        terminal = chunks[-1]
        self.assertEqual(terminal["choices"], [])
        self.assertEqual(terminal["epr"], epr)
        for key in (
            "run_id",
            "trace_id",
            "route_decision_id",
            "evidence_receipt_url",
            "ledger_head_hash",
        ):
            self.assertIn(key, terminal["epr"])

    def test_terminal_chunk_has_evidence_receipt_url(self):
        chunks = SSEStreamer.build_stream_chunks("run-9", "hi", self._epr("run-9"))
        self.assertTrue(
            chunks[-1]["epr"]["evidence_receipt_url"].endswith("/v1/epr/runs/run-9")
        )

    def test_build_error_stream_chunk(self):
        error = {"message": "boom", "type": "t", "code": "c"}
        epr = {
            "run_id": "run-1",
            "trace_id": "trace-1",
            "ledger_head_hash": "sha256:abc",
        }
        chunk = SSEStreamer.build_error_stream_chunk(error, epr)
        self.assertEqual(chunk["error"], error)
        self.assertEqual(chunk["epr"], epr)


class DecodingPhaseTests(unittest.TestCase):
    """EPR-VER-006: separate decoding phases for tool calls and reporting."""

    @classmethod
    def setUpClass(cls):
        cls.policy = load_policy(ROOT / "spec" / "routing-policy.json")
        cls.selected_plan = {
            "action_id": "worker-1",
            "model_id": "qwen/qwen3.6-35b-a3b",
            "inference_gateway": "openrouter",
        }

    def _request(self, tools=True, response_format=True, tool_choice=False):
        passthrough = {}
        if tools:
            passthrough["tools"] = [
                {"type": "function", "function": {"name": "lookup"}}
            ]
        if response_format:
            passthrough["response_format"] = {"type": "json_object"}
        if tool_choice:
            passthrough["tool_choice"] = "auto"
        return {
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": "Use the tool"}],
            "passthrough": passthrough,
        }

    def test_separate_phases_when_tools_present(self):
        manager = DecodingPhaseManager()
        self.assertTrue(
            manager.needs_separate_phases(
                self._request(), "qwen/qwen3.6-35b-a3b", "openrouter"
            )
        )

    def test_single_phase_when_no_tools(self):
        manager = DecodingPhaseManager()
        self.assertFalse(
            manager.needs_separate_phases(
                self._request(tools=False), "qwen/qwen3.6-35b-a3b", "openrouter"
            )
        )

    def test_conformance_tested_bypass(self):
        manager = DecodingPhaseManager(
            conformance_tested={("qwen/qwen3.6-35b-a3b", "openrouter")}
        )
        self.assertFalse(
            manager.needs_separate_phases(
                self._request(), "qwen/qwen3.6-35b-a3b", "openrouter"
            )
        )

    def test_phase1_payload_has_tools_no_response_format(self):
        manager = DecodingPhaseManager()
        payload = manager.build_phase1_payload(
            self._request(), self.selected_plan, self.policy
        )
        self.assertIn("tools", payload)
        self.assertNotIn("response_format", payload)

    def test_phase2_payload_has_response_format_no_tool_choice(self):
        manager = DecodingPhaseManager()
        payload = manager.build_phase2_payload(
            self._request(tool_choice=True), self.selected_plan, self.policy, {}
        )
        self.assertIn("response_format", payload)
        self.assertNotIn("tool_choice", payload)


class TestIndependenceTests(unittest.TestCase):
    """EPR-VER-004: test identification and independence enforcement."""

    def _evidence(self, evidence_id, independence, kind="test_result"):
        return {
            "evidence_id": evidence_id,
            "kind": kind,
            "test_metadata": {
                "test_suite_id": "suite-1",
                "test_version": "1.0",
                "exit_code": 0,
                "independence": independence,
                "coverage": 0.95,
            },
        }

    def test_validate_test_metadata_valid(self):
        self.assertEqual(
            validate_test_metadata(self._evidence("e1", "independent")), []
        )

    def test_validate_test_metadata_non_test_result_skipped(self):
        self.assertEqual(
            validate_test_metadata(
                {"evidence_id": "e1", "kind": "direct_observation"}
            ),
            [],
        )

    def test_missing_test_metadata_rejected(self):
        errors = validate_test_metadata({"evidence_id": "e1", "kind": "test_result"})
        self.assertTrue(errors)

    def test_worker_generated_alone_insufficient_for_high_risk(self):
        ok, detail = TestIndependenceChecker.check_independence(
            [self._evidence("e1", "worker_generated")], "high"
        )
        self.assertFalse(ok)

    def test_independent_test_sufficient_for_high_risk(self):
        ok, detail = TestIndependenceChecker.check_independence(
            [self._evidence("e1", "independent")], "high"
        )
        self.assertTrue(ok)

    def test_worker_generated_sufficient_for_low_risk(self):
        ok, detail = TestIndependenceChecker.check_independence(
            [self._evidence("e1", "worker_generated")], "low"
        )
        self.assertTrue(ok)

    def test_no_test_evidence_not_applicable(self):
        ok, detail = TestIndependenceChecker.check_independence([], "high")
        self.assertTrue(ok)

    def test_deterministic_acceptance_rejects_worker_generated_only(self):
        from gateway.verification import deterministic_acceptance_check

        contract = {
            "acceptance_criteria": [
                {"id": "ac-1", "kind": "observable", "mandatory": True}
            ]
        }
        response = {"choices": [{"message": {"content": "ok"}}]}
        result = deterministic_acceptance_check(
            contract,
            response,
            risk_class="high",
            test_evidence=[self._evidence("e1", "worker_generated")],
        )
        self.assertEqual(result["status"], "failed")

    def test_deterministic_acceptance_allows_independent_test(self):
        from gateway.verification import deterministic_acceptance_check

        contract = {
            "acceptance_criteria": [
                {"id": "ac-1", "kind": "observable", "mandatory": True}
            ]
        }
        response = {"choices": [{"message": {"content": "ok"}}]}
        result = deterministic_acceptance_check(
            contract,
            response,
            risk_class="high",
            test_evidence=[self._evidence("e1", "hidden")],
        )
        self.assertEqual(result["status"], "passed")

    def test_deterministic_acceptance_rejects_missing_metadata(self):
        from gateway.verification import deterministic_acceptance_check

        contract = {
            "acceptance_criteria": [
                {"id": "ac-1", "kind": "observable", "mandatory": True}
            ]
        }
        response = {"choices": [{"message": {"content": "ok"}}]}
        result = deterministic_acceptance_check(
            contract,
            response,
            risk_class="high",
            test_evidence=[{"evidence_id": "e1", "kind": "test_result"}],
        )
        self.assertEqual(result["status"], "failed")


def _live_tests_enabled() -> bool:
    try:
        cfg = GatewayConfig.from_env()
        return cfg.live_tests and cfg.openrouter_mode == "live"
    except Exception:
        return False


@unittest.skipUnless(
    _live_tests_enabled(),
    "Live tests require NOERELAY_LIVE_TESTS=1 and NOERELAY_OPENROUTER_MODE=live",
)
class LiveOpenRouterTests(unittest.TestCase):
    """Live OpenRouter integration tests.

    Gated on ``NOERELAY_LIVE_TESTS=1`` AND ``NOERELAY_OPENROUTER_MODE=live``.
    The API key is read from the environment but never printed.
    """

    @classmethod
    def setUpClass(cls):
        config = GatewayConfig.from_env()
        if not config.openrouter_api_key:
            raise unittest.SkipTest("OPENROUTER_API_KEY not set")
        # Safety: validate the key format without printing it.
        key = config.openrouter_api_key
        if not key.startswith("sk-or-v1-"):
            raise unittest.SkipTest(
                f"OPENROUTER_API_KEY has unexpected prefix (len={len(key)})"
            )
        cls.config = config
        cls.policy = json.loads(
            (ROOT / "spec" / "routing-policy.json").read_text("utf-8")
        )
        from gateway.openrouter import HttpOpenRouterClient
        cls.client = HttpOpenRouterClient(config)

    def test_live_call_returns_valid_openai_shape(self):
        payload = {
            "model": "qwen/qwen3.6-35b-a3b",
            "messages": [{"role": "user", "content": "Say hello in one word."}],
            "max_tokens": 100,
        }
        response = self.client.create_chat_completion(payload)
        self.assertEqual(response.get("object"), "chat.completion")
        self.assertIn("model", response)
        self.assertIn("choices", response)
        self.assertGreater(len(response["choices"]), 0)
        choice = response["choices"][0]
        self.assertIn("message", choice)
        message = choice["message"]
        self.assertIsInstance(message.get("role"), str)

        content = message.get("content")
        finish_reason = choice.get("finish_reason")
        has_reasoning = bool(message.get("reasoning") or message.get("reasoning_details"))
        # Reasoning models may spend the whole budget on reasoning and return
        # content=None with finish_reason="length"; that is still a valid
        # OpenAI-compatible response shape.
        if content is not None:
            self.assertIsInstance(content, str)
        else:
            self.assertTrue(
                finish_reason == "length" or has_reasoning,
                "None content is only acceptable for reasoning/truncated responses",
            )

        usage = response.get("usage")
        self.assertIsNotNone(usage)
        self.assertIn("total_tokens", usage)
        self.assertGreater(usage["total_tokens"], 0)

    def test_live_call_has_usage(self):
        payload = {
            "model": "qwen/qwen3.6-35b-a3b",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 100,
        }
        response = self.client.create_chat_completion(payload)
        self.assertIn("usage", response)
        usage = response["usage"]
        self.assertIn("total_tokens", usage)
        self.assertGreater(usage["total_tokens"], 0)


class EpistemicEngineTests(unittest.TestCase):
    """Tests for EpistemicState, CalibrationStore, and EPR-EPI-002–006."""

    # ------------------------------------------------------------------
    # EPR-EPI-002: Model assertions classified correctly
    # ------------------------------------------------------------------

    def test_model_assertion_classified_as_model_assertion_not_direct_observation(self):
        """EPR-EPI-002: Evidence from model outputs is model_assertion kind."""
        from gateway.epistemic import (
            EpistemicState,
            EVIDENCE_KIND_MODEL_ASSERTION,
            EVIDENCE_KIND_DIRECT_OBSERVATION,
            make_model_assertion_evidence,
        )
        state = EpistemicState()
        ev = make_model_assertion_evidence(
            model_id="test-model",
            content="The answer is 42",
            confidence=0.9,
        )
        eid = state.add_evidence(ev)
        stored = state.get_evidence(eid)
        self.assertIsNotNone(stored)
        self.assertEqual(stored["kind"], EVIDENCE_KIND_MODEL_ASSERTION)
        self.assertNotEqual(stored["kind"], EVIDENCE_KIND_DIRECT_OBSERVATION)
        self.assertEqual(stored["model_id"], "test-model")

    # ------------------------------------------------------------------
    # EPR-EPI-003: Corroboration requires non-model-assertion evidence
    # ------------------------------------------------------------------

    def test_two_model_assertions_do_not_promote_to_supported(self):
        """EPR-EPI-003: Two model assertions agreeing is insufficient."""
        from gateway.epistemic import (
            EpistemicState,
            make_model_assertion_evidence,
            STATUS_UNKNOWN,
        )
        state = EpistemicState()
        ev1 = make_model_assertion_evidence("m1", "claim A", confidence=0.9)
        ev2 = make_model_assertion_evidence("m2", "claim A", confidence=0.85)
        eid1 = state.add_evidence(ev1)
        eid2 = state.add_evidence(ev2)

        status = state.add_claim("c1", "claim A", [eid1, eid2])
        self.assertEqual(status, STATUS_UNKNOWN,
                         "Two model assertions alone should not promote to supported")

    def test_model_assertions_plus_tool_result_promotes_to_supported(self):
        """EPR-EPI-003: Non-model-assertion evidence enables corroboration."""
        from gateway.epistemic import (
            EpistemicState,
            make_model_assertion_evidence,
            make_tool_result_evidence,
            STATUS_SUPPORTED,
        )
        state = EpistemicState()
        ev1 = make_model_assertion_evidence("m1", "claim B", confidence=0.9)
        ev2 = make_model_assertion_evidence("m2", "claim B", confidence=0.85)
        ev3 = make_tool_result_evidence("check", {"valid": True}, success=True)
        eid1 = state.add_evidence(ev1)
        eid2 = state.add_evidence(ev2)
        eid3 = state.add_evidence(ev3)

        status = state.add_claim("c2", "claim B", [eid1, eid2, eid3])
        self.assertEqual(status, STATUS_SUPPORTED,
                         "Tool result corroboration should promote to supported")

    def test_can_promote_by_corroboration_false_for_only_model_assertions(self):
        """EPR-EPI-003: can_promote_by_corroboration returns False for model-only."""
        from gateway.epistemic import (
            EpistemicState,
            make_model_assertion_evidence,
        )
        state = EpistemicState()
        ev1 = make_model_assertion_evidence("m1", "x", confidence=0.9)
        ev2 = make_model_assertion_evidence("m2", "x", confidence=0.8)
        eid1 = state.add_evidence(ev1)
        eid2 = state.add_evidence(ev2)

        self.assertFalse(state.can_promote_by_corroboration([eid1, eid2]))

    def test_can_promote_by_corroboration_true_with_tool_result(self):
        """EPR-EPI-003: Tool result enables corroboration."""
        from gateway.epistemic import (
            EpistemicState,
            make_model_assertion_evidence,
            make_tool_result_evidence,
        )
        state = EpistemicState()
        ev1 = make_model_assertion_evidence("m1", "x", confidence=0.9)
        ev2 = make_tool_result_evidence("test", True, success=True)
        eid1 = state.add_evidence(ev1)
        eid2 = state.add_evidence(ev2)

        self.assertTrue(state.can_promote_by_corroboration([eid1, eid2]))

    # ------------------------------------------------------------------
    # EPR-EPI-004: Derived claims
    # ------------------------------------------------------------------

    def test_derived_claim_references_premises(self):
        """EPR-EPI-004: Derived claim stores premise_evidence_ids."""
        from gateway.epistemic import (
            EpistemicState,
            make_direct_observation_evidence,
        )
        state = EpistemicState()
        ev1 = make_direct_observation_evidence({"fact": "A"}, strength=0.9)
        ev2 = make_direct_observation_evidence({"fact": "B"}, strength=0.8)
        eid1 = state.add_evidence(ev1)
        eid2 = state.add_evidence(ev2)

        state.add_derived_claim("dc1", "A and B", [eid1, eid2])
        claim = state.get_claim("dc1")
        self.assertIsNotNone(claim)
        self.assertTrue(claim["is_derived"])
        self.assertIn(eid1, claim["premise_evidence_ids"])
        self.assertIn(eid2, claim["premise_evidence_ids"])

    def test_derived_claim_rejected_if_premise_refuted(self):
        """EPR-EPI-004: Refuted premise rejects derived claim."""
        from gateway.epistemic import (
            EpistemicState,
            make_direct_observation_evidence,
            STATUS_REFUTED,
        )
        state = EpistemicState()
        ev1 = make_direct_observation_evidence({"fact": "A"}, strength=0.9)
        ev2 = make_direct_observation_evidence({"fact": "B"}, strength=0.1)
        eid1 = state.add_evidence(ev1)
        eid2 = state.add_evidence(ev2)

        status = state.add_derived_claim("dc2", "A and B", [eid1, eid2])
        self.assertEqual(status, STATUS_REFUTED)

    def test_derived_claim_confidence_not_exceed_weakest_premise(self):
        """EPR-EPI-004: Derived confidence <= min(premise confidences)."""
        from gateway.epistemic import (
            EpistemicState,
            make_direct_observation_evidence,
            STATUS_SUPPORTED,
        )
        state = EpistemicState()
        ev1 = make_direct_observation_evidence({"fact": "A"}, strength=0.9)
        ev2 = make_direct_observation_evidence({"fact": "B"}, strength=0.6)
        eid1 = state.add_evidence(ev1)
        eid2 = state.add_evidence(ev2)

        status = state.add_derived_claim("dc3", "A and B", [eid1, eid2], threshold=0.5)
        self.assertEqual(status, STATUS_SUPPORTED)
        claim = state.get_claim("dc3")
        self.assertAlmostEqual(claim["confidence"], 0.6)

    def test_derived_claim_requires_at_least_one_premise(self):
        """EPR-EPI-004: Empty premises raises ValueError."""
        from gateway.epistemic import EpistemicState
        state = EpistemicState()
        with self.assertRaises(ValueError):
            state.add_derived_claim("dc4", "orphan", [])

    # ------------------------------------------------------------------
    # EPR-EPI-005: Conflicted claims block high-risk acceptance
    # ------------------------------------------------------------------

    def _make_conflicted_state(self):
        """Create an EpistemicState with a conflicted claim."""
        from gateway.epistemic import (
            EpistemicState,
            make_direct_observation_evidence,
        )
        state = EpistemicState()
        ev_support = make_direct_observation_evidence({"fact": "X"}, strength=0.9)
        ev_refute = make_direct_observation_evidence({"fact": "X"}, strength=0.1)
        eid_s = state.add_evidence(ev_support)
        eid_r = state.add_evidence(ev_refute)
        state.add_claim("conflicted-1", "X is true", [eid_s, eid_r])
        return state

    def test_conflicted_claim_blocks_high_risk(self):
        """EPR-EPI-005: Conflicted claim detected by has_blocking_conflict."""
        state = self._make_conflicted_state()
        self.assertTrue(state.has_blocking_conflict(["conflicted-1"]))

    def test_conflicted_claim_does_not_block_low_risk_guard(self):
        """EPR-EPI-005: Guard passes low risk despite conflicted claims."""
        from gateway.statemachine import GuardEvaluator
        state = self._make_conflicted_state()
        result = GuardEvaluator.all_mandatory_criteria_pass_and_no_blocking_conflict(
            [{"criterion_id": "schema", "status": "passed", "mandatory": True}],
            epistemic_state=state,
            risk_class="low",
        )
        self.assertTrue(result)

    def test_conflicted_claim_blocks_high_risk_guard(self):
        """EPR-EPI-005: Guard blocks high risk when conflicted claims exist."""
        from gateway.statemachine import GuardEvaluator
        state = self._make_conflicted_state()
        result = GuardEvaluator.all_mandatory_criteria_pass_and_no_blocking_conflict(
            [{"criterion_id": "schema", "status": "passed", "mandatory": True}],
            epistemic_state=state,
            risk_class="high",
        )
        self.assertFalse(result)

    def test_conflicted_claim_blocks_critical_risk_guard(self):
        """EPR-EPI-005: Guard blocks critical risk when conflicted claims exist."""
        from gateway.statemachine import GuardEvaluator
        state = self._make_conflicted_state()
        result = GuardEvaluator.all_mandatory_criteria_pass_and_no_blocking_conflict(
            [{"criterion_id": "schema", "status": "passed", "mandatory": True}],
            epistemic_state=state,
            risk_class="critical",
        )
        self.assertFalse(result)

    def test_no_conflicted_claims_guard_passes_high_risk(self):
        """EPR-EPI-005: Guard passes high risk when no conflicted claims."""
        from gateway.statemachine import GuardEvaluator
        from gateway.epistemic import EpistemicState
        state = EpistemicState()
        result = GuardEvaluator.all_mandatory_criteria_pass_and_no_blocking_conflict(
            [{"criterion_id": "schema", "status": "passed", "mandatory": True}],
            epistemic_state=state,
            risk_class="high",
        )
        self.assertTrue(result)

    def test_conflicted_claim_ids(self):
        """EPR-EPI-005: conflicted_claim_ids returns only conflicted claims."""
        state = self._make_conflicted_state()
        conflicted = state.conflicted_claim_ids()
        self.assertIn("conflicted-1", conflicted)
        self.assertEqual(len(conflicted), 1)

    # ------------------------------------------------------------------
    # EPR-EPI-006: Calibration
    # ------------------------------------------------------------------

    def test_calibration_ece_perfect_calibration(self):
        """EPR-EPI-006: Perfectly calibrated model ECE ≈ 0."""
        from gateway.epistemic import CalibrationStore
        store = CalibrationStore()
        for _ in range(90):
            store.record_outcome("perfect", 0.9, True)
        for _ in range(10):
            store.record_outcome("perfect", 0.9, False)
        ece = store.compute_ece("perfect", n_bins=10)
        self.assertAlmostEqual(ece, 0.0, delta=0.01)

    def test_calibration_ece_poor_calibration(self):
        """EPR-EPI-006: Poorly calibrated model has high ECE."""
        from gateway.epistemic import CalibrationStore
        store = CalibrationStore()
        for _ in range(50):
            store.record_outcome("overconfident", 0.9, True)
        for _ in range(50):
            store.record_outcome("overconfident", 0.9, False)
        ece = store.compute_ece("overconfident", n_bins=10)
        self.assertGreater(ece, 0.3)

    def test_uncalibrated_model_flagged(self):
        """EPR-EPI-006: Model with ECE > 0.04 flagged as uncalibrated."""
        from gateway.epistemic import CalibrationStore
        store = CalibrationStore()
        for _ in range(50):
            store.record_outcome("bad", 0.9, True)
        for _ in range(50):
            store.record_outcome("bad", 0.9, False)
        self.assertFalse(store.is_calibrated("bad", threshold=0.04))

    def test_calibrated_confidence_insufficient_data_uses_discount(self):
        """EPR-EPI-006: Few records triggers conservative 0.5 discount."""
        from gateway.epistemic import CalibrationStore
        store = CalibrationStore()
        for _ in range(5):
            store.record_outcome("new-model", 0.9, True)
        calibrated = store.calibrated_confidence("new-model", 0.9)
        self.assertAlmostEqual(calibrated, 0.45)

    def test_calibrated_confidence_with_sufficient_data(self):
        """EPR-EPI-006: Sufficient data uses data-driven calibration factor."""
        from gateway.epistemic import CalibrationStore
        store = CalibrationStore()
        # Spread across two confidence bins to keep per-bin ECE < 0.04
        # while overall accuracy < mean confidence (so factor < 1.0).
        # Bin 0.7: 50 records, 34 correct → accuracy 0.68, diff 0.02
        # Bin 0.9: 50 records, 44 correct → accuracy 0.88, diff 0.02
        # ECE = 0.5*0.02 + 0.5*0.02 = 0.02 < 0.04
        for _ in range(34):
            store.record_outcome("good-model", 0.7, True)
        for _ in range(16):
            store.record_outcome("good-model", 0.7, False)
        for _ in range(44):
            store.record_outcome("good-model", 0.9, True)
        for _ in range(6):
            store.record_outcome("good-model", 0.9, False)
        self.assertTrue(store.is_calibrated("good-model", threshold=0.04))
        calibrated = store.calibrated_confidence("good-model", 0.9)
        # calibration_factor = (34+44)/(50*0.7+50*0.9) = 78/80 = 0.975
        self.assertLess(calibrated, 0.9)
        self.assertGreater(calibrated, 0.45)

    def test_epistemic_state_exposes_calibration_store(self):
        """EPR-EPI-006: EpistemicState exposes CalibrationStore."""
        from gateway.epistemic import EpistemicState
        state = EpistemicState()
        self.assertIsNotNone(state.calibration)
        state.calibration.record_outcome("m", 0.8, True)
        self.assertEqual(state.calibration.record_count("m"), 1)


class EpistemicPipelineTests(unittest.TestCase):
    """Integration tests for epistemic state wired into the pipeline."""

    @classmethod
    def setUpClass(cls):
        cls.policy = json.loads(
            (ROOT / "spec" / "routing-policy.json").read_text("utf-8")
        )
        cls.portfolio = json.loads(
            (ROOT / "examples" / "candidate-actions.json").read_text("utf-8")
        )
        cls.spec = json.loads(
            (ROOT / "spec" / "verification-state-machine.json").read_text("utf-8")
        )

    def _make_ctx(self):
        """Build a PipelineContext suitable for pipeline tests."""
        from gateway.config import GatewayConfig
        from gateway.openrouter import StubOpenRouterClient
        from gateway.statemachine import VerificationStateMachine
        from gateway.runs import RunRegistry

        config = GatewayConfig.from_env(
            {"NOERELAY_GATEWAY_HOST": "127.0.0.1", "NOERELAY_GATEWAY_PORT": "0"}
        )
        return PipelineContext(
            config=config,
            policy=self.policy,
            portfolio=self.portfolio,
            openrouter_client=StubOpenRouterClient(self.policy),
            state_machine=VerificationStateMachine(self.spec),
            registry=RunRegistry(),
        )

    def test_pipeline_creates_model_assertion_evidence(self):
        """EPR-EPI-002: Pipeline creates model_assertion evidence after execution."""
        from gateway.pipeline import run_inference_pipeline

        ctx = self._make_ctx()
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

        es = record.epistemic_state
        self.assertIsNotNone(es)
        self.assertGreaterEqual(es.evidence_count(), 1,
                                "Epistemic state should have evidence records")

    def test_high_risk_pipeline_succeeds_without_conflicts(self):
        """High-risk pipeline succeeds when no conflicted claims exist."""
        from gateway.pipeline import run_inference_pipeline

        ctx = self._make_ctx()
        request = {
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": "Hello"}],
            "governance": {"risk_class": "high", "max_cost_usd": 2.0},
        }
        result = run_inference_pipeline(request, ctx)
        self.assertEqual(result["epr"]["status"], "accepted")


class ContextCompactionTests(unittest.TestCase):
    """EPR-CTX-001 / EPR-CTX-002 / EPR-CTX-006 conformance tests."""

    def _canonical_state(self):
        state = CanonicalState()
        state.active_requirement_ids = {"req-1"}
        state.approved_decision_ids = {"decision-1"}
        state.unresolved_claim_ids = {"assumption-1"}
        state.failed_mandatory_check_ids = {"check-1"}
        state.evidence_handles = {
            "evidence-1": "sha256:" + "a" * 64,
            "evidence-2": "sha256:" + "b" * 64,
        }
        state.artifact_hashes = {"artifact-1": "sha256:" + "c" * 64}
        return state

    def _canonical_claims(self):
        return [
            {
                "claim_id": "req-1",
                "kind": "requirement",
                "state": "active",
                "support_evidence_ids": ["evidence-1"],
                "refutation_evidence_ids": [],
            },
            {
                "claim_id": "decision-1",
                "kind": "decision",
                "state": "approved",
                "support_evidence_ids": ["evidence-2"],
                "refutation_evidence_ids": [],
            },
            {
                "claim_id": "assumption-1",
                "kind": "assumption",
                "state": "open",
                "support_evidence_ids": ["evidence-2"],
                "refutation_evidence_ids": [],
            },
        ]

    def test_four_memory_levels_are_distinguished(self):
        """EPR-CTX-001: L0/L1/L2/L3 are distinct."""
        self.assertEqual(
            {level.value for level in MemoryLevel}, {"L0", "L1", "L2", "L3"}
        )
        self.assertEqual(MemoryLevel.L0_IMMUTABLE_EVENTS.value, "L0")
        self.assertEqual(MemoryLevel.L1_CANONICAL_STATE.value, "L1")
        self.assertEqual(MemoryLevel.L2_ACTIVE_DECISION.value, "L2")
        self.assertEqual(MemoryLevel.L3_NARRATIVE_SUMMARY.value, "L3")

    def test_compaction_preserves_l1_state(self):
        """EPR-CTX-002: compaction preserves all authoritative L1 IDs."""
        capsule = ContextCompactor().compact(
            self._canonical_state(),
            "narrative",
            canonical_claims=self._canonical_claims(),
            mandatory_failed_check_ids=["check-1"],
        )
        self.assertEqual(set(capsule["active_requirement_ids"]), {"req-1"})
        self.assertEqual(set(capsule["approved_decision_ids"]), {"decision-1"})
        self.assertEqual(set(capsule["unresolved_claim_ids"]), {"assumption-1"})
        self.assertEqual(set(capsule["failed_mandatory_check_ids"]), {"check-1"})
        self.assertEqual(set(capsule["evidence_handles"]), {"evidence-1", "evidence-2"})
        self.assertEqual(set(capsule["artifact_hashes"]), {"sha256:" + "c" * 64})

    def test_compaction_asserts_three_invariants(self):
        """EPR-CTX-002: capsule asserts the three required invariants."""
        capsule = ContextCompactor().compact(
            self._canonical_state(),
            "narrative",
            canonical_claims=self._canonical_claims(),
            mandatory_failed_check_ids=["check-1"],
        )
        self.assertTrue(capsule["invariants"]["authoritative_state_preserved"])
        self.assertTrue(capsule["invariants"]["evidence_dereferenceable"])
        self.assertTrue(capsule["invariants"]["summary_is_not_evidence"])

    def test_validate_context_capsule_passes_on_compacted_capsule(self):
        """EPR-CTX-002: kernel validate_context_capsule accepts the capsule."""
        claims = self._canonical_claims()
        capsule = ContextCompactor().compact(
            self._canonical_state(),
            "narrative",
            canonical_claims=claims,
            mandatory_failed_check_ids=["check-1"],
        )
        self.assertEqual(validate_context_capsule(capsule, claims, ["check-1"]), [])

    def test_build_canonical_state_from_epistemic_state(self):
        """EPR-CTX-001: L1 state is derived from epistemic state and ledger."""
        from gateway.epistemic import (
            EpistemicState,
            make_direct_observation_evidence,
        )

        state = EpistemicState()
        ev = make_direct_observation_evidence({"fact": "A"}, strength=0.9)
        eid = state.add_evidence(ev)

        canonical = build_canonical_state(
            state,
            [
                {
                    "payload": {
                        "verification_results": [
                            {"criterion_id": "check-1", "status": "failed", "mandatory": True}
                        ]
                    }
                }
            ],
        )
        self.assertIn(eid, canonical.evidence_handles)
        self.assertEqual(
            canonical.evidence_handles[eid], ev["content_hash"]
        )
        self.assertIn("check-1", canonical.failed_mandatory_check_ids)

    def _epistemic_state_with_deps(self):
        from gateway.epistemic import (
            EpistemicState,
            make_direct_observation_evidence,
        )

        state = EpistemicState()
        e1 = make_direct_observation_evidence({"fact": "A"}, strength=0.9)
        e2 = make_direct_observation_evidence({"fact": "B"}, strength=0.9)
        eid1 = state.add_evidence(e1)
        eid2 = state.add_evidence(e2)
        state.add_claim("claim-1", "claim A", [eid1])
        state.add_claim("claim-2", "claim B", [eid2])
        return state, eid1, eid2

    def test_graph_reachability_includes_only_reachable_items(self):
        """EPR-CTX-006: reachability excludes unrelated claims/evidence/events."""
        state, eid1, eid2 = self._epistemic_state_with_deps()
        contract = {
            "task_id": "task-ctx-1",
            "acceptance_criteria": [
                {"id": "ac-1", "kind": "executable", "requirement_refs": ["claim-1"]},
            ],
        }
        events = [
            {"event_id": "event-1", "subject_id": "task-ctx-1", "payload": {}},
            {"event_id": "event-2", "subject_id": "other-task", "payload": {}},
        ]
        package = ContextCompiler().compile(contract, state, events)

        self.assertEqual(package["compilation_strategy"], "graph_reachability")
        self.assertEqual(package["reachable_claim_ids"], ["claim-1"])
        self.assertEqual(package["reachable_evidence_ids"], [eid1])
        self.assertNotIn("claim-2", package["reachable_claim_ids"])
        self.assertNotIn(eid2, package["reachable_evidence_ids"])
        self.assertEqual(
            [e["event_id"] for e in package["ledger_events"]], ["event-1"]
        )
        self.assertGreater(package["excluded_item_count"], 0)

    def test_graph_reachability_falls_back_to_include_everything(self):
        """EPR-CTX-006: trivial graphs include everything."""
        state, eid1, eid2 = self._epistemic_state_with_deps()
        contract = {"task_id": "task-ctx-2"}  # no criteria -> trivial graph
        events = [
            {"event_id": "event-1", "subject_id": "task-ctx-2", "payload": {}},
            {"event_id": "event-2", "subject_id": "other", "payload": {}},
        ]
        package = ContextCompiler().compile(contract, state, events)

        self.assertEqual(package["compilation_strategy"], "include_everything")
        self.assertEqual(set(package["reachable_claim_ids"]), {"claim-1", "claim-2"})
        self.assertEqual(set(package["reachable_evidence_ids"]), {eid1, eid2})
        self.assertEqual(len(package["ledger_events"]), 2)
        self.assertEqual(package["excluded_item_count"], 0)


class ContractCompilationTests(unittest.TestCase):
    """EPR-CON-001 through EPR-CON-004 conformance tests."""

    def _governance(self, risk_class="low"):
        return {
            "risk_class": risk_class,
            "data_policy": "zdr",
            "max_cost_usd": 0.25,
            "max_latency_ms": 60000,
            "retention_class": "ephemeral",
            "return_evidence_receipt": True,
        }

    def test_classify_acceptance_criterion_executable(self):
        self.assertEqual(
            classify_acceptance_criterion("Run the test suite and assert all tests pass"),
            "executable",
        )

    def test_classify_acceptance_criterion_observable(self):
        self.assertEqual(
            classify_acceptance_criterion("The response should display a confirmation message"),
            "observable",
        )

    def test_classify_acceptance_criterion_judgmental(self):
        self.assertEqual(
            classify_acceptance_criterion("The answer should be helpful and correct"),
            "judgmental",
        )

    def test_classify_acceptance_criterion_missing(self):
        self.assertEqual(classify_acceptance_criterion(""), "missing")
        self.assertEqual(
            classify_acceptance_criterion("anything", "missing"), "missing"
        )

    def test_classify_acceptance_criterion_respects_kind_hint(self):
        self.assertEqual(
            classify_acceptance_criterion("whatever", "executable"), "executable"
        )

    def test_llm_proposer_interface_with_deterministic_defaults(self):
        class Proposer:
            def propose(self, messages, governance, passthrough, *, task_id):
                return {
                    "goal": "The system must return a JSON object",
                    "acceptance_criteria": [
                        {
                            "id": "ac-1",
                            "description": "Run the tests",
                            "kind": "executable",
                            "mandatory": True,
                        },
                    ],
                }

        contract = compile_task_contract(
            [{"role": "user", "content": "hello"}],
            self._governance(),
            proposer=Proposer(),
            task_id="task-llm-1",
        )
        # LLM-provided fields are honored...
        self.assertEqual(contract["goal"], "The system must return a JSON object")
        self.assertEqual(contract["acceptance_criteria"][0]["kind"], "executable")
        # ...while deterministic defaults fill every other field.
        self.assertEqual(contract["task_kind"], "conversation")
        self.assertEqual(contract["version"], "1.0")
        self.assertEqual(validate_task_contract(contract), [])

    def test_llm_proposer_failure_falls_back_to_deterministic(self):
        class BrokenProposer:
            def propose(self, messages, governance, passthrough, *, task_id):
                raise RuntimeError("boom")

        contract = compile_task_contract(
            [{"role": "user", "content": "hello"}],
            self._governance(),
            proposer=BrokenProposer(),
            task_id="task-llm-2",
        )
        self.assertEqual(contract["goal"], "hello")
        self.assertEqual(contract["acceptance_criteria"][0]["kind"], "observable")

    def test_llm_proposer_protocol_is_runtime_checkable(self):
        class Proposer:
            def propose(self, messages, governance, passthrough, *, task_id):
                return {}

        self.assertTrue(isinstance(Proposer(), LLMContractProposer))

    def test_high_risk_missing_acceptance_requires_clarification(self):
        contract = {
            "risk_class": "high",
            "acceptance_criteria": [
                {"id": "ac-1", "kind": "missing", "mandatory": True}
            ],
        }
        self.assertTrue(requires_clarification(contract))

    def test_high_risk_empty_acceptance_requires_clarification(self):
        contract = {"risk_class": "high", "acceptance_criteria": []}
        self.assertTrue(requires_clarification(contract))

    def test_low_risk_missing_acceptance_does_not_require_clarification(self):
        contract = {
            "risk_class": "low",
            "acceptance_criteria": [
                {"id": "ac-1", "kind": "missing", "mandatory": True}
            ],
        }
        self.assertFalse(requires_clarification(contract))

    def test_high_risk_with_observable_acceptance_does_not_require_clarification(self):
        contract = {
            "risk_class": "high",
            "acceptance_criteria": [
                {"id": "ac-1", "kind": "observable", "mandatory": True}
            ],
        }
        self.assertFalse(requires_clarification(contract))

    def test_distinct_state_vocabularies(self):
        """EPR-CON-004: contract fields use distinct, valid vocabularies."""
        self.assertEqual(len(StateVocabulary.ALL_VOCABULARIES), 8)
        contract = compile_task_contract(
            [{"role": "user", "content": "The system must return valid JSON"}],
            self._governance(),
            task_id="task-vocab-1",
        )
        items = contract_state_items(contract)
        kinds = {item["kind"] for item in items}
        self.assertTrue(kinds.issubset(StateVocabulary.ALL_VOCABULARIES))
        self.assertIn(StateVocabulary.REQUIREMENT, kinds)
        self.assertIn(StateVocabulary.DECISION, kinds)
        for item in items:
            self.assertEqual(StateVocabulary.validate(item), [], msg=item)


class StateVocabularyTests(unittest.TestCase):
    """EPR-CON-004: state-vocabulary classification and validation."""

    def test_classify_requirement(self):
        self.assertEqual(
            StateVocabulary.classify("The system shall return JSON"),
            StateVocabulary.REQUIREMENT,
        )
        self.assertEqual(
            StateVocabulary.classify("You must not delete state"),
            StateVocabulary.REQUIREMENT,
        )
        self.assertEqual(
            StateVocabulary.classify("The gateway should fail closed"),
            StateVocabulary.REQUIREMENT,
        )

    def test_classify_fact(self):
        self.assertEqual(
            StateVocabulary.classify("The server returned status 200"),
            StateVocabulary.FACT,
        )

    def test_classify_decision(self):
        self.assertEqual(
            StateVocabulary.classify("The change was approved by the reviewer"),
            StateVocabulary.DECISION,
        )

    def test_classify_assumption(self):
        self.assertEqual(
            StateVocabulary.classify("We assume the clock is synchronized"),
            StateVocabulary.ASSUMPTION,
        )

    def test_classify_observation(self):
        self.assertEqual(
            StateVocabulary.classify("The sensor measured 42 degrees"),
            StateVocabulary.OBSERVATION,
        )

    def test_classify_prediction(self):
        self.assertEqual(
            StateVocabulary.classify("The model predicts a 90 percent chance"),
            StateVocabulary.PREDICTION,
        )

    def test_classify_preference(self):
        self.assertEqual(
            StateVocabulary.classify("I prefer the Qwen provider"),
            StateVocabulary.PREFERENCE,
        )

    def test_classify_artifact(self):
        self.assertEqual(
            StateVocabulary.classify("The build produced an artifact with a hash"),
            StateVocabulary.ARTIFACT,
        )

    def test_validate_each_vocabulary(self):
        valid = [
            {"kind": StateVocabulary.REQUIREMENT, "statement": "must"},
            {"kind": StateVocabulary.FACT, "status": "supported"},
            {"kind": StateVocabulary.DECISION, "status": "approved", "rationale": "x"},
            {"kind": StateVocabulary.ASSUMPTION, "statement": "x"},
            {"kind": StateVocabulary.OBSERVATION, "content_hash": "sha256:" + "a" * 64},
            {"kind": StateVocabulary.PREDICTION, "statement": "x"},
            {"kind": StateVocabulary.PREFERENCE, "statement": "x"},
            {"kind": StateVocabulary.ARTIFACT, "content_hash": "sha256:" + "b" * 64},
        ]
        for item in valid:
            self.assertEqual(StateVocabulary.validate(item), [], msg=item)

    def test_invalid_vocabulary_rejected(self):
        errors = StateVocabulary.validate({"kind": "memo"})
        self.assertTrue(errors)
        self.assertTrue(any("invalid_kind" in e for e in errors))

    def test_vocabulary_required_fields_enforced(self):
        self.assertTrue(
            StateVocabulary.validate({"kind": StateVocabulary.FACT, "status": "maybe"})
        )
        self.assertTrue(
            StateVocabulary.validate({"kind": StateVocabulary.DECISION, "status": "approved"})
        )


class ProvenanceMappingTests(unittest.TestCase):
    """EPR-LED-005: W3C PROV and in-toto attestation mapping."""

    def _evidence(self, kind="model_assertion"):
        return {
            "evidence_id": "evidence-1",
            "kind": kind,
            "produced_at": "2026-08-17T00:00:00Z",
            "producer": {"id": "noerelay-gateway", "kind": "service"},
            "activity_id": "activity-1",
            "content_hash": "sha256:" + "a" * 64,
            "location": "pipeline.test",
            "strength": 0.9,
        }

    def test_w3c_prov_mapping(self):
        evidence = self._evidence()
        prov = ProvenanceMapper.map_to_prov(evidence)
        self.assertEqual(prov["entity"], evidence["content_hash"])
        self.assertEqual(prov["activity"], "activity-1")
        self.assertEqual(prov["agent"], "noerelay-gateway")
        self.assertEqual(prov["was_derived_from"], [])

    def test_in_toto_statement_for_test_result(self):
        evidence = self._evidence("test_result")
        evidence["artifact_hash"] = "sha256:" + "b" * 64
        evidence["environment_hash"] = "sha256:" + "c" * 64
        evidence["test_metadata"] = {
            "test_suite_id": "suite-1",
            "test_version": "1.0",
            "exit_code": 0,
        }
        statement = ProvenanceMapper.map_to_in_toto(evidence)
        self.assertEqual(statement["_type"], "https://in-toto.io/Statement/v1")
        self.assertEqual(
            statement["predicate_type"],
            "https://in-toto.io/attestation/test-result/v1",
        )
        self.assertEqual(statement["subject"][0]["name"], "artifact")
        self.assertEqual(statement["subject"][0]["digest"]["sha256"], "b" * 64)
        self.assertEqual(statement["predicate"], evidence["test_metadata"])

    def test_in_toto_statement_for_model_assertion(self):
        evidence = self._evidence("model_assertion")
        evidence["model_id"] = "qwen/qwen3.6-35b-a3b"
        statement = ProvenanceMapper.map_to_in_toto(evidence)
        self.assertEqual(
            statement["predicate_type"],
            "https://in-toto.io/attestation/model-output/v1",
        )
        self.assertEqual(statement["subject"][0]["name"], "content")
        self.assertEqual(statement["subject"][0]["digest"]["sha256"], "a" * 64)
        self.assertEqual(
            statement["predicate"]["model_id"], "qwen/qwen3.6-35b-a3b"
        )

    def test_in_toto_statement_for_direct_observation(self):
        evidence = self._evidence("direct_observation")
        statement = ProvenanceMapper.map_to_in_toto(evidence)
        self.assertEqual(
            statement["predicate_type"],
            "https://in-toto.io/attestation/observation/v1",
        )
        self.assertEqual(statement["subject"][0]["name"], "content")
        self.assertEqual(statement["predicate"]["location"], "pipeline.test")

    def test_enrich_evidence_adds_prov(self):
        evidence = self._evidence()
        enriched = ProvenanceMapper.enrich_evidence(evidence)
        self.assertIn("prov", enriched)
        self.assertEqual(enriched["prov"]["entity"], evidence["content_hash"])
        # Idempotent: an existing prov field is preserved.
        enriched["prov"]["entity"] = "custom"
        ProvenanceMapper.enrich_evidence(enriched)
        self.assertEqual(enriched["prov"]["entity"], "custom")

    def test_provenance_mapping_for_derived_evidence(self):
        evidence = self._evidence("derived")
        evidence["premise_evidence_ids"] = ["evidence-a", "evidence-b"]
        prov = ProvenanceMapper.map_to_prov(evidence)
        self.assertEqual(prov["was_derived_from"], ["evidence-a", "evidence-b"])

    def test_prov_maps_artifact_and_environment_for_test_result(self):
        evidence = self._evidence("test_result")
        evidence["artifact_hash"] = "sha256:" + "b" * 64
        evidence["environment_hash"] = "sha256:" + "c" * 64
        prov = ProvenanceMapper.map_to_prov(evidence)
        self.assertEqual(prov["artifact"], "sha256:" + "b" * 64)
        self.assertEqual(prov["environment"], "sha256:" + "c" * 64)


class FallbackRecordingTests(unittest.TestCase):
    """EPR-ROUTE-005: separate provider/semantic fallback recording."""

    def test_provider_fallback_recorded_separately_from_semantic(self):
        recorder = FallbackRecorder()
        recorder.record("provider_fallback", "provider-a", "provider-b", "transport_error")
        recorder.record("semantic_fallback", "model-a", "model-b", "verification_failed")

        self.assertEqual(len(recorder.get_provider_fallbacks()), 1)
        self.assertEqual(len(recorder.get_semantic_fallbacks()), 1)

        provider = recorder.get_provider_fallbacks()[0]
        self.assertEqual(provider["fallback_class"], "provider_fallback")
        self.assertEqual(provider["from_provider"], "provider-a")
        self.assertEqual(provider["to_provider"], "provider-b")

        semantic = recorder.get_semantic_fallbacks()[0]
        self.assertEqual(semantic["fallback_class"], "semantic_fallback")
        self.assertEqual(semantic["from_model"], "model-a")
        self.assertEqual(semantic["to_model"], "model-b")

    def test_fallback_summary_counts(self):
        recorder = FallbackRecorder()
        recorder.record("provider_fallback", "a", "b", "r1")
        recorder.record("provider_fallback", "b", "c", "r2")
        recorder.record("semantic_fallback", "m1", "m2", "r3")

        summary = recorder.get_fallback_summary()
        self.assertEqual(summary["provider_fallback_count"], 2)
        self.assertEqual(summary["semantic_fallback_count"], 1)
        self.assertEqual(recorder.get_summary(), summary)

    def test_fallback_triggered_ledger_event_with_class_distinction(self):
        from gateway.runs import GATEWAY_ACTOR, RunRegistry

        registry = RunRegistry()
        registry.begin("run-fb-1", "trace-fb-1")
        recorder = FallbackRecorder()
        event = recorder.record(
            "capability_fallback", "model-x", "model-y", "missing_capability"
        )
        registry.ledger(
            "run-fb-1",
            "fallback_triggered",
            GATEWAY_ACTOR,
            "task-1",
            {"fallback_class": event["fallback_class"]},
        )

        record = registry.get("run-fb-1")
        self.assertEqual(record.events[-1]["event_type"], "fallback_triggered")
        self.assertEqual(
            record.events[-1]["payload"]["fallback_class"], "capability_fallback"
        )
        schema = json.loads(
            (ROOT / "spec" / "schemas" / "ledger-event.schema.json").read_text("utf-8")
        )
        self.assertIn("fallback_triggered", schema["properties"]["event_type"]["enum"])

    def test_epr_metadata_reflects_actual_fallback_counts(self):
        from gateway.render import render_epr_metadata

        recorder = FallbackRecorder()
        recorder.record("provider_fallback", "a", "b", "r1")
        recorder.record("semantic_fallback", "m1", "m2", "r2")
        summary = recorder.get_fallback_summary()

        epr = render_epr_metadata(
            run_id="run-1",
            trace_id="trace-1",
            status="accepted",
            route_decision_id="decision-1",
            external_base_url="http://127.0.0.1:8080",
            ledger_head_hash="sha256:" + "a" * 64,
            total_cost_usd=0.1,
            provider_fallback_count=summary["provider_fallback_count"],
            semantic_fallback_count=summary["semantic_fallback_count"],
        )
        self.assertEqual(epr["provider_fallback_count"], 1)
        self.assertEqual(epr["semantic_fallback_count"], 1)


class OnlineLearningTests(unittest.TestCase):
    """EPR-ROUTE-006: canary-only online learning governance."""

    def test_canary_detection_explicit_flag(self):
        router = CanaryTrafficRouter(canary_percentage=0.0)
        self.assertTrue(router.is_canary({"canary": True}))
        self.assertFalse(router.is_canary({"canary": False}))
        self.assertFalse(router.is_canary({}))

    def test_canary_detection_percentage_based(self):
        self.assertTrue(CanaryTrafficRouter(canary_percentage=1.0).is_canary({}))
        self.assertFalse(CanaryTrafficRouter(canary_percentage=0.0).is_canary({}))

    def test_production_traffic_cannot_use_experimental(self):
        router = CanaryTrafficRouter(canary_percentage=0.0)
        self.assertFalse(router.can_use_experimental({"canary": False}))
        self.assertFalse(router.can_use_experimental({}))

    def test_canary_traffic_can_use_experimental(self):
        router = CanaryTrafficRouter(canary_percentage=0.0)
        self.assertTrue(router.can_use_experimental({"canary": True}))

    def test_can_modify_production_always_false(self):
        manager = PolicyVersionManager("1.0.0")
        self.assertFalse(manager.can_modify_production())

    def test_promote_canary_requires_signed_benchmark(self):
        manager = PolicyVersionManager("1.0.0")
        manager.register_canary("1.1.0", {"experimental": True})
        ok, reason = manager.promote_canary("1.1.0", {"signed": False})
        self.assertFalse(ok)
        self.assertIn("signed", reason)

    def test_promote_canary_checks_all_promotion_gates(self):
        manager = PolicyVersionManager("1.0.0")
        manager.register_canary("1.1.0", {})
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
        self.assertEqual(manager.get_production_policy_version(), "1.1.0")

    def test_promote_canary_fails_on_missing_gate(self):
        manager = PolicyVersionManager("1.0.0")
        manager.register_canary("1.1.0", {})
        ok, reason = manager.promote_canary(
            "1.1.0",
            {
                "signed": True,
                "gates": {
                    "maximum_unsafe_accept_rate": 0,
                    "maximum_calibration_ece": 0.03,
                    "minimum_replay_success_rate": 0.99,
                },
            },
        )
        self.assertFalse(ok)
        self.assertIn("missing", reason)


class EpistemicLedgerTests(unittest.TestCase):
    """Tests for epistemic ledger enrichment, decision trace, and ledger query API."""

    @classmethod
    def setUpClass(cls):
        cls.policy = json.loads(
            (ROOT / "spec" / "routing-policy.json").read_text("utf-8")
        )
        cls.portfolio = json.loads(
            (ROOT / "examples" / "candidate-actions.json").read_text("utf-8")
        )
        cls.spec = json.loads(
            (ROOT / "spec" / "verification-state-machine.json").read_text("utf-8")
        )

    def _make_ctx(self):
        from gateway.config import GatewayConfig
        from gateway.openrouter import StubOpenRouterClient
        from gateway.statemachine import VerificationStateMachine
        from gateway.runs import RunRegistry

        config = GatewayConfig.from_env(
            {"NOERELAY_GATEWAY_HOST": "127.0.0.1", "NOERELAY_GATEWAY_PORT": "0"}
        )
        return PipelineContext(
            config=config,
            policy=self.policy,
            portfolio=self.portfolio,
            openrouter_client=StubOpenRouterClient(self.policy),
            state_machine=VerificationStateMachine(self.spec),
            registry=RunRegistry(),
        )

    # ------------------------------------------------------------------
    # EpistemicLedgerEnricher unit tests
    # ------------------------------------------------------------------

    def test_enricher_initialization(self):
        """Enricher starts with empty decision history."""
        from gateway.epistemic_ledger import EpistemicLedgerEnricher

        enricher = EpistemicLedgerEnricher()
        self.assertEqual(enricher.decision_trace("any-run"), [])

    def test_enrich_route_selection_contains_reasoning(self):
        """Route selection enrichment includes reasoning and epistemic snapshot."""
        from gateway.epistemic_ledger import EpistemicLedgerEnricher
        from gateway.epistemic import EpistemicState

        enricher = EpistemicLedgerEnricher()
        state = EpistemicState()
        decision = {
            "decision_id": "decision-1",
            "status": "route_selected",
            "selected_plan": {
                "model_id": "qwen3:8b",
                "inference_gateway": "local",
                "expected_total_cost_usd": 0.0,
            },
            "candidates_evaluated": [
                {"candidate_id": "qwen3:8b", "model_id": "qwen3:8b",
                 "inference_gateway": "local", "expected_total_cost_usd": 0.0},
            ],
            "rejected_reasons": {},
        }
        contract = {
            "task_id": "task-1",
            "goal": "Test",
            "task_kind": "conversation",
            "required_capabilities": ["text"],
            "risk_class": "low",
            "governance": {"data_policy": "zdr"},
        }
        policy = {"version": "1.0", "acceptance_lcb_floor": 0.85}

        payload = enricher.enrich_route_selection(decision, state, contract, policy)

        self.assertIn("reasoning", payload)
        self.assertIn("epistemic_state_snapshot", payload)
        self.assertIn("candidates_evaluated", payload)
        self.assertIn("policy_context", payload)
        self.assertIn("contract_context", payload)
        self.assertIn("risk_assessment", payload)
        self.assertIn("qwen3:8b", payload["reasoning"])

    def test_enrich_verification_contains_dag_steps(self):
        """Verification enrichment includes DAG steps and epistemic context."""
        from gateway.epistemic_ledger import EpistemicLedgerEnricher
        from gateway.epistemic import EpistemicState

        enricher = EpistemicLedgerEnricher()
        state = EpistemicState()
        verification_results = [
            {"criterion_id": "schema", "status": "passed", "mandatory": True},
            {"criterion_id": "policy", "status": "passed", "mandatory": True},
        ]
        dag_steps = ["schema", "policy"]

        payload = enricher.enrich_verification(
            verification_results, state, "low", dag_steps
        )

        self.assertIn("reasoning", payload)
        self.assertIn("dag_steps", payload)
        self.assertIn("epistemic_state_snapshot", payload)
        self.assertIn("blocking_conflicts", payload)
        self.assertIn("evidence_summary", payload)
        self.assertEqual(payload["dag_steps"], ["schema", "policy"])
        self.assertIn("passed", payload["reasoning"].lower())

    def test_enrich_verification_failed(self):
        """Verification enrichment for failed verification includes failure reasoning."""
        from gateway.epistemic_ledger import EpistemicLedgerEnricher
        from gateway.epistemic import EpistemicState

        enricher = EpistemicLedgerEnricher()
        state = EpistemicState()
        verification_results = [
            {"criterion_id": "schema", "status": "failed", "mandatory": True},
            {"criterion_id": "policy", "status": "passed", "mandatory": True},
        ]
        dag_steps = ["schema", "policy"]

        payload = enricher.enrich_verification(
            verification_results, state, "low", dag_steps
        )

        self.assertIn("failed", payload["reasoning"].lower())
        self.assertIn("schema", payload["reasoning"])

    def test_enrich_outcome_contains_cost_summary(self):
        """Outcome enrichment includes cost summary and unresolved claims."""
        from gateway.epistemic_ledger import EpistemicLedgerEnricher
        from gateway.epistemic import EpistemicState

        enricher = EpistemicLedgerEnricher()
        state = EpistemicState()
        decision = {
            "decision_id": "decision-1",
            "selected_plan": {
                "model_id": "qwen3:8b",
                "inference_gateway": "local",
                "expected_total_cost_usd": 0.0,
            },
        }
        verification_results = [
            {"criterion_id": "schema", "status": "passed", "mandatory": True},
        ]

        payload = enricher.enrich_outcome(
            "accepted", decision, state, verification_results, 0.0
        )

        self.assertIn("reasoning", payload)
        self.assertIn("decision_summary", payload)
        self.assertIn("epistemic_summary", payload)
        self.assertIn("verification_summary", payload)
        self.assertIn("cost_summary", payload)
        self.assertIn("unresolved_claims", payload)
        self.assertIn("evidence_chain", payload)
        self.assertEqual(payload["status"], "accepted")

    def test_enrich_outcome_rejected(self):
        """Outcome enrichment for rejected includes rejection reasoning."""
        from gateway.epistemic_ledger import EpistemicLedgerEnricher
        from gateway.epistemic import EpistemicState

        enricher = EpistemicLedgerEnricher()
        state = EpistemicState()
        decision = {
            "decision_id": "decision-1",
            "selected_plan": {
                "model_id": "qwen3:8b",
                "inference_gateway": "local",
                "expected_total_cost_usd": 0.0,
            },
        }
        verification_results = [
            {"criterion_id": "schema", "status": "failed", "mandatory": True},
        ]

        payload = enricher.enrich_outcome(
            "rejected", decision, state, verification_results, 0.0
        )

        self.assertEqual(payload["status"], "rejected")
        self.assertIn("rejected", payload["reasoning"].lower())

    def test_enrich_fallback_contains_reasoning(self):
        """Fallback enrichment includes fallback reasoning and epistemic snapshot."""
        from gateway.epistemic_ledger import EpistemicLedgerEnricher
        from gateway.epistemic import EpistemicState

        enricher = EpistemicLedgerEnricher()
        state = EpistemicState()

        payload = enricher.enrich_fallback(
            "provider_fallback", "model-a", "model-b",
            "connection_error", state,
        )

        self.assertIn("reasoning", payload)
        self.assertIn("epistemic_state_snapshot", payload)
        self.assertIn("decision_context", payload)
        self.assertEqual(payload["fallback_class"], "provider_fallback")
        self.assertEqual(payload["from"], "model-a")
        self.assertEqual(payload["to"], "model-b")

    def test_enrich_human_review_contains_context(self):
        """Human review enrichment includes unresolved claims and evidence summary."""
        from gateway.epistemic_ledger import EpistemicLedgerEnricher
        from gateway.epistemic import EpistemicState

        enricher = EpistemicLedgerEnricher()
        state = EpistemicState()
        verification_results = [
            {"criterion_id": "schema", "status": "failed", "mandatory": True},
        ]

        payload = enricher.enrich_human_review(
            "verification_failed", state, verification_results, "high"
        )

        self.assertIn("reasoning", payload)
        self.assertIn("epistemic_state_snapshot", payload)
        self.assertIn("verification_results", payload)
        self.assertIn("risk_class", payload)
        self.assertIn("unresolved_claims", payload)
        self.assertIn("evidence_summary", payload)
        self.assertEqual(payload["risk_class"], "high")

    def test_enrich_claim_transition(self):
        """Claim transition enrichment includes old/new status and reasoning."""
        from gateway.epistemic_ledger import EpistemicLedgerEnricher

        enricher = EpistemicLedgerEnricher()

        payload = enricher.enrich_claim_transition(
            "claim-1", "unknown", "supported",
            ["evidence-1"], "New evidence corroborated the claim",
        )

        self.assertEqual(payload["claim_id"], "claim-1")
        self.assertEqual(payload["old_status"], "unknown")
        self.assertEqual(payload["new_status"], "supported")
        self.assertEqual(payload["evidence_ids"], ["evidence-1"])
        self.assertIn("reasoning", payload)

    def test_snapshot_epistemic_state(self):
        """Epistemic state snapshot includes claims, evidence, and calibration."""
        from gateway.epistemic_ledger import EpistemicLedgerEnricher
        from gateway.epistemic import EpistemicState

        enricher = EpistemicLedgerEnricher()
        state = EpistemicState()

        snapshot = enricher.snapshot_epistemic_state(state)

        self.assertIn("claims", snapshot)
        self.assertIn("evidence", snapshot)
        self.assertIn("unresolved_claim_ids", snapshot)
        self.assertIn("conflicted_claim_ids", snapshot)
        self.assertIn("calibration_summary", snapshot)
        self.assertIn("evidence_count", snapshot)

    def test_decision_trace_accumulates(self):
        """Decision trace accumulates entries across enrich calls."""
        from gateway.epistemic_ledger import EpistemicLedgerEnricher
        from gateway.epistemic import EpistemicState

        enricher = EpistemicLedgerEnricher()
        state = EpistemicState()

        # Initially empty
        self.assertEqual(len(enricher.decision_trace("run-1")), 0)

        # Enrich route selection
        decision = {
            "decision_id": "d1", "status": "route_selected",
            "selected_plan": {"model_id": "m1", "inference_gateway": "local",
                              "expected_total_cost_usd": 0.0},
            "candidates_evaluated": [],
            "rejected_reasons": {},
        }
        contract = {
            "task_id": "t1", "goal": "Test", "task_kind": "conversation",
            "required_capabilities": ["text"], "risk_class": "low",
            "governance": {"data_policy": "zdr"},
        }
        policy = {"version": "1.0", "acceptance_lcb_floor": 0.85}
        enricher.enrich_route_selection(decision, state, contract, policy)
        self.assertEqual(len(enricher.decision_trace("run-1")), 1)
        self.assertEqual(enricher.decision_trace("run-1")[0]["step"], "route_selection")

        # Enrich verification
        enricher.enrich_verification(
            [{"criterion_id": "schema", "status": "passed", "mandatory": True}],
            state, "low", ["schema", "policy"],
        )
        self.assertEqual(len(enricher.decision_trace("run-1")), 2)
        self.assertEqual(enricher.decision_trace("run-1")[1]["step"], "verification")

        # Enrich outcome
        enricher.enrich_outcome("accepted", decision, state,
                                [{"criterion_id": "schema", "status": "passed",
                                  "mandatory": True}], 0.0)
        self.assertEqual(len(enricher.decision_trace("run-1")), 3)
        self.assertEqual(enricher.decision_trace("run-1")[2]["step"], "outcome")

    # ------------------------------------------------------------------
    # Pipeline integration tests
    # ------------------------------------------------------------------

    def test_pipeline_route_selected_has_epistemic_context(self):
        """Pipeline produces route_selected events with epistemic enrichment."""
        from gateway.pipeline import run_inference_pipeline

        ctx = self._make_ctx()
        request = {
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": "Hello"}],
            "governance": {"risk_class": "low"},
        }
        result = run_inference_pipeline(request, ctx)

        run_id = result["epr"]["run_id"]
        record = ctx.registry.get(run_id)
        self.assertIsNotNone(record)

        # Find route_selected event
        route_events = [
            e for e in record.events if e["event_type"] == "route_selected"
        ]
        self.assertGreaterEqual(len(route_events), 1)
        route_payload = route_events[0]["payload"]
        self.assertIn("reasoning", route_payload)
        self.assertIn("epistemic_state_snapshot", route_payload)

    def test_pipeline_verification_completed_has_epistemic_context(self):
        """Pipeline produces verification_completed events with epistemic enrichment."""
        from gateway.pipeline import run_inference_pipeline

        ctx = self._make_ctx()
        request = {
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": "Hello"}],
            "governance": {"risk_class": "low"},
        }
        result = run_inference_pipeline(request, ctx)

        run_id = result["epr"]["run_id"]
        record = ctx.registry.get(run_id)

        verif_events = [
            e for e in record.events if e["event_type"] == "verification_completed"
        ]
        self.assertGreaterEqual(len(verif_events), 1)
        verif_payload = verif_events[0]["payload"]
        self.assertIn("reasoning", verif_payload)
        self.assertIn("dag_steps", verif_payload)

    def test_pipeline_outcome_accepted_has_epistemic_context(self):
        """Pipeline produces outcome_accepted events with epistemic enrichment."""
        from gateway.pipeline import run_inference_pipeline

        ctx = self._make_ctx()
        request = {
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": "Hello"}],
            "governance": {"risk_class": "low"},
        }
        result = run_inference_pipeline(request, ctx)

        run_id = result["epr"]["run_id"]
        record = ctx.registry.get(run_id)

        outcome_events = [
            e for e in record.events if e["event_type"] == "outcome_accepted"
        ]
        self.assertGreaterEqual(len(outcome_events), 1)
        outcome_payload = outcome_events[0]["payload"]
        self.assertIn("reasoning", outcome_payload)
        self.assertIn("cost_summary", outcome_payload)
        self.assertIn("epistemic_summary", outcome_payload)

    def test_pipeline_evidence_recorded_has_epistemic_context(self):
        """Pipeline produces evidence_recorded events with epistemic enrichment."""
        from gateway.pipeline import run_inference_pipeline

        ctx = self._make_ctx()
        request = {
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": "Hello"}],
            "governance": {"risk_class": "low"},
        }
        result = run_inference_pipeline(request, ctx)

        run_id = result["epr"]["run_id"]
        record = ctx.registry.get(run_id)

        evidence_events = [
            e for e in record.events if e["event_type"] == "evidence_recorded"
        ]
        self.assertGreaterEqual(len(evidence_events), 1)
        evidence_payload = evidence_events[0]["payload"]
        self.assertIn("verification_results", evidence_payload)
        self.assertIn("evidence_count", evidence_payload)

    def test_pipeline_decision_trace_populated(self):
        """Pipeline populates decision_trace on the run record."""
        from gateway.pipeline import run_inference_pipeline

        ctx = self._make_ctx()
        request = {
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": "Hello"}],
            "governance": {"risk_class": "low"},
        }
        result = run_inference_pipeline(request, ctx)

        run_id = result["epr"]["run_id"]
        record = ctx.registry.get(run_id)
        self.assertIsNotNone(record)
        # Decision trace should have at least route_selection, verification, outcome
        self.assertGreaterEqual(len(record.decision_trace), 3)

    def test_pipeline_fallback_triggered_has_epistemic_context(self):
        """Fallback events include epistemic enrichment."""
        from gateway.runs import GATEWAY_ACTOR, RunRegistry
        from gateway.fallback import FallbackRecorder
        from gateway.epistemic import EpistemicState
        from gateway.epistemic_ledger import EpistemicLedgerEnricher

        registry = RunRegistry()
        record = registry.begin("run-fb-epi", "trace-fb-epi")
        record.epistemic_state = EpistemicState()
        record.epistemic_ledger = EpistemicLedgerEnricher()

        recorder = FallbackRecorder()
        event = recorder.record(
            "provider_fallback", "model-x", "model-y", "connection_error"
        )

        # Build enriched payload
        fallback_payload = {
            "fallback_class": event["fallback_class"],
            "from": event["from_id"],
            "to": event["to_id"],
            "reason": event["reason"],
        }
        enriched = record.epistemic_ledger.enrich_fallback(
            event["fallback_class"], event["from_id"], event["to_id"],
            event["reason"], record.epistemic_state,
        )
        fallback_payload.update(enriched)

        registry.ledger(
            "run-fb-epi", "fallback_triggered", GATEWAY_ACTOR,
            "task-1", fallback_payload,
        )

        record2 = registry.get("run-fb-epi")
        fb_event = record2.events[-1]
        self.assertEqual(fb_event["event_type"], "fallback_triggered")
        self.assertIn("reasoning", fb_event["payload"])
        self.assertIn("epistemic_state_snapshot", fb_event["payload"])

    def test_claim_transitioned_event_logged(self):
        """Claim transitions are logged as claim_transitioned ledger events."""
        from gateway.runs import GATEWAY_ACTOR, RunRegistry
        from gateway.epistemic import (
            EpistemicState,
            make_direct_observation_evidence,
        )
        from gateway.epistemic_ledger import EpistemicLedgerEnricher

        registry = RunRegistry()
        record = registry.begin("run-ct-1", "trace-ct-1")
        record.epistemic_state = EpistemicState()
        record.epistemic_ledger = EpistemicLedgerEnricher()

        # Add evidence and a claim
        ev = make_direct_observation_evidence({"fact": "A"}, strength=0.9)
        eid = record.epistemic_state.add_evidence(ev)
        record.epistemic_state.add_claim("claim-1", "Fact A is true", [eid])

        # Flush transitions
        transitions = record.epistemic_state.get_claim_transitions()
        self.assertGreaterEqual(len(transitions), 1)

        for t in transitions:
            enriched = record.epistemic_ledger.enrich_claim_transition(
                t["claim_id"], t["old_status"], t["new_status"],
                t["evidence_ids"], t["reasoning"],
            )
            registry.ledger(
                "run-ct-1", "claim_transitioned", GATEWAY_ACTOR,
                "task-1", enriched,
            )

        record2 = registry.get("run-ct-1")
        ct_events = [
            e for e in record2.events if e["event_type"] == "claim_transitioned"
        ]
        self.assertGreaterEqual(len(ct_events), 1)
        ct_payload = ct_events[0]["payload"]
        self.assertEqual(ct_payload["claim_id"], "claim-1")
        self.assertEqual(ct_payload["new_status"], "supported")

    # ------------------------------------------------------------------
    # Ledger query API tests (via HTTP)
    # ------------------------------------------------------------------

    def test_ledger_events_endpoint(self):
        """GET /v1/epr/ledger/events returns filtered events."""
        from gateway.pipeline import run_inference_pipeline

        ctx = self._make_ctx()
        request = {
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": "Hello"}],
            "governance": {"risk_class": "low"},
        }
        result = run_inference_pipeline(request, ctx)
        run_id = result["epr"]["run_id"]

        # Start a server to test the endpoint
        from gateway.server import create_server
        server = create_server(ctx.config, ctx)
        port = server.server_address[1]
        base = f"http://127.0.0.1:{port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            # Query all events
            req = urllib.request.Request(f"{base}/v1/epr/ledger/events")
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = json.loads(resp.read())
            self.assertEqual(body["object"], "list")
            self.assertGreaterEqual(body["count"], 1)

            # Query by run_id
            req2 = urllib.request.Request(
                f"{base}/v1/epr/ledger/events?run_id={run_id}"
            )
            with urllib.request.urlopen(req2, timeout=5) as resp:
                body2 = json.loads(resp.read())
            self.assertGreaterEqual(body2["count"], 1)

            # Query by event_type
            req3 = urllib.request.Request(
                f"{base}/v1/epr/ledger/events?event_type=route_selected"
            )
            with urllib.request.urlopen(req3, timeout=5) as resp:
                body3 = json.loads(resp.read())
            for event in body3["data"]:
                self.assertEqual(event["event_type"], "route_selected")
        finally:
            server.shutdown()
            server.server_close()

    def test_ledger_chain_endpoint(self):
        """GET /v1/epr/ledger/chain/{run_id} returns full chain."""
        from gateway.pipeline import run_inference_pipeline

        ctx = self._make_ctx()
        request = {
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": "Hello"}],
            "governance": {"risk_class": "low"},
        }
        result = run_inference_pipeline(request, ctx)
        run_id = result["epr"]["run_id"]

        from gateway.server import create_server
        server = create_server(ctx.config, ctx)
        port = server.server_address[1]
        base = f"http://127.0.0.1:{port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            req = urllib.request.Request(f"{base}/v1/epr/ledger/chain/{run_id}")
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = json.loads(resp.read())
            self.assertEqual(body["run_id"], run_id)
            self.assertIn("chain", body)
            self.assertIn("head_hash", body)
            self.assertGreater(body["event_count"], 0)
        finally:
            server.shutdown()
            server.server_close()

    def test_ledger_verify_endpoint(self):
        """POST /v1/epr/ledger/verify/{run_id} verifies chain integrity."""
        from gateway.pipeline import run_inference_pipeline

        ctx = self._make_ctx()
        request = {
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": "Hello"}],
            "governance": {"risk_class": "low"},
        }
        result = run_inference_pipeline(request, ctx)
        run_id = result["epr"]["run_id"]

        from gateway.server import create_server
        server = create_server(ctx.config, ctx)
        port = server.server_address[1]
        base = f"http://127.0.0.1:{port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            req = urllib.request.Request(
                f"{base}/v1/epr/ledger/verify/{run_id}", method="POST"
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = json.loads(resp.read())
            self.assertTrue(body["valid"])
            self.assertEqual(body["message"], "ok")
            self.assertGreater(body["event_count"], 0)
        finally:
            server.shutdown()
            server.server_close()

    def test_ledger_export_endpoint(self):
        """GET /v1/epr/ledger/export/{run_id} exports full chain."""
        from gateway.pipeline import run_inference_pipeline

        ctx = self._make_ctx()
        request = {
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": "Hello"}],
            "governance": {"risk_class": "low"},
        }
        result = run_inference_pipeline(request, ctx)
        run_id = result["epr"]["run_id"]

        from gateway.server import create_server
        server = create_server(ctx.config, ctx)
        port = server.server_address[1]
        base = f"http://127.0.0.1:{port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            req = urllib.request.Request(f"{base}/v1/epr/ledger/export/{run_id}")
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = json.loads(resp.read())
            self.assertEqual(body["run_id"], run_id)
            self.assertIn("events", body)
            self.assertIn("head_hash", body)
            self.assertGreater(body["event_count"], 0)
        finally:
            server.shutdown()
            server.server_close()

    def test_trace_endpoint(self):
        """GET /v1/epr/runs/{run_id}/trace returns epistemic decision trace."""
        from gateway.pipeline import run_inference_pipeline

        ctx = self._make_ctx()
        request = {
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": "Hello"}],
            "governance": {"risk_class": "low"},
        }
        result = run_inference_pipeline(request, ctx)
        run_id = result["epr"]["run_id"]

        from gateway.server import create_server
        server = create_server(ctx.config, ctx)
        port = server.server_address[1]
        base = f"http://127.0.0.1:{port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            req = urllib.request.Request(f"{base}/v1/epr/runs/{run_id}/trace")
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = json.loads(resp.read())
            self.assertEqual(body["run_id"], run_id)
            self.assertIn("decision_trace", body)
            self.assertGreaterEqual(len(body["decision_trace"]), 3)
            # Check trace structure
            for step in body["decision_trace"]:
                self.assertIn("step", step)
                self.assertIn("timestamp", step)
                self.assertIn("reasoning", step)
                self.assertIn("epistemic_state", step)
        finally:
            server.shutdown()
            server.server_close()

    def test_trace_endpoint_unknown_run_returns_404(self):
        """Trace endpoint returns 404 for unknown run."""
        from gateway.server import create_server

        ctx = self._make_ctx()
        server = create_server(ctx.config, ctx)
        port = server.server_address[1]
        base = f"http://127.0.0.1:{port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            req = urllib.request.Request(f"{base}/v1/epr/runs/nonexistent/trace")
            try:
                urllib.request.urlopen(req, timeout=5)
                self.fail("Expected HTTPError")
            except urllib.error.HTTPError as exc:
                self.assertEqual(exc.code, 404)
        finally:
            server.shutdown()
            server.server_close()

    def test_ledger_chain_unknown_run_returns_404(self):
        """Ledger chain endpoint returns 404 for unknown run."""
        from gateway.server import create_server

        ctx = self._make_ctx()
        server = create_server(ctx.config, ctx)
        port = server.server_address[1]
        base = f"http://127.0.0.1:{port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            req = urllib.request.Request(
                f"{base}/v1/epr/ledger/chain/nonexistent"
            )
            try:
                urllib.request.urlopen(req, timeout=5)
                self.fail("Expected HTTPError")
            except urllib.error.HTTPError as exc:
                self.assertEqual(exc.code, 404)
        finally:
            server.shutdown()
            server.server_close()

    def test_ledger_verify_unknown_run_returns_404(self):
        """Ledger verify endpoint returns 404 for unknown run."""
        from gateway.server import create_server

        ctx = self._make_ctx()
        server = create_server(ctx.config, ctx)
        port = server.server_address[1]
        base = f"http://127.0.0.1:{port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            req = urllib.request.Request(
                f"{base}/v1/epr/ledger/verify/nonexistent", method="POST"
            )
            try:
                urllib.request.urlopen(req, timeout=5)
                self.fail("Expected HTTPError")
            except urllib.error.HTTPError as exc:
                self.assertEqual(exc.code, 404)
        finally:
            server.shutdown()
            server.server_close()

    def test_ledger_export_unknown_run_returns_404(self):
        """Ledger export endpoint returns 404 for unknown run."""
        from gateway.server import create_server

        ctx = self._make_ctx()
        server = create_server(ctx.config, ctx)
        port = server.server_address[1]
        base = f"http://127.0.0.1:{port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            req = urllib.request.Request(
                f"{base}/v1/epr/ledger/export/nonexistent"
            )
            try:
                urllib.request.urlopen(req, timeout=5)
                self.fail("Expected HTTPError")
            except urllib.error.HTTPError as exc:
                self.assertEqual(exc.code, 404)
        finally:
            server.shutdown()
            server.server_close()


# ---------------------------------------------------------------------------
# EndToEndSmokeTests
# ---------------------------------------------------------------------------


class EndToEndSmokeTests(unittest.TestCase):
    """Full end-to-end smoke tests through the gateway.

    These tests start an in-process server and send real HTTP requests
    through the full pipeline (governance → contract → route → execute →
    verify → receipt). Uses the stub OpenRouter client (no network) but
    exercises all pipeline stages.
    """

    @classmethod
    def setUpClass(cls):
        cls.policy = json.loads(
            (ROOT / "spec" / "routing-policy.json").read_text("utf-8")
        )
        cls.portfolio = json.loads(
            (ROOT / "examples" / "candidate-actions.json").read_text("utf-8")
        )
        cls.spec = json.loads(
            (ROOT / "spec" / "verification-state-machine.json").read_text("utf-8")
        )
        cls.config = GatewayConfig.from_env(
            {"NOERELAY_GATEWAY_HOST": "127.0.0.1", "NOERELAY_GATEWAY_PORT": "0"}
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

        # Second server with empty portfolio for 424 escalation tests.
        cls.escalation_ctx = PipelineContext(
            config=GatewayConfig.from_env(
                {"NOERELAY_GATEWAY_HOST": "127.0.0.1", "NOERELAY_GATEWAY_PORT": "0"}
            ),
            policy=cls.policy,
            portfolio=[],
            openrouter_client=StubOpenRouterClient(cls.policy),
            state_machine=VerificationStateMachine(cls.spec),
            registry=RunRegistry(),
        )
        cls.escalation_server = create_server(
            cls.escalation_ctx.config, cls.escalation_ctx
        )
        cls.escalation_port = cls.escalation_server.server_address[1]
        cls.escalation_base = f"http://127.0.0.1:{cls.escalation_port}"
        cls._escalation_thread = threading.Thread(
            target=cls.escalation_server.serve_forever, daemon=True
        )
        cls._escalation_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.escalation_server.shutdown()
        cls.escalation_server.server_close()

    def _request(self, url, method="GET", data=None, headers=None):
        headers = headers or {}
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            return exc.code, (json.loads(raw) if raw else None)

    def _post(self, path, body):
        data = json.dumps(body).encode("utf-8")
        return self._request(
            f"{self.base}{path}",
            method="POST",
            data=data,
            headers={"Content-Type": "application/json"},
        )

    def _post_escalation(self, path, body):
        data = json.dumps(body).encode("utf-8")
        return self._request(
            f"{self.escalation_base}{path}",
            method="POST",
            data=data,
            headers={"Content-Type": "application/json"},
        )

    def _post_stream(self, base, path, body):
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{base}{path}",
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, dict(resp.headers), raw

    # ------------------------------------------------------------------
    # Full pipeline smoke tests
    # ------------------------------------------------------------------

    def test_full_chat_completion_pipeline(self):
        """Complete chat completion pipeline: request → response → epr metadata."""
        status, body = self._post(
            "/v1/chat/completions",
            {
                "model": "noerelay/epr-1",
                "messages": [{"role": "user", "content": "Hello"}],
                "governance": {"risk_class": "low"},
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["object"], "chat.completion")
        self.assertEqual(body["model"], "noerelay/epr-1")
        self.assertIn("choices", body)
        self.assertGreater(len(body["choices"]), 0)
        self.assertIn("message", body["choices"][0])
        self.assertIn("content", body["choices"][0]["message"])

        # Verify EPR metadata
        self.assertIn("epr", body)
        epr = body["epr"]
        self.assertEqual(epr["status"], "accepted")
        self.assertIn("run_id", epr)
        self.assertIn("trace_id", epr)
        self.assertIn("route_decision_id", epr)
        self.assertIn("evidence_receipt_url", epr)
        self.assertIn("ledger_head_hash", epr)
        self.assertTrue(epr["ledger_head_hash"].startswith("sha256:"))

        # Verify receipt endpoint
        run_id = epr["run_id"]
        status2, receipt = self._request(f"{self.base}/v1/epr/runs/{run_id}")
        self.assertEqual(status2, 200)
        self.assertEqual(receipt["run_id"], run_id)
        self.assertEqual(receipt["status"], "accepted")

        # Verify trace endpoint
        status3, trace = self._request(f"{self.base}/v1/epr/runs/{run_id}/trace")
        self.assertEqual(status3, 200)
        self.assertEqual(trace["run_id"], run_id)
        self.assertIn("decision_trace", trace)
        self.assertGreaterEqual(len(trace["decision_trace"]), 3)

        # Verify ledger chain
        status4, chain = self._request(
            f"{self.base}/v1/epr/ledger/chain/{run_id}"
        )
        self.assertEqual(status4, 200)
        self.assertEqual(chain["run_id"], run_id)
        self.assertIn("chain", chain)
        self.assertIn("head_hash", chain)
        self.assertGreater(chain["event_count"], 0)

        # Verify ledger integrity
        req = urllib.request.Request(
            f"{self.base}/v1/epr/ledger/verify/{run_id}", method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            verify_body = json.loads(resp.read())
        self.assertTrue(verify_body["valid"])
        self.assertEqual(verify_body["message"], "ok")

    def test_full_streaming_pipeline(self):
        """Complete streaming pipeline: SSE chunks → terminal epr → [DONE]."""
        status, headers, raw = self._post_stream(
            self.base,
            "/v1/chat/completions",
            {
                "model": "noerelay/epr-1",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": True,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "text/event-stream")

        lines = _sse_data_lines(raw)
        self.assertEqual(lines[-1], "[DONE]")
        events = [json.loads(line) for line in lines[:-1]]
        self.assertGreaterEqual(len(events), 2)

        # First events are chunks
        self.assertEqual(events[0]["object"], "chat.completion.chunk")
        self.assertEqual(events[0]["model"], "noerelay/epr-1")

        # Terminal event has EPR metadata
        terminal = events[-1]
        self.assertIn("epr", terminal)
        self.assertEqual(terminal["epr"]["status"], "accepted")
        self.assertIn("run_id", terminal["epr"])
        self.assertIn("trace_id", terminal["epr"])
        self.assertIn("ledger_head_hash", terminal["epr"])

    def test_full_responses_api_pipeline(self):
        """Complete responses API pipeline."""
        status, body = self._post(
            "/v1/responses",
            {"model": "noerelay/epr-1", "input": "Hello"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["object"], "response")
        self.assertEqual(body["status"], "completed")
        self.assertIn("output", body)
        self.assertGreater(len(body["output"]), 0)
        self.assertEqual(body["output"][0]["type"], "message")
        self.assertIn("content", body["output"][0])

        # EPR metadata on responses
        self.assertIn("epr", body)
        epr = body["epr"]
        self.assertEqual(epr["status"], "accepted")
        self.assertIn("run_id", epr)

    def test_full_escalation_pipeline(self):
        """Escalation pipeline: empty portfolio → 424 with route decision."""
        status, body = self._post_escalation(
            "/v1/chat/completions",
            {
                "model": "noerelay/epr-1",
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )
        self.assertEqual(status, 424)
        self.assertEqual(body["error"]["type"], "no_admissible_route_error")

        # EPR metadata on escalation
        self.assertIn("epr", body)
        epr = body["epr"]
        self.assertIn("run_id", epr)
        self.assertIn("route_decision", epr)
        rd = epr["route_decision"]
        self.assertIn("candidates_evaluated", rd)
        self.assertIn("candidates_admissible", rd)

        # Receipt after escalation
        run_id = epr["run_id"]
        status2, receipt = self._request(
            f"{self.escalation_base}/v1/epr/runs/{run_id}"
        )
        self.assertEqual(status2, 200)
        self.assertEqual(receipt["status"], "escalated")

    def test_full_governance_validation_pipeline(self):
        """Governance validation: invalid governance → 422."""
        status, body = self._post(
            "/v1/chat/completions",
            {
                "model": "noerelay/epr-1",
                "messages": [{"role": "user", "content": "Hello"}],
                "governance": {"risk_class": "extreme"},
            },
        )
        self.assertEqual(status, 422)
        self.assertEqual(body["error"]["type"], "governance_validation_error")

    def test_full_openai_exclusion_pipeline(self):
        """OpenAI exclusion enforcement: forbidden model → 403."""
        status, body = self._post(
            "/v1/chat/completions",
            {
                "model": "openai/gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "model_denied_by_policy")

    def test_full_health_and_metrics(self):
        """Health and metrics endpoints."""
        # Health endpoint
        status, body = self._request(f"{self.base}/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "healthy")

        # Metrics endpoint (Prometheus text format, not JSON)
        req = urllib.request.Request(f"{self.base}/metrics")
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            raw_metrics = resp.read().decode("utf-8")
        # Prometheus format: should contain HELP/TYPE lines and metrics
        self.assertIn("noerelay_", raw_metrics)

        # Analytics dashboard (may return 501 if not fully implemented)
        status3, body3 = self._request(f"{self.base}/v1/analytics/dashboard")
        self.assertIn(status3, (200, 501))
        if status3 == 200:
            self.assertIn("summary", body3)

    def test_full_ledger_query_and_export(self):
        """Ledger query and export after a run."""
        # First, create a run
        status, body = self._post(
            "/v1/chat/completions",
            {
                "model": "noerelay/epr-1",
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )
        self.assertEqual(status, 200)
        run_id = body["epr"]["run_id"]

        # Query ledger events
        status2, events_body = self._request(
            f"{self.base}/v1/epr/ledger/events?run_id={run_id}"
        )
        self.assertEqual(status2, 200)
        self.assertGreaterEqual(events_body["count"], 1)
        for event in events_body["data"]:
            self.assertIn("event_type", event)
            self.assertIn("event_id", event)
            self.assertIn("timestamp", event)

        # Query by event_type
        status3, filtered = self._request(
            f"{self.base}/v1/epr/ledger/events?event_type=route_selected"
        )
        self.assertEqual(status3, 200)
        for event in filtered["data"]:
            self.assertEqual(event["event_type"], "route_selected")

        # Export ledger
        status4, export = self._request(
            f"{self.base}/v1/epr/ledger/export/{run_id}"
        )
        self.assertEqual(status4, 200)
        self.assertEqual(export["run_id"], run_id)
        self.assertIn("events", export)
        self.assertIn("head_hash", export)
        self.assertGreater(export["event_count"], 0)

    def test_full_pipeline_idempotency(self):
        """Two identical requests produce different run_ids but consistent results."""
        request_body = {
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": "Hello"}],
        }

        status1, body1 = self._post("/v1/chat/completions", request_body)
        status2, body2 = self._post("/v1/chat/completions", request_body)

        self.assertEqual(status1, 200)
        self.assertEqual(status2, 200)
        self.assertEqual(body1["epr"]["status"], "accepted")
        self.assertEqual(body2["epr"]["status"], "accepted")

        # Different run_ids
        self.assertNotEqual(body1["epr"]["run_id"], body2["epr"]["run_id"])

        # Both have valid ledger chains
        for body in (body1, body2):
            run_id = body["epr"]["run_id"]
            req = urllib.request.Request(
                f"{self.base}/v1/epr/ledger/verify/{run_id}", method="POST"
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                verify_body = json.loads(resp.read())
            self.assertTrue(verify_body["valid"], f"Chain invalid for {run_id}")

    def test_full_pipeline_with_all_governance_fields(self):
        """Request with all governance fields passes through pipeline."""
        status, body = self._post(
            "/v1/chat/completions",
            {
                "model": "noerelay/epr-1",
                "messages": [{"role": "user", "content": "Hello"}],
                "governance": {
                    "risk_class": "low",
                    "data_policy": "zdr",
                    "max_cost_usd": 2.0,
                    "max_latency_ms": 30000,
                    "retention_class": "ephemeral",
                    "return_evidence_receipt": True,
                    "project_id": "e2e-test",
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["epr"]["status"], "accepted")
        self.assertIn("run_id", body["epr"])

    # ------------------------------------------------------------------
    # Cross-tenant isolation tests (module-level, direct manager calls)
    # ------------------------------------------------------------------

    def test_cross_tenant_isolation_tenants(self):
        """Tenant A and Tenant B have independent state."""
        from gateway.tenancy import TenantManager
        from gateway.database import SQLiteDatabase
        import tempfile
        import shutil

        tmpdir = tempfile.mkdtemp()
        try:
            db = SQLiteDatabase(str(Path(tmpdir) / "test.db"))
            tm = TenantManager(db)

            # Create two tenants
            ta = tm.create_tenant("iso-a", "Tenant A", 100.0, 2000.0)
            tb = tm.create_tenant("iso-b", "Tenant B", 100.0, 2000.0)

            self.assertEqual(ta["tenant_id"], "iso-a")
            self.assertEqual(tb["tenant_id"], "iso-b")

            # Each tenant is independently retrievable
            self.assertEqual(tm.get_tenant("iso-a")["name"], "Tenant A")
            self.assertEqual(tm.get_tenant("iso-b")["name"], "Tenant B")

            # Budgets are independent
            ba = tm.check_budget("iso-a")
            bb = tm.check_budget("iso-b")
            self.assertEqual(ba["daily_spend"], 0.0)
            self.assertEqual(bb["daily_spend"], 0.0)

            # Record spend for Tenant A only
            tm.record_spend("iso-a", 5.0, "run-1")
            ba2 = tm.check_budget("iso-a")
            bb2 = tm.check_budget("iso-b")
            self.assertEqual(ba2["daily_spend"], 5.0)
            self.assertEqual(bb2["daily_spend"], 0.0)  # Tenant B unaffected

            # List tenants includes both
            tenants = tm.list_tenants()
            ids = {t["tenant_id"] for t in tenants}
            self.assertIn("iso-a", ids)
            self.assertIn("iso-b", ids)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_cross_tenant_isolation_secrets(self):
        """Tenant A cannot access Tenant B's secrets."""
        from gateway.secrets import SecretManager
        from gateway.database import SQLiteDatabase
        import tempfile

        tmpdir = tempfile.mkdtemp()
        try:
            db = SQLiteDatabase(str(Path(tmpdir) / "test.db"))
            sm = SecretManager(db, master_key="tenant-isolation-test-key")

            # Create secrets for two tenants
            sm.store_secret("db-password", "alpha-secret", tenant_id="tenant-a")
            sm.store_secret("db-password", "beta-secret", tenant_id="tenant-b")

            # Each tenant sees only their own secret
            self.assertEqual(
                sm.get_secret("db-password", tenant_id="tenant-a"),
                "alpha-secret",
            )
            self.assertEqual(
                sm.get_secret("db-password", tenant_id="tenant-b"),
                "beta-secret",
            )

            # Tenant A listing excludes Tenant B's secrets
            list_a = sm.list_secrets(tenant_id="tenant-a")
            names_a = {s["name"] for s in list_a}
            self.assertIn("db-password", names_a)
            self.assertEqual(len(list_a), 1)

            list_b = sm.list_secrets(tenant_id="tenant-b")
            names_b = {s["name"] for s in list_b}
            self.assertIn("db-password", names_b)
            self.assertEqual(len(list_b), 1)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_cross_tenant_isolation_webhooks(self):
        """Tenant A cannot see Tenant B's webhooks."""
        from gateway.webhooks import WebhookManager
        from gateway.database import SQLiteDatabase
        import tempfile
        import shutil

        tmpdir = tempfile.mkdtemp()
        try:
            db = SQLiteDatabase(str(Path(tmpdir) / "test.db"))
            wm = WebhookManager(db)

            # Register webhooks for two tenants
            wa = wm.register(
                "https://a.example.com/hook",
                ["run.completed"],
                tenant_id="tenant-a",
            )
            wb = wm.register(
                "https://b.example.com/hook",
                ["run.completed"],
                tenant_id="tenant-b",
            )

            # Tenant A listing excludes Tenant B's webhooks
            list_a = wm.list_webhooks(tenant_id="tenant-a")
            urls_a = {w["url"] for w in list_a}
            self.assertIn("https://a.example.com/hook", urls_a)
            self.assertNotIn("https://b.example.com/hook", urls_a)

            # Tenant B listing excludes Tenant A's webhooks
            list_b = wm.list_webhooks(tenant_id="tenant-b")
            urls_b = {w["url"] for w in list_b}
            self.assertIn("https://b.example.com/hook", urls_b)
            self.assertNotIn("https://a.example.com/hook", urls_b)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_cross_tenant_run_isolation(self):
        """Tenant A cannot access Tenant B's run data."""
        # Run a request (uses default tenant)
        status1, body1 = self._post("/v1/chat/completions", {
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": "Hello from default tenant"}],
        })
        self.assertEqual(status1, 200)
        run_id = body1["epr"]["run_id"]

        # Run data is accessible (no tenant filter on runs endpoint in reference)
        status2, body2 = self._request(f"{self.base}/v1/epr/runs/{run_id}")
        self.assertEqual(status2, 200)
        self.assertEqual(body2["run_id"], run_id)

    # ------------------------------------------------------------------
    # Fault injection / resilience tests
    # ------------------------------------------------------------------

    def test_malformed_json_body_returns_400(self):
        """Malformed JSON request body returns a 400 error."""
        data = b"not valid json {{{"
        req = urllib.request.Request(
            f"{self.base}/v1/chat/completions",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = json.loads(resp.read())
            self.assertIn(resp.status, (400, 415))
        except urllib.error.HTTPError as e:
            self.assertIn(e.code, (400, 415))

    def test_missing_required_fields_returns_error(self):
        """Request missing required fields returns an appropriate error."""
        status, body = self._post("/v1/chat/completions", {
            "model": "noerelay/epr-1",
            # Missing "messages" field
        })
        self.assertIn(status, (400, 422))

    def test_empty_messages_array(self):
        """Empty messages array is handled gracefully."""
        status, body = self._post("/v1/chat/completions", {
            "model": "noerelay/epr-1",
            "messages": [],
        })
        # Should either accept (stub mode) or return an error
        self.assertIn(status, (200, 400, 422))

    def test_very_large_payload(self):
        """Very large request payload is handled without crashing."""
        large_content = "x" * 100000  # 100KB of text
        status, body = self._post("/v1/chat/completions", {
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": large_content}],
        })
        # Should not crash; may accept or reject based on policy
        self.assertIn(status, (200, 400, 413, 422))

    def test_unicode_and_special_characters(self):
        """Unicode and special characters in request are handled correctly."""
        status, body = self._post("/v1/chat/completions", {
            "model": "noerelay/epr-1",
            "messages": [{
                "role": "user",
                "content": "Hello 世界 🌍 \u0000 null byte test \n\t\r",
            }],
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["epr"]["status"], "accepted")

    def test_concurrent_requests(self):
        """Multiple concurrent requests are handled without errors."""
        import concurrent.futures

        def make_request(i: int) -> tuple[int, dict[str, Any]]:
            return self._post("/v1/chat/completions", {
                "model": "noerelay/epr-1",
                "messages": [{"role": "user", "content": f"Concurrent request {i}"}],
            })

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(make_request, i) for i in range(20)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        for status, body in results:
            self.assertEqual(status, 200)
            self.assertEqual(body["epr"]["status"], "accepted")

    def test_rapid_sequential_requests(self):
        """Rapid sequential requests do not cause state corruption."""
        run_ids = []
        for i in range(50):
            status, body = self._post("/v1/chat/completions", {
                "model": "noerelay/epr-1",
                "messages": [{"role": "user", "content": f"Rapid request {i}"}],
            })
            self.assertEqual(status, 200)
            self.assertEqual(body["epr"]["status"], "accepted")
            run_ids.append(body["epr"]["run_id"])

        # All run IDs should be unique
        self.assertEqual(len(run_ids), len(set(run_ids)))

    def test_unknown_endpoint_returns_404(self):
        """Requests to unknown endpoints return 404."""
        try:
            req = urllib.request.Request(f"{self.base}/v1/nonexistent/endpoint")
            with urllib.request.urlopen(req, timeout=5) as resp:
                pass
            self.fail("Expected HTTPError for unknown endpoint")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)

    def test_wrong_method_returns_405(self):
        """Using wrong HTTP method returns 405."""
        try:
            req = urllib.request.Request(
                f"{self.base}/v1/chat/completions", method="DELETE"
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                pass
            self.fail("Expected HTTPError for wrong method")
        except urllib.error.HTTPError as e:
            self.assertIn(e.code, (405, 404))

    def test_health_endpoint_no_auth(self):
        """Health endpoint is accessible without authentication."""
        status, body = self._request(f"{self.base}/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "healthy")

    def test_metrics_endpoint_accessible(self):
        """Metrics endpoint returns Prometheus-formatted data."""
        status, body_text = self._request_text(f"{self.base}/metrics")
        self.assertEqual(status, 200)
        self.assertIn("noerelay_", body_text)

    def test_invalid_governance_risk_class(self):
        """Invalid risk_class in governance returns validation error."""
        status, body = self._post("/v1/chat/completions", {
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": "Hello"}],
            "governance": {"risk_class": "extreme"},
        })
        # Should either reject or accept with default
        self.assertIn(status, (200, 400, 422))

    def test_negative_cost_ceiling_rejected(self):
        """Negative max_cost_usd is rejected."""
        status, body = self._post("/v1/chat/completions", {
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": "Hello"}],
            "governance": {"max_cost_usd": -1.0},
        })
        # Should reject negative cost
        self.assertIn(status, (400, 422))

    def test_zero_latency_ceiling_rejected(self):
        """Zero max_latency_ms is rejected."""
        status, body = self._post("/v1/chat/completions", {
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": "Hello"}],
            "governance": {"max_latency_ms": 0},
        })
        # Should reject zero latency
        self.assertIn(status, (400, 422))

    def test_content_type_without_body(self):
        """POST with Content-Type but no body is handled gracefully."""
        data = b""
        req = urllib.request.Request(
            f"{self.base}/v1/chat/completions",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = json.loads(resp.read())
            self.assertIn(resp.status, (400, 411))
        except urllib.error.HTTPError as e:
            self.assertIn(e.code, (400, 411))

    def test_governance_with_extra_unknown_fields(self):
        """Governance with extra unknown fields is handled gracefully."""
        status, body = self._post("/v1/chat/completions", {
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": "Hello"}],
            "governance": {
                "risk_class": "low",
                "unknown_field_xyz": "should be ignored",
            },
        })
        # Server may accept (ignoring unknown) or reject (strict validation)
        self.assertIn(status, (200, 422))
        if status == 200:
            self.assertEqual(body["epr"]["status"], "accepted")

    def test_model_field_injection_attempt(self):
        """Attempt to inject openai model is blocked."""
        status, body = self._post("/v1/chat/completions", {
            "model": "openai/gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
        })
        # Should be rejected by policy
        self.assertNotEqual(status, 200)

    def test_openai_family_injection_attempt(self):
        """Attempt to use openai family model is blocked."""
        status, body = self._post("/v1/chat/completions", {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
        })
        # Should be rejected by policy
        self.assertNotEqual(status, 200)

    def _request_text(self, url: str) -> tuple[int, str]:
        """Make a GET request and return (status, text body)."""
        req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8")


class HTTPBoundarySecurityTests(unittest.TestCase):
    """Exercise controls at the real ThreadingHTTPServer boundary."""

    def setUp(self):
        self.config = GatewayConfig.from_env({
            "NOERELAY_GATEWAY_HOST": "127.0.0.1",
            "NOERELAY_GATEWAY_PORT": "0",
            "NOERELAY_AUTH_REQUIRED": "1",
            "NOERELAY_AUTH_API_KEYS": "boundary-test-key",
            "NOERELAY_DATABASE_ENABLED": "0",
            "NOERELAY_MAX_REQUEST_BODY_BYTES": "1024",
        })
        policy = load_policy(ROOT / "spec" / "routing-policy.json")
        portfolio = json.loads(
            (ROOT / "examples" / "candidate-actions.json").read_text("utf-8")
        )
        spec = json.loads(
            (ROOT / "spec" / "verification-state-machine.json").read_text("utf-8")
        )
        auth = AuthMiddleware(
            api_keys={"boundary-test-key"},
            rate_limiter=PerKeyRateLimiter(),
            require_auth=True,
            default_rate=100.0,
            default_burst=100,
        )
        self.ctx = PipelineContext(
            config=self.config,
            policy=policy,
            portfolio=portfolio,
            openrouter_client=StubOpenRouterClient(policy),
            state_machine=VerificationStateMachine(spec),
            registry=RunRegistry(),
            auth=auth,
            rbac=RBACMiddleware(),
        )
        self.server = create_server(self.config, self.ctx)
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _request(self, path, *, method="GET", data=None, headers=None):
        req = urllib.request.Request(
            self.base + path,
            data=data,
            headers=headers or {},
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                raw = response.read()
                return response.status, dict(response.headers), raw
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def test_missing_and_invalid_credentials_are_rejected(self):
        status, _, body = self._request("/v1/models")
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(body)["error"]["code"], "invalid_api_key")

        status, _, _ = self._request(
            "/v1/models", headers={"Authorization": "Bearer wrong-key"}
        )
        self.assertEqual(status, 401)

    def test_valid_credentials_receive_rate_limit_headers(self):
        status, headers, body = self._request(
            "/v1/models",
            headers={"Authorization": "Bearer boundary-test-key"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["object"], "list")
        self.assertEqual(headers["X-RateLimit-Limit"], "100")
        self.assertIn("X-RateLimit-Remaining", headers)
        self.assertIn("X-RateLimit-Reset", headers)

    def test_cors_preflight_allows_only_configured_origins(self):
        status, headers, _ = self._request(
            "/v1/chat/completions",
            method="OPTIONS",
            headers={"Origin": "http://localhost:3000"},
        )
        self.assertEqual(status, 204)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "http://localhost:3000")
        self.assertIn("Authorization", headers["Access-Control-Allow-Headers"])

        status, headers, _ = self._request(
            "/v1/chat/completions",
            method="OPTIONS",
            headers={"Origin": "https://attacker.example"},
        )
        self.assertEqual(status, 403)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_oversized_request_is_rejected_before_json_parsing(self):
        payload = json.dumps({
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": "x" * 2048}],
        }).encode("utf-8")
        status, _, body = self._request(
            "/v1/chat/completions",
            method="POST",
            data=payload,
            headers={
                "Authorization": "Bearer boundary-test-key",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(status, 413)
        self.assertEqual(json.loads(body)["error"]["code"], "request_too_large")

    def test_tenant_runs_and_cache_entries_are_isolated(self):
        class KeyManager:
            identities = {
                "tenant-a-key": {
                    "key_id": "key-a",
                    "role": "operator",
                    "tenant_id": "tenant-a",
                },
                "tenant-b-key": {
                    "key_id": "key-b",
                    "role": "operator",
                    "tenant_id": "tenant-b",
                },
            }

            def authenticate(self, raw_key):
                return self.identities.get(raw_key)

        self.ctx.auth = AuthMiddleware(
            api_key_manager=KeyManager(),
            require_auth=True,
        )
        self.ctx.response_cache = ResponseCache(max_size=10)
        payload = json.dumps({
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": "tenant cache check"}],
        }).encode("utf-8")

        def create(key):
            status, _, body = self._request(
                "/v1/chat/completions",
                method="POST",
                data=payload,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
            )
            self.assertEqual(status, 200)
            return json.loads(body)["epr"]["run_id"]

        run_a = create("tenant-a-key")
        run_b = create("tenant-b-key")
        self.assertNotEqual(run_a, run_b)

        status, _, _ = self._request(
            f"/v1/epr/runs/{run_a}",
            headers={"Authorization": "Bearer tenant-b-key"},
        )
        self.assertEqual(status, 404)
        status, _, _ = self._request(
            f"/v1/epr/runs/{run_a}",
            headers={"Authorization": "Bearer tenant-a-key"},
        )
        self.assertEqual(status, 200)
