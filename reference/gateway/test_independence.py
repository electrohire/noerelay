"""Test independence enforcement for the NoeRelay gateway (EPR-VER-004).

Agent-generated tests MUST be identified via ``test_metadata`` on
``test_result`` evidence, and high/critical-risk acceptance MUST include
independent, hidden, mutation, interoperability, proof, or human evidence as
applicable.  ``worker_generated`` tests alone are never sufficient for high or
critical risk.
"""

from __future__ import annotations

from typing import Any

VALID_INDEPENDENCE = frozenset(
    {"worker_generated", "independent", "hidden", "mutation", "formal", "human"}
)

# EPR-VER-004: for high/critical risk these are the sufficient independence
# classes.  ``worker_generated`` is deliberately excluded.
_HIGH_RISK_SUFFICIENT = frozenset({"independent", "hidden", "mutation", "formal", "human"})

_REQUIRED_METADATA_FIELDS = (
    "test_suite_id",
    "test_version",
    "exit_code",
    "independence",
    "coverage",
)


class TestIndependenceChecker:
    """EPR-VER-004: enforces test independence for high/critical risk."""

    @staticmethod
    def validate_test_metadata(evidence: dict[str, Any]) -> list[str]:
        """Validate that ``test_result`` evidence has the required metadata.

        Non-``test_result`` evidence requires no metadata and returns an empty
        list.  Returns a list of human-readable error strings.
        """
        if evidence.get("kind") != "test_result":
            return []

        metadata = evidence.get("test_metadata")
        if not isinstance(metadata, dict):
            return ["test_result_evidence_requires_test_metadata"]

        errors: list[str] = []
        for field in ("test_suite_id", "test_version"):
            if not isinstance(metadata.get(field), str) or not metadata.get(field):
                errors.append(f"test_metadata.{field}_required")

        if not isinstance(metadata.get("exit_code"), int) or isinstance(
            metadata.get("exit_code"), bool
        ):
            errors.append("test_metadata.exit_code_must_be_integer")

        if metadata.get("independence") not in VALID_INDEPENDENCE:
            errors.append("test_metadata.independence_invalid")

        coverage = metadata.get("coverage")
        if (
            isinstance(coverage, bool)
            or not isinstance(coverage, (int, float))
            or not (0.0 <= float(coverage) <= 1.0)
        ):
            errors.append("test_metadata.coverage_out_of_range")

        return errors

    @staticmethod
    def classify_test_evidence(
        evidence_records: list[dict[str, Any]],
    ) -> dict[str, list[str]]:
        """Group ``test_result`` evidence IDs by independence type."""
        groups: dict[str, list[str]] = {kind: [] for kind in VALID_INDEPENDENCE}
        for evidence in evidence_records:
            if evidence.get("kind") != "test_result":
                continue
            metadata = evidence.get("test_metadata") or {}
            independence = metadata.get("independence")
            if independence in groups:
                groups[independence].append(str(evidence.get("evidence_id", "")))
        return groups

    @staticmethod
    def check_independence(
        evidence_records: list[dict[str, Any]], risk_class: str
    ) -> tuple[bool, str]:
        """Check test independence sufficiency for *risk_class*.

        For high/critical risk, ``worker_generated`` tests alone are not
        sufficient.  At least one of ``independent``, ``hidden``, ``mutation``,
        ``formal``, or ``human`` test evidence is required.  When no
        ``test_result`` evidence exists the check is not applicable and passes.
        """
        if risk_class not in ("high", "critical"):
            return True, "test_independence_not_required"

        groups = TestIndependenceChecker.classify_test_evidence(evidence_records)
        test_evidence = [e for e in evidence_records if e.get("kind") == "test_result"]
        if not test_evidence:
            return True, "no_test_evidence"

        if any(groups[kind] for kind in _HIGH_RISK_SUFFICIENT):
            return True, "independent_test_evidence_present"

        return (
            False,
            "worker_generated_tests_alone_are_insufficient_for_high_or_critical_risk",
        )


def validate_test_metadata(evidence: dict[str, Any]) -> list[str]:
    """Module-level wrapper for :meth:`TestIndependenceChecker.validate_test_metadata`."""
    return TestIndependenceChecker.validate_test_metadata(evidence)
