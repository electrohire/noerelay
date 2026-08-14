"""Context capsule conformance checks."""

from __future__ import annotations

from typing import Any


def validate_context_capsule(
    capsule: dict[str, Any],
    canonical_claims: list[dict[str, Any]],
    mandatory_failed_check_ids: list[str],
) -> list[str]:
    """Return invariant violations; an empty list means the capsule is admissible."""

    errors: list[str] = []
    active_requirements = {
        claim["claim_id"]
        for claim in canonical_claims
        if claim.get("kind") == "requirement" and claim.get("state") == "active"
    }
    approved_decisions = {
        claim["claim_id"]
        for claim in canonical_claims
        if claim.get("kind") == "decision" and claim.get("state") == "approved"
    }
    unresolved = {
        claim["claim_id"]
        for claim in canonical_claims
        if (claim.get("kind") == "fact" and claim.get("state") in {"unknown", "conflicted"})
        or (claim.get("kind") == "assumption" and claim.get("state") == "open")
    }
    required_evidence = {
        evidence_id
        for claim in canonical_claims
        if claim.get("claim_id") in active_requirements | approved_decisions | unresolved
        for evidence_id in claim.get("support_evidence_ids", []) + claim.get("refutation_evidence_ids", [])
    }

    checks = [
        ("active_requirement_ids", active_requirements),
        ("approved_decision_ids", approved_decisions),
        ("unresolved_claim_ids", unresolved),
        ("failed_mandatory_check_ids", set(mandatory_failed_check_ids)),
        ("evidence_handles", required_evidence),
    ]
    for field, required in checks:
        present = set(capsule.get(field, []))
        missing = sorted(required - present)
        if missing:
            errors.append(f"{field} missing: {','.join(missing)}")

    invariants = capsule.get("invariants", {})
    for name in (
        "authoritative_state_preserved",
        "evidence_dereferenceable",
        "summary_is_not_evidence",
    ):
        if invariants.get(name) is not True:
            errors.append(f"invariant not asserted: {name}")
    return errors
