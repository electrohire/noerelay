"""Correctness evaluators for benchmark responses."""

from __future__ import annotations

import re
from typing import Any


class Evaluator:
    """Abstract base for correctness evaluators."""

    def evaluate(self, response: str, expected: str, case: dict[str, Any]) -> bool:
        """Return ``True`` when ``response`` satisfies ``expected`` for ``case``."""
        raise NotImplementedError


class ExactMatchEvaluator(Evaluator):
    """Exact string match (case-insensitive, stripped)."""

    def evaluate(self, response: str, expected: str, case: dict[str, Any]) -> bool:
        return response.strip().lower() == expected.strip().lower()


class ContainsEvaluator(Evaluator):
    """Check if response contains the expected string."""

    def evaluate(self, response: str, expected: str, case: dict[str, Any]) -> bool:
        return expected.strip().lower() in response.strip().lower()


class RegexEvaluator(Evaluator):
    """Check if response matches a regex pattern."""

    def evaluate(self, response: str, expected: str, case: dict[str, Any]) -> bool:
        return bool(re.search(expected, response, re.IGNORECASE))


class AcceptanceCriteriaEvaluator(Evaluator):
    """Check if response meets the contract's acceptance criteria.

    For the skeleton this is a minimal well-formedness check: the response is
    non-empty after stripping.
    """

    def evaluate(self, response: str, expected: str, case: dict[str, Any]) -> bool:
        return bool(response and len(response.strip()) > 0)


class CompositeEvaluator(Evaluator):
    """Combine multiple evaluators (all must pass)."""

    def __init__(self, evaluators: list[Evaluator]) -> None:
        self.evaluators = evaluators

    def evaluate(self, response: str, expected: str, case: dict[str, Any]) -> bool:
        return all(e.evaluate(response, expected, case) for e in self.evaluators)


EVALUATORS: dict[str, Evaluator] = {
    "exact_match": ExactMatchEvaluator(),
    "contains": ContainsEvaluator(),
    "regex": RegexEvaluator(),
    "acceptance": AcceptanceCriteriaEvaluator(),
}


def get_evaluator(name: str) -> Evaluator:
    """Return the evaluator registered under ``name`` (defaults to exact match)."""
    return EVALUATORS.get(name, ExactMatchEvaluator())
