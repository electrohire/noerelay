# ADR-0001: Rust owns NoeRelay release authority

**Status:** Accepted  
**Date:** 2026-08-21

## Context

The Python reference implementation proves many NoeRelay semantics, and an earlier plan proposed a Go production control plane. The product requirement is instead a Rust-based trusted core with Python bindings and Go only where operationally appropriate. NoeRelay’s differentiator is not forwarding HTTP: it is enforcing identity, contracts, epistemic state, policy, cost, tool authority, verification, and evidence integrity without hidden bypass paths.

## Decision

Rust owns the public gateway and every authority-changing decision. Domain types live in `noerelay-core`; adapters depend inward on explicit traits. The executable service lives in `noerelay-gateway`. Python exposes bindings to the same Rust types and functions for extension and evaluation. Go may implement protocol adapters, initially A2A, but those adapters receive no provider master secret and cannot accept a run, alter policy, or append authority-changing ledger events without a Rust-authorized command.

The existing Python service remains a conformance oracle during migration. It is not the final production authority and will be removed from the production image only after bidirectional fixture parity and migration gates pass.

## Consequences

- Canonical serialization and hash vectors must be language-independent and tested across Rust/Python/Go.
- The Rust core must avoid framework types and expose deterministic pure functions wherever possible.
- Network, persistence, clock, signing, and execution behavior enter through bounded ports.
- Python and Go can fail independently without changing an already-recorded policy or release decision.
- This is a migration, not a claim that the present repository is already GA-ready.

