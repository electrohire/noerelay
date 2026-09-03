"""Benchmark toolkit for the NoeRelay gateway (dependency-free, stdlib only).

Public API surface:

- Dataset loaders (:class:`DatasetLoader`, :class:`JsonlDatasetLoader`,
  :class:`InlineDatasetLoader`)
- Correctness evaluators (exact match, contains, regex, acceptance, composite)
  — now produce :class:`Finding` objects with evidence classification
- Evaluator contract (:class:`EvaluatorResult`, :class:`Finding`,
  :class:`EvidenceRef`, :class:`ModelRouting`) — spec-kit-evaluator compliant
- Evaluator composition (:func:`compose_results`, :func:`resolve_outcome`)
- Model routing (:func:`recommend_route`, :func:`compute_risk_score`)
- Metric aggregation (:class:`CaseResult`, :class:`BenchmarkResults`,
  :func:`compute_results`, :func:`format_report`)
- The HTTP benchmark runner (:class:`BenchmarkRunner`)
"""

from __future__ import annotations

from .datasets import DatasetLoader, InlineDatasetLoader, JsonlDatasetLoader
from .evaluator_contract import (
    SCHEMA_VERSION,
    EscalationTrigger,
    EvaluatorInfo,
    EvaluatorMetadata,
    EvaluatorResult,
    EvidenceRef,
    Finding,
    ModelRouting,
    NextAction,
    TierEstimate,
    derive_next_action,
    derive_outcome,
    load_result_file,
    make_finding_id,
    make_result,
    make_timestamp,
    validate_result,
)
from .evaluator_compose import (
    compose_from_directory,
    compose_results,
    resolve_outcome,
    write_composed_result,
)
from .evaluator_route import (
    PHASE_RISK_BASELINE,
    TIER_PRICING,
    compute_risk_score,
    compute_savings,
    recommend_route,
)
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
    compute_human_intervention_rate,
    compute_replay_success_rate,
    compute_requirement_trace_coverage,
    compute_rework_rate,
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
    # Datasets
    "DatasetLoader",
    "JsonlDatasetLoader",
    "InlineDatasetLoader",
    "HuggingFaceDatasetLoader",
    "DATASET_REGISTRY",
    "build_registry_from_manifest",
    "get_cohort_loaders",
    # Evaluators (contract-aware)
    "Evaluator",
    "ExactMatchEvaluator",
    "ContainsEvaluator",
    "RegexEvaluator",
    "AcceptanceCriteriaEvaluator",
    "CompositeEvaluator",
    "EVALUATORS",
    "get_evaluator",
    # Evaluator Contract
    "SCHEMA_VERSION",
    "EvaluatorResult",
    "EvaluatorInfo",
    "EvaluatorMetadata",
    "Finding",
    "EvidenceRef",
    "NextAction",
    "ModelRouting",
    "TierEstimate",
    "EscalationTrigger",
    "make_finding_id",
    "make_result",
    "make_timestamp",
    "derive_outcome",
    "derive_next_action",
    "validate_result",
    "load_result_file",
    # Evaluator Composition
    "compose_results",
    "compose_from_directory",
    "resolve_outcome",
    "write_composed_result",
    # Model Routing
    "recommend_route",
    "compute_risk_score",
    "compute_savings",
    "PHASE_RISK_BASELINE",
    "TIER_PRICING",
    # Harnesses
    "HarnessAdapter",
    "SWEBenchHarnessAdapter",
    "BFCLHarnessAdapter",
    "ContractComplianceAdapter",
    "HARNESSES",
    "get_harness",
    # Metrics
    "CaseResult",
    "BenchmarkResults",
    "compute_results",
    "format_report",
    # Advanced Metrics
    "compute_brier_score",
    "compute_selective_risk",
    "compute_route_regret",
    "compute_context_evidence_recall",
    "compute_replay_success_rate",
    "compute_unsafe_accept_rate",
    "compute_requirement_trace_coverage",
    "compute_tool_call_success_rate",
    "compute_human_intervention_rate",
    "compute_rework_rate",
    "compute_escalation_rate",
    "compute_all_metrics",
    # Runner
    "BenchmarkRunner",
]