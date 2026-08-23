# Changelog

All notable changes to NoeRelay will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Architecture

- Establish Rust as the trusted policy, routing, budget, verification, ledger, and release-authority boundary.
- Add a compiling Rust OpenAI-compatible gateway with explicit OpenRouter model routing and a development-only stub plane.
- Add PyO3 bindings over the Rust authority functions instead of duplicating policy in Python.
- Add an authenticated Go A2A v1.0 adapter using the official A2A SDK; the adapter owns no routing or release decisions.
- Add the authoritative requirements catalog, verification matrix, and Rust authority ADR.
- Add transactional PostgreSQL authority snapshots, append-only scoped ledger storage, usage rollups, and restart-safe signed receipts.
- Add Ed25519 receipt signing and offline verification, contract-bound context manifests, fail-closed high-risk release gates, and governance traceability checks.
- Add digest-pinned Rust and Go container builds plus PostgreSQL-backed deployment examples.
- Upgrade PyO3 to the advisory-fixed 0.29 line, build the A2A adapter with Go 1.26.6, and gate CI on RustSec and Go vulnerability scans.

### Security

- Enforce API-key authentication automatically on non-loopback listeners and fail startup when no key is configured.
- Wire authentication, role checks, per-key rate limits, tenant identity, audit records, exact-origin CORS, request-size limits, and security headers through the real HTTP boundary.
- Isolate cached responses, runs, receipts, traces, and ledger reads by authenticated tenant.
- Replace unauthenticated XOR secret storage with versioned authenticated encryption, tamper detection, explicit master-key configuration, and transactional key rotation while retaining legacy read support for migration.
- Reject private, loopback, link-local, credential-bearing, and malformed webhook targets by default; redact webhook secrets from API responses.
- Restrict import and restore paths to the database directory and create sensitive database artifacts with restrictive permissions where supported.

### Changed

- Bound run, alert, audit-delivery, SIEM, webhook, and cache retention to prevent unbounded process growth.
- Validate compatibility parameters, finite numeric values, and upstream passthrough fields more strictly.
- Close worker-thread SQLite connections and webhook responses deterministically and avoid a signal-thread shutdown deadlock.
- Harden Docker and Kubernetes defaults, including non-root/read-only execution, explicit secrets, authenticated metrics, egress restrictions, and a one-replica SQLite limit.
- Add five-minute quick-start and production-deployment guides and correct the Open WebUI network/authentication instructions.

## [0.1.0-draft] — 2026-08-20

### Added

#### Core Gateway
- OpenAI-compatible `/v1/chat/completions` endpoint with full parameter support (temperature, top_p, max_tokens, stop, stream, n, presence_penalty, frequency_penalty, logit_bias, user)
- OpenAI-compatible `/v1/models` endpoint returning `noerelay/epr-1` virtual model
- OpenAI-compatible `/v1/responses` endpoint
- SSE streaming support with EPR metadata in terminal chunk
- Graceful shutdown with request draining
- CORS headers for cross-origin access
- Structured JSON logging to stdout/file
- Prometheus metrics endpoint (`/metrics`) with 15 metric types
- Health check endpoint (`/health`) with version info

#### EPR Governance
- Hash-chained evidence ledger with SHA-256 integrity verification
- Evidence receipts binding inputs, route, artifacts, verification, cost, claims, and ledger head
- Four-valued epistemic fact adjudication (unknown, supported, refuted, conflicted)
- Typed epistemic state vocabulary (fact, requirement, decision, assumption, observation, prediction, preference, artifact)
- Verification DAG with deterministic checks before judgmental review
- Independent verifier requirement for high-risk work
- W3C PROV-compatible provenance mapping
- L0-L3 memory model with graph-reachability context compilation
- Compaction-safe context capsules preserving authoritative state

#### Routing & Portfolio
- Deterministic lexicographic routing with hard constraint filtering
- Cost optimization only among admissible routes
- Provider fallback (transport failure) and semantic fallback (quality failure) distinction
- Explicit non-OpenAI model routing through OpenRouter
- OpenAI model family and namespace denial (4-layer enforcement)
- Local Ollama model integration with automatic discovery
- Model lifecycle management (pull, remove, recommendations)
- True cost model with pricing snapshots

#### Multi-Tenancy & Security
- API key authentication with creation, listing, revocation, and rotation
- Role-based access control (RBAC)
- Token-bucket rate limiting per API key
- Tenant CRUD with daily/monthly budget enforcement
- Cross-tenant isolation
- Secret management with redaction in logs
- Non-root Docker container user
- Multi-stage Docker build
- Zero runtime dependencies (pure Python stdlib)

#### Compression
- RTK compression Phase 1: Message deduplication
- RTK compression Phase 2: Context pruning
- RTK compression Phase 3: Content summarization
- RTK compression Phase 4: Auto strategy selection with profiling
- Compression result caching
- Optional Rust bridge for native acceleration (`rtk/` crate)

#### Dashboard & Analytics
- Server-rendered HTML dashboard
- Cost analytics with breakdown by model, tenant, risk class
- Performance analytics (latency percentiles, throughput)
- Usage analytics (request counts, token usage)
- Escalation analytics
- Audit log viewing
- Benchmark results viewing
- Model ranking and comparison

#### Operations
- Alerting system with configurable rules (cost, latency, escalation)
- Webhook registration and dispatch
- Backup and restore API endpoints
- Data export/import
- Runtime configuration management
- Kill switch infrastructure (per tenant, project, model, provider)

#### Testing
- 1068 tests across 18 test files, all passing
- Core gateway pipeline tests (242 tests)
- Governance and policy tests (93 tests)
- Compression tests (83 tests)
- API surface completeness tests (81 tests)
- Model lifecycle tests (75 tests)
- Dashboard UI tests with Playwright (69 tests)
- Database persistence tests (63 tests)
- Analytics tests (59 tests)
- Integration tests (59 tests)
- Local model tests (58 tests)
- Benchmark advanced tests (45 tests)
- HTTP client tests (26 tests)
- Spec conformance tests (17 tests)
- Remote smoke tests (3 tests, manual, gated)

#### CI/CD
- Conformance workflow (secret-free, PR and main-branch)
- CI workflow (Python 3.11/3.12 matrix, Docker build)
- Test Environment Smoke workflow (manual-only, main-guarded)
- Docker Compose with NoeRelay + Ollama
- Kubernetes manifests (Deployment, Service, ConfigMap, Secret, Ingress, HPA, NetworkPolicy, PDB, PVC, ServiceMonitor)

#### Documentation
- README.md with architecture diagram, quick start, API reference
- Architecture specification (EPR-1 normative requirements)
- API reference (62+ endpoints)
- Integration guide (Open WebUI, zoo-code, LangChain, curl)
- Deployment guide (Docker, K8s, bare metal, TLS)
- Admin guide (backup/restore, tenants, alerts, webhooks)
- Threat model (10 identified threats with mitigations)
- Gap analysis (~18% release readiness)
- Product completion plan (8-phase, 20-22 week plan)
- Research basis documentation
- Environment configuration guide
- Continuation handoff document
- Benchmarking guide
- Runbooks
- Gateway compatibility profile

#### Specifications
- OpenAPI 3.1 specification (`spec/openapi.json`)
- Routing policy specification (`spec/routing-policy.json`)
- Verification state machine (`spec/verification-state-machine.json`)
- Benchmark manifest (`spec/benchmark-manifest.json`)
- 9 JSON Schema 2020-12 domain contracts in `spec/schemas/`

#### Examples
- Candidate portfolio actions (`examples/candidate-actions.json`)
- Context capsule (`examples/context-capsule.json`)
- High-risk coding contract (`examples/high-risk-coding-contract.json`)
- OpenAI SDK test script (`examples/openai-sdk-test.py`)
- LangChain test script (`examples/langchain-test.py`)
- curl test script (`examples/curl-test.sh`)

#### Integrations
- Open WebUI docker-compose sidecar (`docker-compose.openwebui.yml`)
- Open WebUI setup guide (`docs/open-webui-setup.md`)
- Codex instructions document (`docs/CODEX-INSTRUCTIONS.md`)

### Known Limitations (Reference Kernel)

- Some runtime state is in memory; configured SQLite persistence covers operational records but is not a horizontally scalable database
- No PostgreSQL persistence (planned for Go production phase)
- No Go production control plane (Python is conformance oracle)
- No protobuf contract lineage
- No A2A agent interoperability
- No MCP tool integration
- No AG-UI event adapter
- No tool sandbox or governed tools
- No multimodal adapters (vision, image generation)
- No OpenTelemetry distributed tracing
- No OIDC/OAuth authentication
- No container vulnerability scanning or SBOM generation
- Auth disabled by default (loopback-only bind)
- No formal security review completed

### Upcoming (Go Production Phase)

- Go 1.25+ production control plane
- PostgreSQL persistence with immutable event tables
- Protobuf canonical IR with multi-language SDK bindings
- Durable execution with transactional outbox
- A2A v1.0 agent interoperability
- MCP tool integration with sandboxed execution
- AG-UI event adapter (TypeScript)
- Multimodal adapters (vision, image generation, image editing)
- Full verification pipeline with pluggable nodes
- Signed benchmark evaluation and promotion pipeline
- TypeScript operator console
- OpenTelemetry distributed tracing
- Kill switches by tenant, project, model, provider, tool, policy, modality
- OIDC/OAuth authentication
- Container vulnerability scanning and SBOM generation
- Formal security review and penetration testing
- Disaster recovery exercises
- SLO dashboards with burn-rate alerts
