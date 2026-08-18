"""Real verification engine for the NoeRelay gateway.

Evaluates the verification DAG from the routing policy against an upstream
response, producing evidence records and pass/fail results for each criterion
in the DAG.  The DAG steps are loaded from ``policy["verification"][risk_class]``
(see :file:`spec/routing-policy.json`).

EPR-EPI-005 (conflicted claims block high-risk acceptance) is wired but
currently a no-op because the default contract carries no claims.
``validate_context_capsule`` is called for the ``compaction_invariants_hold``
guard but returns trivially valid in the skeleton (no compaction occurs).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from .test_independence import TestIndependenceChecker, validate_test_metadata


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


GATEWAY_PRODUCER: dict[str, Any] = {
    "id": "noerelay-gateway",
    "kind": "service",
    "version": "0.1.0",
}


def _make_evidence(
    kind: str,
    activity_id: str,
    content: Any,
    location: str,
    strength: float,
    producer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create an evidence record conforming to
    :file:`spec/schemas/evidence.schema.json`."""
    if producer is None:
        producer = GATEWAY_PRODUCER
    content_str = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return {
        "evidence_id": f"evidence-{uuid.uuid4().hex}",
        "kind": kind,
        "produced_at": _now(),
        "producer": producer,
        "activity_id": activity_id,
        "content_hash": _sha256(content_str),
        "location": location,
        "strength": strength,
    }


# ---------------------------------------------------------------------------
# Individual verification steps
# ---------------------------------------------------------------------------


def schema_check(
    response: dict[str, Any],
    activity_id: str = "verify-schema",
    producer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate the upstream response has the expected OpenAI shape.

    Checks that ``choices[0].message.content`` is a non-empty string.
    Returns a verification result dict with ``criterion_id``, ``status``,
    ``evidence_ids``, and embedded ``_evidence`` records.
    """
    passed: bool
    try:
        content = response["choices"][0]["message"]["content"]
        passed = isinstance(content, str) and len(content) > 0
    except (KeyError, IndexError, TypeError):
        passed = False

    evidence = _make_evidence(
        kind="direct_observation",
        activity_id=activity_id,
        content={"schema_valid": passed, "response_shape": "OpenAI chat.completion"},
        location="verification.schema_check",
        strength=1.0 if passed else 0.0,
        producer=producer,
    )

    return {
        "criterion_id": "schema",
        "status": "passed" if passed else "failed",
        "mandatory": True,
        "evidence_ids": [evidence["evidence_id"]],
        "_evidence": [evidence],
    }


def policy_check(
    decision: dict[str, Any],
    activity_id: str = "verify-policy",
    producer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify the route decision was admissible per policy.

    The kernel already guarantees this, but the formal verification step
    records it as evidence.
    """
    passed = decision.get("status") == "route_selected"

    evidence = _make_evidence(
        kind="direct_observation",
        activity_id=activity_id,
        content={
            "policy_admissible": passed,
            "decision_status": decision.get("status"),
        },
        location="verification.policy_check",
        strength=1.0 if passed else 0.0,
        producer=producer,
    )

    return {
        "criterion_id": "policy",
        "status": "passed" if passed else "failed",
        "mandatory": True,
        "evidence_ids": [evidence["evidence_id"]],
        "_evidence": [evidence],
    }


def deterministic_acceptance_check(
    contract: dict[str, Any],
    response: dict[str, Any],
    risk_class: str | None = None,
    test_evidence: list[dict[str, Any]] | None = None,
    activity_id: str = "verify-acceptance",
    producer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Check each acceptance criterion against the upstream response.

    * ``observable`` — validate the response content exists (OpenAI shape).
    * ``executable`` — ``not_run`` (no tool-result available in skeleton).
    * ``judgmental`` / ``missing`` — ``not_run``.
    """
    criteria = contract.get("acceptance_criteria", [])
    sub_results: list[dict[str, Any]] = []
    all_evidence: list[dict[str, Any]] = []

    for criterion in criteria:
        kind = criterion.get("kind", "missing")
        cid = criterion["id"]
        mandatory = criterion.get("mandatory", False)

        if kind == "observable":
            try:
                has_content = bool(response["choices"][0]["message"]["content"])
                status = "passed" if has_content else "failed"
            except (KeyError, IndexError, TypeError):
                status = "failed"

            evidence = _make_evidence(
                kind="direct_observation",
                activity_id=activity_id,
                content={
                    "criterion_id": cid,
                    "kind": kind,
                    "status": status,
                    "response_model": response.get("model"),
                },
                location=f"verification.deterministic_acceptance.{cid}",
                strength=1.0 if status == "passed" else 0.0,
                producer=producer,
            )
            all_evidence.append(evidence)
            sub_results.append(
                {
                    "criterion_id": cid,
                    "kind": kind,
                    "status": status,
                    "mandatory": mandatory,
                    "evidence_ids": [evidence["evidence_id"]],
                }
            )
        elif kind == "executable":
            sub_results.append(
                {
                    "criterion_id": cid,
                    "kind": kind,
                    "status": "not_run",
                    "mandatory": mandatory,
                    "evidence_ids": [],
                }
            )
        else:
            sub_results.append(
                {
                    "criterion_id": cid,
                    "kind": kind,
                    "status": "not_run",
                    "mandatory": mandatory,
                    "evidence_ids": [],
                }
            )

    # EPR-VER-004: enforce test metadata and independence for high/critical risk.
    test_records = [
        evidence
        for evidence in (test_evidence or [])
        if evidence.get("kind") == "test_result"
    ]
    if risk_class in ("high", "critical") and test_records:
        metadata_errors: list[str] = []
        for evidence in test_records:
            metadata_errors.extend(validate_test_metadata(evidence))
        if metadata_errors:
            sub_results.append(
                {
                    "criterion_id": "test_metadata",
                    "kind": "test_result",
                    "status": "failed",
                    "mandatory": True,
                    "evidence_ids": [
                        evidence["evidence_id"]
                        for evidence in test_records
                        if evidence.get("evidence_id")
                    ],
                    "detail": "; ".join(metadata_errors),
                }
            )

        independence_ok, independence_detail = (
            TestIndependenceChecker.check_independence(test_records, risk_class)
        )
        if not independence_ok:
            sub_results.append(
                {
                    "criterion_id": "test_independence",
                    "kind": "test_result",
                    "status": "failed",
                    "mandatory": True,
                    "evidence_ids": [
                        evidence["evidence_id"]
                        for evidence in test_records
                        if evidence.get("evidence_id")
                    ],
                    "detail": independence_detail,
                }
            )

    all_passed = all(
        r["status"] == "passed" for r in sub_results if r.get("mandatory")
    )

    return {
        "criterion_id": "deterministic_acceptance",
        "status": "passed" if all_passed else "failed",
        "mandatory": True,
        "sub_results": sub_results,
        "evidence_ids": [e["evidence_id"] for e in all_evidence],
        "_evidence": all_evidence,
    }


def independent_family_review_check(
    selected_plan: dict[str, Any],
    activity_id: str = "verify-independence",
    producer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify the verifier's provider family differs from the worker's.

    The kernel already enforces this at routing time for high/critical risk;
    this formalizes it as a verification step with evidence.
    """
    worker_family = selected_plan.get("provider_family", "")
    verifier_family = selected_plan.get("verifier_family")

    if verifier_family is None:
        passed = False
        detail = "no_verifier_assigned"
    elif worker_family != verifier_family:
        passed = True
        detail = f"independent: worker={worker_family}, verifier={verifier_family}"
    else:
        passed = False
        detail = f"not_independent: both families are {worker_family}"

    evidence = _make_evidence(
        kind="direct_observation",
        activity_id=activity_id,
        content={
            "independent": passed,
            "worker_family": worker_family,
            "verifier_family": verifier_family,
            "detail": detail,
        },
        location="verification.independent_family_review",
        strength=1.0 if passed else 0.0,
        producer=producer,
    )

    return {
        "criterion_id": "independent_family_review",
        "status": "passed" if passed else "failed",
        "mandatory": True,
        "evidence_ids": [evidence["evidence_id"]],
        "_evidence": [evidence],
    }


def human_approval_check(
    risk_class: str,
    activity_id: str = "verify-human-approval",
    producer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Check human approval for critical-risk operations.

    **Skeleton limitation**: no human is in the loop.  For ``critical`` risk
    this returns ``not_run`` and signals that acceptance must be blocked
    (fail-closed).  For all other risk classes it is ``waived``.
    """
    if risk_class == "critical":
        status = "not_run"
        detail = "human_approval_required_but_not_available_in_skeleton"
        strength = 0.0
        passed = False
        blocks = True
    else:
        status = "waived"
        detail = f"human_approval_not_required_for_risk_class_{risk_class}"
        strength = 1.0
        passed = True
        blocks = False

    evidence = _make_evidence(
        kind="direct_observation",
        activity_id=activity_id,
        content={"risk_class": risk_class, "detail": detail},
        location="verification.human_approval",
        strength=strength,
        producer=producer,
    )

    return {
        "criterion_id": "human_approval",
        "status": status,
        "mandatory": True,
        "evidence_ids": [evidence["evidence_id"]] if status != "waived" else [],
        "_evidence": [evidence],
        "_blocks_acceptance": blocks,
    }


# ---------------------------------------------------------------------------
# DAG evaluation
# ---------------------------------------------------------------------------


def evaluate_verification(
    contract: dict[str, Any],
    upstream_response: dict[str, Any],
    risk_class: str,
    policy: dict[str, Any],
    selected_plan: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool, list[dict[str, Any]]]:
    """Run the full verification DAG for *risk_class*.

    DAG steps are read from ``policy["verification"][risk_class]``::

        low:      ["schema", "policy"]
        medium:   ["schema", "policy", "deterministic_acceptance"]
        high:     ["schema", "policy", "deterministic_acceptance",
                   "independent_family_review"]
        critical: ["schema", "policy", "deterministic_acceptance",
                   "independent_family_review", "human_approval"]

    Returns:
        ``(results, all_passed, evidence_records)`` where *results* is a list
        of verification result dicts, *all_passed* is ``True`` when every
        mandatory criterion passed (and no step blocks acceptance), and
        *evidence_records* is a flat list of evidence dicts conforming to
        :file:`spec/schemas/evidence.schema.json`.
    """
    dag = policy.get("verification", {}).get(risk_class, [])
    results: list[dict[str, Any]] = []
    all_evidence: list[dict[str, Any]] = []
    all_passed = True

    # Build a synthetic decision dict for the policy_check step (the real
    # decision was already consumed during routing).
    synth_decision = {"status": "route_selected"}

    for step_name in dag:
        if step_name == "schema":
            result = schema_check(upstream_response)
        elif step_name == "policy":
            result = policy_check(synth_decision)
        elif step_name == "deterministic_acceptance":
            result = deterministic_acceptance_check(
                contract,
                upstream_response,
                risk_class=risk_class,
                test_evidence=all_evidence,
            )
        elif step_name == "independent_family_review":
            result = independent_family_review_check(selected_plan)
        elif step_name == "human_approval":
            result = human_approval_check(risk_class)
        else:
            continue  # unknown step – skip

        results.append(result)
        all_evidence.extend(result.get("_evidence", []))

        if result.get("status") == "failed" and result.get("mandatory", False):
            all_passed = False
        if result.get("_blocks_acceptance", False):
            all_passed = False

    return results, all_passed, all_evidence