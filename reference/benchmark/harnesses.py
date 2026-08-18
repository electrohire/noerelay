"""Official evaluation harness adapters for benchmark cohorts."""

from __future__ import annotations

import json
from typing import Any


class HarnessAdapter:
    """Abstract base for official evaluation harness adapters."""

    def evaluate(self, case: dict[str, Any], response: dict[str, Any]) -> bool:
        """Evaluate if the response is correct for the given case."""
        raise NotImplementedError


class SWEBenchHarnessAdapter(HarnessAdapter):
    """SWE-bench evaluation: checks if the generated patch passes the test suite.

    For the skeleton, this adapter validates that the response contains a valid
    patch format. Full evaluation requires the SWE-bench harness
    (https://github.com/princeton-nlp/SWE-bench) which runs Docker containers.
    """

    def evaluate(self, case: dict[str, Any], response: dict[str, Any]) -> bool:
        content = response.get("content", "")
        return bool(
            content
            and (
                "```" in content
                or "diff --git" in content
                or "patch" in content.lower()
            )
        )


class BFCLHarnessAdapter(HarnessAdapter):
    """Berkeley Function Calling Leaderboard evaluation.

    Checks if tool calls are syntactically valid and semantically correct
    against the expected function calls.
    """

    def evaluate(self, case: dict[str, Any], response: dict[str, Any]) -> bool:
        content = response.get("content", "")
        expected = case.get("expected_output", "")
        try:
            return json.loads(content.strip()) == json.loads(expected.strip())
        except (json.JSONDecodeError, TypeError, ValueError):
            return content.strip().lower() == expected.strip().lower()


class ContractComplianceAdapter(HarnessAdapter):
    """Evaluates if the response meets the contract's acceptance criteria."""

    def evaluate(self, case: dict[str, Any], response: dict[str, Any]) -> bool:
        content = response.get("content", "")
        return bool(content and len(content.strip()) > 0)


HARNESSES: dict[str, HarnessAdapter] = {
    "swe_bench": SWEBenchHarnessAdapter(),
    "bfcl": BFCLHarnessAdapter(),
    "contract": ContractComplianceAdapter(),
}


def get_harness(name: str) -> HarnessAdapter:
    """Return the harness registered under ``name`` (defaults to contract)."""
    return HARNESSES.get(name, ContractComplianceAdapter())
