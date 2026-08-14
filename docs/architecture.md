# EPR-1 Normative Architecture

## 1. Purpose

EPR-1 defines a virtual model endpoint whose generative component proposes task contracts, actions, and user-facing responses while deterministic components govern routing, evidence, verification, context assembly, and release.

The key words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative.

## 2. Planes

| Plane | Responsibility | May use an LLM? | Final authority? |
|---|---|---:|---:|
| Protocol | OpenAI-compatible request and response handling | No | Yes, for protocol validity |
| Contract | Convert intent into typed deliverables and acceptance criteria | Yes | No |
| Policy | Permissions, privacy, risk, budgets, capability filters | No | Yes |
| Decision | Rank admissible next actions | Yes, as a feature source | Deterministic selector |
| Execution | Models, tools, retrieval, image operations, humans | Yes | No |
| Verification | Tests, proof, policy checks, independent review | Sometimes | Yes |
| Epistemic | Claims, evidence, contradiction, provenance | No for state transitions | Yes |
| Memory | Event log, artifact graph, active state, narrative cache | Only for narrative cache | Yes |
| Learning | Offline routing and compaction improvements | Yes | No production self-modification |

## 3. Normative requirements

### API and compatibility

- **EPR-API-001:** The service MUST expose `/v1/models`, `/v1/chat/completions`, and `/v1/responses` or provide a documented compatibility adapter.
- **EPR-API-002:** Standard OpenAI request fields MUST pass through without semantic reinterpretation unless the compatibility profile documents the difference.
- **EPR-API-003:** Governance metadata MUST be optional. A deterministic default policy MUST apply when it is absent.
- **EPR-API-004:** Streaming MUST preserve route identity and final evidence-receipt discoverability.
- **EPR-API-005:** Image understanding and image generation MUST be represented as separate capabilities. A vision-language model MUST NOT be assumed to generate images.

### Contract compilation

- **EPR-CON-001:** The contract compiler MAY propose a task contract, but schema validation and policy defaults MUST be deterministic.
- **EPR-CON-002:** Acceptance criteria MUST be classified as `executable`, `observable`, `judgmental`, or `missing`.
- **EPR-CON-003:** High-risk work with missing acceptance criteria MUST NOT execute autonomously.
- **EPR-CON-004:** Requirements, facts, decisions, assumptions, observations, predictions, preferences, and artifacts MUST use distinct state vocabularies.

### Routing

- **EPR-ROUTE-001:** Routing MUST occur at step level and MAY choose a model, tool, retrieval action, clarification, verifier, image service, formal solver, human review, or abstention.
- **EPR-ROUTE-002:** Policy, capability, availability, data handling, acceptance lower bound, independent-review, latency, and budget checks MUST be hard constraints.
- **EPR-ROUTE-003:** Expected cost MUST be optimized only among admissible routes.
- **EPR-ROUTE-004:** The router MUST preserve rejected-candidate reasons for audit.
- **EPR-ROUTE-005:** Provider-availability fallback and semantic-quality fallback MUST be recorded separately.
- **EPR-ROUTE-006:** Online learning MUST be restricted to canary traffic and MUST NOT modify the active production policy without versioned evaluation and promotion.

### Epistemic state

- **EPR-EPI-001:** Facts MUST use four-valued adjudication: `unknown`, `supported`, `refuted`, or `conflicted`.
- **EPR-EPI-002:** Model assertions MUST NOT be treated as observations.
- **EPR-EPI-003:** Multiple model assertions MUST NOT be promoted solely because they agree.
- **EPR-EPI-004:** A derived claim MUST reference its premises and MUST NOT exceed the weakest required premise without new evidence.
- **EPR-EPI-005:** Conflicted claims MUST block dependent high-risk acceptance gates.
- **EPR-EPI-006:** Confidence used for routing MUST be calibrated on environment-labeled outcomes; model self-confidence alone is insufficient.

### Provenance and ledger

- **EPR-LED-001:** Every state transition MUST append an immutable, hash-linked ledger event.
- **EPR-LED-002:** Evidence MUST identify producer, activity, timestamp, content hash, and retrieval or execution location.
- **EPR-LED-003:** Test and proof evidence MUST bind to the exact artifact and environment hashes.
- **EPR-LED-004:** Accepted outcomes MUST produce an evidence receipt containing verification results, cost, route, unresolved claims, and ledger head hash.
- **EPR-LED-005:** A production implementation SHOULD map provenance to W3C PROV and software attestations to in-toto.

### Verification

- **EPR-VER-001:** Verification MUST be a DAG of checks, not a single model opinion.
- **EPR-VER-002:** Deterministic checks MUST run before judgmental model review where applicable.
- **EPR-VER-003:** A worker MUST NOT be its own sole verifier for high-risk work.
- **EPR-VER-004:** Agent-generated tests MUST be identified; high-risk acceptance MUST include independent, hidden, mutation, interoperability, proof, or human evidence as applicable.
- **EPR-VER-005:** Formal proof MUST be scoped to the encoded specification and MUST NOT imply that the specification itself is correct.
- **EPR-VER-006:** Tool-call generation and schema-constrained reporting SHOULD occur in separate decoding phases unless the exact model-serving combination has passed conformance testing.

### Context and compaction

- **EPR-CTX-001:** The system MUST distinguish immutable events, canonical artifact state, active decision state, and disposable narrative summaries.
- **EPR-CTX-002:** The LLM MAY propose narrative compaction but MUST NOT delete authoritative state.
- **EPR-CTX-003:** Active requirements, approved decisions, unresolved claims, failed mandatory checks, evidence handles, and artifact hashes MUST survive compaction.
- **EPR-CTX-004:** A summary MUST NOT become the sole evidence for a claim.
- **EPR-CTX-005:** Archived evidence MUST remain dereferenceable by stable identifier.
- **EPR-CTX-006:** Context packages SHOULD be compiled by graph reachability from the current task rather than by transcript recency alone.

## 4. Typed epistemic semantics

| Kind | States |
|---|---|
| `fact` | `unknown`, `supported`, `refuted`, `conflicted` |
| `requirement` | `draft`, `active`, `ambiguous`, `superseded`, `rejected` |
| `decision` | `proposed`, `approved`, `revoked` |
| `assumption` | `open`, `validated`, `invalidated` |
| `observation` | `valid`, `stale`, `invalid`, `irreproducible` |
| `prediction` | `uncalibrated`, `calibrated`, `expired` |
| `preference` | `active`, `superseded` |
| `artifact` | `proposed`, `built`, `tested`, `released`, `rejected` |

For facts, store support and refutation separately. Given a policy threshold `t`:

```text
support >= t and refutation < t  => supported
support < t  and refutation >= t => refuted
support >= t and refutation >= t => conflicted
otherwise                         => unknown
```

## 5. Lexicographic routing algorithm

For each next-action candidate:

1. Reject unavailable candidates.
2. Reject capability mismatches.
3. Reject privacy, retention, region, permission, or provider-policy mismatches.
4. Reject candidates below the risk-class acceptance lower bound.
5. Construct required independent verification pairs.
6. Reject plans exceeding per-step budget or latency ceilings.
7. Sort surviving plans by expected total cost, then latency, then higher acceptance lower bound.
8. Select the first plan and retain the remainder as semantic fallbacks.
9. If no plan survives, return `escalation_required`; never fail open.

Expected total cost includes model calls, tools, verification, expected retry, expected fallback, infrastructure, and expected human review.

## 6. Verification hierarchy

1. Protocol and schema validation.
2. Permission, policy, and data-governance checks.
3. Compiler, type checker, linter, static analyzer, deterministic unit tests.
4. Integration, interoperability, property, mutation, hidden, and adversarial tests.
5. Formal proof or model checking where justified.
6. Independent-family model review for residual semantic criteria.
7. Human authorization for normative, destructive, or safety-critical decisions.

Passing a lower layer does not waive a required higher layer.

## 7. Memory model

- **L0 Event log:** immutable inputs, outputs, actions, costs, and hashes.
- **L1 Artifact graph:** canonical requirements, architecture, decisions, code, tests, evidence, and trace links.
- **L2 Active state:** current task, constraints, open risks, contradictions, failed checks, and acceptance gates.
- **L3 Narrative cache:** disposable prose optimized for model consumption.

Only L3 may be lossy. Compaction is itself a ledgered activity.

## 8. Failure classes

| Class | Meaning | Required behavior |
|---|---|---|
| Transport | Timeout, rate limit, provider outage | Provider fallback |
| Capability | Missing modality, context, tool, schema support | Capability fallback |
| Quality | Tests or acceptance checks fail | Repair or semantic fallback |
| Epistemic | Evidence missing, stale, or conflicting | Gather information or escalate |
| Policy | Permission, privacy, safety, or budget violation | Reject or request authorization |
| Specification | Goal or acceptance criteria are ambiguous | Targeted clarification |

## 9. Promotion rule

A model, tool, policy, prompt, or compactor version MUST remain a canary until it passes the benchmark manifest for every task and risk cohort in which it will be used. Aggregate success MUST NOT hide a failing high-risk cohort.
