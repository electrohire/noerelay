from __future__ import annotations

import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
except ImportError:  # Optional standards-level validation.
    Draft202012Validator = None
    Registry = None
    Resource = None


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reference"))

from epr.epistemic import adjudicate_fact
from epr.kernel import select_route
from epr.ledger import append_event, verify_chain
from epr.memory import validate_context_capsule


def load_json(relative: str):
    with (ROOT / relative).open("r", encoding="utf-8") as handle:
        return json.load(handle)


class JsonSyntaxTests(unittest.TestCase):
    def test_all_json_files_parse(self):
        for path in ROOT.rglob("*.json"):
            with self.subTest(path=path):
                with path.open("r", encoding="utf-8") as handle:
                    json.load(handle)


@unittest.skipIf(Draft202012Validator is None, "jsonschema is optional")
class JsonSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schemas = {
            path.name: load_json(str(path.relative_to(ROOT)).replace("\\", "/"))
            for path in (ROOT / "spec" / "schemas").glob("*.schema.json")
        }
        cls.store = {
            schema["$id"]: schema
            for schema in cls.schemas.values()
            if "$id" in schema
        }

    def _registry(self):
        return Registry().with_resources(
            (uri, Resource.from_contents(schema))
            for uri, schema in self.store.items()
        )

    def validator(self, name: str):
        schema = self.schemas[name]
        Draft202012Validator.check_schema(schema)
        registry = self._registry()
        return Draft202012Validator(schema, registry=registry)

    def test_task_contract_matches_schema(self):
        self.validator("task-contract.schema.json").validate(
            load_json("examples/high-risk-coding-contract.json")
        )

    def test_candidate_actions_match_schema(self):
        validator = self.validator("candidate-action.schema.json")
        for candidate in load_json("examples/candidate-actions.json"):
            validator.validate(candidate)

    def test_context_capsule_matches_schema(self):
        self.validator("context-capsule.schema.json").validate(
            load_json("examples/context-capsule.json")
        )

    def test_benchmark_manifest_matches_schema(self):
        schema = load_json("spec/benchmark-manifest.schema.json")
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(load_json("spec/benchmark-manifest.json"))


class EpistemicTests(unittest.TestCase):
    def test_four_valued_fact_adjudication(self):
        self.assertEqual(adjudicate_fact(0.99, 0.1, 0.95), "supported")
        self.assertEqual(adjudicate_fact(0.1, 0.99, 0.95), "refuted")
        self.assertEqual(adjudicate_fact(0.99, 0.99, 0.95), "conflicted")
        self.assertEqual(adjudicate_fact(0.8, 0.8, 0.95), "unknown")


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.contract = load_json("examples/high-risk-coding-contract.json")
        self.candidates = load_json("examples/candidate-actions.json")
        self.policy = load_json("spec/routing-policy.json")

    def test_selects_lowest_cost_admissible_plan_with_independent_verifier(self):
        decision = select_route(self.contract, self.candidates, self.policy)
        self.assertEqual(decision["status"], "route_selected")
        self.assertEqual(decision["selected_plan"]["action_id"], "qwen3.6-35b-a3b-worker")
        self.assertEqual(decision["selected_plan"]["inference_gateway"], "openrouter")
        self.assertEqual(decision["selected_plan"]["model_id"], "qwen/qwen3.6-35b-a3b")
        self.assertEqual(decision["selected_plan"]["verifier_family"], "anthropic")
        self.assertNotEqual(
            decision["selected_plan"]["provider_family"],
            decision["selected_plan"]["verifier_family"],
        )

    def test_rejects_cheaper_candidate_below_acceptance_floor(self):
        decision = select_route(self.contract, self.candidates, self.policy)
        audit = {item["candidate_id"]: item for item in decision["candidate_audit"]}
        self.assertFalse(audit["cheap-code-worker"]["admissible"])
        self.assertTrue(any("acceptance_lcb_below" in reason for reason in audit["cheap-code-worker"]["reasons"]))

    def test_fails_closed_without_independent_verifier(self):
        candidates = [item for item in self.candidates if item["provider_family"] == "qwen"]
        decision = select_route(self.contract, candidates, self.policy)
        self.assertEqual(decision["status"], "escalation_required")
        self.assertNotIn("selected_plan", decision)

    def test_missing_high_risk_acceptance_requires_clarification(self):
        contract = deepcopy(self.contract)
        contract["acceptance_criteria"][0]["kind"] = "missing"
        decision = select_route(contract, self.candidates, self.policy)
        self.assertEqual(decision["status"], "clarification_required")

    def test_global_policy_blocks_openai_family_even_if_task_allows_it(self):
        contract = deepcopy(self.contract)
        contract["governance"]["allowed_provider_families"].append("openai")
        contract["governance"]["denied_provider_families"] = []
        candidates = deepcopy(self.candidates)
        candidates[0]["provider_family"] = "openai"
        candidates[0]["model_id"] = "non-openai-namespace/disguised-model"
        decision = select_route(contract, candidates, self.policy)
        audit = {item["candidate_id"]: item for item in decision["candidate_audit"]}
        self.assertIn("model_family_denied", audit["qwen3.6-35b-a3b-worker"]["reasons"])

    def test_global_policy_blocks_openai_model_namespace(self):
        candidates = deepcopy(self.candidates)
        candidates[0]["model_id"] = "openai/forbidden-model"
        decision = select_route(self.contract, candidates, self.policy)
        audit = {item["candidate_id"]: item for item in decision["candidate_audit"]}
        self.assertIn("model_id_denied", audit["qwen3.6-35b-a3b-worker"]["reasons"])

    def test_all_example_models_use_explicit_openrouter_ids(self):
        for candidate in self.candidates:
            if candidate["action_kind"] == "model":
                self.assertEqual(candidate["inference_gateway"], "openrouter")
                self.assertNotEqual(candidate["model_id"], "openrouter/auto")
                self.assertFalse(candidate["model_id"].lower().startswith("openai/"))

    def test_openrouter_cannot_auto_select_or_use_openai_upstream(self):
        inference = self.policy["inference"]
        openrouter = inference["openrouter"]
        self.assertEqual(inference["allowed_gateways"], ["openrouter"])
        self.assertFalse(openrouter["automatic_model_selection_allowed"])
        self.assertIn("openai", openrouter["provider_routing"]["ignore"])
        self.assertTrue(openrouter["image_generation"]["explicit_model_id_required"])


class LedgerTests(unittest.TestCase):
    def test_hash_chain_detects_tampering(self):
        events = []
        append_event(
            events,
            {
                "event_id": "event-1",
                "run_id": "run-1",
                "timestamp": "2026-08-14T12:00:00Z",
                "actor": {"id": "policy-1", "kind": "policy"},
                "event_type": "policy_checked",
                "subject_id": "task-1",
                "payload": {"allowed": True},
            },
        )
        append_event(
            events,
            {
                "event_id": "event-2",
                "run_id": "run-1",
                "timestamp": "2026-08-14T12:00:01Z",
                "actor": {"id": "router-1", "kind": "service"},
                "event_type": "route_selected",
                "subject_id": "task-1",
                "payload": {"route": "worker-1"},
            },
        )
        self.assertEqual(verify_chain(events), (True, "ok"))
        events[0]["payload"]["allowed"] = False
        valid, message = verify_chain(events)
        self.assertFalse(valid)
        self.assertIn("content hash mismatch", message)


class MemoryTests(unittest.TestCase):
    def test_context_capsule_preserves_authoritative_state(self):
        capsule = load_json("examples/context-capsule.json")
        claims = [
            {
                "claim_id": "req-auth-042",
                "kind": "requirement",
                "state": "active",
                "support_evidence_ids": [],
                "refutation_evidence_ids": [],
            },
            {
                "claim_id": "decision-auth-library",
                "kind": "decision",
                "state": "approved",
                "support_evidence_ids": ["evidence-architecture-record"],
                "refutation_evidence_ids": [],
            },
            {
                "claim_id": "assumption-clock-source",
                "kind": "assumption",
                "state": "open",
                "support_evidence_ids": ["evidence-failing-test"],
                "refutation_evidence_ids": [],
            },
        ]
        errors = validate_context_capsule(capsule, claims, ["check-expired-token"])
        self.assertEqual(errors, [])

    def test_context_capsule_rejects_lost_unresolved_claim(self):
        capsule = load_json("examples/context-capsule.json")
        capsule["unresolved_claim_ids"] = []
        claims = [
            {
                "claim_id": "assumption-clock-source",
                "kind": "assumption",
                "state": "open",
                "support_evidence_ids": [],
                "refutation_evidence_ids": [],
            }
        ]
        errors = validate_context_capsule(capsule, claims, [])
        self.assertTrue(any("unresolved_claim_ids missing" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
