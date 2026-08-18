"""EPR-ROUTE-006: canary-only online learning governance.

Production policy is immutable at runtime (``can_modify_production`` is
always False).  Canary traffic may use experimental models/policies; canary
policy versions can only be promoted to production with signed benchmark
results that pass every promotion gate from the benchmark manifest.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

_PROMOTION_GATES: dict[str, float] = {
    "maximum_unsafe_accept_rate": 0.0,
    "maximum_calibration_ece": 0.04,
    "minimum_replay_success_rate": 0.99,
    "minimum_context_evidence_recall": 0.99,
    "maximum_route_regret_usd": 0.05,
}

_BENCHMARK_MANIFEST_PATH = (
    Path(__file__).resolve().parents[2] / "spec" / "benchmark-manifest.json"
)
_BENCHMARK_MANIFEST_CACHE: dict[str, Any] | None = None


def _load_benchmark_manifest() -> dict[str, Any]:
    """Load the benchmark manifest (cached) for cohort promotion gates."""
    global _BENCHMARK_MANIFEST_CACHE
    if _BENCHMARK_MANIFEST_CACHE is None:
        try:
            _BENCHMARK_MANIFEST_CACHE = json.loads(
                _BENCHMARK_MANIFEST_PATH.read_text("utf-8")
            )
        except (OSError, json.JSONDecodeError):
            _BENCHMARK_MANIFEST_CACHE = {}
    cache = _BENCHMARK_MANIFEST_CACHE
    return cache if cache is not None else {}


def _load_cohort_gates() -> dict[str, dict[str, float]]:
    """Map cohort ids to their promotion gates from the benchmark manifest."""
    manifest = _load_benchmark_manifest()
    result: dict[str, dict[str, float]] = {}
    for cohort in manifest.get("cohorts", []):
        gates = cohort.get("promotion_gates")
        if isinstance(gates, dict):
            result[str(cohort.get("id", ""))] = {
                str(name): float(limit) for name, limit in gates.items()
            }
    return result


class CanaryTrafficRouter:
    """EPR-ROUTE-006: routes canary traffic to experimental models."""

    def __init__(self, canary_percentage: float = 0.0) -> None:
        self._canary_percentage = canary_percentage

    def is_canary(self, governance: dict[str, Any] | None) -> bool:
        """Check whether a request is canary traffic.

        An explicit ``canary`` boolean in governance metadata is authoritative.
        Otherwise, traffic is sampled according to ``canary_percentage``.
        """
        if not isinstance(governance, dict):
            return False
        if governance.get("canary") is True:
            return True
        if governance.get("canary") is False:
            return False
        if self._canary_percentage <= 0.0:
            return False
        if self._canary_percentage >= 1.0:
            return True
        return random.random() < self._canary_percentage

    def can_use_experimental(self, governance: dict[str, Any] | None) -> bool:
        """Only canary traffic may use experimental models/policies."""
        return self.is_canary(governance)


class PolicyVersionManager:
    """EPR-ROUTE-006: manages policy versions and canary promotion."""

    def __init__(self, production_version: str = "1.0.0") -> None:
        self._production_version = production_version
        self._canary_versions: dict[str, dict[str, Any]] = {}
        self._promotion_gates = dict(_PROMOTION_GATES)
        self._cohort_gates = _load_cohort_gates()

    def get_production_policy_version(self) -> str:
        """Return the current production policy version."""
        return self._production_version

    def get_canary_policy_versions(self) -> list[str]:
        """Return registered canary policy versions."""
        return list(self._canary_versions.keys())

    def register_canary(
        self, version: str, metadata: dict[str, Any] | None = None
    ) -> None:
        """Register a canary policy version with optional metadata."""
        self._canary_versions[version] = dict(metadata or {})

    def promote_canary(
        self, canary_version: str, benchmark_results: dict[str, Any]
    ) -> tuple[bool, str]:
        """Attempt to promote a canary version to production.

        Promotion requires signed benchmark results and every promotion gate
        to pass.  Supports both the legacy flat ``gates`` payload and the
        manifest-shaped ``cohorts`` payload.
        """
        if not benchmark_results.get("signed"):
            return False, "benchmark results must be signed"
        if canary_version not in self._canary_versions:
            return False, "canary_version_not_registered"

        cohorts = benchmark_results.get("cohorts")
        if isinstance(cohorts, dict) and cohorts:
            failures = self._check_cohort_gates(cohorts)
            failures.extend(self._check_global_gates(cohorts))
        else:
            failures = self._check_promotion_gates(benchmark_results)

        if failures:
            return False, "promotion_gates_failed: " + "; ".join(failures)

        self._production_version = canary_version
        self._canary_versions[canary_version]["promoted"] = True
        return True, f"canary {canary_version} promoted to production"

    def can_modify_production(self) -> bool:
        """EPR-ROUTE-006: production_self_modification is false."""
        return False

    def _check_promotion_gates(self, benchmark_results: dict[str, Any]) -> list[str]:
        """Check all promotion gates.  Returns failures (empty means pass)."""
        measured = benchmark_results.get("gates")
        if not isinstance(measured, dict):
            measured = benchmark_results
        failures: list[str] = []
        for gate, limit in self._promotion_gates.items():
            if gate not in measured:
                failures.append(f"missing_gate:{gate}")
                continue
            value = measured[gate]
            if gate.startswith("minimum_"):
                if value < limit:
                    failures.append(f"{gate}_below:{value}<{limit}")
            else:
                if value > limit:
                    failures.append(f"{gate}_exceeded:{value}>{limit}")
        return failures

    def _get_cohort_gates(self, cohort_name: str) -> dict[str, float]:
        """Return the promotion gates for ``cohort_name`` from the manifest."""
        return self._cohort_gates.get(cohort_name, {})

    @staticmethod
    def _metric_key(gate_name: str) -> str:
        """Strip the ``minimum_``/``maximum_`` direction prefix from a gate."""
        for prefix in ("minimum_", "maximum_"):
            if gate_name.startswith(prefix):
                return gate_name[len(prefix):]
        return gate_name

    @staticmethod
    def _check_gate(gate_name: str, threshold: float, actual: float) -> bool:
        if gate_name.startswith("minimum_"):
            return actual >= threshold
        return actual <= threshold

    def _resolve_metric(
        self, cohort_results: dict[str, Any], gate_name: str
    ) -> float | None:
        """Resolve a gate's measured value from a cohort result dict."""
        candidates = [gate_name, self._metric_key(gate_name)]
        if self._metric_key(gate_name) == "accepted_outcome_rate":
            candidates.append("accuracy")
        for candidate in candidates:
            value = cohort_results.get(candidate)
            if value is not None:
                return float(value)
        return None

    def _check_cohort_gates(
        self, cohorts: dict[str, dict[str, Any]]
    ) -> list[str]:
        """Check each cohort's manifest gates.  Returns failures."""
        failures: list[str] = []
        for cohort_name, cohort_results in cohorts.items():
            gates = self._get_cohort_gates(cohort_name)
            for gate, limit in gates.items():
                actual = self._resolve_metric(cohort_results, gate)
                if actual is None:
                    failures.append(f"cohort {cohort_name} missing gate {gate}")
                elif not self._check_gate(gate, limit, actual):
                    failures.append(
                        f"cohort {cohort_name} failed gate {gate}: "
                        f"{actual} vs {limit}"
                    )
        return failures

    def _check_global_gates(
        self, cohorts: dict[str, dict[str, Any]]
    ) -> list[str]:
        """Check global gates against the worst cohort performance."""
        failures: list[str] = []
        for gate, limit in self._promotion_gates.items():
            values: list[float] = []
            for cohort_results in cohorts.values():
                actual = self._resolve_metric(cohort_results, gate)
                if actual is not None:
                    values.append(actual)
            if not values:
                failures.append(f"missing_gate:{gate}")
                continue
            worst = min(values) if gate.startswith("minimum_") else max(values)
            if not self._check_gate(gate, limit, worst):
                failures.append(f"{gate}_failed:{worst}vs{limit}")
        return failures
