# NoeRelay v1 product requirements

**Status:** Authoritative implementation baseline  
**Architecture authority:** Rust core  
**Public contract:** OpenAI-compatible HTTP/JSON/SSE  
**Primary model plane:** OpenRouter  
**Method:** requirement -> linked verification -> observed evidence -> release decision

## Product outcome

NoeRelay is a virtual OpenAI API endpoint for people who want a low-friction, “vibe coding” experience while their organization receives disciplined execution underneath. Every request is normalized, scoped to an organization/user/project, compiled into a task contract, routed to the lowest expected-cost admissible model or agent, executed with governed tools, verified against explicit requirements, and recorded in a tamper-evident ledger. The compatible response remains simple; the structure, uncertainty, evidence, costs, and audit trail remain available to authorized operators.

“Complete” means every `MUST` requirement below has an automated or explicitly human verification gate and every gate has evidence. It does not mean that NoeRelay can certify an organization’s legal compliance or that every future model, agent, tool, and regulatory framework is supported.

## Architectural invariants

- Rust owns authentication decisions, canonical request state, task contracts, policy, routing, budgets, tool authorization, verification orchestration, epistemic state, ledger writes, release decisions, and the public gateway.
- Python is a generated/bound binding and isolated extension surface for evaluation, data science, notebooks, and model-specific analysis. Python cannot independently accept a run or mutate authoritative policy.
- Go is permitted for narrow protocol or operational adapters where its ecosystem is materially better, initially A2A interoperability. Go adapters are untrusted callers of the Rust authority boundary.
- SQL, TypeScript, PowerShell, POSIX shell, and future languages MAY be used at justified storage, client, UI, and operator boundaries under [ADR-0002](adr/0002-justified-polyglot-boundaries.md); none may duplicate Rust authority decisions.
- One versioned schema lineage defines wire and domain objects. Language-specific handwritten competing definitions are prohibited.
- OpenRouter is the default model plane. NoeRelay, not OpenRouter automatic routing, owns admissibility and final model selection.
- Models and agents may propose plans and tool actions. Deterministic policy authorizes them.
- Every accepted result is bound to an immutable contract, route decision, verification result, cost record, unresolved-claim set, and ledger head.
- Context reduction may discard conversational redundancy but may not discard authoritative requirements, decisions, contradictions, approvals, evidence handles, or unresolved claims.

## Requirements

### API and compatibility

| ID | Requirement | Acceptance outcome |
|---|---|---|
| `NR-API-001` | The service MUST implement `/v1/models`, `/v1/chat/completions`, and `/v1/responses`. | Official OpenAI-client-shaped fixtures pass for supported fields without client-specific adapters. |
| `NR-API-002` | Chat and Responses MUST support streaming and non-streaming operation. | SSE framing, terminal events, disconnect cancellation, and error behavior pass contract tests. |
| `NR-API-003` | Unsupported fields MUST be rejected or explicitly documented; they MUST NOT be silently reinterpreted. | Negative compatibility fixtures produce stable OpenAI-shaped errors. |
| `NR-API-004` | Every request MUST carry or derive organization, project, environment, user, session, request, and trace identities. | Missing or conflicting scope fails closed; identifiers appear in authorized audit views. |
| `NR-API-005` | Idempotency keys MUST bind to caller scope and normalized request hash. | Same key/same input replays one result; same key/different input returns conflict. |
| `NR-API-006` | The outward response MUST remain simple while governance metadata is available through response extensions, headers, and run/receipt endpoints. | Standard clients work unchanged; authorized clients can retrieve the complete evidence chain. |

### Identity, tenancy, and administration

| ID | Requirement | Acceptance outcome |
|---|---|---|
| `NR-IAM-001` | Tenant, project, environment, principal, role, API key, quota, and policy scopes MUST be server-enforced. | Cross-tenant and cross-project negative matrices cannot read, infer, mutate, or enumerate foreign data. |
| `NR-IAM-002` | API keys MUST be hashed at rest, scoped, revocable, rotatable, rate-limited, and never returned after creation. | Rotation is atomic and old-key use immediately fails. |
| `NR-IAM-003` | Administrative routes MUST use deny-by-default RBAC and immutable audit events. | Every route maps to an explicit permission; unmapped routes deny non-admin callers. |
| `NR-IAM-004` | External identity federation MUST be implementable behind a versioned identity port. | OIDC claims map to the same canonical principal and scope model without changing domain policy. |
| `NR-IAM-005` | Tenant deletion and retention operations MUST cover authoritative rows, artifacts, caches, credentials, and derived views. | Deletion tests prove no scoped record remains except legally configured tombstones/audit proofs. |

### Contract and specification discipline

| ID | Requirement | Acceptance outcome |
|---|---|---|
| `NR-SPEC-001` | Every run MUST compile a versioned task contract containing outcome, constraints, acceptance criteria, risk, scope, budgets, allowed tools/agents, and required evidence. | Execution cannot start without a valid immutable contract. |
| `NR-SPEC-002` | The system MUST maintain bidirectional architecture -> requirement -> test -> evidence traceability. | Orphaned `MUST` requirements, tests, or accepted evidence fail the release gate. |
| `NR-SPEC-003` | High and critical risk work MUST reject missing acceptance criteria rather than invent them. | Adversarial vague requests stop in clarification/approval states. |
| `NR-SPEC-004` | Requirement and policy revisions MUST be immutable and runs MUST pin exact revisions. | Replaying a run never reads a moving `latest` revision. |
| `NR-SPEC-005` | The governance workflow MUST distinguish proposed, observed, inferred, contradicted, accepted, and rejected statements. | Tests prove an unobserved model claim cannot become verification evidence. |
| `NR-SPEC-006` | Release authority MUST require linked test evidence, not model self-attestation. | Self-reported “tests pass” text cannot satisfy a gate without an observed test event. |

### Model, agent, and route intelligence

| ID | Requirement | Acceptance outcome |
|---|---|---|
| `NR-ROUTE-001` | The registry MUST store explicit OpenRouter model IDs, capabilities, context limits, price snapshots, data policy, regions, health, benchmark versions, and allowed roles. | Unversioned or incomplete candidates are quarantined. |
| `NR-ROUTE-002` | Routing MUST filter every hard constraint before optimization. | Property tests prove an inadmissible candidate never wins regardless of price. |
| `NR-ROUTE-003` | Among admissible plans, routing MUST minimize expected total cost, then latency, then maximize calibrated acceptance likelihood. | Deterministic fixtures return the same plan and complete rejection reasons. |
| `NR-ROUTE-004` | Expected total cost MUST include inference, tools, verification, retries, fallback, infrastructure, and expected human review. | Cost-selection tests detect locally cheap but globally expensive plans. |
| `NR-ROUTE-005` | OpenRouter automatic model selection MUST NOT replace NoeRelay route authority. | Upstream requests always contain the explicit selected model and bounded provider policy. |
| `NR-ROUTE-006` | Recommendations MUST learn only from versioned, scoped observations and MUST remain advisory until signed promotion. | Online statistics cannot mutate an active policy or acceptance threshold. |
| `NR-ROUTE-007` | Recommendations MUST report uncertainty, cohort coverage, freshness, and reasons. | Sparse or stale data produces abstention or qualified advice, never false certainty. |
| `NR-ROUTE-008` | Provider fallback, capability fallback, semantic fallback, epistemic escalation, and specification clarification MUST be distinct and bounded. | Each class has separate budgets, events, metrics, and terminal behavior. |

### Context and epistemic behavior

| ID | Requirement | Acceptance outcome |
|---|---|---|
| `NR-CTX-001` | Context MUST be compiled from durable project/user/session state rather than forwarded as an unbounded transcript. | Token use stays within the selected model limit with an auditable inclusion manifest. |
| `NR-CTX-002` | Deduplication, pruning, summarization, and retrieval MUST preserve protected nodes. | Property tests preserve requirements, decisions, contradictions, approvals, evidence handles, and active tool state. |
| `NR-CTX-003` | Summaries MUST carry provenance and an uncertainty class. | Summaries cannot overwrite source evidence and can be expanded back to source handles. |
| `NR-CTX-004` | Claims MUST use four-valued epistemic state: supported, refuted, both, or neither. | Truth-table and merge tests preserve contradictions instead of averaging them away. |
| `NR-CTX-005` | The system MUST distinguish facts, requirements, decisions, assumptions, observations, predictions, preferences, and artifacts. | Each type has valid transitions, evidence rules, retention, and rendering behavior. |
| `NR-CTX-006` | When evidence is insufficient, the system MUST clarify, gather information, abstain, or escalate. | No-route and low-confidence fixtures never fabricate an accepted answer. |

### Governed execution and tools

| ID | Requirement | Acceptance outcome |
|---|---|---|
| `NR-EXEC-001` | Runs and steps MUST be durable, cancelable, resumable, leased, and idempotent. | Worker-death and duplicate-delivery tests converge to one terminal result and one side effect. |
| `NR-EXEC-002` | Timeouts, retries, and circuit breakers MUST be classified by operation and failure class. | Fault injection proves policy failures are not retried as transport failures. |
| `NR-EXEC-003` | Tool schemas, revisions, grants, credentials, egress, inputs, outputs, and side effects MUST be explicit. | A model-proposed tool call executes only after deterministic authorization. |
| `NR-EXEC-004` | Side-effecting tools MUST require idempotency and risk-appropriate approval. | Replay and retry cannot duplicate the side effect. |
| `NR-EXEC-005` | Tool and code execution MUST occur in a bounded sandbox with resource, filesystem, network, and output limits. | Escape, SSRF, fork bomb, secret access, and output-flood suites fail closed. |
| `NR-EXEC-006` | MCP sessions MUST be isolated per server and principal; advertised capability MUST NOT grant authority. | Token forwarding and cross-session capability attacks fail. |
| `NR-EXEC-007` | Agent delegation MUST be authenticated, allowlisted, contract-bound, depth/fan-out/cycle limited, budgeted, and independently verified. | A2A loop, flood, replay, cancellation, and foreign-tenant tests pass. |
| `NR-EXEC-008` | Architecture -> requirements -> implementation -> tests work MAY be delegated, but acceptance MUST remain with an independent verifier and the Rust release gate. | The implementing agent cannot satisfy its own high-risk acceptance requirement. |

### Verification, ledger, and compliance evidence

| ID | Requirement | Acceptance outcome |
|---|---|---|
| `NR-VER-001` | A risk-scaled verification DAG MUST run deterministic checks before probabilistic review. | Check ordering and dependency tests are deterministic and replayable. |
| `NR-VER-002` | High/critical work MUST use verifier independence appropriate to the risk. | Same-family-only review cannot satisfy an independent-family gate. |
| `NR-VER-003` | Verification failure MUST trigger bounded repair, fallback, clarification, rejection, or escalation. | No failure path silently marks a run accepted. |
| `NR-LED-001` | All authority-changing events MUST be canonicalized and hash-linked. | Tamper, deletion, reorder, and cross-run splice tests invalidate the chain. |
| `NR-LED-002` | Accepted runs MUST produce signed/verifiable receipts binding scope, input, contract, route, artifacts, checks, costs, claims, and ledger head. | Offline verification detects any altered bound field. |
| `NR-LED-003` | Audit views MUST support organization, project, user, model, agent, tool, policy, and time filters without exposing hidden reasoning or secrets. | Authorization and export fixtures prove scoped completeness and redaction. |
| `NR-COMP-001` | Compliance mappings MUST be versioned evidence aids, not legal-certification claims. | Reports identify framework version, applicable controls, evidence, gaps, owner, and review date. |
| `NR-COMP-002` | Retention, residency, privacy, legal hold, deletion, and export policies MUST be explicit per tenant/project. | Policy simulation and lifecycle tests prove configured behavior. |

### Cost, analytics, and learning

| ID | Requirement | Acceptance outcome |
|---|---|---|
| `NR-COST-001` | Token, request, tool, verification, artifact, and human-review cost MUST be measured or explicitly estimated per attempt. | Reconciliation separates estimated, provider-reported, and billed values. |
| `NR-COST-002` | Usage and cost MUST aggregate by organization, project, environment, user, API key, run, model, agent, and tool. | Roll-up totals equal source attempt records within declared rounding rules. |
| `NR-COST-003` | Hard budgets MUST be reserved before execution and reconciled after execution. | Concurrent requests cannot overspend a shared cap. |
| `NR-COST-004` | Reports MUST show quality/cost/latency tradeoffs and route regret, not only spend. | Cohort reports compare chosen and admissible alternatives with uncertainty. |
| `NR-COST-005` | Feedback MUST distinguish preference, deterministic outcome, environment outcome, verifier judgment, and human authorization. | A thumbs-up cannot be treated as proof of correctness. |

### Operations, security, and release

| ID | Requirement | Acceptance outcome |
|---|---|---|
| `NR-OPS-001` | The service MUST expose separate liveness, readiness, metrics, trace, structured-log, and sanitized event interfaces. | Dependency failure removes readiness without causing false liveness failure. |
| `NR-OPS-002` | Administrative kill switches MUST exist globally and by tenant, project, provider, model, agent, and tool. | Kill-switch tests stop new/cached work and produce audit evidence. |
| `NR-OPS-003` | Backups, point-in-time recovery, ledger verification, and disaster recovery MUST be exercised. | A documented restore drill meets declared RPO/RTO and validates receipts afterward. |
| `NR-SEC-001` | TLS, secret management, deny-by-default egress, input/body/concurrency limits, least privilege, and secure headers MUST be enforced in the supported production profile. | Production configuration fails closed when any mandatory control is absent. |
| `NR-SEC-002` | Authentication, authorization, SSRF, injection, tenant crossover, ledger tampering, quota abuse, and secret redaction MUST have adversarial suites. | No critical/high finding remains open at release. |
| `NR-SEC-003` | Dependencies, licenses, source, containers, SBOMs, provenance, and releases MUST be scanned and signed. | CI produces attributable artifacts and blocks known disallowed risk. |
| `NR-REL-001` | The release MUST publish measurable SLO, load, soak, and fault-injection results for the supported deployment profile. | Results meet declared concurrency, latency, error, durability, RPO, and RTO targets. |
| `NR-REL-002` | A release MUST have product, engineering, security, evaluation, and operations sign-off evidence. | Missing approval leaves the release candidate non-GA. |
| `NR-REL-003` | “100% ready” MUST refer to this frozen requirement set and a named deployment profile, never to universal fitness or automatic legal compliance. | Release record identifies remaining external/customer responsibilities and non-goals. |

## Supported v1 deployment profile

The intended first organizational profile is a single-region highly available service using the Rust gateway/core, PostgreSQL, S3-compatible artifact storage, a durable worker queue/outbox, OpenRouter over restricted HTTPS egress, an external secret manager, and an identity-aware TLS ingress. Redis is optional and never authoritative. Python evaluation workers and the Go A2A adapter run with independent least-privilege identities.

## Explicit non-goals

- Universal legal or regulated-industry certification.
- Silent autonomous production self-modification.
- Treating model consensus, user preference, or generated tests as truth.
- Unrestricted tools, shell access, agent discovery, or provider routing.
- Guaranteeing the cheapest model irrespective of quality, privacy, or verification cost.
- Exposing chain-of-thought; the ledger records decisions, evidence, and concise rationale instead.
