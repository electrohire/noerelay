# ADR-0003: Advisory learned ranking within deterministic routing

**Status**: Proposed  
**Date**: 2026-09-03  
**Supersedes**: None  
**Superseded by**: None

## Context

NoeRelay currently uses a single-stage deterministic router that filters candidates by constraints and selects the cheapest admissible option. The integration mission requires adding learned ranking (LLMRouter) as an advisory layer that may reorder already-admissible candidates without ever bypassing policy, budgets, or verification requirements.

## Decision

### Architecture

```
policy filtering → admissible candidates → optional advisory ranker
                 → NoeRelay deterministic selector → execution → verification
```

The ranker receives only candidates already deemed admissible by policy, budget, data-classification, capability, and provider filters. It returns ranking advice that may reorder candidates. NoeRelay retains final authority over selection.

### Invariants

1. **Rankers may abstain or fail.** If the ranker crashes, times out, returns malformed advice, recommends a prohibited candidate, or is disabled, NoeRelay falls back to deterministic cost→latency→LCB→id ordering.

2. **Revisions are versioned.** Every ranker has an immutable revision identifier. Active revisions require explicit promotion through the governed lifecycle.

3. **Freshness is enforced.** Advice carries `generated_at_unix_ms` and optional `expires_at_unix_ms`. Stale advice is discarded.

4. **Candidate-set binding is enforced.** Advice carries a `candidate_set_hash` over the exact admissible set it ranked. Mismatch → discard.

5. **Feature-set binding is enforced.** Advice carries a `features_hash` over the feature schema used. Mismatch → discard.

6. **Training is offline.** Production traffic never modifies or promotes the active ranker. Training uses versioned, sanitized dataset exports.

7. **Deterministic fallback is always available.** The existing `Router::select()` logic remains the baseline and fallback.

### Production Modes

| Mode | Behavior |
|------|----------|
| `disabled` | No learned-ranker call; deterministic routing only |
| `shadow` | Record advice without influencing selection; compare against actual routes |
| `advisory` | Advice may reorder already-admissible candidates; NoeRelay retains final authority |

Begin in `shadow` mode. Never begin with `advisory` on security, release, governance, or high-risk architecture cohorts.

### Ranking Advice Contract

The `RankingAdvice` type is provider-neutral. It does not reference LLMRouter specifically. Any ranker implementing the `AdvisoryRanker` trait may produce advice.

```rust
pub struct RankingAdvice {
    pub schema_version: String,
    pub ranker: RankerIdentity,
    pub run_id: Uuid,
    pub cohort: String,
    pub features_hash: String,
    pub candidate_set_hash: String,
    pub candidate_scores: Vec<CandidateRanking>,
    pub trained_through_unix_ms: Option<u64>,
    pub generated_at_unix_ms: u64,
    pub expires_at_unix_ms: Option<u64>,
    pub advisory_only: bool,
}
```

### Validation Rules

On receipt of ranking advice, NoeRelay validates:
1. Schema version is supported
2. Ranker identity and revision are known and active
3. `advisory_only` is true (non-advisory advice is rejected)
4. `candidate_set_hash` matches the hash of the admissible set
5. `features_hash` matches the hash of the feature schema used
6. All `candidate_id` values in scores exist in the admissible set
7. No duplicate candidate IDs
8. Scores are within defined bounds
9. Advice is not expired

On any validation failure: discard the advice, route deterministically, and record the structured reason. Advisory failure must not fail the job.

### Decision Provenance

Every route decision records:
- Whether advice participated
- Ranker identity and revision
- Whether NoeRelay followed or overrode the advice
- Why (if overridden)

## Consequences

### Positive
- Learned ranking can improve cost/latency/quality without compromising safety
- Shadow mode enables safe evaluation before production use
- Provider-neutral contract allows multiple ranker implementations
- Deterministic fallback ensures availability even if the ranker fails

### Negative
- Additional latency for ranker calls (mitigated by timeouts and circuit breakers)
- Increased complexity in the routing path
- Requires careful monitoring of ranker health and advice quality

### Neutral
- Existing routing behavior is unchanged when ranking is disabled
- All existing tests continue to pass