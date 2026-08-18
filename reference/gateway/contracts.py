"""Deterministic task-contract compilation from wire requests.

EPR-CON-001: an LLM-backed compiler MAY propose a task contract, but schema
validation and policy defaults MUST be deterministic.  This module provides
the deterministic default compiler and an optional :class:`LLMContractProposer`
protocol whose output is always re-validated and completed with deterministic
defaults before use.

EPR-CON-002: acceptance criteria are classified as ``executable``,
``observable``, ``judgmental``, or ``missing``.

EPR-CON-003: high/critical-risk work with missing acceptance criteria is
flagged via :func:`requires_clarification` and MUST NOT execute autonomously.

EPR-CON-004: contract fields decompose into distinct state vocabularies via
:class:`gateway.state_vocabulary.StateVocabulary`.

The compiled contract conforms to ``spec/schemas/task-contract.schema.json``.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .state_vocabulary import StateVocabulary

# Documented default acceptance criterion (never ``kind: "missing"``, so the
# kernel's ``_missing_acceptance()`` is not triggered by default traffic).
_DEFAULT_ACCEPTANCE_CRITERION = {
    "id": "ac-default-response",
    "description": "A schema-valid response was produced within policy.",
    "kind": "observable",
    "mandatory": True,
}

_MAX_GOAL_LENGTH = 10000

_ACCEPTANCE_KINDS = frozenset({"executable", "observable", "judgmental", "missing"})
_TASK_KINDS = frozenset(
    {
        "conversation",
        "factual_research",
        "requirements",
        "architecture",
        "coding",
        "verification",
        "image_understanding",
        "image_generation",
        "computer_use",
        "other",
    }
)
_RISK_CLASSES = frozenset({"low", "medium", "high", "critical"})


def _extract_goal(messages: list[dict[str, Any]]) -> str:
    """Concatenate user-role message text, truncated to the schema limit."""
    parts: list[str] = []
    for message in messages:
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
    goal = "\n".join(parts).strip()
    return goal[:_MAX_GOAL_LENGTH] if goal else "<non-text input>"


def _detect_image(messages: list[dict[str, Any]]) -> bool:
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image_url":
                    return True
    return False


def _build_contract_governance(
    merged_governance: dict[str, Any], risk_class: str
) -> dict[str, Any]:
    """Map the OpenAPI governance shape to the task-contract governance shape.

    ``required_acceptance_probability`` is omitted when absent so the kernel's
    ``_required_lcb`` receives the default ``0`` (not ``None``).
    """
    result: dict[str, Any] = {
        "data_policy": merged_governance["data_policy"],
        "max_cost_usd": merged_governance["max_cost_usd"],
        "max_latency_ms": merged_governance["max_latency_ms"],
        "human_approval_required": risk_class == "critical",
    }
    probability = merged_governance.get("required_acceptance_probability")
    if probability is not None:
        result["required_acceptance_probability"] = probability
    retention = merged_governance.get("retention_class")
    if retention is not None:
        result["retention_class"] = retention
    return result


def classify_acceptance_criterion(description: str, kind_hint: str | None = None) -> str:
    """Classify an acceptance criterion (EPR-CON-002).

    * ``executable`` — verifiable by running code/tests.
    * ``observable`` — verifiable by inspecting output/behavior.
    * ``judgmental`` — requires human or model judgment.
    * ``missing`` — no acceptance criteria provided.

    An explicit, valid *kind_hint* is authoritative.  Otherwise the
    classification is inferred deterministically from *description*.
    """
    if not description or not str(description).strip():
        return "missing"
    if kind_hint in _ACCEPTANCE_KINDS:
        return kind_hint

    text = str(description).lower()
    if any(
        token in text
        for token in (
            "test",
            "run",
            "execute",
            "assert",
            "compile",
            "build",
            "lint",
            "script",
            "command",
            "code",
            "pass",
        )
    ):
        return "executable"
    if any(
        token in text
        for token in (
            "observe",
            "display",
            "output",
            "response",
            "show",
            "render",
            "visible",
            "appear",
            "inspect",
        )
    ):
        return "observable"
    return "judgmental"


def requires_clarification(contract: dict[str, Any]) -> bool:
    """Return True when high/critical-risk work lacks usable acceptance criteria.

    EPR-CON-003: high-risk work with missing acceptance criteria MUST NOT
    execute autonomously.  The contract is flagged when acceptance criteria are
    absent or every criterion is ``missing`` kind.
    """
    if contract.get("risk_class") not in ("high", "critical"):
        return False
    criteria = contract.get("acceptance_criteria", [])
    if not criteria:
        return True
    return all(criterion.get("kind") == "missing" for criterion in criteria)


def validate_task_contract(contract: dict[str, Any]) -> list[str]:
    """Dependency-free deterministic schema validation of a task contract.

    Returns a list of human-readable errors (empty when valid).  This is the
    deterministic validation gate that every contract — including LLM-proposed
    contracts — must pass (EPR-CON-001).
    """
    errors: list[str] = []
    if contract.get("version") != "1.0":
        errors.append("version_must_be_1_0")
    if not contract.get("task_id"):
        errors.append("task_id_required")
    if not contract.get("goal"):
        errors.append("goal_required")
    if contract.get("task_kind") not in _TASK_KINDS:
        errors.append("task_kind_invalid")
    if contract.get("risk_class") not in _RISK_CLASSES:
        errors.append("risk_class_invalid")

    criteria = contract.get("acceptance_criteria")
    if not criteria:
        errors.append("acceptance_criteria_required")
    else:
        for index, criterion in enumerate(criteria):
            prefix = f"acceptance_criteria[{index}]"
            if not criterion.get("id"):
                errors.append(f"{prefix}.id_required")
            if not str(criterion.get("description", "")).strip():
                errors.append(f"{prefix}.description_required")
            if criterion.get("kind") not in _ACCEPTANCE_KINDS:
                errors.append(f"{prefix}.kind_invalid")
            if not isinstance(criterion.get("mandatory"), bool):
                errors.append(f"{prefix}.mandatory_invalid")

    governance = contract.get("governance")
    if not governance:
        errors.append("governance_required")
    elif not governance.get("data_policy"):
        errors.append("governance_data_policy_required")

    return errors


def contract_state_items(contract: dict[str, Any]) -> list[dict[str, Any]]:
    """Decompose a contract into distinct state-vocabulary items (EPR-CON-004).

    The goal is classified by :meth:`StateVocabulary.classify`; acceptance
    criteria are ``requirement`` items; the governance decision is a
    ``decision`` item.  Every returned item carries a distinct ``kind`` from
    :data:`StateVocabulary.ALL_VOCABULARIES`.
    """
    items: list[dict[str, Any]] = []
    goal = contract.get("goal", "")
    if goal:
        items.append(
            {
                "kind": StateVocabulary.classify(goal),
                "statement": goal,
                "source": "goal",
            }
        )
    for criterion in contract.get("acceptance_criteria", []):
        items.append(
            {
                "kind": StateVocabulary.REQUIREMENT,
                "statement": criterion.get("description", ""),
                "source": f"acceptance_criterion:{criterion.get('id')}",
            }
        )
    governance = contract.get("governance", {})
    items.append(
        {
            "kind": StateVocabulary.DECISION,
            "status": "rejected" if governance.get("human_approval_required") else "approved",
            "rationale": f"risk_class={contract.get('risk_class')}",
            "source": "governance",
        }
    )
    return items


def _finalize_acceptance_criteria(
    proposed: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Apply deterministic defaults to (possibly LLM-proposed) criteria."""
    if not proposed:
        return [dict(_DEFAULT_ACCEPTANCE_CRITERION)]

    result: list[dict[str, Any]] = []
    for index, criterion in enumerate(proposed):
        description = str(criterion.get("description", "")).strip()
        kind = classify_acceptance_criterion(description, criterion.get("kind"))
        if not description:
            description = _DEFAULT_ACCEPTANCE_CRITERION["description"]

        mandatory = criterion.get("mandatory")
        if not isinstance(mandatory, bool):
            mandatory = True

        item: dict[str, Any] = {
            "id": criterion.get("id") or f"ac-proposed-{index}",
            "description": description,
            "kind": kind,
            "mandatory": mandatory,
        }
        if criterion.get("evaluator_ref") is not None:
            item["evaluator_ref"] = criterion["evaluator_ref"]
        if criterion.get("requirement_refs") is not None:
            item["requirement_refs"] = criterion["requirement_refs"]
        result.append(item)
    return result


@runtime_checkable
class LLMContractProposer(Protocol):
    """Interface for LLM-assisted contract proposals (EPR-CON-001).

    An implementation returns a (possibly partial) task-contract dict.  The
    deterministic compiler remains authoritative: the proposal is merged with
    deterministic defaults and re-validated before use.
    """

    def propose(
        self,
        messages: list[dict[str, Any]],
        governance: dict[str, Any],
        passthrough: dict[str, Any] | None,
        *,
        task_id: str,
    ) -> dict[str, Any]: ...


def _finalize_contract(
    messages: list[dict[str, Any]],
    merged_governance: dict[str, Any],
    passthrough: dict[str, Any] | None,
    task_id: str,
    proposal: dict[str, Any],
) -> dict[str, Any]:
    """Build a schema-conformant contract, completing the proposal with defaults."""
    passthrough = passthrough or {}
    has_image = _detect_image(messages)

    capabilities = ["text"]
    if passthrough.get("tools"):
        capabilities.append("tool_calling")
    if passthrough.get("response_format") or passthrough.get("text"):
        capabilities.append("structured_output")
    if has_image:
        capabilities.append("vision")

    modalities = ["text"]
    if has_image:
        modalities.append("image")

    risk_class = merged_governance["risk_class"]

    goal = proposal.get("goal")
    if not goal:
        goal = _extract_goal(messages)
    goal = goal[:_MAX_GOAL_LENGTH]

    return {
        "version": "1.0",
        "task_id": task_id,
        "project_id": merged_governance.get("project_id", "project-default"),
        "goal": goal,
        "task_kind": proposal.get("task_kind") or "conversation",
        "risk_class": risk_class,
        "input_modalities": modalities,
        "required_capabilities": capabilities,
        "acceptance_criteria": _finalize_acceptance_criteria(
            proposal.get("acceptance_criteria")
        ),
        "governance": _build_contract_governance(merged_governance, risk_class),
    }


def compile_task_contract(
    messages: list[dict[str, Any]],
    merged_governance: dict[str, Any],
    passthrough: dict[str, Any] | None = None,
    *,
    task_id: str,
    proposer: LLMContractProposer | None = None,
) -> dict[str, Any]:
    """Compile a deterministic task contract from a wire request.

    Args:
        messages: OpenAI-format message list.
        merged_governance: Governance dict after merge with defaults.
        passthrough: Standard OpenAI fields carried verbatim (``tools``,
            ``response_format``, ``text``, etc.).
        task_id: Pre-generated task identifier.
        proposer: Optional LLM-assisted contract proposer.  Its output is
            merged with deterministic defaults and re-validated.  Any proposer
            failure falls back to the deterministic compiler (EPR-CON-001).

    Returns:
        A dict conforming to ``spec/schemas/task-contract.schema.json``.
    """
    proposal: dict[str, Any] = {}
    if proposer is not None:
        try:
            proposed = proposer.propose(
                messages, merged_governance, passthrough, task_id=task_id
            )
            if proposed is not None:
                if not isinstance(proposed, dict):
                    raise TypeError("LLM contract proposal must be a dict")
                proposal = proposed
        except Exception:
            # EPR-CON-001: the deterministic compiler is the fallback.
            proposal = {}

    contract = _finalize_contract(
        messages, merged_governance, passthrough, task_id, proposal
    )
    errors = validate_task_contract(contract)
    if errors:
        raise ValueError(
            "compiled contract failed schema validation: " + "; ".join(errors)
        )
    return contract
