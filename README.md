# NoeRelay

**Evidence-governed AI orchestration that routes by capability, verifies by policy, and optimizes for the lowest acceptable total cost.**

NoeRelay is a virtual model with an OpenAI-compatible client wire protocol, backed by the **Epistemic Portfolio Runtime (EPR-1)**. Model inference is routed through OpenRouter to explicit non-OpenAI model IDs. Clients interact with one stable model endpoint while NoeRelay can delegate individual steps to different language models, deterministic tools, retrievers, image processors, image generators, formal solvers, or human reviewers.

The generative layer proposes. Deterministic policy, evidence, provenance, and verification layers decide what may execute and what may be released.

> **Provider boundary:** Protocol compatibility is not provider usage. NoeRelay does not use OpenAI-hosted models, rejects the `openai` family and `openai/` model namespace, and disables automatic model selection that could bypass the evaluated portfolio.

> **Project status:** `0.1.0-draft` executable specification and dependency-free Python reference kernel. This repository defines the contracts a production gateway must preserve; it is not yet a production inference service.

## What “NoeRelay” means

**Noe** is derived from *noetic*: relating to knowledge, reasoning, and the conditions under which something can be known. **Relay** describes how the system delegates work to the most appropriate model or technology, carries evidence between stages, and invokes explicit fallbacks when a route cannot meet its acceptance contract.

Together, **NoeRelay** means an epistemically governed relay for AI work.

- **NoeRelay** is the application and stable virtual-model identity.
- **EPR-1** is the normative runtime architecture and interoperability contract.
- Recommended API model ID: `noerelay/epr-1`.

## Why this exists

A single “best” model is rarely the best system. Models differ by cost, latency, modality, context capacity, tool reliability, data-handling policy, and performance on a specific task cohort. An inexpensive specialist may be sufficient for one step while another requires a stronger model, a deterministic program, an independent verifier, or a human decision.

NoeRelay turns that portfolio into one governed model surface:

1. Compile user intent into typed deliverables and acceptance criteria.
2. Apply hard permission, privacy, capability, risk, budget, and latency constraints.
3. Select the lowest-expected-cost admissible route.
4. Execute through the appropriate model, tool, or multimodal service.
5. Verify through a risk-scaled DAG of deterministic and independent checks.
6. Adjudicate claims against evidence and record every transition in a hash-linked ledger.
7. Release a result only when its acceptance contract is satisfied; otherwise repair, fall back, clarify, abstain, or escalate.

## Architecture

```mermaid
flowchart LR
    C["OpenAI-wire-compatible client"] --> A["NoeRelay protocol adapter"]
    A --> T["Task-contract compiler"]
    T --> P["Deterministic policy gates"]
    P --> R["Portfolio router"]

    R --> M["Explicit non-OpenAI models via OpenRouter"]
    R --> D["Deterministic tools and solvers"]
    R --> X["Retrieval and data systems"]
    R --> I["Image processing and generation"]
    R --> H["Human authorization"]

    M --> V["Verification DAG"]
    D --> V
    X --> V
    I --> V
    H --> V

    V --> E["Epistemic state and evidence ledger"]
    E --> K["Context compiler"]
    K --> O["Compatible response and evidence receipt"]
    V -. "repair or semantic fallback" .-> R
```

The complete normative design is in [docs/architecture.md](docs/architecture.md). Requirements use the `EPR-*` identifiers and the terms **MUST**, **MUST NOT**, **SHOULD**, and **MAY** normatively.

## Core properties

- **Step-level portfolio routing:** choose a model, tool, retriever, image service, verifier, formal solver, human review, clarification, or abstention for each step.
- **Constraint-first optimization:** cost is optimized only after policy, capability, availability, privacy, acceptance, independence, latency, and budget constraints pass.
- **Risk-scaled verification:** deterministic checks run before residual model judgment; high-risk workers cannot be their own sole verifier.
- **Typed epistemic state:** facts, requirements, decisions, assumptions, observations, predictions, preferences, and artifacts have distinct state vocabularies.
- **Four-valued facts:** a fact is `unknown`, `supported`, `refuted`, or `conflicted`; support and refutation are retained separately.
- **Replayable provenance:** accepted outcomes bind route, checks, cost, unresolved claims, artifact hashes, and the ledger head into an evidence receipt.
- **Epistemically safe compaction:** lossy summaries may be regenerated, but authoritative requirements, decisions, failures, evidence handles, and artifact hashes cannot be compacted away.
- **Multimodal separation:** image understanding, image processing, and image generation are modeled as separate capabilities rather than assumed to belong to one model.
- **Fail-closed behavior:** if no admissible route exists, NoeRelay requests clarification or escalation instead of silently lowering the acceptance bar.

## Guarantee boundary

NoeRelay does **not** claim that a language model—or a collection of agreeing models—can guarantee semantic truth. It specifies enforceable process guarantees:

1. Unsupported claims cannot be promoted to verified state.
2. Policy, privacy, permission, capability, and risk constraints are hard filters.
3. Cost is optimized only among routes that meet the acceptance lower bound.
4. High-risk work requires evidence independent of the worker where policy demands it.
5. Context compaction cannot delete authoritative state or its evidence handles.
6. Every accepted result has a replayable evidence receipt and hash-linked ledger position.

## API compatibility

The draft [OpenAPI 3.1 specification](spec/openapi.json) defines the intended public surface:

| Endpoint | Purpose |
|---|---|
| `GET /v1/models` | Lists the stable `noerelay/epr-1` virtual model. |
| `POST /v1/chat/completions` | OpenAI-compatible chat completions with optional governance metadata. |
| `POST /v1/responses` | OpenAI Responses-compatible execution with optional governance metadata. |
| `GET /v1/epr/runs/{run_id}` | Retrieves the evidence receipt for a completed or escalated run. |

Standard request fields pass through. An optional `governance` object can specify project identity, risk class, cost and latency ceilings, required acceptance probability, data policy, retention class, and evidence-receipt behavior.

OpenAI compatibility in this section describes request and response shapes only. The backend model gateway is OpenRouter, and deterministic policy blocks OpenAI model families, namespaces, and upstream endpoints. No `OPENAI_API_KEY` is used.

Example request against a future conforming gateway:

```bash
curl "$NOERELAY_BASE_URL/v1/responses" \
  -H "Authorization: Bearer $NOERELAY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "noerelay/epr-1",
    "input": "Implement and verify the requested change.",
    "governance": {
      "project_id": "example-project",
      "risk_class": "high",
      "max_cost_usd": 2.00,
      "max_latency_ms": 120000,
      "required_acceptance_probability": 0.95,
      "data_policy": "no_training",
      "retention_class": "project",
      "return_evidence_receipt": true
    }
  }'
```

## Repository contents

```text
docs/
  architecture.md                 Normative architecture and invariants
  benchmarking.md                 Hugging Face acquisition and evaluation policy
  environment.md                  Local and GitHub test credentials
  research-basis.md               Research and standards basis
examples/
  candidate-actions.json          Candidate portfolio actions
  context-capsule.json            Compaction-safe active context
  high-risk-coding-contract.json  Typed high-risk task contract
reference/
  demo.py                         Deterministic routing demonstration
  epr/                            Dependency-free Python reference kernel
spec/
  openapi.json                    OpenAI-compatible API contract
  routing-policy.json             Deterministic lexicographic route policy
  verification-state-machine.json Fail-closed execution lifecycle
  benchmark-manifest*.json        Evaluation and promotion contracts
  schemas/                        JSON Schema 2020-12 domain contracts
tests/
  test_spec.py                    Executable conformance tests
.github/workflows/
  conformance.yml                 Secret-free pull request and main-branch CI
  test-environment-smoke.yml      Main-guarded, manual credential smoke check
```

## Quick start

### Requirements

- Python 3.11 or newer
- No third-party packages for the reference kernel
- Optional: `jsonschema` for standards-level schema validation tests

### Run the conformance tests

```powershell
python -m unittest discover -s tests -v
```

Install the optional schema validator to enable every test:

```powershell
python -m pip install jsonschema
python -m unittest discover -s tests -v
```

### Run the route-selection demo

```powershell
python reference/demo.py
```

The example demonstrates deterministic selection of the least-cost admissible worker plus an independent provider-family verifier. Cheaper candidates below the acceptance floor are retained in the audit as rejected rather than selected.

### Configure live inference and benchmarks

Offline conformance tests require no API keys. Live model tests use `OPENROUTER_API_KEY`; Hugging Face benchmark acquisition uses `HF_TOKEN` when authentication is needed. Do not commit either value and do not configure `OPENAI_API_KEY`. See [docs/environment.md](docs/environment.md) and [docs/benchmarking.md](docs/benchmarking.md).

## What the tests cover

- Parsing every JSON artifact in the repository.
- JSON Schema validation of task contracts, candidate actions, context capsules, and benchmark manifests.
- Four-valued epistemic adjudication.
- Lowest-cost admissible routing with independent verification.
- Rejection of cheaper candidates below the acceptance lower bound.
- Fail-closed escalation when no independent verifier is available.
- Clarification when high-risk acceptance criteria are missing.
- Detection of hash-ledger tampering.
- Preservation of authoritative state during context compaction.
- Rejection of OpenAI model families and `openai/` model IDs even if a task attempts to allow them.

## Routing and fallback semantics

NoeRelay distinguishes two fallback classes:

- **Provider fallback:** transport failure, timeout, rate limit, or provider outage.
- **Semantic fallback:** an executed route fails quality, evidence, or acceptance checks.

Candidate plans are ordered lexicographically only after hard filtering. Expected total cost includes model and tool calls, verification, retries, fallback probability, infrastructure, and expected human review—not merely input/output token price.

## Epistemic memory

The memory design has four levels:

- **L0 — Event log:** immutable actions, inputs, outputs, costs, and hashes.
- **L1 — Artifact graph:** canonical requirements, architecture, decisions, code, tests, evidence, and trace links.
- **L2 — Active state:** current constraints, risks, contradictions, failed checks, and acceptance gates.
- **L3 — Narrative cache:** disposable prose optimized for model consumption.

Only L3 may be lossy. Context is compiled by graph reachability from the active task rather than transcript recency alone, and compaction is itself a ledgered activity.

## Research basis

[docs/research-basis.md](docs/research-basis.md) records the current primary research, standards, and preprints supporting portfolio routing, calibration, verification, provenance, agent evaluation, and context management. Research results are evidence for design choices, not production guarantees; relevant claims must be reproduced against NoeRelay's own task and risk cohorts.

## Security and responsible disclosure

This is a private ElectroHire repository. Do not place production credentials, customer data, proprietary model prompts, or regulated evidence in examples, commits, issues, or test fixtures. Report suspected vulnerabilities privately to the repository maintainers; see [SECURITY.md](SECURITY.md).

## Development and contribution

Changes to normative behavior require corresponding schemas, examples, tests, and research or standards rationale. See [CONTRIBUTING.md](CONTRIBUTING.md) for the expected workflow.

## Roadmap

- Build the production OpenAI-wire-compatible gateway backed exclusively by explicit non-OpenAI models through OpenRouter.
- Add provider and capability registries for text, vision, image generation, tools, retrieval, and local execution.
- Implement durable W3C PROV-compatible storage and in-toto-aligned artifact attestations.
- Add an evaluation harness for cost, latency, acceptance probability, calibration, and cohort regressions.
- Implement governed context compilation over project requirements, architecture, decisions, tests, and evidence.
- Add canary policy/model promotion, observability, budgets, and human authorization workflows.
- Fine-tune or distill a minimal front-facing contract compiler while retaining deterministic release authority.

## Ownership and license

Copyright © 2026 ElectroHire. All rights reserved.

This repository is private and proprietary. No open-source license or permission to copy, redistribute, sublicense, or use the software outside authorized ElectroHire work is granted. See [LICENSE](LICENSE).
