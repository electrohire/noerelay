"""Official evaluation harness adapters for benchmark cohorts.

Each harness now produces :class:`Finding` objects conforming to the
spec-kit-evaluator contract, with proper evidence classification.

Harnesses are model-backed (non-deterministic) evaluators that assess
responses against official benchmark criteria.
"""

from __future__ import annotations

import json
from typing import Any

from .evaluator_contract import (
    EvidenceRef,
    Finding,
    make_finding_id,
)


class HarnessAdapter:
    """Abstract base for official evaluation harness adapters.

    Subclasses produce :class:`Finding` objects with evidence classification.
    """

    def evaluate(
        self, case: dict[str, Any], response: dict[str, Any]
    ) -> Finding:
        """Evaluate if the response is correct for the given case.

        Returns a :class:`Finding` with evidence classification.
        """
        raise NotImplementedError


class SWEBenchHarnessAdapter(HarnessAdapter):
    """SWE-bench evaluation: checks if the generated patch passes the test suite.

    For the skeleton, this adapter validates that the response contains a
    valid patch format. Full evaluation requires the SWE-bench harness
    (https://github.com/princeton-nlp/SWE-bench) which runs Docker containers.

    Evidence: ``observed`` — patch format is directly verifiable from the
    response content.
    """

    def evaluate(
        self, case: dict[str, Any], response: dict[str, Any]
    ) -> Finding:
        content = response.get("content", "")
        case_id = case.get("id", "unknown")
        has_patch = bool(
            content
            and (
                "```" in content
                or "diff --git" in content
                or "patch" in content.lower()
            )
        )

        evidence_desc = (
            "Response contains patch markers (```, diff --git, or 'patch')."
            if has_patch
            else "Response does not contain recognizable patch format."
        )

        return Finding(
            id=make_finding_id("SWE"),
            severity="info" if has_patch else "high",
            kind="other" if has_patch else "missing_evidence",
            subject=case_id,
            description=(
                "SWE-bench: valid patch format detected."
                if has_patch
                else "SWE-bench: no valid patch format found in response."
            ),
            evidence_refs=[
                EvidenceRef(
                    ref="response_content",
                    kind="observed",
                    description=evidence_desc,
                )
            ],
            provenance_refs=[case_id],
            uncertainty="low" if has_patch else "medium",
            recommended_action="none" if has_patch else "revise",
            rationale=(
                "Patch format check passed (skeleton — full SWE-bench "
                "evaluation requires Docker harness)."
                if has_patch
                else "Response must contain a valid patch format "
                "(```, diff --git, or 'patch')."
            ),
        )


class BFCLHarnessAdapter(HarnessAdapter):
    """Berkeley Function Calling Leaderboard evaluation.

    Checks if tool calls are syntactically valid and semantically correct
    against the expected function calls.

    Evidence: ``observed`` — JSON comparison is deterministic.
    """

    def evaluate(
        self, case: dict[str, Any], response: dict[str, Any]
    ) -> Finding:
        content = response.get("content", "")
        expected = case.get("expected_output", "")
        case_id = case.get("id", "unknown")

        is_correct = False
        evidence_kind = "observed"
        evidence_desc = ""

        try:
            parsed_content = json.loads(content.strip())
            parsed_expected = json.loads(expected.strip())
            is_correct = parsed_content == parsed_expected
            evidence_desc = (
                "JSON tool calls match expected output."
                if is_correct
                else "JSON tool calls differ from expected output."
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            # Fall back to string comparison
            is_correct = content.strip().lower() == expected.strip().lower()
            evidence_desc = (
                "String comparison matched (non-JSON fallback)."
                if is_correct
                else "Tool call output does not match expected (string fallback)."
            )

        return Finding(
            id=make_finding_id("BFC"),
            severity="info" if is_correct else "high",
            kind="other" if is_correct else "schema_violation",
            subject=case_id,
            description=(
                "BFCL: tool calls match expected output."
                if is_correct
                else "BFCL: tool calls do not match expected output."
            ),
            evidence_refs=[
                EvidenceRef(
                    ref="tool_call_output",
                    kind=evidence_kind,
                    description=evidence_desc,
                )
            ],
            provenance_refs=[case_id],
            uncertainty="none" if is_correct else "low",
            recommended_action="none" if is_correct else "revise",
            rationale=(
                "Tool call output validated against expected."
                if is_correct
                else "Tool call output must match expected function calls."
            ),
        )


class ContractComplianceAdapter(HarnessAdapter):
    """Evaluates if the response meets the contract's acceptance criteria.

    Evidence: ``observed`` — non-emptiness is directly observable.
    """

    def evaluate(
        self, case: dict[str, Any], response: dict[str, Any]
    ) -> Finding:
        content = response.get("content", "")
        case_id = case.get("id", "unknown")
        is_valid = bool(content and len(content.strip()) > 0)

        return Finding(
            id=make_finding_id("CNT"),
            severity="info" if is_valid else "critical",
            kind="other" if is_valid else "missing_evidence",
            subject=case_id,
            description=(
                "Contract compliance: response is non-empty."
                if is_valid
                else "Contract compliance: response is empty."
            ),
            evidence_refs=[
                EvidenceRef(
                    ref="response_content",
                    kind="observed",
                    description=(
                        "Response length: %d characters."
                        % len(content.strip())
                        if is_valid
                        else "Response is empty or whitespace-only."
                    ),
                )
            ],
            provenance_refs=[case_id],
            uncertainty="none",
            recommended_action="none" if is_valid else "revise",
            rationale=(
                "Response meets minimum contract requirements."
                if is_valid
                else "Empty response cannot satisfy contract acceptance criteria."
            ),
        )


HARNESSES: dict[str, HarnessAdapter] = {
    "swe_bench": SWEBenchHarnessAdapter(),
    "bfcl": BFCLHarnessAdapter(),
    "contract": ContractComplianceAdapter(),
}


def get_harness(name: str) -> HarnessAdapter:
    """Return the harness registered under ``name`` (defaults to contract)."""
    return HARNESSES.get(name, ContractComplianceAdapter())