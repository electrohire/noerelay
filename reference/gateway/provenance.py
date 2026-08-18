"""EPR-LED-005: provenance mapping for evidence records.

Maps evidence records to W3C PROV-compatible identifiers and to in-toto
Statement v1 attestations.  Dependency-free (stdlib only).
"""

from __future__ import annotations

from typing import Any

IN_TOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v1"

_PREDICATE_TYPES: dict[str, str] = {
    "test_result": "https://in-toto.io/attestation/test-result/v1",
    "formal_proof": "https://in-toto.io/attestation/proof/v1",
    "model_assertion": "https://in-toto.io/attestation/model-output/v1",
    "direct_observation": "https://in-toto.io/attestation/observation/v1",
    "tool_result": "https://in-toto.io/attestation/tool-result/v1",
    "source_document": "https://in-toto.io/attestation/source-document/v1",
    "human_attestation": "https://in-toto.io/attestation/human-attestation/v1",
    "derived": "https://in-toto.io/attestation/derived/v1",
}
_DEFAULT_PREDICATE_TYPE = "https://in-toto.io/attestation/evidence/v1"


def _sha256_digest(hash_value: Any) -> str:
    """Strip the ``sha256:`` prefix for an in-toto digest value."""
    if isinstance(hash_value, str) and hash_value.startswith("sha256:"):
        return hash_value[len("sha256:"):]
    return hash_value or ""


def _subject(name: str, hash_value: Any) -> list[dict[str, Any]]:
    return [{"name": name, "digest": {"sha256": _sha256_digest(hash_value)}}]


def _predicate_for(evidence: dict[str, Any], kind: str) -> dict[str, Any]:
    """Build the in-toto predicate for an evidence kind."""
    if kind == "test_result":
        return dict(evidence.get("test_metadata", {}))
    if kind == "formal_proof":
        return dict(evidence.get("test_metadata", evidence.get("proof_metadata", {})))
    if kind == "model_assertion":
        return {
            "model_id": evidence.get("model_id", ""),
            "strength": evidence.get("strength"),
            "location": evidence.get("location", ""),
        }
    if kind == "direct_observation":
        return {
            "location": evidence.get("location", ""),
            "strength": evidence.get("strength"),
            "activity_id": evidence.get("activity_id", ""),
        }
    return {
        "evidence_id": evidence.get("evidence_id", ""),
        "location": evidence.get("location", ""),
        "strength": evidence.get("strength"),
    }


class ProvenanceMapper:
    """EPR-LED-005: Maps evidence to W3C PROV and in-toto attestations."""

    @staticmethod
    def map_to_prov(evidence: dict[str, Any]) -> dict[str, Any]:
        """Map an evidence record to W3C PROV-compatible identifiers.

        ``entity`` is the evidence's content hash, ``activity`` is the
        producing activity, ``agent`` is the responsible producer, and
        ``was_derived_from`` is the premise derivation chain.  ``test_result``
        and ``formal_proof`` evidence additionally expose ``artifact`` and
        ``environment`` PROV entities.
        """
        prov: dict[str, Any] = {
            "entity": evidence.get("content_hash", ""),
            "activity": evidence.get("activity_id", ""),
            "agent": evidence.get("producer", {}).get("id", ""),
            "was_derived_from": evidence.get("premise_evidence_ids", []),
        }
        kind = evidence.get("kind")
        if kind in ("test_result", "formal_proof"):
            prov["artifact"] = evidence.get("artifact_hash", "")
            prov["environment"] = evidence.get("environment_hash", "")
        return prov

    @staticmethod
    def map_to_in_toto(evidence: dict[str, Any]) -> dict[str, Any]:
        """Map an evidence record to an in-toto Statement v1 attestation."""
        kind = evidence.get("kind", "")
        if kind in ("test_result", "formal_proof"):
            subject = _subject("artifact", evidence.get("artifact_hash", ""))
        else:
            subject = _subject("content", evidence.get("content_hash", ""))
        return {
            "_type": IN_TOTO_STATEMENT_TYPE,
            "subject": subject,
            "predicate_type": _PREDICATE_TYPES.get(kind, _DEFAULT_PREDICATE_TYPE),
            "predicate": _predicate_for(evidence, kind),
        }

    @staticmethod
    def enrich_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
        """Add a ``prov`` field to *evidence* when absent, then return it."""
        if "prov" not in evidence:
            evidence["prov"] = ProvenanceMapper.map_to_prov(evidence)
        return evidence
