"""Correctness evaluators for benchmark responses.

Each evaluator now produces :class:`Finding` objects conforming to the
spec-kit-evaluator contract, with proper evidence classification
(``observed`` for deterministic checks, ``inferred`` for derived results).

Design rule: deterministic checks run before probabilistic review.
All evaluators in this module are deterministic.
"""

from __future__ import annotations

import re
from typing import Any

from .evaluator_contract import (
    EvidenceRef,
    Finding,
    make_finding_id,
)


class Evaluator:
    """Abstract base for correctness evaluators.

    Subclasses produce :class:`Finding` objects with evidence classification
    instead of returning raw booleans.
    """

    def evaluate(
        self, response: str, expected: str, case: dict[str, Any]
    ) -> Finding:
        """Evaluate ``response`` against ``expected`` for ``case``.

        Returns a :class:`Finding` with evidence classification.
        """
        raise NotImplementedError


class ExactMatchEvaluator(Evaluator):
    """Exact string match (case-insensitive, stripped).

    Evidence: ``observed`` — the response text is directly compared to
    the expected output.
    """

    def evaluate(
        self, response: str, expected: str, case: dict[str, Any]
    ) -> Finding:
        case_id = case.get("id", "unknown")
        is_match = response.strip().lower() == expected.strip().lower()
        return Finding(
            id=make_finding_id("EXM"),
            severity="info" if is_match else "high",
            kind="other",
            subject=case_id,
            description=(
                "Response matches expected output exactly."
                if is_match
                else "Response does not match expected output."
            ),
            evidence_refs=[
                EvidenceRef(
                    ref="response_text",
                    kind="observed",
                    description=(
                        "Response: '%s'" % response[:100]
                        if is_match
                        else "Expected: '%s', Got: '%s'"
                        % (expected[:100], response[:100])
                    ),
                )
            ],
            provenance_refs=[case_id],
            uncertainty="none",
            recommended_action="none" if is_match else "revise",
            rationale=(
                "Exact match confirmed."
                if is_match
                else "String comparison failed (case-insensitive, stripped)."
            ),
        )


class ContainsEvaluator(Evaluator):
    """Check if response contains the expected string.

    Evidence: ``observed`` — substring presence is directly verifiable.
    """

    def evaluate(
        self, response: str, expected: str, case: dict[str, Any]
    ) -> Finding:
        case_id = case.get("id", "unknown")
        contains = expected.strip().lower() in response.strip().lower()
        return Finding(
            id=make_finding_id("CTN"),
            severity="info" if contains else "high",
            kind="other",
            subject=case_id,
            description=(
                "Response contains expected content."
                if contains
                else "Response does not contain expected content."
            ),
            evidence_refs=[
                EvidenceRef(
                    ref="response_text",
                    kind="observed",
                    description=(
                        "Found '%s' in response."
                        % expected[:80]
                        if contains
                        else "Expected substring '%s' not found."
                        % expected[:80]
                    ),
                )
            ],
            provenance_refs=[case_id],
            uncertainty="none",
            recommended_action="none" if contains else "revise",
            rationale=(
                "Substring match confirmed."
                if contains
                else "Substring not found in response."
            ),
        )


class RegexEvaluator(Evaluator):
    """Check if response matches a regex pattern.

    Evidence: ``observed`` — regex match result is deterministic.
    """

    def evaluate(
        self, response: str, expected: str, case: dict[str, Any]
    ) -> Finding:
        case_id = case.get("id", "unknown")
        matches = bool(re.search(expected, response, re.IGNORECASE))
        return Finding(
            id=make_finding_id("RGX"),
            severity="info" if matches else "high",
            kind="other",
            subject=case_id,
            description=(
                "Response matches expected pattern."
                if matches
                else "Response does not match expected pattern."
            ),
            evidence_refs=[
                EvidenceRef(
                    ref="response_text",
                    kind="observed",
                    description=(
                        "Pattern '%s' matched."
                        % expected[:80]
                        if matches
                        else "Pattern '%s' did not match."
                        % expected[:80]
                    ),
                )
            ],
            provenance_refs=[case_id],
            uncertainty="none",
            recommended_action="none" if matches else "revise",
            rationale=(
                "Regex pattern matched."
                if matches
                else "Regex pattern did not match response."
            ),
        )


class AcceptanceCriteriaEvaluator(Evaluator):
    """Check if response meets the contract's acceptance criteria.

    For the skeleton this is a minimal well-formedness check: the response
    is non-empty after stripping.

    Evidence: ``observed`` — non-emptiness is directly observable.
    """

    def evaluate(
        self, response: str, expected: str, case: dict[str, Any]
    ) -> Finding:
        case_id = case.get("id", "unknown")
        is_valid = bool(response and len(response.strip()) > 0)
        return Finding(
            id=make_finding_id("ACP"),
            severity="info" if is_valid else "critical",
            kind="other" if is_valid else "missing_evidence",
            subject=case_id,
            description=(
                "Response meets acceptance criteria (non-empty)."
                if is_valid
                else "Response is empty — acceptance criteria not met."
            ),
            evidence_refs=[
                EvidenceRef(
                    ref="response_text",
                    kind="observed",
                    description=(
                        "Response length: %d characters."
                        % len(response.strip())
                        if is_valid
                        else "Response is empty or whitespace-only."
                    ),
                )
            ],
            provenance_refs=[case_id],
            uncertainty="none",
            recommended_action="none" if is_valid else "revise",
            rationale=(
                "Response is non-empty and well-formed."
                if is_valid
                else "Empty response cannot satisfy acceptance criteria."
            ),
        )


class CompositeEvaluator(Evaluator):
    """Combine multiple evaluators (all must pass).

    Evidence: ``inferred`` — composite result is derived from sub-evaluators.
    """

    def __init__(self, evaluators: list[Evaluator]) -> None:
        self.evaluators = evaluators

    def evaluate(
        self, response: str, expected: str, case: dict[str, Any]
    ) -> Finding:
        sub_findings = [
            e.evaluate(response, expected, case) for e in self.evaluators
        ]
        all_passed = all(
            f.recommended_action == "none" for f in sub_findings
        )
        failed = [f for f in sub_findings if f.recommended_action != "none"]
        case_id = case.get("id", "unknown")

        return Finding(
            id=make_finding_id("CMP"),
            severity="info" if all_passed else "high",
            kind="other",
            subject=case_id,
            description=(
                "All %d sub-evaluators passed." % len(sub_findings)
                if all_passed
                else "%d/%d sub-evaluators failed."
                % (len(failed), len(sub_findings))
            ),
            evidence_refs=[
                EvidenceRef(
                    ref="sub_evaluator:%s" % f.id,
                    kind="inferred",
                    description=f.description,
                )
                for f in sub_findings
            ],
            provenance_refs=[case_id],
            uncertainty="none",
            recommended_action="none" if all_passed else "revise",
            rationale=(
                "All sub-evaluators passed."
                if all_passed
                else "One or more sub-evaluators failed: %s"
                % ", ".join(f.id for f in failed)
            ),
        )


EVALUATORS: dict[str, Evaluator] = {
    "exact_match": ExactMatchEvaluator(),
    "contains": ContainsEvaluator(),
    "regex": RegexEvaluator(),
    "acceptance": AcceptanceCriteriaEvaluator(),
}


def get_evaluator(name: str) -> Evaluator:
    """Return the evaluator registered under ``name`` (defaults to exact match)."""
    return EVALUATORS.get(name, ExactMatchEvaluator())