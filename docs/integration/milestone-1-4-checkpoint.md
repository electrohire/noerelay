# Milestone 1-4 Checkpoint Report

**Date**: 2026-09-03T13:17:00Z  
**Status**: ✅ Complete — ready for review before Milestone 5  
**Mission**: [`NOERELAY_INTEGRATION_MISSION.md`](C:\Users\trist\Downloads\NOERELAY_INTEGRATION_MISSION.md)

---

## Summary

Milestones 1-4 are complete. No production behavior has changed. All 132 existing tests pass. The foundation for advisory learned ranking is in place: a provider-neutral `RankingAdvice` contract, a staged router with filter→rank→select stages, and a working LLMRouter Python sidecar.

---

## Files Created/Modified

### New Files

| File | Purpose | Lines |
|------|---------|-------|
| `docs/integration/learned-routing-architecture.md` | Repository audit & integration plan | ~250 |
| `docs/adr/0003-advisory-learned-ranking.md` | ADR: advisory ranking within deterministic routing | ~120 |
| `docs/adr/0004-governed-local-agent-execution.md` | ADR: governed local-agent execution | ~130 |
| `docs/adr/0005-hash-chained-epistemic-ledger.md` | ADR: hash-chained ledger & analytics | ~180 |
| `crates/noerelay-core/src/ranking.rs` | RankingAdvice contract, AdvisoryRanker trait, validation | ~420 |
| `scripts/llmrouter_sidecar.py` | LLMRouter Python sidecar (health/version/rank) | ~280 |

### Modified Files

| File | Change |
|------|--------|
| `crates/noerelay-core/src/lib.rs` | Added `pub mod ranking` + re-exports for ranking and staged routing types |
| `crates/noerelay-core/src/routing.rs` | Added `StagedRouter`, `StagedRouteDecision`, `RankingProvenance`, 4 new tests |

---

## Test Results

```
cargo test -p noerelay-core
  test result: ok. 132 passed; 0 failed; 0 ignored; 0 measured
```

### New Tests (20 total)

**ranking.rs** (16 tests):
- `valid_advice_passes_validation`
- `wrong_schema_version_is_rejected`
- `non_advisory_advice_is_rejected`
- `expired_advice_is_rejected`
- `non_expired_advice_without_expiry_passes`
- `candidate_set_hash_mismatch_is_rejected`
- `features_hash_mismatch_is_rejected`
- `unknown_candidate_is_rejected`
- `duplicate_candidate_is_rejected`
- `score_out_of_bounds_is_rejected`
- `empty_scores_is_rejected`
- `candidate_set_hash_is_deterministic`
- `candidate_set_hash_changes_with_different_candidates`
- `candidate_set_hash_is_order_independent`
- `features_hash_is_deterministic`
- `ranker_error_display`

**routing.rs staged_tests** (4 tests):
- `disabled_ranking_is_identical_to_baseline`
- `ranker_failure_falls_back_to_deterministic`
- `shadow_mode_records_but_does_not_affect_selection`
- `advisory_mode_reorders_by_ranker_scores`

---

## Behavior Compatibility

| Check | Status |
|-------|--------|
| Existing `Router::select()` unchanged | ✅ |
| All 132 existing tests pass | ✅ |
| `StagedRouter` with `RankingMode::Disabled` produces identical output to `Router` | ✅ |
| No existing API surface modified | ✅ |
| No production behavior changed | ✅ |

---

## Security Boundaries

| Boundary | Status |
|----------|--------|
| Ranker receives only admissible candidates (already filtered) | ✅ |
| `advisory_only` must be `true` — non-advisory advice rejected | ✅ |
| Candidate set hash binding prevents candidate injection | ✅ |
| Feature hash binding prevents feature tampering | ✅ |
| Score bounds enforced (0..=1_000_000 ppm) | ✅ |
| Expired advice rejected | ✅ |
| Unknown/duplicate candidates rejected | ✅ |
| Ranker failure → deterministic fallback (never fails the job) | ✅ |
| Sidecar receives sanitized features only (no secrets, raw prompts, credentials) | ✅ |
| Sidecar has no access to production credentials or policy | ✅ |
| Sidecar cannot dispatch model calls, add candidates, or release results | ✅ |

---

## Unresolved Audit Findings

| # | Finding | Severity | Recommended Action |
|---|---------|----------|--------------------|
| 1 | Ledger missing event envelope, signatures, partitions, Merkle proofs | High | Milestone 10 |
| 2 | No evaluator ingestion contract | High | Milestone 5 |
| 3 | No Spec Kit integration | High | Milestone 6 |
| 4 | Agent registry exists but no governed dispatch | High | Milestone 8 |
| 5 | `rtk/` crate name conflicts with external Rust Token Killer | High | Milestone 9 |
| 6 | No analytics projections | Medium | Milestone 11 |
| 7 | No compliance registry | Medium | Milestone 12 |
| 8 | Router uses flat Candidate list, not registry | Medium | Milestone 7 |
| 9 | No `epistemic_kind`/`epistemic_status` on ledger events | Low | Milestone 10 |
| 10 | No `unsupported` evidence kind | Low | Milestone 5 |

---

## Recommended Scope for Milestone 5

Milestone 5 (Evaluator Ingestion) should:

1. Create a generic evaluator-result ingestion contract in `noerelay-core` (new `evaluator_ingestion.rs` module)
2. Map Spec Kit outcomes (`pass`, `warn`, `iterate`, `clarify`, `gather_evidence`, `block`) to NoeRelay actions
3. Preserve evidence distinctions: `observed`, `inferred`, `asserted`, `contradicted`, `unsupported`
4. Add `unsupported` to the existing `EnvelopeStatus` enum
5. Wire evaluator results into the verification DAG
6. Add tests for all outcome mappings and evidence kind distinctions

**Do not proceed** into candidate/agent refactors (Milestones 7-8) until Milestones 5-6 are complete and reviewed.

---

## LLMRouter Sidecar Verification

```
GET /health  → {"status":"healthy","timestamp":1788441379160}
GET /version → {"sidecar":"llmrouter-sidecar","version":"0.1.0","schema_version":"1.0.0","model":"qwen3:8b"}
POST /rank   → Calls Ollama with ranking prompt, returns RankingAdvice JSON
```

The sidecar starts on port 9878, uses `qwen3:8b` by default, and enforces:
- 1 MB request size limit
- 30s HTTP timeout, 60s LLM call timeout
- JSON schema validation on input
- Graceful fallback to acceptance-LCB-based ranking if LLM response is unparseable
- 5-minute advice TTL