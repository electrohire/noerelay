# NoeRelay implementation and release status

**Assessment date:** 2026-08-21  
**Verdict:** Architecture-correct, tested vertical slice; not GA release-ready

This status distinguishes repository implementation from organizational release evidence. Passing a unit test is evidence for that implementation boundary, not proof of legal compliance, availability, security, or fitness for every organization.

## Implemented and exercised

- Rust workspace and single trusted authority semantics for identity scope validation, immutable contract compilation, hard-constraint routing, integer budgets, tool authorization decisions, four-valued epistemics, context manifests, verification DAGs, hash-chain events, advisory recommendations, usage rollups, and requirement/test/evidence traceability.
- Rust OpenAI-wire gateway routes Chat and Responses requests to an explicit OpenRouter model, authenticates requests, limits request bodies, disables redirects, applies timeouts, withholds unverifiable high-risk output, and provides OpenAI-shaped errors.
- Live request path now stages reservation -> contract -> route -> provider -> observed schema evidence -> cost reconciliation -> release/rejection -> receipt. HTTP 200 from a provider is not itself release evidence.
- PostgreSQL migrations and Rust storage commit versioned authority state, forced tenant RLS, append-only ledger rows, signed receipts, token/cost usage records, and model-observation storage. A real PostgreSQL round trip and gateway restart/reload were exercised.
- Accepted receipts are hash-bound and Ed25519-signed. Python exposes trusted-public-key verification only.
- Context reduction protects system/developer decisions, tool state, and the current user requirement; optional history is deterministically selected under an explicit budget and the manifest hash is contract-bound.
- Cost reporting aggregates requests, input/output tokens, and integer micro-USD by configured organization/project/user.
- The built-in governance release-gate endpoint rejects orphaned trace nodes and model-claimed evidence; mandatory requirements require linked observed passing evidence.
- The Go adapter uses the official A2A v2 SDK, authenticates inbound work, and delegates to the Rust `/v1/responses` authority. It cannot route or release.
- Rust and Go container images build as non-root services. Compose runs Rust + PostgreSQL by default and exposes A2A/Ollama only through explicit profiles.

## Partially implemented boundaries

| Boundary | Current behavior | Remaining work before GA |
|---|---|---|
| OpenAI compatibility | Text chat/Responses, non-stream and terminal-buffered SSE | Multipart/multimodal content, complete tool-call schemas, all error/usage edge cases, incremental governed streaming, official-client matrix |
| Identity and tenancy | One API key and one organization/project scope per deployment; user/session attribution | Rust API-key registry, key rotation/revocation, OIDC/service identities, RBAC/admin roles, quotas per tenant, non-enumerating cross-tenant suite |
| Durable execution | PostgreSQL snapshot/version/ledger/receipt commit and restart recovery | Normalized run/attempt/work-item state, outbox/leases, crash recovery during provider calls, idempotent retries, cancellation, distributed workers, multi-replica conflict reload/retry |
| Cost | Candidate expected-total reservation, response tokens, estimated-cost receipts and rollups | Provider pricing snapshots, exact billed-cost reconciliation, tool/verification/human cost ingestion, currency policy, invoice reconciliation, time-series/export UI |
| Context/epistemics | Deterministic message compaction and pure four-valued claim engine | Persistent project memory graph, contradiction extraction/corroboration, source reliability, artifact retrieval, tokenizer-specific accounting, privacy deletion semantics |
| Verification | Risk-scaled DAG and fail-closed high/critical release | Pluggable deterministic test runners, sandboxed verifier workers, approval API, repair loops, asynchronous evidence submission, signed external attestations |
| Tools/agents | Pure Rust authorization rules and authenticated inbound A2A adapter | MCP discovery/execution sandbox, egress broker, secret grants, idempotent side effects, outbound A2A trust registry, loop/depth/fan-out budgets, reconnect/replay/cancel E2E |
| Recommendations | Cohort-scoped Wilson lower-bound advisory logic | Durable observation ingestion from live runs, drift detection, evaluation registry, canary/promotion workflow, operator explanation UI |
| Operations | Health/readiness, non-root images, PostgreSQL migrations, graceful shutdown | Metrics/traces/log correlation in Rust, SLO alerts, secret-manager/HSM signing, backup tooling, PITR drill, SBOM/provenance/signing, multi-architecture images |

## Release blockers outside a truthful repository-only claim

- Independent product, security, privacy/legal, evaluation, and operations approval has not occurred.
- No published load, soak, chaos, upstream-failure, RPO, or RTO evidence meets the quantitative release targets yet.
- No external penetration test, threat-model review, dependency/license decision, or container/SBOM provenance review has been signed off.
- No controlled organizational pilot has established usability, supportability, incident response, data-retention behavior, or model-quality calibration for a declared compliance profile.
- The Kubernetes manifests are fail-closed templates, not a validated production environment.

## Observed local evidence

- `cargo test --workspace`: 50 Rust core tests, 11 gateway tests, the Python binding test, and the storage invariant test pass.
- PostgreSQL integration: migration, transaction, reload, ledger, signed receipt, conflicting-receipt rollback, and token/cost rollup pass against PostgreSQL 17.
- `cargo clippy --workspace --all-targets -- -D warnings` and `cargo fmt --all -- --check`: pass.
- `go test ./...` and `go vet ./...` in `services/a2a-adapter`: pass.
- `cargo audit`: pass after upgrading PyO3; the sole ignored RustSec entry is an uncompiled optional SQLx MySQL/RSA edge, with `cargo tree -i rsa --target all` empty.
- `govulncheck` under the digest-pinned Go 1.26.6 builder: no reachable vulnerabilities found.
- Full legacy Python conformance oracle: 1,075 tests pass.
- Rust gateway image, Go A2A image, Compose configuration, durable Compose restart, signed-receipt retrieval, cost reporting, and the observed-evidence governance gate: pass.
- Ten Kubernetes YAML resources parse locally; cluster-side admission, controller compatibility, and runtime behavior remain unvalidated release blockers.
- Docker Scout container CVE evidence was not produced because the local scanner requires external Docker account authentication; this remains a release gate.

The authoritative completion definition remains [requirements.md](requirements.md) plus [verification-matrix.md](verification-matrix.md). This file must be updated when observed evidence changes; optimistic prose is not a release gate.
