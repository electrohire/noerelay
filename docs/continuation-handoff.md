# NoeRelay Continuation Handoff

**Last updated:** 2026-08-14

> **Superseded:** This handoff predates ADR-0001 and must not be used as the current architecture baseline. Start with [`requirements.md`](requirements.md), [`verification-matrix.md`](verification-matrix.md), and [`adr/0001-rust-release-authority.md`](adr/0001-rust-release-authority.md). Rust—not Go—owns release authority.

**Canonical repository:** [electrohire/noerelay](https://github.com/electrohire/noerelay)

**Current delivery branch:** `docs/product-completion-plan`

**Current pull request:** [PR #1 — Define NoeRelay v1 product completion architecture](https://github.com/electrohire/noerelay/pull/1)

This document is the machine-independent restart point for a new developer or coding agent. Read it before changing code, repository settings, workflows, secrets, or architecture.

## 1. Current state

NoeRelay is currently a `0.1.0-draft` architecture, specification set, and dependency-free Python reference kernel. It is not yet a production inference service.

Completed and present in the repository:

- the EPR-1 normative architecture and epistemic governance rules;
- JSON Schemas, routing policy, verification state machine, OpenAPI contract, examples, and benchmark manifest;
- an executable Python reference kernel for routing, epistemic adjudication, ledger chaining, and governed memory;
- secret-free conformance tests and guarded GitHub Actions;
- protected `Test`-environment smoke tooling for OpenRouter and Hugging Face credentials;
- research and benchmarking foundations;
- a complete v1 product plan, language decision, protocol architecture, workstreams, milestones, launch gates, initial epics, and GA checklist in [`product-completion-plan.md`](product-completion-plan.md).

At this handoff, PR #1 contains the product plan and is expected to require an independent review before merge. Its conformance check passed before this handoff update. Inspect the live PR state rather than assuming it is unchanged.

## 2. Locked architectural direction

Treat these as accepted baseline decisions unless new evidence justifies an ADR and reviewed plan change.

### Language ownership

- **Go 1.25+** owns the production control plane: public API, authentication, contracts, deterministic policy, routing, budget authority, durable execution, verification orchestration, epistemic transitions, ledger writes, and release decisions.
- **Python 3.12+** remains first-class for the customer SDK, executable specification, Hugging Face acquisition/evaluation, research, notebooks, and isolated analysis or training workers. Python workers return typed proposals, measurements, or artifacts; they do not own release authority.
- **TypeScript** owns the browser/Node SDK and operator console.
- **Rust** is reserved for a measured, narrowly scoped need such as a sandbox host, cryptographic canonicalization, or native media processing. Do not create a second general backend in Rust.
- **Versioned Protocol Buffers** are the internal cross-language contract lineage, with generated Go, Python, and TypeScript bindings. Public compatibility remains HTTP/JSON/SSE.

### Model and provider boundary

- OpenRouter is the model-inference gateway.
- Every route uses an explicit, evaluated, non-OpenAI model ID.
- Do not add `OPENAI_API_KEY`, OpenAI-hosted models, the `openai/` model namespace, OpenAI model families, or automatic upstream model selection.
- An OpenAI-compatible wire protocol is a client compatibility feature; it does not authorize OpenAI provider usage.
- Hugging Face is used for benchmark and dataset acquisition. Promotion authority remains NoeRelay's signed evaluation process.

### Agent and tool communication

- **A2A v1.0** is the external agent-to-agent boundary. NoeRelay will expose one inbound A2A server and use one outbound dispatcher for allowlisted specialists.
- **MCP** is the agent-to-tools/resources boundary. It does not replace NoeRelay tool authorization.
- **AG-UI** is the agent-to-user event projection for the console and compatible clients. It is not authoritative storage.
- NoeRelay's durable protobuf task/event model and evidence ledger remain the source of truth.
- The planning/router model may propose a delegation. Deterministic code must authenticate the agent, resolve a pinned Agent Card, validate policy/capability/data constraints, reserve budget, prevent cycles, persist the decision, and only then dispatch.
- Remote messages and artifacts are untrusted assertions until scanned, content-addressed, provenance-bound, and independently verified.
- Agent communication is hub-and-spoke through NoeRelay. Enforce depth, fan-out, deadline, concurrency, attempt, cost, data-class, and endpoint limits. Do not enable uncontrolled peer-to-peer chatter.

## 3. Repository and governance constraints

- The repository is public but proprietary. Do not add customer data, private prompts, credentials, or regulated evidence.
- Work on short-lived branches and use pull requests. Do not push implementation work directly to `main`.
- Keep the required conformance check and independent-review rule intact.
- Do not change collaborator, team, or individual permissions as part of product implementation. `tbitcs` is expected to retain full administrative control. Permission work is outside the next implementation scope.
- Do not weaken `Test` environment protections, expose secrets to pull-request workflows, or enable paid generation in ordinary CI.
- Model output proposes. Deterministic policy authorizes. Verification and evidence determine release.

## 4. Resume on a new machine

Prerequisites are Git, GitHub CLI, Python 3.11+ for the existing conformance suite, and later Go 1.25+ for production implementation.

```powershell
gh auth status
gh repo clone electrohire/noerelay
Set-Location noerelay
gh pr view 1
```

If PR #1 is still open, resume and validate its branch:

```powershell
gh pr checkout 1
git status --short
python -m unittest discover -s tests -v
gh pr checks 1
```

If PR #1 has merged, start from the updated protected branch:

```powershell
git switch main
git pull --ff-only origin main
python -m unittest discover -s tests -v
git switch -c feat/go-foundation
```

Before implementation, read these documents in order:

1. [`../README.md`](../README.md)
2. [`architecture.md`](architecture.md)
3. [`product-completion-plan.md`](product-completion-plan.md)
4. [`research-basis.md`](research-basis.md)
5. [`benchmarking.md`](benchmarking.md)
6. [`environment.md`](environment.md)
7. [`../SECURITY.md`](../SECURITY.md)
8. [`../CONTRIBUTING.md`](../CONTRIBUTING.md)

## 5. Environment names, not secret values

Local Windows User variables:

```text
OPENROUTER_API_KEY
HF_TOKEN
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_HTTP_REFERER=https://github.com/electrohire/noerelay
OPENROUTER_APP_TITLE=NoeRelay
NOERELAY_LIVE_TESTS=0
HF_HOME=C:\Users\<your-user>\.cache\huggingface
```

The GitHub environment name is exactly `Test`. At this handoff, the secret names `OPENROUTER_API_KEY` and `HF_TOKEN` and all four documented non-secret OpenRouter/live-test variables were present. Verify their current names and configuration without printing secret values:

```powershell
gh secret list --env Test
gh variable list --env Test
```

Never commit a populated `.env` file. Never echo, print, or place token values in a prompt, issue, pull request, test fixture, log, or evidence receipt.

## 6. Immediate next implementation sequence

Do not begin broad feature development before PR #1 is reviewed and merged. The next implementation branch should deliver the first vertical foundation from section 16 of the product plan.

### PR A — Go and contract foundation

Recommended branch: `feat/go-foundation`

Deliverables:

1. Add ADRs recording the Go-first control plane, polyglot boundaries, and A2A/MCP/AG-UI protocol split.
2. Create the Go workspace and entry-point layout:
   - `cmd/noerelay-api`
   - `cmd/noerelay-worker`
   - `internal/domain`
   - `internal/protocol`
   - `internal/policy`
   - `internal/storage`
3. Add `proto` as the internal schema lineage and generate pinned Go/Python/TypeScript bindings from a minimal versioned envelope.
4. Preserve `reference/epr` as the Python conformance oracle; do not rewrite or delete it.
5. Add the Python SDK/evaluation and TypeScript workspace boundaries without introducing application framework sprawl.
6. Extend CI with pinned Go setup, formatting, vet, unit tests, vulnerability scanning, protobuf compatibility/linting, and the existing Python conformance suite.
7. Add reproducible local developer commands and dependency locks.

Acceptance conditions:

- a clean clone can generate contracts and run all checks using documented commands;
- generated bindings agree on schema/version fixtures;
- existing 20 Python tests still pass;
- Go formatting, vet, unit tests, and vulnerability checks pass;
- no service contains production behavior, secret dependency, OpenAI provider dependency, or duplicated hand-written domain schemas;
- CI on an untrusted pull request receives no protected environment secrets.

### PR B — Durable walking skeleton

After PR A merges, implement authenticated request normalization → task contract → deterministic mock route → durable run/step/outbox → mock execution → ledger event → compatible response. It must be idempotent, cancellation-aware, restart-recoverable, and replayable before any live model call is added.

### PR C — Live core

Add the quarantined model registry and explicit non-OpenAI OpenRouter adapter, then streaming, budget reservation/reconciliation, endpoint health, and transport fallback. Keep semantic fallback distinct from provider retry.

### PR D — Governed agent slice

Add the immutable agent registry and A2A v1 server/client walking slice using the official Go SDK and compatibility kit. One allowlisted specialist delegation must map to a durable step, obey depth/budget/cancellation, produce a content-addressed artifact, and pass verification before release.

The remaining sequence is authoritative in [`product-completion-plan.md`](product-completion-plan.md). Do not pull tools, media, learned routing, or unrestricted multi-agent behavior ahead of the durable policy/evidence foundation.

## 7. Verification commands

Current repository validation:

```powershell
python -m unittest discover -s tests -v
git diff --check
git status --short
```

Protected remote smoke is manual-only and should normally not be run during a handoff. If explicitly authorized later, follow [`environment.md`](environment.md) and the guarded workflow; do not bypass its exact branch, confirmation, approval, or spending controls.

## 8. Copy-paste prompt for the next coding agent

```text
Continue NoeRelay from https://github.com/electrohire/noerelay.

First read README.md, docs/continuation-handoff.md, docs/architecture.md,
docs/product-completion-plan.md, docs/research-basis.md, SECURITY.md, and
CONTRIBUTING.md. Inspect PR #1 and its checks. If it is still open, do not begin
implementation on top of an unapproved architecture unless explicitly asked;
review or finish that PR first. If merged, start feat/go-foundation and implement
PR A from the handoff.

Preserve these decisions: Go owns the production control plane; Python is the
SDK/reference/evaluation boundary; TypeScript is the console/client boundary;
Rust requires measured justification. Use explicit non-OpenAI models through
OpenRouter only. Use A2A v1 for governed agent delegation, MCP for tools/data,
and AG-UI for user events. The deterministic policy and evidence ledger remain
authoritative. Do not change repository permissions, weaken protected workflows,
or expose Test environment secrets. Run all existing conformance tests and use a
short-lived branch and pull request for changes.
```

## 9. First decision on resumption

The first action is not choosing another framework or model. It is determining whether PR #1 has merged. If not, complete its independent review. If yes, execute PR A exactly far enough to establish one generated contract lineage and a clean Go production foundation without prematurely building the live router.
