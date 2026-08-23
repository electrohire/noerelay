# ADR-0002: Justified polyglot boundaries

- **Status:** Accepted
- **Date:** 2026-08-21
- **Supersedes:** Any blanket “Python-only” or “no other languages” statement in historical drafts

## Decision

NoeRelay is deliberately polyglot, but Rust remains the only trusted policy and release authority. A language may be introduced only when it provides a documented ecosystem, safety, portability, or operator-value advantage that outweighs its supply-chain and maintenance cost.

| Boundary | Language or format | Tangible value | Authority limit |
|---|---|---|---|
| Gateway, governance kernel, routing, budget, epistemics, ledger, release | Rust | Memory safety, predictable performance, one auditable authority implementation | Authoritative |
| SDK bindings, evaluation science, benchmark/data workers | Python | Dominant ML/evaluation ecosystem and low-friction notebook/client use | Calls Rust; cannot override an authority decision |
| A2A interoperability adapter | Go | Official A2A SDK, strong network-service tooling, simple deployable adapter | Authenticated translator only |
| Durable relational schema and migrations | PostgreSQL SQL | Native constraints, transactions, row security, indexing, auditable migrations | Enforces invariants defined by Rust; no policy-in-stored-procedure fork |
| Console and browser SDK | TypeScript | Browser type system and web ecosystem | Presentation/client only |
| Operator and release automation | PowerShell and POSIX shell | Native Windows and Unix automation/smoke coverage | Reproducible orchestration only; no policy logic |
| Portable wire/domain contracts | JSON Schema/OpenAPI | Cross-language validation and generated clients | Generated/validated against Rust semantics |

## Admission test for another language

A change adding a language MUST identify its owning directory, concrete advantage, trust level, contract with Rust, dependency/patch process, CI owner, and removal or migration path. It MUST NOT duplicate route selection, authorization, budget, verification, ledger, epistemic transition, or release logic outside Rust. CI MUST test the boundary in both directions.

## Consequences

- Polyglot components remain replaceable adapters around versioned contracts.
- A convenient library alone is not enough to create a second authority implementation.
- Historical Python reference code remains a conformance oracle during migration and is not shipped as the production authority.
