"""Context and compaction machinery for the NoeRelay gateway.

Implements:

* EPR-CTX-001 — four memory levels (immutable events, canonical artifact
  state, active decision state, disposable narrative summaries).
* EPR-CTX-002 — narrative compaction that preserves authoritative state and
  asserts the three context-capsule invariants.
* EPR-CTX-006 — context packages compiled by graph reachability from the
  current task rather than by transcript recency alone.

The module is dependency-free (stdlib only).  It imports the unmodified
:func:`epr.memory.validate_context_capsule` kernel function.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import deque
from datetime import datetime, timezone
from enum import Enum
from collections.abc import Iterator
from typing import Any

from epr.memory import validate_context_capsule


class MemoryLevel(Enum):
    """The four EPR-CTX-001 memory levels."""

    L0_IMMUTABLE_EVENTS = "L0"
    L1_CANONICAL_STATE = "L1"
    L2_ACTIVE_DECISION = "L2"
    L3_NARRATIVE_SUMMARY = "L3"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


class CanonicalState:
    """L1 — authoritative state that survives narrative compaction.

    This is the "source of truth" referenced by a :class:`ContextCompactor`
    capsule: active requirements, approved decisions, unresolved claims,
    failed mandatory checks, dereferenceable evidence handles, and produced
    artifact hashes.
    """

    def __init__(self) -> None:
        self.active_requirement_ids: set[str] = set()
        self.approved_decision_ids: set[str] = set()
        self.unresolved_claim_ids: set[str] = set()
        self.failed_mandatory_check_ids: set[str] = set()
        # evidence_id -> content_hash (dereferenceable handles).
        self.evidence_handles: dict[str, str] = {}
        # artifact_id -> sha256 content hash.
        self.artifact_hashes: dict[str, str] = {}


def build_canonical_state(
    epistemic_state: Any,
    ledger_events: list[dict[str, Any]] | None = None,
) -> CanonicalState:
    """Build L1 :class:`CanonicalState` from epistemic state and ledger events.

    Gateway claims default to ``fact`` when they carry no explicit ``kind``
    (the gateway's :class:`~gateway.epistemic.EpistemicState` stores four-valued
    facts).  Failed mandatory verification checks are recovered from ledgered
    ``verification_results`` payloads.
    """
    state = CanonicalState()

    claims = getattr(epistemic_state, "iter_claims", lambda: {})()
    evidence = getattr(epistemic_state, "iter_evidence", lambda: {})()

    for claim_id, claim in claims.items():
        kind = claim.get("kind", "fact")
        status = claim.get("state", claim.get("status"))
        if kind == "requirement" and status == "active":
            state.active_requirement_ids.add(claim_id)
        elif kind == "decision" and status == "approved":
            state.approved_decision_ids.add(claim_id)
        elif (kind == "fact" and status in {"unknown", "conflicted"}) or (
            kind == "assumption" and status == "open"
        ):
            state.unresolved_claim_ids.add(claim_id)

    for evidence_id, ev in evidence.items():
        content_hash = ev.get("content_hash")
        if not content_hash:
            content_hash = _sha256(json.dumps(ev, sort_keys=True, default=str))
        state.evidence_handles[evidence_id] = content_hash

    for event in ledger_events or []:
        payload = event.get("payload", {}) or {}
        for result in payload.get("verification_results") or []:
            if result.get("mandatory") and result.get("status") in {
                "failed",
                "not_run",
            }:
                criterion_id = result.get("criterion_id")
                if criterion_id:
                    state.failed_mandatory_check_ids.add(criterion_id)

    return state


class ContextCompactor:
    """EPR-CTX-002: produce a context capsule that preserves L1 state.

    Narrative summaries (L3) may be proposed by an LLM, but compaction MUST
    preserve every piece of L1 authoritative state and MUST NOT delete it.
    The resulting capsule asserts the three invariants and is validated by the
    unmodified :func:`epr.memory.validate_context_capsule`.
    """

    def compact(
        self,
        canonical_state: CanonicalState,
        narrative: str,
        *,
        canonical_claims: list[dict[str, Any]] | None = None,
        mandatory_failed_check_ids: list[str] | None = None,
        task_id: str | None = None,
        source_ledger_head_hash: str | None = None,
    ) -> dict[str, Any]:
        """Produce a schema-shaped ``ContextCapsule`` from *canonical_state*.

        Raises :class:`ValueError` if the capsule fails the kernel's
        ``validate_context_capsule`` invariants (fail closed — compaction must
        never silently drop authoritative state).
        """
        claims = canonical_claims or []
        failed_ids = (
            sorted(mandatory_failed_check_ids)
            if mandatory_failed_check_ids is not None
            else sorted(canonical_state.failed_mandatory_check_ids)
        )
        ledger_hash = source_ledger_head_hash or _sha256(narrative)

        capsule: dict[str, Any] = {
            "capsule_id": f"capsule-{uuid.uuid4().hex}",
            "task_id": task_id or "task-unknown",
            "generated_at": _now(),
            "source_ledger_head_hash": ledger_hash,
            "active_requirement_ids": sorted(canonical_state.active_requirement_ids),
            "approved_decision_ids": sorted(canonical_state.approved_decision_ids),
            "unresolved_claim_ids": sorted(canonical_state.unresolved_claim_ids),
            "failed_mandatory_check_ids": failed_ids,
            "evidence_handles": sorted(canonical_state.evidence_handles),
            "artifact_hashes": sorted(canonical_state.artifact_hashes.values()),
            "narrative": narrative,
            "invariants": {
                "authoritative_state_preserved": True,
                "evidence_dereferenceable": True,
                "summary_is_not_evidence": True,
            },
        }

        errors = validate_context_capsule(capsule, claims, failed_ids)
        if errors:
            raise ValueError("invalid context capsule: " + "; ".join(errors))
        return capsule


class ContextCompiler:
    """EPR-CTX-006: compile context packages by graph reachability.

    The dependency graph is rooted at the current task and follows
    ``task -> acceptance_criteria -> claims -> evidence/premises``.  BFS from
    the task node collects only the reachable claims, evidence, and ledger
    events.  When the graph is trivial (a single task with no dependencies),
    the compiler falls back to "include everything".
    """

    def compile(
        self,
        task_contract: dict[str, Any],
        epistemic_state: Any,
        ledger_events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build a ``ContextPackage`` reachable from the current task."""
        task_id = task_contract.get("task_id", "task-unknown")
        criteria = list(task_contract.get("acceptance_criteria", []))
        claims = self._as_dict(getattr(epistemic_state, "iter_claims", lambda: {})())
        evidence = self._as_dict(getattr(epistemic_state, "iter_evidence", lambda: {})())
        events = list(ledger_events or [])

        graph = self._build_graph(task_id, criteria, claims, evidence)

        if self._is_trivial(graph):
            return self._package(
                strategy="include_everything",
                task_id=task_id,
                claims=list(claims.values()),
                evidence=list(evidence.values()),
                events=events,
                total_items=len(claims) + len(evidence) + len(events),
            )

        reachable = self._reach(graph, "task")
        reachable_claim_ids = {
            node.partition(":")[2] for node in reachable if node.startswith("claim:")
        }
        reachable_evidence_ids = {
            node.partition(":")[2] for node in reachable if node.startswith("evidence:")
        }
        reachable_criterion_ids = {
            node.partition(":")[2] for node in reachable if node.startswith("criterion:")
        }

        reachable_claims = [
            claims[cid] for cid in sorted(reachable_claim_ids) if cid in claims
        ]
        reachable_evidence = [
            evidence[eid] for eid in sorted(reachable_evidence_ids) if eid in evidence
        ]
        reachable_events = self._reachable_events(
            events,
            task_id,
            reachable_claim_ids | reachable_evidence_ids | reachable_criterion_ids,
        )

        return self._package(
            strategy="graph_reachability",
            task_id=task_id,
            claims=reachable_claims,
            evidence=reachable_evidence,
            events=reachable_events,
            total_items=len(claims) + len(evidence) + len(events),
        )

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    @staticmethod
    def _as_dict(mapping: Any) -> dict[str, dict[str, Any]]:
        return mapping if isinstance(mapping, dict) else {}

    def _build_graph(
        self,
        task_id: str,
        criteria: list[dict[str, Any]],
        claims: dict[str, dict[str, Any]],
        evidence: dict[str, dict[str, Any]],
    ) -> dict[str, set[str]]:
        """Return an adjacency map (node -> dependent nodes) for BFS."""
        graph: dict[str, set[str]] = {"task": set()}
        claim_ids = set(claims)
        evidence_ids = set(evidence)

        def add_edge(a: str, b: str) -> None:
            graph.setdefault(a, set()).add(b)
            graph.setdefault(b, set())

        for criterion in criteria:
            criterion_node = f"criterion:{criterion.get('id')}"
            graph.setdefault(criterion_node, set())
            add_edge("task", criterion_node)
            for ref in criterion.get("requirement_refs", []):
                if ref in claim_ids:
                    add_edge(criterion_node, f"claim:{ref}")
                elif ref in evidence_ids:
                    add_edge(criterion_node, f"evidence:{ref}")

        for claim_id, claim in claims.items():
            claim_node = f"claim:{claim_id}"
            graph.setdefault(claim_node, set())
            for eid in list(claim.get("evidence_ids", [])) + list(
                claim.get("premise_evidence_ids", [])
            ):
                if eid in evidence_ids:
                    add_edge(claim_node, f"evidence:{eid}")

        for evidence_id in evidence:
            graph.setdefault(f"evidence:{evidence_id}", set())

        return graph

    @staticmethod
    def _is_trivial(graph: dict[str, set[str]]) -> bool:
        """True when the task node has no outgoing dependencies."""
        return not graph.get("task", set())

    @staticmethod
    def _reach(graph: dict[str, set[str]], root: str) -> set[str]:
        seen: set[str] = {root}
        queue: deque[str] = deque([root])
        while queue:
            node = queue.popleft()
            for neighbor in graph.get(node, ()):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)
        return seen

    # ------------------------------------------------------------------
    # Ledger-event reachability
    # ------------------------------------------------------------------

    def _reachable_events(
        self,
        events: list[dict[str, Any]],
        task_id: str,
        reachable_ids: set[str],
    ) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        for event in events:
            if event.get("subject_id") == task_id:
                selected.append(event)
                continue
            if self._references(event.get("payload"), reachable_ids):
                selected.append(event)
        return selected

    def _references(self, payload: Any, reachable_ids: set[str]) -> bool:
        if not reachable_ids:
            return False
        for value in self._flatten(payload):
            if isinstance(value, str) and any(
                rid and rid in value for rid in reachable_ids
            ):
                return True
        return False

    def _flatten(self, value: Any) -> Iterator[Any]:
        if isinstance(value, dict):
            for item in value.values():
                yield from self._flatten(item)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                yield from self._flatten(item)
        else:
            yield value

    # ------------------------------------------------------------------
    # Package assembly
    # ------------------------------------------------------------------

    def _package(
        self,
        *,
        strategy: str,
        task_id: str,
        claims: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
        events: list[dict[str, Any]],
        total_items: int,
    ) -> dict[str, Any]:
        claim_ids = sorted(str(c["claim_id"]) for c in claims if c.get("claim_id"))
        evidence_ids = sorted(
            str(e["evidence_id"]) for e in evidence if e.get("evidence_id")
        )
        event_ids = sorted(str(e["event_id"]) for e in events if e.get("event_id"))
        included = len(claims) + len(evidence) + len(events)

        return {
            "package_id": f"context-{uuid.uuid4().hex}",
            "task_id": task_id,
            "compilation_strategy": strategy,
            "reachable_claim_ids": claim_ids,
            "reachable_evidence_ids": evidence_ids,
            "reachable_event_ids": event_ids,
            "claims": claims,
            "evidence": evidence,
            "ledger_events": events,
            "excluded_item_count": max(0, total_items - included),
        }
