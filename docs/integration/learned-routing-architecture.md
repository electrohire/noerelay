# Learned Routing Architecture — Repository Audit & Integration Plan

**Status**: Milestone 1 checkpoint  
**Date**: 2026-09-03  
**Mission**: [`NOERELAY_INTEGRATION_MISSION.md`](C:\Users\trist\Downloads\NOERELAY_INTEGRATION_MISSION.md)

---

## 1. Current Routing Flow & Candidate Representation

### 1.1 Router (`crates/noerelay-core/src/routing.rs`)

The current router is a single-stage deterministic selector:

```text
candidates → filter (constraints) → sort (cost, latency, LCB, id) → pick first
```

**`Candidate`** struct binds:
- `candidate_id`, `openrouter_model_id`, `provider`
- `available`, `capabilities` (BTreeSet), `maximum_data_class`
- `cost` (CostBreakdown), `latency_p95_ms`, `acceptance_lcb_ppm`
- `supports_independent_verification`

**`Constraints`** struct binds:
- `required_capabilities`, `data_class`, `allowed_providers`
- `max_total_cost_microusd`, `max_latency_ms`, `min_acceptance_lcb_ppm`
- `require_independent_verification`

**`RouteDecision`** output:
- `selected_candidate_id`, `selected_openrouter_model_id`
- `expected_total_cost_microusd`
- `rejections: Vec<CandidateRejection>` (each with structured `RejectionReason`)

**Gap**: No ranking stage exists. The router sorts by cost→latency→LCB→id and picks the first admissible candidate. There is no place for an advisory ranker to reorder candidates.

### 1.2 Gateway (`crates/noerelay-gateway/src/lib.rs`, 1929 lines)

The gateway orchestrates the full lifecycle: contract compilation → routing → execution → verification → ledgering → receipt. It calls `Router::select()` directly. The gateway is the integration point where a ranking stage would be inserted.

### 1.3 Candidate Configuration

Candidates are configured via `NOERELAY_CANDIDATES_JSON` environment variable (JSON array of `Candidate` structs). No dynamic registry lookup at routing time — all candidates are loaded at startup.

---

## 2. Current Model Gateway & Agent Registry

### 2.1 Registry (`crates/noerelay-core/src/registry.rs`)

Full lifecycle-managed registry with:
- **`ModelRevision`**: 30+ fields including pricing, data policy, regions, health, benchmarks, provenance
- **`ProviderRevision`**: endpoint, modalities, rate limits, data policy
- **`AgentRevision`**: endpoint, trust root, capabilities, allowed models, data policy
- **`ToolRevision`**: input/output schemas, risk class, side effects, permissions

Lifecycle state machine: `Draft → Proposed → Reviewed → Approved → Active → Quarantined/Superseded`

**Gap**: The registry exists but the router doesn't use it — it uses the flat `Candidate` list from environment config. The registry's `AgentRevision` has `endpoint`, `trust_root`, `allowed_models` — ready for governed local-agent dispatch (Milestone 8).

### 2.2 OpenRouter Integration (`reference/gateway/openrouter.py`)

Python reference implementation with `StubOpenRouterClient` and `HttpOpenRouterClient`. The stub returns `[noerelay stub] {user_text[:200]}`. The live client posts to OpenRouter's API.

### 2.3 Local Models (`reference/gateway/local_models.py`)

`LocalModelClient` sends requests to Ollama/vLLM-compatible endpoints. `StubLocalModelClient` returns deterministic responses.

---

## 3. Current Evidence, Epistemic-State, Ledger, Receipt & Analytics Models

### 3.1 Evidence (`crates/noerelay-core/src/evidence.rs`)

`EvidenceEnvelope` with:
- Status types: `Claimed`, `ObservedPass`, `ObservedFail`, `Inferred`, `Contradicted`, `Accepted`, `Rejected`
- Artifact hashes (result + logs), source revision, environment profile
- Runner identity, independent verifier identity
- `is_release_ready()`: only `ObservedPass` and `Accepted` qualify
- `requires_independent_verifier()`: checks for missing verifier on observed evidence

**Already aligned with mission**: Distinguishes observed from claimed. Missing: `unsupported` evidence kind (plan §8).

### 3.2 Ledger (`crates/noerelay-core/src/ledger.rs`)

Hash-chained append-only ledger:
- `LedgerEvent` with sequence, timestamps, org/project/run IDs, kind, payload, previous_hash, event_hash
- SHA-256 hashing via `serde_json::to_vec` canonicalization
- `verify()` checks sequence continuity, hash chain integrity, and event hash correctness
- Genesis hash: 64 zero bytes
- Event kinds: `RequestAccepted`, `ContractCompiled`, `RouteSelected`, `ToolAuthorized`, `AttemptCompleted`, `VerificationObserved`, `ClaimTransitioned`, `CostReconciled`, `RunReleased`, `RunRejected`, `AdministrativeAction`

**Gaps vs mission §10**:
- No event envelope with `ledger_schema_version`, `event_id` (UUID), `tenant_id`, `contract_id`, `epistemic_kind`, `epistemic_status`, `subject_refs`, `actor`, `policy_revision`, `payload_schema`, `payload_hash` (separate from event_hash), `signature`
- No canonical JSON (RFC 8785) — uses `serde_json::to_vec` which is deterministic but not RFC 8785
- No partitions, concurrency control (CAS), checkpoints, Merkle proofs, signatures, or anchoring
- No epistemic semantics (supported/refuted/conflicted/unknown)
- No evidence lifecycle (retention, legal hold, redaction, crypto-erasure)
- No verification/recovery tools beyond `verify()`

### 3.3 Receipt (`crates/noerelay-core/src/receipt.rs`)

Ed25519-signed `RunReceipt`:
- `ReceiptSigner` from 32-byte seed with key ID validation
- `ReceiptVerifier` from base64 public key
- Signature verification with tamper detection
- Algorithm pinning (Ed25519 only)

**Already aligned**: Signatures, key IDs, algorithm pinning. Missing: certificate chains, key rotation, validity windows, revocation.

### 3.4 Analytics

No analytics layer exists. The ledger is the only persistent state. No projections, warehouses, metrics, or dashboards.

---

## 4. Current Observations & Context Compaction

### 4.1 Observations

No formal observation schema exists. The `improvement_analyzer.py` in Python computes composite scores from benchmark results but doesn't persist structured observations.

### 4.2 RTK / Context Compaction

The `rtk/` crate is named `noerelay-rtk` — this is the **internal** RTK concept, NOT the external Rust Token Killer. Per mission §9, this must be renamed before integrating external RTK.

No context compaction implementation exists yet. The `context.rs` module exists but was not inspected in this audit.

---

## 5. Provider & Verification Restrictions

### 5.1 Provider Restrictions

- `Constraints.allowed_providers`: BTreeSet filter in router
- `Candidate.provider`: single provider string
- `RejectionReason::ProviderDenied`: structured rejection

### 5.2 Verification Independence (`crates/noerelay-core/src/verification.rs`)

`VerificationDag` enforces:
- Topological ordering with cycle detection
- Dependency checking (results must pass before dependents)
- **Independent verifier enforcement**: For `High`/`Critical` risk, `CheckKind::IndependentReview` must have `verifier_family != worker_family`
- Release outcomes: `Accepted`, `RepairRequired`, `EscalationRequired`, `HumanApprovalRequired`

**Already aligned with mission §2 (Verification independence)**: `generator != verifier` enforced via `verifier_family` comparison.

---

## 6. Existing Test & Release Invariants

### 6.1 Router Tests (`routing.rs`)
- Cheapest admissible candidate wins
- Inadmissible candidates never selected
- Every rejection reason recorded
- Selection is stable for equal scores

### 6.2 Ledger Tests (`ledger.rs`)
- Valid chain verifies
- Changed payload detected
- Deletion detected
- Reordering detected

### 6.3 Evidence Tests (`evidence.rs`)
- Status-based release readiness
- Revision validation
- Artifact hash presence

### 6.4 Receipt Tests (`receipt.rs`)
- Trusted key verifies
- Tampering detected
- Replacement key rejected

### 6.5 Contract Tests (`contract.rs`)
- Deterministic compilation
- High-risk requires acceptance criteria
- Duplicate criteria rejected

### 6.6 Budget Tests (`budget.rs`)
- Reservations prevent overspend
- Failed reconciliation is atomic
- Unused capacity returned

---

## 7. Gaps Between Repository & Mission

| # | Gap | Severity | Mission Ref |
|---|-----|----------|-------------|
| 1 | No ranking stage in router | Critical | §5, §7 |
| 2 | No `RankingAdvice` contract | Critical | §5 |
| 3 | No LLMRouter sidecar | Critical | §7 |
| 4 | Ledger missing event envelope, signatures, partitions, Merkle proofs | High | §10 |
| 5 | No evaluator ingestion contract | High | §8 |
| 6 | No Spec Kit integration | High | §8 |
| 7 | Agent registry exists but no governed dispatch | High | §9 |
| 8 | `rtk/` crate name conflicts with external Rust Token Killer | High | §9 |
| 9 | No analytics projections | Medium | §11 |
| 10 | No compliance registry | Medium | §17 |
| 11 | No training/promotion pipeline | Medium | §13 |
| 12 | No failure tests for ranking scenarios | Medium | §15 |
| 13 | Router uses flat Candidate list, not registry | Medium | §7 |
| 14 | No `epistemic_kind`/`epistemic_status` on ledger events | Low | §10.5 |
| 15 | No `unsupported` evidence kind | Low | §8 |

---

## 8. Migration Map

### Phase 1: Non-breaking foundation (Milestones 1-4)
```
1. Add RankingAdvice type to noerelay-core (new module, no existing code changed)
2. Add AdvisoryRanker trait to noerelay-core
3. Refactor Router::select() into filter → optional_rank → select stages
   (preserve existing output for disabled ranking)
4. Add LLMRouter Python sidecar with /rank, /health, /version
   (shadow mode only — records advice, doesn't affect routing)
```

### Phase 2: Evaluator & candidate generalization (Milestones 5-8)
```
5. Add evaluator-result ingestion contract
6. Bridge Spec Kit lifecycle hooks
7. Generalize route targets to ModelRevision/AgentRevision
8. Implement governed local-agent dispatch
```

### Phase 3: Infrastructure hardening (Milestones 9-11)
```
9. Rename rtk/ crate, integrate external RTK
10. Harden ledger with signatures, checkpoints, Merkle proofs
11. Build analytics projections
```

### Phase 4: Compliance & training (Milestones 12-17)
```
12-17. Compliance registry, CMMC, EU AI Act, training, promotion, mode migration
```

### Invariant: No parallel sources of truth
- Router remains the single authority for route decisions
- Ledger remains the single authority for event history
- Registry remains the single authority for model/agent/tool identity
- Ranking advice is always advisory — never authoritative

---

## 9. RTK Naming Conflict

The `rtk/` crate (`noerelay-rtk`) must be renamed before integrating the external Rust Token Killer. Proposed rename: `noerelay-compact` or `noerelay-context`. The external RTK integration will use the `rtk` name as specified in the mission.

---

## 10. Locked Invariants

1. **Rust authority**: All policy, routing, budgets, verification, ledgering, and release decisions remain in Rust (§2)
2. **Advisory ranking**: Rankers may abstain, fail, or be disabled; deterministic fallback always available (§2)
3. **Evidence integrity**: Raw evidence never silently replaced by summaries (§2)
4. **Verification independence**: `generator != verifier` for high-risk work (§2)
5. **No self-modifying policy**: Production traffic never auto-promotes rankers (§2)
6. **Ledger integrity ≠ truth**: Hash chain proves ordering, not correctness (§2)
7. **Analytics never become authority**: Dashboards are derived projections (§2)
8. **Existing tests must pass**: All deterministic regression fixtures remain identical (§19)