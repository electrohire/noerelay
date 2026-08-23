# Legacy Hand-Written Schemas — Deprecated

These JSON Schema files were hand-written before the canonical schema
lineage was established (FND-02). They conflict with the Rust domain types
in `crates/noerelay-core/src/`, which are now the single source of truth.

**Do not use these schemas for new development.** The canonical schemas
are generated from Rust types and live in `spec/schemas/generated/`.

## Why These Conflict

| Legacy Schema | Conflicts with Rust Type | Key Difference |
|---|---|---|
| `task-contract.schema.json` | `contract::TaskContract` | Uses `task_id`, `goal`, float `max_cost_usd` vs `request_id`, `outcome`, integer `max_cost_microusd` |
| `candidate-action.schema.json` | `routing::Candidate` | Uses `action_kind`, `provider_family` vs `openrouter_model_id`, `capabilities` |
| `route-decision.schema.json` | `routing::RouteDecision` | Uses `decision_id`, `selected_plan` vs `selected_candidate_id`, `rejections` |
| `claim.schema.json` | `epistemic::Claim` | Uses `created_at`, `confidence` vs `claim_id`, `state`, `supporting_evidence` |
| `context-capsule.schema.json` | `context::ContextManifest` | Uses `capsule_id`, `narrative` vs `budget_tokens`, `included`, `manifest_hash` |
| `evidence.schema.json` | `traceability::Evidence` | Uses `kind`, `producer` vs `evidence_id`, `test_id`, `status` |
| `evidence-receipt.schema.json` | `receipt::SignedRunReceipt` | Uses `receipt_id`, `total_cost` vs `receipt`, `signature_base64` |
| `ledger-event.schema.json` | `ledger::LedgerEvent` | Uses `event_id`, `actor` vs `sequence`, `organization_id` |
| `common.schema.json` | Various Rust types | Defines `identifier`, `money` etc. with different names/structures |
| `signed-run-receipt.schema.json` | `receipt::SignedRunReceipt` | Aligned but hand-written; replaced by generated version |

## Migration

If you reference these schemas, migrate to the generated schemas in
`spec/schemas/generated/`. See `spec/schemas/version.json` for the
current schema version and `docs/migrations/schema-v1-to-v2.md` for
detailed migration guidance.