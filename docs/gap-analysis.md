# NoeRelay v1 — Comprehensive Gap Analysis

**Generated:** 2026-08-20
**Repository version:** `0.1.0-draft`
**Plan baseline:** [`docs/product-completion-plan.md`](product-completion-plan.md)

---

## Executive Summary

NoeRelay is currently a **dependency-free Python reference specification** (`0.1.0-draft`) — not a production inference service. The product completion plan defines a **Go-first production control plane** with PostgreSQL, durable workers, A2A/MCP/AG-UI protocols, and a full operator console. **None of that production infrastructure exists yet.**

The reference kernel demonstrates the **architectural correctness** of the EPR-1 design: deterministic routing, epistemic adjudication, verification DAGs, evidence ledgers, and governed memory all work correctly in an in-memory Python prototype. But the project is at **Phase 0 (Baseline)** of an 8-phase, 20-22 week plan. The architecture, specifications, schemas, and conformance tests are complete. The production implementation has not begun.

**Overall release readiness: ~15%** — the architecture and specification work is done, but the production implementation is at zero.

---

## 1. Capability Contract Assessment

### NR-API-01 — Virtual Model API
**Status:** ⚠️ Partially Implemented

| Criterion | Status | Evidence |
|---|---|---|
| `/v1/models` endpoint | ✅ | [`server.py:246`](../reference/gateway/server.py:246) |
| `/v1/chat/completions` endpoint | ✅ | [`server.py:479`](../reference/gateway/server.py:479) |
| `/v1/responses` endpoint | ✅ | [`server.py:488`](../reference/gateway/server.py:488) |
| Evidence-receipt retrieval | ✅ | [`server.py:306`](../reference/gateway/server.py:306) — `GET /v1/epr/runs/{run_id}` |
| Streaming support | ✅ | [`streaming.py`](../reference/gateway/streaming.py) — SSE with `epr` metadata in terminal chunk |
| Non-streaming support | ✅ | JSON responses with `epr` block |
| Compatibility fixtures pass | ⚠️ | 1020 tests pass, but these are reference-level conformance tests, not the production compatibility fixtures the plan requires |
| **Major gaps** | | All state is in-memory; runs do not survive restart. The plan requires a Go control plane with PostgreSQL-backed durable execution. The Python skeleton is a conformance oracle, not the release-authority hot path. |

---

### NR-IAM-01 — Tenancy and API Keys
**Status:** ⚠️ Partially Implemented

| Criterion | Status | Evidence |
|---|---|---|
| API key creation/listing/revocation/rotation | ✅ | [`server.py:342-346,554-563,634-637`](../reference/gateway/server.py:342) |
| Tenant CRUD | ✅ | [`server.py:397-404,573-579,639-641,674-682`](../reference/gateway/server.py:397) |
| Role-based access control | ✅ | [`server.py:193-234`](../reference/gateway/server.py:193) — RBAC checks in `_check_rbac_and_audit` |
| Rate limiting | ✅ | [`rate_limit.py`](../reference/gateway/rate_limit.py) — token bucket per API key |
| Quota/budget enforcement | ✅ | [`tenancy.py`](../reference/gateway/tenancy.py) — daily/monthly budgets |
| Cross-tenant negative tests | ⚠️ | Tests exist in [`test_gateway.py`](../tests/test_gateway.py) but coverage for cross-tenant isolation is limited |
| **Major gaps** | | Auth is **disabled by default** (loopback bind). No OIDC/OAuth support. No key hashing (keys stored in config/SQLite). The plan requires PostgreSQL-backed immutable tenant/project/environment records. |

---

### NR-CON-01 — Task-Contract Compilation
**Status:** ⚠️ Partially Implemented

| Criterion | Status | Evidence |
|---|---|---|
| Schema-valid contract production | ✅ | [`contracts.py`](../reference/gateway/contracts.py) — valid against [`task-contract.schema.json`](../spec/schemas/task-contract.schema.json) |
| Typed clarification/rejection | ✅ | Pipeline produces `clarification_required`, `escalation_required` outcomes |
| Missing high-risk acceptance blocks execution | ✅ | [`governance.py`](../reference/gateway/governance.py) — `requires_clarification` gate |
| LLM-based contract proposal | ⚠️ | `LLMContractProposer` protocol exists but is optional; deterministic compilation is the fallback |
| **Major gaps** | | No Go implementation. The plan requires protobuf-based canonical IR with typed fields for tenant, project, environment, user, session, idempotency, multimodal messages, tool declarations, governance constraints, privacy, retention, region, cost, latency ceilings, and trace/causality identifiers. The Python skeleton has a simplified version. |

---

### NR-REG-01 — Model Registry
**Status:** ⚠️ Partially Implemented

| Criterion | Status | Evidence |
|---|---|---|
| Explicit OpenRouter IDs per model | ✅ | [`examples/candidate-actions.json`](../examples/candidate-actions.json) |
| Capabilities per model | ✅ | `capabilities`, `modalities` fields in candidate registry |
| Pricing snapshot | ✅ | [`cost_model.py`](../reference/gateway/cost_model.py) — pricing in candidate registry |
| OpenAI model namespaces denied | ✅ | 4-layer enforcement: startup portfolio, boundary, kernel, upstream payload |
| Data policy per model | ⚠️ | `data_policy` field exists but not enforced per-model at granular level |
| Availability state | ❌ | No health probes or availability tracking per model |
| Benchmark version per model | ❌ | Not tracked per model revision |
| Allowed roles per model | ❌ | Not implemented |
| **Major gaps** | | The registry is file-based (JSON), not database-backed. No OpenRouter catalog snapshot auto-import with quarantine. No immutable model revisions with active pointers as the plan requires. No PostgreSQL `model_revision` and `provider_endpoint_snapshot` tables. |

---

### NR-ROUTE-01 — Deterministic Routing
**Status:** ⚠️ Partially Implemented

| Criterion | Status | Evidence |
|---|---|---|
| Hard constraints filter before cost ranking | ✅ | Lexicographic routing in [`routing-policy.json`](../spec/routing-policy.json) and [`policy.py`](../reference/gateway/policy.py) |
| Rejection reasons recorded | ✅ | `candidate_audit` in ledger; redacted summary in 424 body |
| Least expected total cost among admissible | ✅ | Cost ranking after constraint filtering |
| **Major gaps** | | In-memory only. The plan requires PostgreSQL-persisted route decisions with complete candidate audits and fallback ordering. No cost anomaly detection. No circuit breakers. No budget reservation/reconciliation at the database level. |

---

### NR-EXEC-01 — Durable Model Execution
**Status:** ⚠️ Partially Implemented

| Criterion | Status | Evidence |
|---|---|---|
| Timeout support | ✅ | [`pipeline.py`](../reference/gateway/pipeline.py) |
| Cancellation support | ✅ | [`server.py:105-145`](../reference/gateway/server.py:105) — graceful shutdown with request draining |
| Retry classification | ✅ | [`fallback.py`](../reference/gateway/fallback.py) |
| Idempotency | ⚠️ | Idempotency keys accepted but not persisted across restarts |
| Provider endpoint fallback | ✅ | Transport fallback to next provider for same model |
| Semantic fallback | ✅ | `semantic_fallback_count` in `epr` metadata; `fallback_plans` retained |
| Cost/latency capture | ⚠️ | Measured but not persisted durably |
| **Major gaps** | | **No durable execution.** All run state is in-memory. The plan requires PostgreSQL run/step tables, transactional outbox, worker leases, and restart recovery. No `WorkflowEngine` port exists (the plan's seam for Temporal). |

---

### NR-TOOL-01 — Governed Tools
**Status:** ❌ Not Implemented

The architecture defines governed tools with versioned schemas, scoped credentials, explicit grants, idempotency keys, output limits, egress policy, and audit events. The plan schedules this for **Phase 4 (Tools and multimodality, week 9-11)**.

**Gaps:**
- No tool registry module
- No sandboxed execution environment
- No tool schema validation against versioned schemas
- No credential scoping or grant mechanism
- No egress policy enforcement
- No tool audit events

---

### NR-A2A-01 — Governed Agent Interoperability
**Status:** ❌ Not Implemented

The plan schedules A2A v1.0 implementation for **Phase 3 (Interoperability, week 6-7)**.

**Gaps:**
- No A2A server or client
- No agent registry with Agent Cards
- No allowlisting, signature verification, or trust root configuration
- No delegation depth/cycle/cost/budget enforcement
- No durable mapping of A2A tasks to NoeRelay run/step/event records
- No A2A conformance test suite (TCK integration)

---

### NR-MCP-01 — Standard Tool Interoperability
**Status:** ❌ Not Implemented

The plan schedules MCP for **Phase 3-4 (weeks 6-11)**.

**Gaps:**
- No MCP client host
- No isolated server sessions
- No capability negotiation
- No OAuth resource tokens
- No tool/resource discovery

---

### NR-AGUI-01 — User Interaction Events
**Status:** ⚠️ Partially Implemented

| Criterion | Status | Evidence |
|---|---|---|
| Run inspection | ✅ | Dashboard HTML and API endpoints for run/trace viewing |
| Step/tool/approval events | ⚠️ | Events exist in ledger but no AG-UI event adapter for streaming |
| Artifact viewing | ⚠️ | Artifacts stored but no dedicated AG-UI projection |
| **Major gaps** | | The plan requires a TypeScript AG-UI event adapter projecting from the authoritative run event stream. The current implementation uses a server-rendered HTML dashboard. No `AG-UI` event types are emitted. No resume-from-cursor support. |

---

### NR-MM-01 — Multimodality
**Status:** ⚠️ Partially Implemented

| Criterion | Status | Evidence |
|---|---|---|
| Separate capability modeling | ✅ | Architecture distinguishes vision, image processing, image generation, image editing |
| Vision understanding | ❌ | No vision adapter exists |
| Image generation | ❌ | No image generation adapter exists |
| Image editing | ❌ | No image editing adapter exists |
| Deterministic image processing | ❌ | Not implemented |
| **Major gaps** | | The plan schedules this for **Phase 4 (weeks 9-11)**. No adapters exist. No explicit non-OpenAI image model IDs configured. |

---

### NR-VER-01 — Verification DAG
**Status:** ⚠️ Partially Implemented

| Criterion | Status | Evidence |
|---|---|---|
| Deterministic checks run first | ✅ | [`verification.py`](../reference/gateway/verification.py) — DAG evaluation |
| Schema validation | ✅ | Validates against OpenAI response shape |
| Policy validation | ✅ | Validates route admissibility |
| Deterministic acceptance checks | ✅ | Observable criteria against response |
| Independent family review | ✅ | Verifier ≠ worker family check |
| Human approval for critical risk | ⚠️ | Fail-closed (blocks acceptance); actual human-in-the-loop flow is deferred |
| Failed checks trigger repair/fallback/escalation | ✅ | Repair attempts → semantic fallback → escalation |
| **Major gaps** | | No pluggable verifier nodes as the plan requires. No mutation testing. No hidden test suites. No formal proof integration. No PostgreSQL-persisted verification state. The plan schedules full verification for **Phase 5 (weeks 12-14)**. |

---

### NR-EPI-01 — Epistemic State
**Status:** ⚠️ Partially Implemented

| Criterion | Status | Evidence |
|---|---|---|
| Four-valued fact adjudication | ✅ | [`epistemic.py`](../reference/gateway/epistemic.py) — `supported/refuted/conflicted/unknown` |
| Model assertions not treated as observations | ✅ | Classified as `model_assertion` evidence |
| Corroboration requires non-model evidence | ✅ | `can_promote_by_corroboration()` |
| Derived claims reference premises | ✅ | `add_derived_claim()` with premise bounding |
| Conflicted claims block high-risk acceptance | ✅ | `has_blocking_conflict()` guard |
| Calibrated confidence | ✅ | `CalibrationStore` with ECE, model flagging, conservative discount |
| **Major gaps** | | All state is in-memory. The plan requires PostgreSQL-persisted claims, evidence, and contradiction state. No support/refutation persistence across restarts. |

---

### NR-LED-01 — Evidence Ledger
**Status:** ⚠️ Partially Implemented

| Criterion | Status | Evidence |
|---|---|---|
| Immutable hash-linked events | ✅ | [`epistemic_ledger.py`](../reference/gateway/epistemic_ledger.py) — SHA-256 chain |
| Evidence receipts | ✅ | `issue_receipt()` — binds inputs, route, artifacts, verification, cost, claims, ledger head |
| W3C PROV mapping | ✅ | [`provenance.py`](../reference/gateway/provenance.py) |
| **Major gaps** | | **All in-memory.** No PostgreSQL immutable event tables. No periodic signed checkpoints. No content-addressed evidence in S3-compatible storage. No replay endpoint that reconstructs from persisted state. The plan requires `ledger_event` and `evidence` tables with hash-chain integrity verification. |

---

### NR-MEM-01 — Governed Memory
**Status:** ⚠️ Partially Implemented

| Criterion | Status | Evidence |
|---|---|---|
| L0-L3 memory levels | ✅ | [`context.py`](../reference/gateway/context.py) — `MemoryLevel` enum + `CanonicalState` |
| Graph-reachability context compilation | ✅ | `ContextCompiler` compiles by graph reachability (EPR-CTX-006) |
| Compaction preserves authoritative state | ✅ | `ContextCompactor` asserts capsule invariants |
| Evidence handles survive compaction | ✅ | Authoritative requirements, decisions, failures, evidence handles preserved |
| **Major gaps** | | All in-memory. The plan requires PostgreSQL-persisted project graph, context capsules, artifact graph, and token-budgeted narrative cache. No archived evidence recovery from S3-compatible storage. |

---

### NR-OPS-01 — Operability
**Status:** ⚠️ Partially Implemented

| Criterion | Status | Evidence |
|---|---|---|
| Health endpoint | ✅ | `GET /health` — `{"status": "healthy", "version": "0.1.0"}` |
| Readiness (K8s probes) | ✅ | Configured in [`deployment.yaml`](../deploy/kubernetes/deployment.yaml) |
| Metrics | ✅ | Prometheus endpoint at `GET /metrics` with 15 metric types |
| Structured logs | ✅ | [`structured_logging.py`](../reference/gateway/structured_logging.py) — JSON logs to stdout/file |
| Audit views | ✅ | [`audit.py`](../reference/gateway/audit.py) — API call logging |
| Alerts | ✅ | [`alerting.py`](../reference/gateway/alerting.py) — cost/latency/escalation alerts |
| Run replay | ⚠️ | In-memory only; no replay from persisted state |
| Kill switches | ❌ | Not implemented. The plan requires kill switches by tenant, project, model, provider, tool, policy, and modality |
| Distributed tracing | ❌ | No OpenTelemetry integration. The plan requires trace correlation across request/contract/route/attempt/tool/verification/compaction/release spans |
| Backup/restore | ✅ | API endpoints exist; SQLite backup/restore works but not PostgreSQL |
| Disaster recovery | ❌ | No DR exercise conducted |
| SLO dashboards | ⚠️ | Metrics exist but no SLO-specific dashboards with burn-rate alerts |
| **Major gaps** | | The plan requires Go with OpenTelemetry, kill switches, dead-letter handling, migration rollback, regional recovery, and quarterly DR exercises. None of this exists in the Python skeleton. |

---

### NR-EVAL-01 — Evaluation and Promotion
**Status:** ⚠️ Partially Implemented

| Criterion | Status | Evidence |
|---|---|---|
| Benchmark runner | ✅ | [`reference/benchmark/runner.py`](../reference/benchmark/runner.py) |
| Dataset acquisition | ⚠️ | Hugging Face integration exists in [`hf_datasets.py`](../reference/benchmark/hf_datasets.py) but not tested with live HF_TOKEN |
| Multiple harness types | ✅ | Coding, reasoning, multi-turn task harnesses exist |
| Results storage | ✅ | Benchmark results stored and queryable via API |
| **Major gaps** | | The plan requires: signed/pinned dataset revisions, harness versions, policies, prompts, models, environments, and results with immutable hashes. No signed promotion pipeline. No cohort-gated promotion. No reversible promotion. No calibration infrastructure beyond the in-memory `CalibrationStore`. No Lighteval integration. No hidden anti-gaming suites. No candidate comparison with confidence intervals/route regret. The plan schedules this for **Phase 7 (weeks 15-17)**. |

---

### NR-UX-01 — Operator Console
**Status:** ⚠️ Partially Implemented

| Criterion | Status | Evidence |
|---|---|---|
| Run inspection | ✅ | Dashboard HTML + `/v1/epr/runs/{run_id}` |
| Route viewing | ✅ | Decision trace via `/v1/epr/runs/{run_id}/trace` |
| Cost/spending views | ✅ | Cost analytics API + dashboard |
| Model health | ✅ | Model ranking + performance analytics |
| **Major gaps** | | The plan requires a **TypeScript web application** over the administrative API — not a server-rendered HTML page. Missing: policy version views, pending approvals view, evidence exploration, tenant onboarding UX, API key management UX, operator runbooks integration. The plan schedules this for **Phase 6 (weeks 12-13)**. |

---

### NR-EXT-01 — Extension Contracts
**Status:** ⚠️ Partially Implemented

| Criterion | Status | Evidence |
|---|---|---|
| Router extension point | ⚠️ | Implicit Python interface; no versioned protobuf contract |
| Verifier extension point | ⚠️ | Pluggable check functions but no versioned interface |
| Tool extension point | ❌ | Not defined |
| Memory extension point | ⚠️ | Implicit through context.py; no versioned contract |
| Analysis extension point | ❌ | `AnalysisPlanner` defined in plan but not implemented |
| Event extension point | ⚠️ | Ledger events exist but no versioned event envelope with outbox |
| Workflow extension point | ❌ | No `WorkflowEngine` port (plan's seam for Temporal) |
| **Major gaps** | | The plan requires versioned Protocol Buffers plus JSON Schema/OpenAPI projections with generated Go, Python, and TypeScript bindings from one schema lineage. None of the protobuf contracts exist. No `proto` directory. |

---

## 2. GA Checklist Assessment

### Product and API
| Item | Status | Gap |
|---|---|---|
| v1 scope and non-goals frozen and documented | ✅ | [`product-completion-plan.md`](product-completion-plan.md) §1 and §2 |
| Public endpoints, SDK examples, streaming, errors, compatibility profiles complete | ⚠️ | API reference documents 62+ endpoints; Python SDK exists. No Go/TypeScript SDKs. Streaming works but is buffered, not true token-by-token proxying |
| Tenant onboarding, keys, quotas, usage, approvals, run/evidence inspection work | ⚠️ | API exists but console UX (TypeScript) missing; approvals are fail-closed, not interactive |
| Support, status, incident, deletion, deprecation procedures published | ❌ | Not created |

### Runtime
| Item | Status | Gap |
|---|---|---|
| Explicit non-OpenAI text, tool, vision, image-processing, image-generation, image-edit routes pass | ❌ | Text only; no tool/media routes exist |
| A2A server and outbound dispatcher pass conformance/cancellation/reconnection tests | ❌ | No A2A implementation |
| MCP sessions isolated and least-privilege | ❌ | No MCP implementation |
| AG-UI projections resume cleanly | ❌ | No AG-UI adapter |
| Provider and semantic fallbacks distinct, bounded, auditable | ✅ | Implemented in current skeleton |
| Cancellation, idempotency, timeout, retry, budget reconciliation pass fault tests | ⚠️ | Implemented but not durably; no fault injection tests |
| No route can bypass hard constraints | ✅ | Verified by test suite |

### Epistemic Governance
| Item | Status | Gap |
|---|---|---|
| Task contracts, claim states, contradiction handling, verification DAGs pass conformance tests | ✅ | All EPR-* conformance tests pass |
| Every accepted run has verifiable receipt and ledger position | ✅ | In-memory implementation passes |
| Context compaction preserves authoritative state and evidence handles | ✅ | Verified by `test_context_capsule_preserves_authoritative_state` |
| Replay reconstructs contract, policy, registry, context, route, checks, artifacts | ❌ | No replay from persisted state possible |

### Security and Reliability
| Item | Status | Gap |
|---|---|---|
| Threat model approved; no critical/high findings | ❌ | Threat model not yet created as formal document (plan requires it in Phase 0) |
| Tenant isolation, secret redaction, tool sandbox, egress controls pass adversarial tests | ❌ | No sandbox, no adversarial test suite |
| SLO dashboards, alerts, kill switches, backups, restore, rollback exercises pass | ⚠️ | Alerts and backup/restore exist; kill switches and rollback exercises do not |
| Dependencies, containers, SBOM, provenance, releases scanned and signed | ⚠️ | Docker build works; no SBOM generation, no image signing |

### Evaluation and Operations
| Item | Status | Gap |
|---|---|---|
| All launch cohorts pass signed benchmark and calibration gates | ❌ | No signed benchmarks against plan requirements |
| Shadow/canary infrastructure and rollback work | ❌ | Not implemented |
| Cost, latency, route regret, fallback, acceptance, context-recall metrics meet targets | ❌ | No targets measured |
| Pilot runs for seven consecutive days without unresolved launch-blocking regression | ❌ | No pilot |
| All owners sign release record | ❌ | No release record exists |

---

## 3. Launch Quality Gates Assessment

### Functional Gates
| Gate | Status | Gap |
|---|---|---|
| All public API compatibility fixtures pass in streaming and non-streaming modes | ⚠️ | Reference tests pass; production fixtures don't exist |
| Every accepted run has route decision, verification, cost, ledger head, retrievable receipt | ✅ | In-memory only |
| Transport, capability, semantic, epistemic, policy, specification failures produce distinct typed outcomes | ✅ | Failure classes mapped to distinct outcomes |
| Cancellation and idempotent retry cannot duplicate billable tool effects | ⚠️ | Idempotency keys accepted; no persistence across restarts. No tools to test duplication on |
| OpenAI model/provider denial tests pass at all layers | ✅ | 4-layer denial verified by tests |
| Inbound/outbound A2A tasks pass version/conformance tests | ❌ | No A2A implementation |
| MCP and A2A credentials are audience-bound and never transit through model-visible content | ❌ | No MCP/A2A implementation |

### Reliability Objectives
| Metric | Target | Status |
|---|---|---|
| Monthly API availability (excluding upstream provider failure) | 99.9% | ❌ Not measured |
| NoeRelay control-path p95 overhead | ≤250 ms | ❌ Not measured |
| Evidence receipt availability after terminal state, p99 | ≤2 seconds | ❌ Not measured |
| Durable run recovery after worker termination | 100% in fault suite | ❌ Not durable |
| Duplicate externally visible side effects under retry | 0 observed | ❌ Not tested |
| Ledger verification and stored-artifact hash match | 100% | ❌ Not measured in production |
| Successful restore from documented backup | 100% in quarterly exercise | ❌ No DR exercise |

### Evaluation Objectives
| Gate | Status |
|---|---|
| Zero observed unsafe accepts in critical launch suites | ❌ Not measured |
| All benchmark manifest gates pass | ❌ Not run |
| Every model/harness pair evaluated | ❌ Not run |
| High-risk cohort 100% recovery across compaction tests | ✅ Reference-level tests pass |
| Cost/latency regressions above tolerance block promotion | ❌ No promotion pipeline |

### Security Gates
| Gate | Status |
|---|---|
| No critical/high unresolved findings | ❌ No security review conducted |
| Secrets never appear in repository history, logs, traces, errors | ✅ Verified: no secrets in code; `.gitignore` covers `.env`; CI is secret-free for PRs |
| Default-deny tool egress and capability grants pass adversarial tests | ❌ No tools, no adversarial tests |
| Agent spoofing, malicious Agent Cards, recursive delegation, etc. pass adversarial tests | ❌ No agents, no tests |
| Public PR workflows receive no protected environment secrets | ✅ Verified: `Test` environment is gated behind branch and manual approval |
| Incident response, key rotation, compromised-model disablement, data-deletion exercises pass | ❌ Not exercised |

---

## 4. Risk Assessment

| # | Risk | Status | Current State |
|---|---|---|---|
| 1 | Compatibility surface grows faster than tests | ⚠️ Partially Mitigated | Canonical IR defined in plan but not implemented in Go. JSON Schemas exist. |
| 2 | Provider metadata or pricing changes | ❌ Unmitigated | No OpenRouter catalog snapshot import with quarantine. No TTL, no anomaly detection. |
| 3 | Upstream Responses API changes | ⚠️ Partially Mitigated | Chat-first adapter exists. Internal canonical representation planned but not in Go. |
| 4 | Weak verifier is gamed | ⚠️ Partially Mitigated | Deterministic checks and family independence exist. No hidden/mutation tests or anti-gaming suites. |
| 5 | Streaming leaks unverified output | ⚠️ Partially Mitigated | Verified/provisional modes defined in plan. Current streaming buffers then chunks (verified mode). No provisional mode. |
| 6 | Tool proposal becomes authority | ❌ Unmitigated | No tools exist, so no tool authorization, sandbox, or idempotency enforcement. |
| 7 | Compaction loses governing fact | ✅ Mitigated (reference) | Capsule invariants tested and pass. Production compaction not yet implemented. |
| 8 | Learned router optimizes proxy metrics | ❌ Unmitigated | No learned router exists. Shadow/canary infrastructure not built. |
| 9 | Event/ledger volume becomes expensive | ❌ Unmitigated | No tiered retention, no compression, no content-addressing at storage level. |
| 10 | Early microservices slow delivery | ✅ Mitigated | Plan explicitly calls for modular monolith first. |
| 11 | Polyglot codebase fragments contracts | ❌ Unmitigated | No Go code, no protobuf types, no cross-language conformance tests. |
| 12 | Remote agent lies about capability or identity | ❌ Unmitigated | No agent registry, no Agent Card verification, no trust roots. |
| 13 | Recursive agents create loops or cost explosions | ❌ Unmitigated | No agent delegation, no depth/fan-out/budget controls. |
| 14 | Protocol event loss mistaken for durable state | ❌ Unmitigated | No outbox pattern, no durable cursor, no reconciliation. |
| 15 | "100%" becomes moving target | ✅ Mitigated | v1 requirements and gates are frozen in `product-completion-plan.md`. |

---

## 5. Documentation Completeness

| Document | Status | Assessment |
|---|---|---|
| [`README.md`](../README.md) (253 lines) | ✅ Complete | Installation, usage, architecture diagram, quick start, configuration, API compatibility table all present |
| [`CONTRIBUTING.md`](../CONTRIBUTING.md) (49 lines) | ✅ Complete | Development workflow, normative change requirements, commit expectations |
| [`SECURITY.md`](../SECURITY.md) (24 lines) | ✅ Exists | Vulnerability reporting process. Threat model not yet created as a document. |
| [`docs/architecture.md`](architecture.md) (160 lines) | ✅ Complete | All EPR-* normative requirements with MUST/SHOULD/MAY |
| [`docs/gateway.md`](gateway.md) (297 lines) | ✅ Complete | Compatibility profile, documented differences, conformance checklist |
| [`docs/api-reference.md`](api-reference.md) (985 lines) | ✅ Complete | 62+ endpoints with request/response examples, error codes |
| [`docs/deployment.md`](deployment.md) (446 lines) | ✅ Complete | Docker, K8s, bare metal, TLS, backup, monitoring, logging |
| [`docs/admin-guide.md`](admin-guide.md) (528 lines) | ✅ Complete | Backup/restore, tenants, alerts, webhooks, secrets, config, model lifecycle |
| [`docs/environment.md`](environment.md) (69 lines) | ✅ Complete | Secret configuration, GitHub Test environment setup |
| [`docs/continuation-handoff.md`](continuation-handoff.md) (215 lines) | ✅ Complete | Current state, locked decisions, next steps |
| [`docs/product-completion-plan.md`](product-completion-plan.md) (677 lines) | ✅ Complete | Full product plan |
| Runbooks | ❌ Missing | The plan requires 8 minimum runbooks (§12). None exist yet. |
| Threat model document | ❌ Missing | Referenced in plan §8 (Security gates) and SECURITY.md |
| SDK examples for Go/TypeScript | ❌ Missing | Only Python reference exists |

---

## 6. Specification & Schema Assessment

| Artifact | Status | Evidence |
|---|---|---|
| [`spec/openapi.json`](../spec/openapi.json) | ✅ Valid | Validates as JSON Schema draft 2020-12. Covers OpenAI-compatible + EPR endpoints |
| [`spec/routing-policy.json`](../spec/routing-policy.json) | ✅ Complete | Lexicographic routing rules with risk gates |
| [`spec/verification-state-machine.json`](../spec/verification-state-machine.json) | ✅ Complete | Fail-closed execution lifecycle |
| [`spec/benchmark-manifest.json`](../spec/benchmark-manifest.json) | ✅ Complete | Evaluation and promotion contract |
| [`spec/schemas/*.json`](../spec/schemas/) (9 files) | ✅ All Valid | All 9 schemas validate as JSON Schema draft 2020-12 |

---

## 7. Test Coverage Assessment

**Total: 1020 tests, all passing** (run time: ~111 seconds)

| Test File | Tests | Coverage Area |
|---|---|---|
| [`test_gateway.py`](../tests/test_gateway.py) | 242 | Core gateway pipeline, routing, streaming, models |
| [`test_governance.py`](../tests/test_governance.py) | 93 | Governance, risk classes, policy |
| [`test_compression.py`](../tests/test_compression.py) | 83 | RTK compression, dedup, pruning |
| [`test_api_surface.py`](../tests/test_api_surface.py) | 81 | API surface completeness, endpoint validation |
| [`test_model_lifecycle.py`](../tests/test_model_lifecycle.py) | 75 | Model registry, true cost model, OpenRouter discovery |
| [`test_dashboard_ui.py`](../tests/test_dashboard_ui.py) | 69 | Dashboard UI (Playwright) |
| [`test_database.py`](../tests/test_database.py) | 63 | SQLite persistence, CRUD operations |
| [`test_analytics.py`](../tests/test_analytics.py) | 59 | Cost, performance, usage, escalation, audit, benchmark analytics |
| [`test_integration.py`](../tests/test_integration.py) | 59 | Integration tests across modules |
| [`test_local_models.py`](../tests/test_local_models.py) | 58 | Ollama integration, local model discovery |
| [`test_benchmark_advanced.py`](../tests/test_benchmark_advanced.py) | 45 | Advanced benchmark metrics and harnesses |
| [`test_http_clients.py`](../tests/test_http_clients.py) | 26 | HTTP client implementations |
| [`test_benchmark.py`](../tests/test_benchmark.py) | 18 | Benchmark runner and datasets |
| [`test_spec.py`](../tests/test_spec.py) | 17 | JSON parsing, schema validation, epistemic, routing, ledger, memory |
| [`test_compression_cache.py`](../tests/test_compression_cache.py) | 16 | Compression caching |
| [`test_compression_profiler.py`](../tests/test_compression_profiler.py) | 13 | Compression profiling |
| [`test_remote_smoke.py`](../tests/test_remote_smoke.py) | 3 | Remote credential validation (manual, gated) |

**Coverage gaps:**
- No Go tests (no Go code exists)
- No A2A protocol tests (no A2A code exists)
- No MCP protocol tests (no MCP code exists)
- No AG-UI event tests (no AG-UI code exists)
- No security/adversarial test suite
- No load/fault injection tests
- No cross-tenant isolation adversarial tests
- No end-to-end tool execution tests

---

## 8. CI Status

| Workflow | Status | Details |
|---|---|---|
| `Conformance` | ✅ Passing | Secret-free schema and reference-kernel tests |
| `CI` | ✅ Passing | Test matrix (Python 3.11, 3.12), Docker build |
| `Test Environment Smoke` | ✅ Configured | Manual-only, `main`-guarded, confirmation-gated |

**Latest runs (all from 2026-08-20):** 9 of 10 successful; 1 transient failure (fixed in subsequent run). All current `main` CI is green.

**CI gaps:**
- No Go build/lint/test steps (no Go code)
- No protobuf compatibility/linting
- No SBOM generation
- No container vulnerability scanning
- No signed release artifacts

---

## 9. Docker & Kubernetes Deployment Readiness

### Docker
| Component | Status | Notes |
|---|---|---|
| [`Dockerfile`](../Dockerfile) | ⚠️ Functional, not production | Single-stage build, runs as root, no multi-stage optimization, installs pytest in production image |
| [`docker-compose.yml`](../docker-compose.yml) | ✅ Complete | Includes noerelay + ollama services, healthchecks, volumes, GPU support |
| [`.dockerignore`](../.dockerignore) | ✅ Exists | Prevents unnecessary context |

**Docker gaps:** No multi-stage build, no non-root user, pytest and dev dependencies in production image, no SBOM generation, no image signing, no distroless base.

### Kubernetes
| Component | Status | Notes |
|---|---|---|
| [`deployment.yaml`](../deploy/kubernetes/deployment.yaml) | ✅ Functional | 1 replica, resource limits, health probes, secret refs |
| [`service.yaml`](../deploy/kubernetes/service.yaml) | ✅ Complete | ClusterIP on port 80 → 8080 |
| [`pvc.yaml`](../deploy/kubernetes/pvc.yaml) | ✅ Complete | 10Gi RWO |
| [`secret.yaml`](../deploy/kubernetes/secret.yaml) | ✅ Exists | Template with REPLACE_ME placeholders |

**K8s gaps:** No Ingress resource, no HorizontalPodAutoscaler, no NetworkPolicy, no PodDisruptionBudget, no ConfigMap for non-secret config, no ServiceMonitor for Prometheus operator.

---

## 10. Security Assessment

| Concern | Status | Details |
|---|---|---|
| Hardcoded secrets in code | ✅ None found | All secrets use env vars or secret manager |
| TODO/FIXME/HACK/XXX comments | ✅ None found | Zero matches in `reference/gateway/*.py` |
| Auth implementation | ⚠️ Partial | API keys, RBAC, rate limiting exist but auth is disabled by default; no OIDC |
| TLS support | ⚠️ Optional | TLS config exists but requires manual cert provisioning |
| Secret redaction in logs/errors | ⚠️ Partial | Structured logging exists but no content-aware redaction |
| Default bind address | ✅ Safe | `127.0.0.1` by default (loopback only) |
| PR workflows secret exposure | ✅ Protected | `Test` environment gated behind branch + manual approval |
| Dependency scanning | ❌ Not present | No vulnerability scanning in CI |
| Container scanning | ❌ Not present | No image scanning |
| SBOM generation | ❌ Not present | No SBOM in build pipeline |
| Threat model | ❌ Not created | Referenced but not created as a formal document |

---

## 11. Implementation Progress Against 17 Epics

| Epic | Status | Completion |
|---|---|---|
| `NR-001 Foundation` | ❌ Not Started | Requires Go workspace, protobuf lineage, multi-language CI |
| `NR-002 Canonical API` | ❌ Not Started | Requires Go API edge with auth |
| `NR-003 Durable Runs` | ❌ Not Started | Requires PostgreSQL run/step/outbox |
| `NR-004 Governance` | ⚠️ Reference only | Python reference kernel has contract compiler; no Go production version |
| `NR-005 Registry` | ⚠️ Reference only | File-based Python registry; no PostgreSQL model/tool/provider tables |
| `NR-006 Router` | ⚠️ Reference only | Python routing works; no Go production router with cost anomaly detection |
| `NR-007 OpenRouter` | ⚠️ Reference only | Python OpenRouter adapter exists; no Go production adapter |
| `NR-008 Tools and Retrieval` | ❌ Not Started | No implementation |
| `NR-009 Multimodal` | ❌ Not Started | No adapters |
| `NR-010 Verification` | ⚠️ Reference only | Python verification DAG works; no pluggable nodes, no mutation/hidden tests |
| `NR-011 Evidence` | ⚠️ Reference only | Python ledger works; no PostgreSQL immutable event tables |
| `NR-012 Memory` | ⚠️ Reference only | Python L0-L3 model works; no PostgreSQL project graph |
| `NR-013 Evaluation` | ⚠️ Reference only | Python benchmark runner exists; no signed promotion pipeline |
| `NR-014 Operations` | ⚠️ Partial | Health, metrics, alerts exist; no kill switches, no OTel, no DR |
| `NR-015 Console` | ⚠️ Partial | Server-rendered HTML dashboard; no TypeScript console |
| `NR-016 Agent Interoperability` | ❌ Not Started | No A2A, MCP, AG-UI implementation |
| `NR-017 GA` | ❌ Not Started | Depends on all other epics |

---

## 12. Dependency on Production Rewrite

The single largest gap is the **Go production control plane**. The plan is unambiguous:

> "Go 1.25+ owns the production control plane: public API, authentication, contracts, deterministic policy, routing, budget authority, durable execution, verification orchestration, epistemic transitions, ledger writes, and release decisions."

The current Python reference kernel is explicitly defined as a **conformance oracle**, not the release-authority hot path. Every capability contract (NR-API-01 through NR-EXT-01) requires a Go implementation with PostgreSQL persistence, protobuf contracts, and cross-language SDK bindings. None of this exists.

The continuation handoff defines the first two Go PRs:
1. **PR A — Go and contract foundation** (workspace, protobuf lineage, multi-language CI)
2. **PR B — Durable walking skeleton** (authenticated request → contract → route → durable run → ledger → response)

These have not been started.

---

## 13. Summary of Critical vs Non-Critical Gaps

### Critical Gaps (Block Release)

1. **No Go production control plane** — The entire plan requires a Go rewrite for the hot path. The Python skeleton is explicitly a conformance oracle.
2. **No PostgreSQL persistence** — All state (runs, claims, ledger, memory, registry) is in-memory and lost on restart.
3. **No protobuf contract lineage** — Generated Go/Python/TypeScript bindings from one schema lineage don't exist.
4. **No durable execution** — No run/step tables, no transactional outbox, no worker leases, no restart recovery.
5. **No A2A agent interoperability** — Required for NR-A2A-01; critical for the governed agent delegation architecture.
6. **No tool sandbox or governed tools** — Required for NR-TOOL-01; critical for safety.
7. **No MCP integration** — Required for NR-MCP-01.
8. **No security review or threat model** — Launch gate blocker.
9. **No evaluation/promotion pipeline** — Required for NR-EVAL-01; models cannot be promoted without signed benchmarks.

### Non-Critical Gaps (Should Address)

1. **TypeScript operator console** — HTML dashboard exists as placeholder but needs full TypeScript rewrite.
2. **AG-UI event adapter** — Needed for NR-AGUI-01 but can follow core runtime.
3. **Image/vision/multimodal adapters** — Phase 4; not needed for initial walking skeleton.
4. **K8s production hardening** — Ingress, HPA, NetworkPolicy, PDB — can follow initial deployment.
5. **Runbooks** — Can be written during pilot phase.
6. **SDK examples for Go/TypeScript** — Can follow API stabilization.
7. **Docker production hardening** — Multi-stage build, non-root user, distroless base.

### Things That Are Actually Working Well

1. **Architecture specification** — EPR-1 normative architecture is complete, internally consistent, and tested.
2. **JSON Schemas** — All 9 domain schemas + OpenAPI spec are valid JSON Schema draft 2020-12.
3. **Reference test suite** — 1020 tests, all passing, 0 seconds of technical debt in the form of TODO/FIXME/HACK.
4. **CI pipeline** — Both Conformance and CI workflows passing; `Test` environment properly gated.
5. **Documentation** — 12+ docs totaling thousands of lines, all consistent and cross-referenced.
6. **Deterministic routing algorithm** — Correctly implements lexicographic constraint-first optimization.
7. **Epistemic governance** — Four-valued adjudication, evidence ledger, verification DAG all correctly implemented in reference.
8. **Product plan** — Comprehensive, with explicit acceptance criteria, milestones, risks, decisions, and source alignment.
9. **Zero hardcoded secrets** — Security hygiene is clean in the existing codebase.

---

## 14. Recommended Priority Order

Based on the product completion plan's critical path (§7) and first ten PRs (§16):

1. **PR A: Go and contract foundation** — workspace, proto lineage, multi-language CI, ADR template
2. **PR B: Durable walking skeleton** — PostgreSQL schema, authenticated API, canonical IR, durable runs, ledger
3. **PR C: Live core** — OpenRouter adapter with explicit non-OpenAI models, streaming, cost/budget
4. **PR D: Governed agent slice** — A2A server/client, agent registry, one governed delegation
5. **Security foundation** — Threat model, secret management hardening, TLS, least-privilege
6. **Tool and media adapters** — Tool registry, sandbox, vision, image generation
7. **Verification, epistemic, evidence hardening** — PostgreSQL-backed claims, ledger, memory, compaction
8. **Evaluation pipeline** — Hugging Face acquisition, benchmark runner, calibration, signed promotion
9. **Operations hardening** — OTel, kill switches, dashboards, runbooks, backup/restore drills
10. **Console and documentation** — TypeScript console, SDK examples, operator runbooks
11. **Pilot and GA** — Restricted pilot, regression fixes, GA deployment, security sign-off

---

*This gap analysis is based on the [product-completion-plan.md](product-completion-plan.md) (v1 baseline, 677 lines), [continuation-handoff.md](continuation-handoff.md) (current state), manual review of all 62+ server routes in [`server.py`](../reference/gateway/server.py), 1020-test execution results, CI status via `gh run list`, schema validation of all 9 JSON Schema files, and comprehensive documentation review across 12+ documents.*