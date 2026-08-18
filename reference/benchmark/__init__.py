"""Benchmark toolkit for the NoeRelay gateway (dependency-free, stdlib only).

Public API surface:

- Dataset loaders (:class:`DatasetLoader`, :class:`JsonlDatasetLoader`,
  :class:`InlineDatasetLoader`)
- Correctness evaluators (exact match, contains, regex, acceptance, composite)
- Metric aggregation (:class:`CaseResult`, :class:`BenchmarkResults`,
  :func:`compute_results`, :func:`format_report`)
- The HTTP benchmark runner (:class:`BenchmarkRunner`)
"""

from __future__ import annotations

from .datasets import DatasetLoader, InlineDatasetLoader, JsonlDatasetLoader
from .evaluators import (
    EVALUATORS,
    AcceptanceCriteriaEvaluator,
    CompositeEvaluator,
    ContainsEvaluator,
    Evaluator,
    ExactMatchEvaluator,
    RegexEvaluator,
    get_evaluator,
)
from .advanced_metrics import (
    compute_all_metrics,
    compute_brier_score,
    compute_context_evidence_recall,
    compute_escalation_rate,
    compute_requirement_trace_coverage,
    compute_replay_success_rate,
    compute_route_regret,
    compute_selective_risk,
    compute_tool_call_success_rate,
    compute_unsafe_accept_rate,
)
from .harnesses import (
    HARNESSES,
    BFCLHarnessAdapter,
    ContractComplianceAdapter,
    HarnessAdapter,
    SWEBenchHarnessAdapter,
    get_harness,
)
from .hf_datasets import (
    DATASET_REGISTRY,
    HuggingFaceDatasetLoader,
    build_registry_from_manifest,
    get_cohort_loaders,
)
from .metrics import (
    BenchmarkResults,
    CaseResult,
    compute_results,
    format_report,
)
from .runner import BenchmarkRunner

__all__ = [
    "DatasetLoader",
    "JsonlDatasetLoader",
    "InlineDatasetLoader",
    "HuggingFaceDatasetLoader",
    "DATASET_REGISTRY",
    "build_registry_from_manifest",
    "get_cohort_loaders",
    "Evaluator",
    "ExactMatchEvaluator",
    "ContainsEvaluator",
    "RegexEvaluator",
    "AcceptanceCriteriaEvaluator",
    "CompositeEvaluator",
    "EVALUATORS",
    "get_evaluator",
    "HarnessAdapter",
    "SWEBenchHarnessAdapter",
    "BFCLHarnessAdapter",
    "ContractComplianceAdapter",
    "HARNESSES",
    "get_harness",
    "CaseResult",
    "BenchmarkResults",
    "compute_results",
    "format_report",
    "compute_brier_score",
    "compute_selective_risk",
    "compute_route_regret",
    "compute_context_evidence_recall",
    "compute_replay_success_rate",
    "compute_unsafe_accept_rate",
    "compute_requirement_trace_coverage",
    "compute_tool_call_success_rate",
    "compute_escalation_rate",
    "compute_all_metrics",
    "BenchmarkRunner",
]
