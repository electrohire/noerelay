# ADR-0005: Hash-chained epistemic ledger and analytics projections

**Status**: Proposed  
**Date**: 2026-09-03  
**Supersedes**: None  
**Superseded by**: None

## Context

NoeRelay has an existing hash-chained ledger (`crates/noerelay-core/src/ledger.rs`) with SHA-256 event hashing, sequence continuity verification, and tamper detection. The integration mission requires hardening this ledger with cryptographic signatures, Merkle proofs, partitioning, epistemic semantics, evidence lifecycle management, and rebuildable analytics projections — all while preserving the existing ledger as the single authoritative source.

## Decision

### Extend, Don't Replace

The existing `Ledger` type is the authoritative implementation. All hardening is done by extending it, not creating a parallel ledger. The existing `LedgerEvent` structure is evolved to include the richer event envelope while maintaining backward compatibility for existing event kinds.

### Event Envelope

Every event will bind:

```json
{
  "ledger_schema_version": "1.0",
  "ledger_id": "tenant/project partition",
  "sequence": 42,
  "event_id": "uuid",
  "tenant_id": "opaque tenant id",
  "organization_id": "opaque organization id",
  "project_id": "opaque project id",
  "run_id": "uuid",
  "contract_id": "uuid",
  "event_type": "evidence.observed",
  "epistemic_kind": "observation",
  "epistemic_status": "supported",
  "subject_refs": ["claim/artifact/decision ids"],
  "actor": {"kind": "agent", "id": "...", "revision": "..."},
  "policy_revision": "...",
  "payload_schema": "...",
  "payload_hash": "sha256:...",
  "previous_event_hash": "sha256:...",
  "occurred_at": "source time",
  "recorded_at": "authority time",
  "event_hash": "sha256:...",
  "signature": {"key_id": "...", "algorithm": "...", "value": "..."}
}
```

Existing domain vocabulary is preserved where stronger. New event types cover: contract compilation, admissibility, route advice, route choice, execution, raw/derived artifacts, evidence classification, contradiction, verification, authorization, repair, fallback, escalation, release, policy/ranker promotion, and analytics-export checkpoints.

### Deterministic Hashing

- Use `serde_json::to_vec` canonicalization (already deterministic for the existing struct shapes). Evaluate RFC 8785 JSON Canonicalization Scheme for cross-language interoperability.
- Compute `payload_hash` over immutable payload bytes (SHA-256).
- Compute `event_hash` over domain separator, ledger schema version, ledger ID, sequence, previous hash, payload hash, event metadata, and signer identity — excluding the signature value itself.
- Pin algorithms by identifier. SHA-256 is the interoperability baseline.
- Genesis event uses the existing `GENESIS_HASH` (64 zero bytes).

### Ordering, Concurrency, and Partitions

- Monotonically increasing sequence numbers per ledger partition.
- Append with compare-and-swap (CAS) or transactional expected-head semantics.
- Conflict → reload head and retry through bounded authority logic.
- Partition at tenant/project boundary.
- Cross-partition relationships use hashed references and signed checkpoints.

### Signatures, Checkpoints, and Anchoring

- Sign event hashes using Ed25519 (consistent with existing receipt signing).
- Preserve key IDs, validity windows, and signing purpose.
- Batch event hashes into Merkle roots for efficient verification.
- Periodically anchor signed checkpoint roots to an independent trust domain.

### Epistemic Semantics

Keep facts, requirements, assumptions, observations, inferences, predictions, preferences, decisions, and artifacts distinct. Epistemic state can be `unknown`, `supported`, `refuted`, or `conflicted`. Corrections append new events; they never overwrite history. Conflicting evidence remains queryable.

### Evidence and Privacy Lifecycle

- Large/sensitive evidence stored as content-addressed artifacts; ledger only hashes, metadata, classification, and authorized handles.
- Evidence integrity separated from access. Valid hash ≠ read permission.
- Support retention classes, legal hold, residency, export controls, tenant-managed encryption, and cryptographic erasure.
- Redaction creates a new event and restricted replacement view; never rewrites prior event hash.
- Secrets, raw prompts, customer code, and regulated data excluded from analytics projections by default.

### Analytics Projections

```
Authoritative ledger → Verified change stream → Operational read models
                      → Versioned analytics warehouse → Semantic metrics layer
                      → Enterprise dashboards and APIs
                      → Sanitized training datasets
```

- Projectors verify chain continuity and signatures before consuming events.
- Each projection stores source ledger ID, sequence range, head hash, projector revision, schema revision, and rebuild timestamp.
- Projections are idempotent and rebuildable from a verified checkpoint.
- Analytics outages must not block routing or release.

## Consequences

### Positive
- Cryptographic proof of event integrity and ordering
- Enterprise-grade audit trail with signatures and Merkle proofs
- Rebuildable analytics that never affect production routing
- Clear separation between content integrity and epistemic validity

### Negative
- Increased storage for signatures, Merkle proofs, and checkpoints
- Additional latency for signature operations and CAS appends
- Complexity of partition management and cross-partition references

### Neutral
- Existing ledger API preserved; new fields are additive
- Existing `verify()` continues to work for hash-chain integrity
- Analytics are optional; routing works without them