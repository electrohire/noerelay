"""Evaluator result contract dataclasses and builders.

Conforms to the spec-kit-evaluator contract defined in
``spec/schemas/evaluator-result.schema.json``. Provides Python-native dataclasses
for constructing, validating, and serializing evaluator results with evidence
classification, uncertainty representation, and contradiction preservation.

Design rules (from the evaluator contract):
1. Generated assertions MUST remain distinguishable from observed evidence.
2. Model self-attestation MUST NOT satisfy an evidence gate by itself.
3. Contradictions MUST be preserved, not collapsed into a single answer.
4. Insufficient evidence MUST be represented explicitly.
5. Deterministic checks SHOULD run before probabilistic review.
6. Higher-risk work MAY require evaluator independence.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

# -- Schema version -----------------------------------------------------------
SCHEMA_VERSION = "1.0"

# -- Type aliases -------------------------------------------------------------
Outcome = Literal["pass", "warn", "iterate", "clarify", "gather_evidence", "block"]
Severity = Literal["critical", "high", "medium", "low", "info"]
FindingKind = Literal[
    "unsupported_claim",
    "contradiction",
    "missing_evidence",
    "ambiguous_requirement",
    "unverified_assertion",
    "provenance_gap",
    "schema_violation",
    "policy_violation",
    "security_concern",
    "coverage_gap",
    "traceability_gap",
    "risk_unaddressed",
    "assumption_unvalidated",
    "other",
]
EvidenceKind = Literal["observed", "inferred", "asserted", "contradicted", "unsupported"]
Uncertainty = Literal["none", "low", "medium", "high", "insufficient_evidence"]
RecommendedAction = Literal[
    "none",
    "gather_evidence",
    "clarify",
    "revise",
    "iterate",
    "escalate",
    "accept_risk",
    "block",
]
Phase = Literal[
    "after_specify",
    "after_plan",
    "after_tasks",
    "after_implement",
    "after_analyze",
    "after_checklist",
    "after_clarify",
    "after_constitution",
    "after_converge",
    "after_taskstoissues",
]
ModelTier = Literal["budget", "standard", "premium", "portfolio"]


# -- Dataclasses --------------------------------------------------------------


@dataclass
class EvidenceRef:
    """A reference to evidence supporting or contradicting a finding."""

    ref: str
    """File path, URL, or artifact identifier."""
    kind: EvidenceKind
    """Nature of the evidence."""
    description: str = ""
    """Brief description of what the evidence shows."""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"ref": self.ref, "kind": self.kind}
        if self.description:
            d["description"] = self.description
        return d


@dataclass
class Finding:
    """An individual finding from an evaluator."""

    id: str
    """Unique finding identifier within this result (e.g., EPI-001)."""
    severity: Severity
    """Finding severity."""
    kind: FindingKind
    """Classification of the finding."""
    subject: str
    """Identifier of the artifact element the finding relates to."""
    description: str = ""
    """Human-readable description."""
    evidence_refs: list[EvidenceRef] = field(default_factory=list)
    """References to observed evidence."""
    provenance_refs: list[str] = field(default_factory=list)
    """References to source artifacts."""
    uncertainty: Uncertainty = "none"
    """Level of uncertainty about the finding."""
    recommended_action: RecommendedAction = "none"
    """Recommended action for this finding."""
    rationale: str = ""
    """Brief rationale for the finding and recommendation."""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "severity": self.severity,
            "kind": self.kind,
            "subject": self.subject,
        }
        if self.description:
            d["description"] = self.description
        if self.evidence_refs:
            d["evidence_refs"] = [er.to_dict() for er in self.evidence_refs]
        if self.provenance_refs:
            d["provenance_refs"] = self.provenance_refs
        if self.uncertainty != "none":
            d["uncertainty"] = self.uncertainty
        if self.recommended_action != "none":
            d["recommended_action"] = self.recommended_action
        if self.rationale:
            d["rationale"] = self.rationale
        return d


@dataclass
class NextAction:
    """Recommended next action for the workflow."""

    kind: Outcome
    """Type of next action."""
    target_phase: str | None = None
    """Target phase to iterate back to (for iterate actions)."""
    message: str = ""
    """Human-readable message about the next action."""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind}
        if self.target_phase:
            d["target_phase"] = self.target_phase
        if self.message:
            d["message"] = self.message
        return d


@dataclass
class EvaluatorInfo:
    """Metadata about the evaluator that produced a result."""

    id: str
    """Unique evaluator identifier."""
    version: str
    """Semantic version of the evaluator."""
    name: str = ""
    """Human-readable evaluator name."""
    url: str = ""
    """Evaluator homepage or documentation URL."""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"id": self.id, "version": self.version}
        if self.name:
            d["name"] = self.name
        if self.url:
            d["url"] = self.url
        return d


@dataclass
class EscalationTrigger:
    """A condition that would trigger escalation to a higher model tier."""

    condition: str
    """Condition that triggers escalation."""
    escalate_to: Literal["standard", "premium"]
    """Tier to escalate to."""

    def to_dict(self) -> dict[str, Any]:
        return {"condition": self.condition, "escalate_to": self.escalate_to}


@dataclass
class TierEstimate:
    """Estimated tokens and cost for a model tier."""

    estimated_tokens: int = 0
    estimated_cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "estimated_tokens": self.estimated_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
        }


@dataclass
class ModelRouting:
    """Model routing recommendation for the next SDD phase."""

    recommended_tier: ModelTier
    """Recommended model tier."""
    reason: str = ""
    """Why this tier is recommended."""
    escalation_triggers: list[EscalationTrigger] = field(default_factory=list)
    """Conditions that would trigger escalation."""
    estimated_tokens: int = 0
    """Estimated tokens for the next phase at this tier."""
    estimated_cost_usd: float = 0.0
    """Estimated USD cost for the next phase at this tier."""
    tier_breakdown: dict[str, TierEstimate] = field(default_factory=dict)
    """Cost/token comparison across all tiers."""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "recommended_tier": self.recommended_tier,
        }
        if self.reason:
            d["reason"] = self.reason
        if self.escalation_triggers:
            d["escalation_triggers"] = [et.to_dict() for et in self.escalation_triggers]
        if self.estimated_tokens:
            d["estimated_tokens"] = self.estimated_tokens
        if self.estimated_cost_usd:
            d["estimated_cost_usd"] = self.estimated_cost_usd
        if self.tier_breakdown:
            d["tier_breakdown"] = {
                k: v.to_dict() for k, v in self.tier_breakdown.items()
            }
        return d


@dataclass
class EvaluatorMetadata:
    """Additional metadata about the evaluation run."""

    timestamp: str = ""
    """ISO 8601 timestamp of the evaluation."""
    duration_ms: int = 0
    """Evaluation duration in milliseconds."""
    artifacts_evaluated: list[str] = field(default_factory=list)
    """List of artifacts that were evaluated."""
    model: str | None = None
    """AI model used for the evaluation, if applicable."""
    deterministic: bool = True
    """Whether the evaluator is deterministic."""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.timestamp:
            d["timestamp"] = self.timestamp
        if self.duration_ms:
            d["duration_ms"] = self.duration_ms
        if self.artifacts_evaluated:
            d["artifacts_evaluated"] = self.artifacts_evaluated
        if self.model:
            d["model"] = self.model
        d["deterministic"] = self.deterministic
        return d


@dataclass
class EvaluatorResult:
    """A complete evaluator result conforming to the evaluator contract.

    This is the top-level object produced by any evaluator. It contains
    findings with evidence classification, an aggregate outcome, and
    optional model routing recommendations.
    """

    evaluator: EvaluatorInfo
    """Metadata about the evaluator."""
    phase: Phase
    """Lifecycle phase when the evaluator ran."""
    outcome: Outcome
    """Aggregate evaluator outcome."""
    findings: list[Finding] = field(default_factory=list)
    """Individual findings from the evaluation."""
    summary: str = ""
    """One-paragraph human-readable summary."""
    next_action: NextAction | None = None
    """Recommended next action for the workflow."""
    model_routing: ModelRouting | None = None
    """Model routing recommendation."""
    metadata: EvaluatorMetadata = field(default_factory=EvaluatorMetadata)
    """Additional metadata about the evaluation run."""
    state: dict[str, Any] = field(default_factory=dict)
    """Opaque state for pause/resume."""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "evaluator": self.evaluator.to_dict(),
            "phase": self.phase,
            "outcome": self.outcome,
            "findings": [f.to_dict() for f in self.findings],
        }
        if self.summary:
            d["summary"] = self.summary
        if self.next_action:
            d["next_action"] = self.next_action.to_dict()
        if self.model_routing:
            d["model_routing"] = self.model_routing.to_dict()
        if self.metadata.timestamp or self.metadata.duration_ms:
            d["metadata"] = self.metadata.to_dict()
        if self.state:
            d["state"] = self.state
        return d

    def to_json(self, indent: int = 2) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    def write(self, path: Path) -> None:
        """Write the result to a JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")

    @property
    def critical_count(self) -> int:
        """Number of critical-severity findings."""
        return sum(1 for f in self.findings if f.severity == "critical")

    @property
    def high_count(self) -> int:
        """Number of high-severity findings."""
        return sum(1 for f in self.findings if f.severity == "high")

    @property
    def evidence_gap_count(self) -> int:
        """Number of findings with evidence gaps."""
        return sum(
            1
            for f in self.findings
            if f.kind in ("unsupported_claim", "missing_evidence")
        )

    @property
    def is_blocking(self) -> bool:
        """Whether the outcome blocks further progress."""
        return self.outcome in ("block", "gather_evidence")


# -- Builder helpers ----------------------------------------------------------


def make_finding_id(prefix: str = "FND") -> str:
    """Generate a unique finding ID."""
    return "%s-%s" % (prefix, uuid.uuid4().hex[:6].upper())


def make_timestamp() -> str:
    """Generate an ISO 8601 timestamp for the current UTC time."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def derive_outcome(findings: list[Finding]) -> Outcome:
    """Derive the aggregate outcome from a list of findings.

    Rules:
    - Any critical finding -> ``block``
    - Any high finding -> ``iterate``
    - Any medium finding -> ``warn``
    - Otherwise -> ``pass``
    """
    severities = {f.severity for f in findings}
    if "critical" in severities:
        return "block"
    if "high" in severities:
        return "iterate"
    if "medium" in severities:
        return "warn"
    return "pass"


def derive_next_action(outcome: Outcome, target_phase: str | None = None) -> NextAction:
    """Derive a NextAction from an outcome."""
    messages: dict[Outcome, str] = {
        "pass": "All checks passed. Proceed to next phase.",
        "warn": "Issues found but not blocking. Continue with warnings recorded.",
        "iterate": "Issues require revisiting a prior phase.",
        "clarify": "Ambiguities need human resolution. Pause for human input.",
        "gather_evidence": "Insufficient evidence to decide. Pause for evidence collection.",
        "block": "Hard blocker. Cannot proceed.",
    }
    return NextAction(
        kind=outcome,
        target_phase=target_phase if outcome == "iterate" else None,
        message=messages.get(outcome, ""),
    )


def make_result(
    evaluator_id: str,
    evaluator_version: str,
    phase: Phase,
    findings: list[Finding],
    evaluator_name: str = "",
    deterministic: bool = True,
    model: str | None = None,
    duration_ms: int = 0,
) -> EvaluatorResult:
    """Build a complete EvaluatorResult from findings.

    This is the primary convenience constructor. It derives the outcome,
    next_action, and metadata from the provided findings.
    """
    outcome = derive_outcome(findings)
    return EvaluatorResult(
        evaluator=EvaluatorInfo(
            id=evaluator_id,
            version=evaluator_version,
            name=evaluator_name or evaluator_id,
        ),
        phase=phase,
        outcome=outcome,
        findings=findings,
        summary=_build_summary(evaluator_id, findings, outcome),
        next_action=derive_next_action(outcome),
        metadata=EvaluatorMetadata(
            timestamp=make_timestamp(),
            duration_ms=duration_ms,
            deterministic=deterministic,
            model=model,
        ),
    )


def _build_summary(
    evaluator_id: str, findings: list[Finding], outcome: Outcome
) -> str:
    """Build a one-paragraph summary string."""
    if not findings:
        return "%s found no issues." % evaluator_id
    by_sev: dict[str, int] = {}
    for f in findings:
        by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
    parts = []
    for sev in ("critical", "high", "medium", "low", "info"):
        if sev in by_sev:
            parts.append("%d %s" % (by_sev[sev], sev))
    return "%s found %d issue(s) (%s). Outcome: %s." % (
        evaluator_id,
        len(findings),
        ", ".join(parts),
        outcome,
    )


# -- Validation ---------------------------------------------------------------


def validate_result(result: dict[str, Any]) -> list[str]:
    """Lightweight structural validation of an evaluator result dict.

    Returns a list of error messages (empty if valid). This is a fast
    structural check; full JSON Schema validation requires a schema
    validator library.
    """
    errors: list[str] = []
    required = ["schema_version", "evaluator", "phase", "outcome", "findings"]
    for key in required:
        if key not in result:
            errors.append("missing required key '%s'" % key)
    if "evaluator" in result:
        ev = result["evaluator"]
        if not isinstance(ev, dict):
            errors.append("'evaluator' must be an object")
        else:
            for ek in ("id", "version"):
                if ek not in ev:
                    errors.append("evaluator missing '%s'" % ek)
    if "findings" in result and not isinstance(result["findings"], list):
        errors.append("'findings' must be an array")
    valid_outcomes = {"pass", "warn", "iterate", "clarify", "gather_evidence", "block"}
    if "outcome" in result and result["outcome"] not in valid_outcomes:
        errors.append(
            "invalid outcome '%s'; must be one of %s"
            % (result["outcome"], sorted(valid_outcomes))
        )
    return errors


def load_result_file(filepath: Path) -> EvaluatorResult | None:
    """Load and validate an evaluator result JSON file.

    Returns an EvaluatorResult or None if the file is invalid.
    """
    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    errors = validate_result(data)
    if errors:
        return None
    return _from_dict(data)


def _from_dict(data: dict[str, Any]) -> EvaluatorResult:
    """Deserialize an EvaluatorResult from a dict (internal use)."""
    ev = data["evaluator"]
    evaluator = EvaluatorInfo(
        id=ev["id"],
        version=ev["version"],
        name=ev.get("name", ""),
        url=ev.get("url", ""),
    )
    findings = []
    for fd in data.get("findings", []):
        evidence_refs = [
            EvidenceRef(
                ref=er["ref"],
                kind=er["kind"],
                description=er.get("description", ""),
            )
            for er in fd.get("evidence_refs", [])
        ]
        findings.append(
            Finding(
                id=fd["id"],
                severity=fd["severity"],
                kind=fd["kind"],
                subject=fd["subject"],
                description=fd.get("description", ""),
                evidence_refs=evidence_refs,
                provenance_refs=fd.get("provenance_refs", []),
                uncertainty=fd.get("uncertainty", "none"),
                recommended_action=fd.get("recommended_action", "none"),
                rationale=fd.get("rationale", ""),
            )
        )
    na = data.get("next_action")
    next_action = None
    if na:
        next_action = NextAction(
            kind=na["kind"],
            target_phase=na.get("target_phase"),
            message=na.get("message", ""),
        )
    mr = data.get("model_routing")
    model_routing = None
    if mr:
        triggers = [
            EscalationTrigger(condition=et["condition"], escalate_to=et["escalate_to"])
            for et in mr.get("escalation_triggers", [])
        ]
        breakdown: dict[str, TierEstimate] = {}
        for tier_key in ("budget", "standard", "premium"):
            if tier_key in mr.get("tier_breakdown", {}):
                tb = mr["tier_breakdown"][tier_key]
                breakdown[tier_key] = TierEstimate(
                    estimated_tokens=tb.get("estimated_tokens", 0),
                    estimated_cost_usd=tb.get("estimated_cost_usd", 0.0),
                )
        model_routing = ModelRouting(
            recommended_tier=mr["recommended_tier"],
            reason=mr.get("reason", ""),
            escalation_triggers=triggers,
            estimated_tokens=mr.get("estimated_tokens", 0),
            estimated_cost_usd=mr.get("estimated_cost_usd", 0.0),
            tier_breakdown=breakdown,
        )
    md = data.get("metadata", {})
    metadata = EvaluatorMetadata(
        timestamp=md.get("timestamp", ""),
        duration_ms=md.get("duration_ms", 0),
        artifacts_evaluated=md.get("artifacts_evaluated", []),
        model=md.get("model"),
        deterministic=md.get("deterministic", True),
    )
    return EvaluatorResult(
        evaluator=evaluator,
        phase=data["phase"],
        outcome=data["outcome"],
        findings=findings,
        summary=data.get("summary", ""),
        next_action=next_action,
        model_routing=model_routing,
        metadata=metadata,
        state=data.get("state", {}),
    )