# NoeRelay — Comprehensive Codex Instructions for Commercial Release Polish

**Document version:** 1.0
**Generated:** 2026-08-20
**Target repository:** `c:/Users/trist/Development/ElectroHire/norelay`
**Intended audience:** OpenAI Codex (coding agent) performing a full-project polish pass

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Repository Structure](#2-repository-structure)
3. [Current State](#3-current-state)
4. [Objectives for Codex](#4-objectives-for-codex)
   - [A. Code Quality & Polish](#a-code-quality--polish)
   - [B. API Completeness](#b-api-completeness)
   - [C. Security Hardening](#c-security-hardening)
   - [D. Test Coverage](#d-test-coverage)
   - [E. Documentation](#e-documentation)
   - [F. Open WebUI Integration](#f-open-webui-integration)
   - [G. Other Client Tool Integrations](#g-other-client-tool-integrations)
   - [H. Deployment Readiness](#h-deployment-readiness)
   - [I. Commercial Readiness Checklist](#i-commercial-readiness-checklist)
5. [How to Test NoeRelay with Client Tools](#5-how-to-test-noerelay-with-client-tools)
6. [Key Files to Review](#6-key-files-to-review)
7. [Acceptance Criteria](#7-acceptance-criteria)
8. [Things NOT to Do](#8-things-not-to-do)
9. [Quick Reference: Commands & Paths](#9-quick-reference-commands--paths)

---

## 1. Project Overview

### What NoeRelay Is

NoeRelay is a **local OpenAI-compatible gateway** (EPR-1) written in **pure Python** using only the standard library. It runs on `http://127.0.0.1:8080` and provides:

- **OpenAI-compatible endpoints:** [`/v1/chat/completions`](reference/gateway/server.py:479), [`/v1/models`](reference/gateway/server.py:246), [`/v1/responses`](reference/gateway/server.py:488)
- **EPR (Epistemic Process Recording):** Hash-chained evidence ledger, evidence receipts, run traces, four-valued fact adjudication
- **Governance:** Deterministic routing policy, risk classes (low/medium/high/critical), cost/latency caps, data policy enforcement
- **RTK Compression (Phases 1-4):** Dedup, prune, summarize, auto strategies with optional Rust bridge
- **Dashboard:** Server-rendered HTML dashboard with metrics, analytics, run inspection
- **Multi-tenancy:** API keys, RBAC, rate limiting, quota/budget enforcement
- **Model portfolio:** Local Ollama models + cloud OpenRouter models with explicit non-OpenAI routing

### Architecture

The gateway is a [`ThreadingHTTPServer`](reference/gateway/server.py:15) with a strict layering:

```
server.py  →  handlers.py  →  pipeline.py  →  epr/
```

- [`reference/gateway/`](reference/gateway/) — 50+ pure Python modules, zero external dependencies
- [`reference/epr/`](reference/epr/) — Epistemic Process Recording kernel (ledger, memory, epistemic state)
- [`reference/benchmark/`](reference/benchmark/) — Benchmarking harness with Hugging Face dataset integration
- [`rtk/`](rtk/) — Rust crate for native compression acceleration (optional)

### Zoo-Code Integration

NoeRelay is the routing layer for zoo-code. All 5 core zoo-code modes route through NoeRelay:
1. **Architect** — planning and design
2. **Code** — implementation
3. **Ask** — explanations and documentation
4. **Debug** — troubleshooting
5. **Orchestrator** — multi-step coordination

### Current Version

`0.1.0-draft` — executable specification and dependency-free Python reference kernel. This is a **conformance oracle**, not yet a production inference service. The Go production rewrite is the next major phase.

---

## 2. Repository Structure

```
norelay/
├── .github/workflows/          # CI: Conformance + CI + Docker workflows
│   ├── conformance.yml          # Secret-free PR and main-branch CI
│   ├── ci.yml                   # Test matrix (Python 3.11, 3.12), Docker build
│   └── test-environment-smoke.yml # Manual-only, main-guarded credential smoke
├── .zoo-code/                   # Zoo-code integration configuration
├── benchmarks/                  # Benchmark task datasets (JSONL)
│   ├── coding-tasks.jsonl
│   ├── multi-turn-tasks.jsonl
│   ├── quick-test.jsonl
│   └── reasoning-tasks.jsonl
├── deploy/kubernetes/           # K8s manifests
│   ├── configmap.yaml
│   ├── deployment.yaml
│   ├── hpa.yaml
│   ├── ingress.yaml
│   ├── networkpolicy.yaml
│   ├── pdb.yaml
│   ├── pvc.yaml
│   ├── secret.yaml
│   ├── service.yaml
│   └── servicemonitor.yaml
├── docs/                        # All documentation (12+ docs)
│   ├── admin-guide.md           # Backup/restore, tenants, alerts, webhooks, secrets
│   ├── api-reference.md         # 62+ endpoints with request/response examples
│   ├── architecture.md          # EPR-1 normative architecture (EPR-* requirements)
│   ├── benchmarking.md          # Hugging Face acquisition and evaluation policy
│   ├── continuation-handoff.md  # Current state, locked decisions, next steps
│   ├── deployment.md            # Docker, K8s, bare metal, TLS, backup, monitoring
│   ├── environment.md           # Secret configuration, GitHub Test environment
│   ├── gap-analysis.md          # ~18% release readiness assessment
│   ├── gateway.md               # Compatibility profile, documented differences
│   ├── integration-guide.md     # Open WebUI, zoo-code, LangChain, curl examples
│   ├── product-completion-plan.md # Full 8-phase, 20-22 week product plan
│   ├── research-basis.md        # Research and standards basis
│   ├── runbooks.md              # Operational runbooks
│   └── threat-model.md          # Security threat model (10 identified threats)
├── examples/                    # Example JSON artifacts
│   ├── candidate-actions.json   # Portfolio candidate actions
│   ├── context-capsule.json     # Compaction-safe active context
│   └── high-risk-coding-contract.json # Typed high-risk task contract
├── plans/                       # Architecture and integration plans
│   ├── gateway-architecture.md
│   └── rtk-integration.md
├── reference/                   # Reference implementation
│   ├── demo.py                  # Deterministic routing demonstration
│   ├── benchmark/               # Benchmarking harness (7 modules)
│   ├── epr/                     # EPR kernel (4 modules)
│   └── gateway/                 # Gateway source (50+ modules)
├── rtk/                         # Rust crate for native compression
│   ├── Cargo.toml
│   ├── pyproject.toml
│   └── src/                     # dedup.rs, lib.rs, prune.rs, tokenizer.rs
├── scripts/                     # Startup, smoke test, benchmark, test-subagent
│   ├── model_lifecycle.py
│   ├── remote_service_smoke.py
│   ├── run_benchmark.py
│   ├── start-dashboard.cmd / .sh
│   ├── stop-dashboard.cmd / .sh
│   ├── test-subagent.py
│   └── verify-zoo-code.py
├── spec/                        # Specifications and schemas
│   ├── openapi.json             # OpenAI-compatible API contract (OpenAPI 3.1)
│   ├── routing-policy.json      # Deterministic lexicographic route policy
│   ├── verification-state-machine.json # Fail-closed execution lifecycle
│   ├── benchmark-manifest.json  # Evaluation and promotion contract
│   └── schemas/                 # 9 JSON Schema 2020-12 domain contracts
├── tests/                       # 1057 tests across 18 files
│   ├── conftest.py
│   ├── dashboard_requirements.py
│   ├── test_analytics.py        # 59 tests
│   ├── test_api_surface.py      # 81 tests
│   ├── test_benchmark.py        # 18 tests
│   ├── test_benchmark_advanced.py # 45 tests
│   ├── test_compression.py      # 83 tests
│   ├── test_compression_cache.py # 16 tests
│   ├── test_compression_profiler.py # 13 tests
│   ├── test_dashboard_ui.py     # 69 tests (Playwright)
│   ├── test_database.py         # 63 tests
│   ├── test_gateway.py          # 242 tests
│   ├── test_governance.py       # 93 tests
│   ├── test_http_clients.py     # 26 tests
│   ├── test_integration.py      # 59 tests
│   ├── test_local_models.py     # 58 tests
│   ├── test_model_lifecycle.py  # 75 tests
│   ├── test_remote_smoke.py     # 3 tests (manual, gated)
│   ├── test_spec.py             # 17 tests
│   └── test_structured_logging.py
├── .dockerignore
├── .env.example                 # Environment variable template
├── .gitignore
├── configure-zoo-code.cmd / .sh # Zoo-code configuration scripts
├── CONTRIBUTING.md              # Contribution guidelines
├── docker-compose.yml           # Docker Compose (NoeRelay + Ollama)
├── Dockerfile                   # Multi-stage Docker build
├── LICENSE                      # Proprietary license
├── README.md                    # 253-line project README
└── SECURITY.md                  # Security policy and vulnerability reporting
```

### Key Gateway Modules

| Module | Purpose |
|---|---|
| [`server.py`](reference/gateway/server.py) | ThreadingHTTPServer adapter, route dispatch, graceful shutdown |
| [`handlers.py`](reference/gateway/handlers.py) | Pure-function request handlers for all 62+ endpoints |
| [`pipeline.py`](reference/gateway/pipeline.py) | Request pipeline: contract → route → execute → verify → release |
| [`config.py`](reference/gateway/config.py) | Gateway configuration from env vars and files |
| [`auth.py`](reference/gateway/auth.py) | API key authentication and RBAC |
| [`rate_limit.py`](reference/gateway/rate_limit.py) | Token-bucket rate limiting per API key |
| [`policy.py`](reference/gateway/policy.py) | Deterministic routing policy enforcement |
| [`portfolio.py`](reference/gateway/portfolio.py) | Model portfolio management |
| [`openrouter.py`](reference/gateway/openrouter.py) | OpenRouter API integration |
| [`local_models.py`](reference/gateway/local_models.py) | Ollama local model integration |
| [`compression.py`](reference/gateway/compression.py) | RTK compression strategies (Phases 1-4) |
| [`streaming.py`](reference/gateway/streaming.py) | SSE streaming with EPR metadata |
| [`render.py`](reference/gateway/render.py) | Response rendering and error envelopes |
| [`governance.py`](reference/gateway/governance.py) | Governance validation and risk classification |
| [`contracts.py`](reference/gateway/contracts.py) | Task contract compilation |
| [`verification.py`](reference/gateway/verification.py) | Verification DAG execution |
| [`epistemic.py`](reference/gateway/epistemic.py) | Four-valued epistemic state management |
| [`epistemic_ledger.py`](reference/gateway/epistemic_ledger.py) | Hash-chained evidence ledger |
| [`context.py`](reference/gateway/context.py) | L0-L3 memory model and context compilation |
| [`tenancy.py`](reference/gateway/tenancy.py) | Multi-tenant isolation and budgets |
| [`dashboard.py`](reference/gateway/dashboard.py) | Server-rendered HTML dashboard |
| [`analytics.py`](reference/gateway/analytics.py) | Cost, performance, usage, escalation analytics |
| [`database.py`](reference/gateway/database.py) | SQLite persistence layer |
| [`prometheus.py`](reference/gateway/prometheus.py) | Prometheus metrics endpoint |
| [`structured_logging.py`](reference/gateway/structured_logging.py) | JSON structured logging |
| [`alerting.py`](reference/gateway/alerting.py) | Cost/latency/escalation alerting |
| [`audit.py`](reference/gateway/audit.py) | API call audit logging |
| [`fallback.py`](reference/gateway/fallback.py) | Provider and semantic fallback handling |
| [`cost_model.py`](reference/gateway/cost_model.py) | Cost estimation and tracking |
| [`cost_controls.py`](reference/gateway/cost_controls.py) | Cost cap enforcement |
| [`provenance.py`](reference/gateway/provenance.py) | W3C PROV mapping |
| [`rbac.py`](reference/gateway/rbac.py) | Role-based access control |
| [`secrets.py`](reference/gateway/secrets.py) | Secret management |
| [`webhooks.py`](reference/gateway/webhooks.py) | Webhook registration and dispatch |
| [`model_lifecycle.py`](reference/gateway/model_lifecycle.py) | Model pull/remove/recommendation lifecycle |
| [`cache.py`](reference/gateway/cache.py) | Response caching |
| [`compression_cache.py`](reference/gateway/compression_cache.py) | Compression result caching |
| [`compression_profiler.py`](reference/gateway/compression_profiler.py) | Compression strategy profiling |
| [`config_manager.py`](reference/gateway/config_manager.py) | Runtime configuration management |
| [`db_registry.py`](reference/gateway/db_registry.py) | Database registry |
| [`decoding.py`](reference/gateway/decoding.py) | Response decoding |
| [`escalation_policy.py`](reference/gateway/escalation_policy.py) | Escalation policy |
| [`local_policy.py`](reference/gateway/local_policy.py) | Local model routing policy |
| [`local_portfolio.py`](reference/gateway/local_portfolio.py) | Local model portfolio |
| [`online_learning.py`](reference/gateway/online_learning.py) | Online learning (canary-only) |
| [`persistence.py`](reference/gateway/persistence.py) | Data persistence |
| [`rtk_bridge.py`](reference/gateway/rtk_bridge.py) | Rust RTK bridge |
| [`runs.py`](reference/gateway/runs.py) | Run management |
| [`siem.py`](reference/gateway/siem.py) | Security information and event management |
| [`state_vocabulary.py`](reference/gateway/state_vocabulary.py) | Typed epistemic state vocabulary |
| [`statemachine.py`](reference/gateway/statemachine.py) | Verification state machine |
| [`test_independence.py`](reference/gateway/test_independence.py) | Test independence verification |

---

## 3. Current State

### Test Suite
- **1057 tests** across 18 test files, all passing
- Run time: ~111 seconds
- Largest test files: [`test_gateway.py`](tests/test_gateway.py) (242 tests), [`test_governance.py`](tests/test_governance.py) (93 tests), [`test_compression.py`](tests/test_compression.py) (83 tests)

### CI Status
- **Conformance:** ✅ Passing (secret-free schema and reference-kernel tests)
- **CI:** ✅ Passing (test matrix Python 3.11/3.12, Docker build)
- **Test Environment Smoke:** ✅ Configured (manual-only, main-guarded)

### Gap Analysis
The comprehensive gap analysis at [`docs/gap-analysis.md`](docs/gap-analysis.md) rates overall release readiness at **~18%**. Key findings:

**What's working well:**
- Architecture specification is complete and internally consistent
- All 9 JSON Schemas + OpenAPI spec are valid JSON Schema draft 2020-12
- 1057 tests pass with zero TODO/FIXME/HACK comments
- CI pipeline is green
- 12+ docs totaling thousands of lines, all cross-referenced
- Deterministic routing algorithm correctly implements lexicographic constraint-first optimization
- Epistemic governance (four-valued adjudication, evidence ledger, verification DAG) correctly implemented
- Zero hardcoded secrets

**Critical gaps (for Go production phase — NOT this polish pass):**
- No Go production control plane (Python is conformance oracle)
- No PostgreSQL persistence (all state is in-memory)
- No protobuf contract lineage
- No A2A/MCP/AG-UI implementation
- No tool sandbox

**Gaps addressable in this polish pass:**
- Docker production hardening (non-root user already done; multi-stage already done)
- K8s manifests completeness (Ingress, HPA, NetworkPolicy, PDB, ServiceMonitor already exist)
- Documentation polish and accuracy verification
- Integration examples for client tools
- Open WebUI docker-compose sidecar
- CHANGELOG.md creation
- Test coverage for any untested code paths

### What the Python Reference Does Well
The Python reference kernel is **maximally complete** as a conformance oracle. It demonstrates:
- Full OpenAI-compatible API surface (62+ endpoints)
- Deterministic routing with hard constraint filtering
- Four-valued epistemic adjudication
- Hash-chained evidence ledger
- RTK compression Phases 1-4
- Multi-tenancy with RBAC, rate limiting, quotas
- Dashboard, metrics, analytics
- Graceful shutdown with request draining
- Structured JSON logging
- Prometheus metrics

---

## 4. Objectives for Codex

### A. Code Quality & Polish

#### A.1 Review Every Python File in [`reference/gateway/`](reference/gateway/)

Perform a systematic review of all 50+ modules. For each file:

1. **Read the entire file** — understand its purpose and how it fits into the pipeline
2. **Check for edge cases:**
   - Empty inputs (empty strings, empty lists, empty dicts)
   - Very large inputs (long messages, many messages, large JSON bodies)
   - Unicode and special characters
   - Concurrent access patterns (the server uses `ThreadingHTTPServer`)
   - Network timeouts and connection errors
   - Malformed JSON
   - Missing required fields
3. **Verify error handling:**
   - Every `try/except` should catch specific exceptions, not bare `except:`
   - Error responses should use [`render.error_envelope()`](reference/gateway/render.py) consistently
   - Error messages should not leak internal state or secrets
   - HTTP status codes should be appropriate (400 for bad request, 401 for auth, 403 for forbidden, 404 for not found, 422 for validation, 424 for no route, 500 for internal)
4. **Check type hints:**
   - All function signatures should have complete type annotations
   - Use `from __future__ import annotations` consistently
   - Generic types should be properly parameterized
5. **Check for race conditions:**
   - The server is multi-threaded ([`ThreadingHTTPServer`](reference/gateway/server.py:15))
   - Shared state (cache, ledger, profiler, config) must be thread-safe
   - Look for non-atomic read-modify-write operations
   - Check that locks are used consistently
6. **Verify logging:**
   - All log messages should use [`structured_logging.py`](reference/gateway/structured_logging.py)
   - Log levels should be appropriate (DEBUG for detailed, INFO for normal, WARNING for issues, ERROR for failures)
   - No secrets in log messages
7. **Check for memory leaks:**
   - The cache in [`cache.py`](reference/gateway/cache.py) should have size limits
   - The compression profiler in [`compression_profiler.py`](reference/gateway/compression_profiler.py) should not grow unbounded
   - The ledger in [`epistemic_ledger.py`](reference/gateway/epistemic_ledger.py) should have configurable retention
   - Any circular references that could prevent garbage collection

#### A.2 Specific Files Requiring Extra Attention

| File | Special Concerns |
|---|---|
| [`server.py`](reference/gateway/server.py) | Route dispatch completeness, graceful shutdown, error handling in all routes |
| [`pipeline.py`](reference/gateway/pipeline.py) | Pipeline stage ordering, error propagation, timeout handling |
| [`handlers.py`](reference/gateway/handlers.py) | Input validation, error responses, consistency across all 62+ handlers |
| [`auth.py`](reference/gateway/auth.py) | API key validation timing attacks, RBAC enforcement |
| [`rate_limit.py`](reference/gateway/rate_limit.py) | Token bucket correctness, race conditions under concurrency |
| [`streaming.py`](reference/gateway/streaming.py) | SSE format correctness, connection cleanup, error during stream |
| [`compression.py`](reference/gateway/compression.py) | Edge cases in dedup/prune/summarize, large context handling |
| [`openrouter.py`](reference/gateway/openrouter.py) | Error handling for upstream failures, timeout, retry logic |
| [`local_models.py`](reference/gateway/local_models.py) | Ollama API compatibility, error handling when Ollama is down |
| [`database.py`](reference/gateway/database.py) | SQLite thread safety, connection management, migration handling |

#### A.3 Code Style Consistency

- All files should use the same import style (absolute imports from `gateway.`)
- Docstrings should follow a consistent format
- Line length should be reasonable (prefer ≤100 characters)
- No dead code or commented-out blocks
- No `print()` statements (use logging instead)
- No `TODO`, `FIXME`, `HACK`, `XXX` comments (currently zero — maintain this)

---

### B. API Completeness

#### B.1 Verify OpenAPI Specification Coverage

Cross-reference every endpoint in [`spec/openapi.json`](spec/openapi.json) against the route table in [`server.py`](reference/gateway/server.py). The OpenAPI spec defines these public endpoints:

| Endpoint | Method | Handler |
|---|---|---|
| `/v1/models` | GET | [`handle_list_models`](reference/gateway/server.py:55) |
| `/v1/chat/completions` | POST | [`handle_chat_completions`](reference/gateway/server.py:27) |
| `/v1/responses` | POST | [`handle_responses`](reference/gateway/server.py:69) |
| `/v1/epr/runs/{run_id}` | GET | [`handle_get_run`](reference/gateway/server.py:42) |

Verify that:
1. Each endpoint returns the documented response shape
2. Error responses match the documented error codes (422, 424, 404)
3. The `epr` metadata block is present in responses when governance is enabled
4. Streaming responses use proper SSE format (`text/event-stream`)

#### B.2 OpenAI Parameter Compatibility

The [`/v1/chat/completions`](reference/gateway/server.py:479) endpoint must handle all standard OpenAI parameters:

| Parameter | Type | Required | Status |
|---|---|---|---|
| `model` | string | Yes | Must be `noerelay/epr-1` or a known model |
| `messages` | array | Yes | Role/content message list |
| `temperature` | number | No | Sampling temperature (0-2) |
| `top_p` | number | No | Nucleus sampling |
| `max_tokens` | integer | No | Maximum completion tokens |
| `stop` | string/array | No | Stop sequences |
| `stream` | boolean | No | SSE streaming |
| `n` | integer | No | Number of completions |
| `presence_penalty` | number | No | Presence penalty (-2 to 2) |
| `frequency_penalty` | number | No | Frequency penalty (-2 to 2) |
| `logit_bias` | object | No | Token logit bias |
| `user` | string | No | End-user identifier |

Verify that:
1. All parameters are accepted without error
2. Parameters are passed through to the upstream model (OpenRouter/Ollama)
3. Unknown parameters are ignored (not rejected) for forward compatibility
4. The `governance` extension object is properly handled

#### B.3 Model List Endpoint

[`GET /v1/models`](reference/gateway/server.py:246) must return:
```json
{
  "object": "list",
  "data": [
    {
      "id": "noerelay/epr-1",
      "object": "model",
      "created": 1700000000,
      "owned_by": "electrohire"
    }
  ]
}
```

Verify that:
1. The response shape matches OpenAI's format exactly
2. The `noerelay/epr-1` virtual model is always present
3. Additional models from the portfolio are included when available

#### B.4 Streaming Verification

Streaming must:
1. Set `Content-Type: text/event-stream`
2. Send `data: {...}\n\n` formatted chunks
3. Include `[DONE]` as the final message
4. Include EPR metadata in the terminal chunk (run_id, receipt_id, chain_hash)
5. Handle client disconnection gracefully
6. Not buffer the entire response before sending

#### B.5 Error Response Format

All error responses must follow the OpenAI error format:
```json
{
  "error": {
    "message": "Human-readable error description",
    "type": "invalid_request_error",
    "code": "invalid_model"
  }
}
```

Error types should map appropriately:
- `invalid_request_error` — malformed request, missing fields
- `authentication_error` — invalid or missing API key
- `rate_limit_error` — rate limit exceeded
- `server_error` — internal server error

---

### C. Security Hardening

#### C.1 Authentication Review

Review [`auth.py`](reference/gateway/auth.py):
1. API key validation should use constant-time comparison to prevent timing attacks
2. API keys should be stored hashed (SHA-256 minimum), not in plaintext
3. The `Authorization: Bearer <key>` header should be parsed correctly
4. Auth should be enabled by default when binding to non-loopback addresses
5. Failed auth attempts should be logged (without the key value)

#### C.2 Rate Limiting Review

Review [`rate_limit.py`](reference/gateway/rate_limit.py):
1. Token bucket algorithm should be correct (no off-by-one errors)
2. Rate limit state should be per-API-key, not global
3. Rate limit headers should be included in responses:
   - `X-RateLimit-Limit`
   - `X-RateLimit-Remaining`
   - `X-RateLimit-Reset`
4. Rate limit should not be bypassable via concurrent requests
5. Default rate limits should be reasonable (e.g., 60 req/min for chat, 1000 req/min for models)

#### C.3 CORS Headers

Verify CORS headers in [`server.py`](reference/gateway/server.py):
1. `Access-Control-Allow-Origin` should be configurable, not wildcard in production
2. `Access-Control-Allow-Methods` should list only needed methods
3. `Access-Control-Allow-Headers` should include `Authorization`, `Content-Type`
4. OPTIONS preflight requests should be handled

#### C.4 Sensitive Data Protection

1. No API keys, tokens, or secrets in error responses
2. No internal paths or stack traces in error responses (except in debug mode)
3. Structured logs should redact sensitive fields
4. The `.env` file should be in `.gitignore` (verify)
5. No credentials in test fixtures or example files

#### C.5 Injection Vulnerability Check

1. **Command injection:** No user input should be passed to `os.system()`, `subprocess`, or similar
2. **Path traversal:** File paths from user input should be validated/sanitized
3. **SQL injection:** SQLite queries should use parameterized statements
4. **JSON injection:** JSON parsing should handle malicious inputs gracefully
5. **Header injection:** Response headers should not contain user-controlled newlines

#### C.6 Threat Model Verification

Review [`docs/threat-model.md`](docs/threat-model.md) and verify all 10 identified threats have mitigations implemented:

1. **T-001: API Key Theft** — Keys stored in SQLite; verify hashing
2. **T-002: Unauthorized Cross-Tenant Access** — Verify tenant isolation in all handlers
3. **T-003: Rate Limit Bypass** — Verify token bucket correctness
4. **T-004: Model Output Injection** — Verify model outputs are treated as proposals
5. **T-005: Ledger Tampering** — Verify hash chain integrity checks
6. **T-006: Verification Bypass** — Verify DAG execution order
7. **T-007: Denial of Service** — Verify request size limits, timeout handling
8. **T-008: Information Disclosure** — Verify error message sanitization
9. **T-009: SQLite File Access** — Verify file permissions
10. **T-010: Dependency Confusion** — N/A (zero dependencies)

---

### D. Test Coverage

#### D.1 Run the Full Test Suite

```powershell
cd c:/Users/trist/Development/ElectroHire/norelay
python -m unittest discover -s tests -v
```

All 1057 tests must pass. If any fail:
1. Identify the root cause
2. Fix the code (not the test, unless the test is wrong)
3. Re-run until all pass

#### D.2 Identify Untested Code Paths

1. Run with coverage:
   ```powershell
   python -m pip install coverage
   python -m coverage run -m unittest discover -s tests -v
   python -m coverage report -m
   python -m coverage html
   ```
2. Review the coverage report for any modules below 80% coverage
3. Add tests for untested branches, especially:
   - Error handling paths
   - Edge cases (empty inputs, large inputs, concurrent access)
   - Streaming error scenarios
   - Rate limiting edge cases
   - Auth failure scenarios

#### D.3 Add Integration Tests

Create or enhance tests in [`tests/test_integration.py`](tests/test_integration.py):
1. Full request → response lifecycle with the HTTP server
2. Streaming response end-to-end
3. Multi-tenant isolation (tenant A cannot see tenant B's runs)
4. API key creation → usage → revocation lifecycle
5. Rate limit exhaustion and recovery

#### D.4 Add OpenAI Client Compatibility Tests

Create tests that use the OpenAI Python SDK to verify compatibility:
1. `client.models.list()` returns `noerelay/epr-1`
2. `client.chat.completions.create()` works with all standard parameters
3. Streaming via `stream=True` works correctly
4. Error responses are properly deserialized by the SDK

#### D.5 Add Load/Stress Tests

Create basic load tests:
1. Concurrent request handling (10, 50, 100 simultaneous requests)
2. Large payload handling (messages with 100k+ tokens)
3. Sustained load over 5 minutes
4. Rate limit behavior under load

---

### E. Documentation

#### E.1 Review All Existing Docs

Read and verify every document in [`docs/`](docs/):

| Document | What to Check |
|---|---|
| [`README.md`](README.md) | Quick start, installation, configuration, usage, API reference, architecture overview all present and accurate |
| [`docs/architecture.md`](docs/architecture.md) | All EPR-* requirements match implementation |
| [`docs/api-reference.md`](docs/api-reference.md) | All 62+ endpoints match [`server.py`](reference/gateway/server.py) routes |
| [`docs/integration-guide.md`](docs/integration-guide.md) | All integration examples are correct and tested |
| [`docs/deployment.md`](docs/deployment.md) | Docker, K8s, bare metal instructions are accurate |
| [`docs/admin-guide.md`](docs/admin-guide.md) | Backup/restore, tenant management, alerts, webhooks instructions work |
| [`docs/threat-model.md`](docs/threat-model.md) | All mitigations are implemented |
| [`docs/gap-analysis.md`](docs/gap-analysis.md) | Assessment is current and accurate |
| [`docs/gateway.md`](docs/gateway.md) | Compatibility profile is accurate |
| [`docs/environment.md`](docs/environment.md) | Environment variable docs match [`.env.example`](.env.example) |
| [`docs/benchmarking.md`](docs/benchmarking.md) | Benchmark instructions work |
| [`docs/runbooks.md`](docs/runbooks.md) | Runbooks are actionable |
| [`docs/continuation-handoff.md`](docs/continuation-handoff.md) | Current state is accurate |
| [`docs/product-completion-plan.md`](docs/product-completion-plan.md) | Plan is current |
| [`docs/research-basis.md`](docs/research-basis.md) | Research references are accurate |

#### E.2 Fix Any Inaccuracies

For each document:
1. Verify every file path reference is correct
2. Verify every code example runs (or clearly marks as illustrative)
3. Verify every command works on Windows (PowerShell) and Linux (bash)
4. Update any outdated information
5. Fix any broken cross-references

#### E.3 Create Missing Documentation

If any of these are missing, create them:
- [`docs/quickstart.md`](docs/quickstart.md) — 5-minute quick start guide
- [`docs/production-deployment.md`](docs/production-deployment.md) — Production deployment guide
- [`docs/open-webui-setup.md`](docs/open-webui-setup.md) — Step-by-step Open WebUI setup (create this regardless)

#### E.4 Ensure README.md Has Everything

[`README.md`](README.md) must include:
- [x] Project description and purpose
- [x] Architecture diagram (Mermaid)
- [x] Quick start instructions
- [x] Installation requirements
- [x] Configuration reference
- [x] API compatibility table
- [x] Repository structure overview
- [x] Test instructions
- [x] Security/disclosure information
- [x] License information
- [ ] Badges (CI status, version, license) — add if missing
- [ ] Link to CHANGELOG.md — add after creating

---

### F. Open WebUI Integration

#### F.1 Create Docker Compose Override

Create [`docker-compose.openwebui.yml`](docker-compose.openwebui.yml) that adds Open WebUI as a sidecar to the existing [`docker-compose.yml`](docker-compose.yml). Requirements:

1. Use the official Open WebUI image: `ghcr.io/open-webui/open-webui:main`
2. Configure Open WebUI to connect to NoeRelay:
   - `OPENAI_API_BASE_URL=http://noerelay:8080/v1`
   - `OPENAI_API_KEY=${NOERELAY_API_KEY:-noerelay}`
3. Expose Open WebUI on port 3000
4. Add healthcheck for Open WebUI
5. Add dependency on the `noerelay` service
6. Mount a volume for Open WebUI data persistence

#### F.2 Create Open WebUI Setup Guide

Create [`docs/open-webui-setup.md`](docs/open-webui-setup.md) with:

1. **Prerequisites:** Docker and Docker Compose installed
2. **Quick Start:**
   ```bash
   docker-compose -f docker-compose.yml -f docker-compose.openwebui.yml up -d
   ```
3. **Access:** Open `http://localhost:3000`
4. **Configuration:**
   - Create an admin account on first access
   - Go to Settings → Connections → OpenAI API
   - The connection should be pre-configured via environment variables
   - If manual: Base URL = `http://noerelay:8080/v1`, API Key = any value
5. **Verification:**
   - The model `noerelay/epr-1` should appear in the model dropdown
   - Send a test message and verify the response
   - Check that EPR metadata is visible in the response
6. **Troubleshooting:**
   - Connection refused → ensure NoeRelay is healthy
   - Model not found → check NoeRelay logs
   - Auth errors → verify API key configuration
7. **Architecture Diagram:** Show how Open WebUI → NoeRelay → OpenRouter/Ollama

#### F.3 Test Open WebUI Integration

After creating the compose file:
1. Start the stack: `docker-compose -f docker-compose.yml -f docker-compose.openwebui.yml up -d`
2. Verify NoeRelay health: `curl http://localhost:8080/health`
3. Verify Open WebUI is accessible: `curl http://localhost:3000`
4. Verify model listing works through Open WebUI
5. Verify chat completion works through Open WebUI
6. Verify EPR metadata is included in responses

---

### G. Other Client Tool Integrations

#### G.1 Ollama (as Model Provider)

Document in [`docs/integration-guide.md`](docs/integration-guide.md):

```bash
# Start Ollama
ollama serve
# Pull a model
ollama pull qwen3:8b
# Start NoeRelay (routes to Ollama at localhost:11434)
cd reference
$env:NOERELAY_OPENROUTER_MODE='live'
python -m gateway
# Now any OpenAI-compatible client can point at http://localhost:8080/v1
```

Verify:
1. NoeRelay discovers Ollama models automatically
2. Requests are routed to Ollama when appropriate
3. Ollama unavailability triggers fallback to OpenRouter

#### G.2 OpenAI Python SDK

Create [`examples/openai-sdk-test.py`](examples/openai-sdk-test.py):

```python
"""Test NoeRelay compatibility with the OpenAI Python SDK.

Usage:
    pip install openai
    python examples/openai-sdk-test.py
"""
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8080/v1",
    api_key="any-value"  # or your NoeRelay API key
)

# List models
models = client.models.list()
print(f"Models: {[m.id for m in models.data]}")

# Chat completion (non-streaming)
response = client.chat.completions.create(
    model="noerelay/epr-1",
    messages=[{"role": "user", "content": "Hello! What is 2+2?"}],
    temperature=0.7,
    max_tokens=100
)
print(f"Response: {response.choices[0].message.content}")
print(f"Model: {response.model}")
print(f"Usage: {response.usage}")

# Chat completion (streaming)
stream = client.chat.completions.create(
    model="noerelay/epr-1",
    messages=[{"role": "user", "content": "Count from 1 to 5."}],
    stream=True
)
print("Streaming: ", end="")
for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
print()
```

#### G.3 LangChain

Create [`examples/langchain-test.py`](examples/langchain-test.py):

```python
"""Test NoeRelay compatibility with LangChain.

Usage:
    pip install langchain-openai
    python examples/langchain-test.py
"""
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    openai_api_base="http://127.0.0.1:8080/v1",
    openai_api_key="any-value",
    model_name="noerelay/epr-1",
    temperature=0.7,
)

# Simple invocation
response = llm.invoke("What is the capital of France?")
print(f"Response: {response.content}")

# Streaming
for chunk in llm.stream("Count from 1 to 5."):
    print(chunk.content, end="")
print()
```

#### G.4 LlamaIndex

Document in [`docs/integration-guide.md`](docs/integration-guide.md):

```python
from llama_index.llms.openai import OpenAI

llm = OpenAI(
    api_base="http://127.0.0.1:8080/v1",
    api_key="any-value",
    model="noerelay/epr-1",
)
```

#### G.5 Direct curl

Create [`examples/curl-test.sh`](examples/curl-test.sh):

```bash
#!/usr/bin/env bash
# Test NoeRelay with curl
# Usage: bash examples/curl-test.sh

BASE_URL="${NOERELAY_BASE_URL:-http://127.0.0.1:8080}"

echo "=== Health Check ==="
curl -s "$BASE_URL/health" | python -m json.tool

echo ""
echo "=== List Models ==="
curl -s "$BASE_URL/v1/models" | python -m json.tool

echo ""
echo "=== Chat Completion ==="
curl -s -X POST "$BASE_URL/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer any-value" \
  -d '{
    "model": "noerelay/epr-1",
    "messages": [{"role": "user", "content": "Hello! What is 2+2?"}],
    "temperature": 0.7,
    "max_tokens": 100
  }' | python -m json.tool

echo ""
echo "=== Streaming Chat Completion ==="
curl -s -X POST "$BASE_URL/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer any-value" \
  -d '{
    "model": "noerelay/epr-1",
    "messages": [{"role": "user", "content": "Count from 1 to 5."}],
    "stream": true
  }'

echo ""
echo "=== All tests complete ==="
```

#### G.6 Create Integration Example Scripts

All example scripts should:
1. Be self-contained with clear usage instructions
2. Handle errors gracefully (connection refused, auth failure, etc.)
3. Print informative output
4. Work on Windows (PowerShell) and Linux/macOS (bash)
5. Document required dependencies

---

### H. Deployment Readiness

#### H.1 Dockerfile Review

Review [`Dockerfile`](Dockerfile):
1. ✅ Multi-stage build (builder + runtime)
2. ✅ Non-root user (`noerelay`)
3. ✅ Health check configured
4. ✅ Zero pip install in production image
5. ⚠️ Verify the `PYTHONPATH` is correct for the gateway module
6. ⚠️ Verify all needed files are copied (scripts/, spec/, examples/)
7. ⚠️ Consider adding `--chown=noerelay:noerelay` to COPY commands
8. ⚠️ Verify the CMD works correctly

#### H.2 Docker Compose Review

Review [`docker-compose.yml`](docker-compose.yml):
1. ✅ NoeRelay service with healthcheck
2. ✅ Ollama sidecar with GPU support
3. ✅ Volume mounts for data persistence
4. ✅ Environment variable configuration
5. ⚠️ Verify the healthcheck command works inside the container
6. ⚠️ Verify volume paths are correct

#### H.3 Kubernetes Manifests Review

Review all files in [`deploy/kubernetes/`](deploy/kubernetes/):

| File | What to Check |
|---|---|
| [`deployment.yaml`](deploy/kubernetes/deployment.yaml) | Resource limits, health probes, secret refs, replica count |
| [`service.yaml`](deploy/kubernetes/service.yaml) | Port mapping, selector labels |
| [`configmap.yaml`](deploy/kubernetes/configmap.yaml) | Non-secret config values |
| [`secret.yaml`](deploy/kubernetes/secret.yaml) | Secret keys (no actual values) |
| [`ingress.yaml`](deploy/kubernetes/ingress.yaml) | TLS config, host rules |
| [`hpa.yaml`](deploy/kubernetes/hpa.yaml) | Min/max replicas, CPU/memory targets |
| [`networkpolicy.yaml`](deploy/kubernetes/networkpolicy.yaml) | Ingress/egress rules |
| [`pdb.yaml`](deploy/kubernetes/pdb.yaml) | Min available, max unavailable |
| [`pvc.yaml`](deploy/kubernetes/pvc.yaml) | Storage size, access mode |
| [`servicemonitor.yaml`](deploy/kubernetes/servicemonitor.yaml) | Prometheus scrape config |

#### H.4 Environment Variables

Review [`.env.example`](.env.example):
1. All required variables are documented
2. Default values are provided where sensible
3. Secret variables are clearly marked
4. No actual secret values are present

#### H.5 Create Production Deployment Guide

Create [`docs/production-deployment.md`](docs/production-deployment.md) if missing, covering:
1. Docker deployment (single container)
2. Docker Compose deployment (with Ollama + Open WebUI)
3. Kubernetes deployment (using the provided manifests)
4. TLS configuration
5. Backup and restore procedures
6. Monitoring and alerting setup
7. Scaling considerations
8. Security hardening checklist

---

### I. Commercial Readiness Checklist

#### I.1 License

- [ ] [`LICENSE`](LICENSE) file exists and is correct
- [ ] Copyright notice is current (© 2026 ElectroHire)
- [ ] License type is clearly stated

#### I.2 Contributing Guide

- [ ] [`CONTRIBUTING.md`](CONTRIBUTING.md) is complete
- [ ] Development workflow is documented
- [ ] Normative change requirements are clear
- [ ] Commit and review expectations are stated

#### I.3 Security Policy

- [ ] [`SECURITY.md`](SECURITY.md) is complete
- [ ] Supported versions are stated
- [ ] Vulnerability reporting process is documented
- [ ] Security controls are listed
- [ ] Responsible disclosure timeline is defined

#### I.4 README

- [ ] Professional and comprehensive
- [ ] Badges for CI status, version, license
- [ ] Clear value proposition
- [ ] Quick start that works
- [ ] Architecture overview
- [ ] API reference
- [ ] Link to full documentation

#### I.5 Version Numbering

- [ ] Version in [`README.md`](README.md) is correct (`0.1.0-draft`)
- [ ] Version in [`spec/openapi.json`](spec/openapi.json) is correct (`0.1.0`)
- [ ] Version in [`docs/threat-model.md`](docs/threat-model.md) is correct
- [ ] Version in [`reference/gateway/__init__.py`](reference/gateway/__init__.py) is correct

#### I.6 Changelog

- [ ] [`CHANGELOG.md`](CHANGELOG.md) exists
- [ ] Documents all notable changes
- [ ] Follows [Keep a Changelog](https://keepachangelog.com/) format
- [ ] Version 0.1.0-draft entry is complete

#### I.7 Release Notes Template

- [ ] Release notes template exists or is documented in CONTRIBUTING.md
- [ ] Template includes: version, date, changes, migration notes, known issues

---

## 5. How to Test NoeRelay with Client Tools

### Open WebUI

```bash
# Start NoeRelay + Open WebUI with docker-compose
docker-compose -f docker-compose.yml -f docker-compose.openwebui.yml up -d

# Check health
curl http://localhost:8080/health

# Open http://localhost:3000
# Create admin account on first access
# Settings → Connections → OpenAI API
# Base URL: http://noerelay:8080/v1
# API Key: any-value (or configure NOERELAY_AUTH_API_KEYS)
# Model: noerelay/epr-1
```

### Ollama (as Model Provider)

```bash
# Start Ollama
ollama serve

# Pull a model
ollama pull qwen3:8b

# Start NoeRelay (it will route to Ollama at localhost:11434)
cd reference
$env:NOERELAY_OPENROUTER_MODE='live'
python -m gateway

# Now any OpenAI-compatible client can point at http://localhost:8080/v1
```

### Direct curl

```bash
# Health check
curl http://127.0.0.1:8080/health

# List models
curl http://127.0.0.1:8080/v1/models

# Chat completion
curl -X POST http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"noerelay/epr-1","messages":[{"role":"user","content":"Hello"}]}'

# Streaming chat completion
curl -X POST http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"noerelay/epr-1","messages":[{"role":"user","content":"Hello"}],"stream":true}'
```

### OpenAI Python SDK

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8080/v1", api_key="any")

# List models
models = client.models.list()
print([m.id for m in models.data])

# Chat completion
response = client.chat.completions.create(
    model="noerelay/epr-1",
    messages=[{"role": "user", "content": "Hello"}]
)
print(response.choices[0].message.content)

# Streaming
stream = client.chat.completions.create(
    model="noerelay/epr-1",
    messages=[{"role": "user", "content": "Hello"}],
    stream=True
)
for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

### LangChain

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    openai_api_base="http://127.0.0.1:8080/v1",
    openai_api_key="any",
    model_name="noerelay/epr-1"
)

response = llm.invoke("Hello!")
print(response.content)
```

### LlamaIndex

```python
from llama_index.llms.openai import OpenAI

llm = OpenAI(
    api_base="http://127.0.0.1:8080/v1",
    api_key="any",
    model="noerelay/epr-1"
)
```

---

## 6. Key Files to Review

### Tier 1 — Core Gateway (Must Review Every Line)

| File | Lines | Review Focus |
|---|---|---|
| [`reference/gateway/server.py`](reference/gateway/server.py) | 872 | All routes, error handling, graceful shutdown, CORS, streaming dispatch |
| [`reference/gateway/handlers.py`](reference/gateway/handlers.py) | ~2000+ | Input validation, error responses, consistency across all 62+ handlers |
| [`reference/gateway/pipeline.py`](reference/gateway/pipeline.py) | ~500+ | Pipeline stages, compression integration, timeout handling, error propagation |
| [`reference/gateway/config.py`](reference/gateway/config.py) | ~200+ | All config options, env var parsing, defaults |
| [`reference/gateway/auth.py`](reference/gateway/auth.py) | ~150+ | API key validation, RBAC, constant-time comparison |
| [`reference/gateway/rate_limit.py`](reference/gateway/rate_limit.py) | ~100+ | Token bucket, concurrency safety, headers |
| [`reference/gateway/streaming.py`](reference/gateway/streaming.py) | ~200+ | SSE format, connection cleanup, EPR metadata in terminal chunk |
| [`reference/gateway/render.py`](reference/gateway/render.py) | ~100+ | Error envelopes, response formatting |

### Tier 2 — Routing & Models

| File | Review Focus |
|---|---|
| [`reference/gateway/policy.py`](reference/gateway/policy.py) | Routing policy enforcement, constraint filtering |
| [`reference/gateway/portfolio.py`](reference/gateway/portfolio.py) | Model portfolio management |
| [`reference/gateway/openrouter.py`](reference/gateway/openrouter.py) | OpenRouter integration, error handling, retry |
| [`reference/gateway/local_models.py`](reference/gateway/local_models.py) | Ollama integration, model discovery |
| [`reference/gateway/fallback.py`](reference/gateway/fallback.py) | Provider and semantic fallback |
| [`reference/gateway/cost_model.py`](reference/gateway/cost_model.py) | Cost estimation |
| [`reference/gateway/cost_controls.py`](reference/gateway/cost_controls.py) | Cost cap enforcement |

### Tier 3 — Governance & EPR

| File | Review Focus |
|---|---|
| [`reference/gateway/governance.py`](reference/gateway/governance.py) | Risk classification, governance validation |
| [`reference/gateway/contracts.py`](reference/gateway/contracts.py) | Task contract compilation |
| [`reference/gateway/verification.py`](reference/gateway/verification.py) | Verification DAG |
| [`reference/gateway/epistemic.py`](reference/gateway/epistemic.py) | Four-valued adjudication |
| [`reference/gateway/epistemic_ledger.py`](reference/gateway/epistemic_ledger.py) | Hash-chained ledger |
| [`reference/gateway/context.py`](reference/gateway/context.py) | L0-L3 memory model |
| [`reference/gateway/provenance.py`](reference/gateway/provenance.py) | W3C PROV mapping |

### Tier 4 — Operations & Infrastructure

| File | Review Focus |
|---|---|
| [`reference/gateway/database.py`](reference/gateway/database.py) | SQLite thread safety, migrations |
| [`reference/gateway/cache.py`](reference/gateway/cache.py) | Cache size limits, eviction |
| [`reference/gateway/compression.py`](reference/gateway/compression.py) | All compression strategies |
| [`reference/gateway/compression_cache.py`](reference/gateway/compression_cache.py) | Compression caching |
| [`reference/gateway/compression_profiler.py`](reference/gateway/compression_profiler.py) | Profiler memory bounds |
| [`reference/gateway/dashboard.py`](reference/gateway/dashboard.py) | Dashboard HTML and data |
| [`reference/gateway/analytics.py`](reference/gateway/analytics.py) | Analytics queries |
| [`reference/gateway/prometheus.py`](reference/gateway/prometheus.py) | Metrics endpoint |
| [`reference/gateway/structured_logging.py`](reference/gateway/structured_logging.py) | JSON log format |
| [`reference/gateway/alerting.py`](reference/gateway/alerting.py) | Alert rules and dispatch |
| [`reference/gateway/audit.py`](reference/gateway/audit.py) | Audit logging |
| [`reference/gateway/tenancy.py`](reference/gateway/tenancy.py) | Tenant isolation, budgets |
| [`reference/gateway/rbac.py`](reference/gateway/rbac.py) | Role definitions and checks |
| [`reference/gateway/secrets.py`](reference/gateway/secrets.py) | Secret storage |
| [`reference/gateway/webhooks.py`](reference/gateway/webhooks.py) | Webhook dispatch |
| [`reference/gateway/model_lifecycle.py`](reference/gateway/model_lifecycle.py) | Model pull/remove/recommend |

### Tier 5 — Specifications & Tests

| File | Review Focus |
|---|---|
| [`spec/openapi.json`](spec/openapi.json) | API specification completeness |
| [`spec/routing-policy.json`](spec/routing-policy.json) | Routing policy spec |
| [`spec/verification-state-machine.json`](spec/verification-state-machine.json) | State machine spec |
| All files in [`spec/schemas/`](spec/schemas/) | Schema validation |
| All files in [`tests/`](tests/) | Test coverage, correctness |

### Tier 6 — Deployment & Config

| File | Review Focus |
|---|---|
| [`Dockerfile`](Dockerfile) | Production readiness |
| [`docker-compose.yml`](docker-compose.yml) | Service configuration |
| [`.env.example`](.env.example) | Environment variable docs |
| [`.dockerignore`](.dockerignore) | Build context optimization |
| [`.gitignore`](.gitignore) | Sensitive file exclusion |
| All files in [`deploy/kubernetes/`](deploy/kubernetes/) | K8s manifest completeness |
| All files in [`.github/workflows/`](.github/workflows/) | CI correctness |

---

## 7. Acceptance Criteria

### Must Pass (Blocking)

- [ ] All 1057+ tests pass: `python -m unittest discover -s tests -v`
- [ ] CI is green (Conformance + CI + Docker workflows)
- [ ] No new TODO/FIXME/HACK/XXX comments introduced
- [ ] No security vulnerabilities found (or all found are fixed)
- [ ] Docker image builds: `docker build -t noerelay .`
- [ ] Docker image runs: `docker run --rm -p 8080:8080 noerelay`
- [ ] Health endpoint responds: `curl http://localhost:8080/health`
- [ ] Models endpoint responds: `curl http://localhost:8080/v1/models`
- [ ] Chat completions endpoint responds: `curl -X POST http://localhost:8080/v1/chat/completions -H "Content-Type: application/json" -d '{"model":"noerelay/epr-1","messages":[{"role":"user","content":"Hello"}]}'`
- [ ] Gateway works in stub mode (no API key needed)
- [ ] Gateway works in live mode with OpenRouter API key

### Should Pass (Important)

- [ ] Open WebUI can connect and chat through NoeRelay
- [ ] Ollama can be used as a model provider
- [ ] OpenAI Python SDK can connect and use all standard parameters
- [ ] LangChain can connect and chat
- [ ] Streaming responses work correctly (SSE format)
- [ ] Error responses follow OpenAI format
- [ ] All docs are accurate and cross-references work
- [ ] [`CHANGELOG.md`](CHANGELOG.md) exists and is current
- [ ] [`docs/open-webui-setup.md`](docs/open-webui-setup.md) is complete
- [ ] All example scripts in [`examples/`](examples/) work
- [ ] docker-compose with Open WebUI sidecar works
- [ ] README is professional with badges

### Nice to Have

- [ ] Test coverage above 85% for all gateway modules
- [ ] Load test results documented
- [ ] Performance benchmarks documented
- [ ] All K8s manifests validated

---

## 8. Things NOT to Do

### DO NOT:

1. **Start the Go rewrite** — The Go production control plane is a separate phase. This polish pass is Python-only.
2. **Change the architecture** — The gateway is pure Python stdlib `ThreadingHTTPServer`. Do not introduce frameworks (FastAPI, Flask, etc.).
3. **Add external dependencies** — The gateway is dependency-free. Do not add `pip install` requirements.
4. **Remove any existing tests** — Only add tests, never remove. If a test is wrong, fix the code.
5. **Change the EPR ledger format** — The hash-chain format must remain backward-compatible.
6. **Change the OpenAI-compatible API contract** — The request/response shapes must match OpenAI's format.
7. **Add OpenAI model support** — The gateway explicitly blocks `openai` family and `openai/` namespace. Do not weaken this.
8. **Change the default bind address** — `127.0.0.1` (loopback-only) is the safe default.
9. **Commit credentials or secrets** — No API keys, tokens, or passwords in any file.
10. **Modify the product completion plan** — [`docs/product-completion-plan.md`](docs/product-completion-plan.md) is the authoritative plan.

---

## 9. Quick Reference: Commands & Paths

### Running Tests

```powershell
# All tests
python -m unittest discover -s tests -v

# Specific test file
python -m unittest tests.test_gateway -v

# With coverage
python -m pip install coverage
python -m coverage run -m unittest discover -s tests -v
python -m coverage report -m
```

### Running the Gateway

```powershell
# Stub mode (no API keys needed)
cd reference
$env:NOERELAY_OPENROUTER_MODE='stub'
python -m gateway

# Live mode (needs OPENROUTER_API_KEY)
cd reference
$env:NOERELAY_OPENROUTER_MODE='live'
python -m gateway

# With dashboard
scripts/start-dashboard.cmd stub
scripts/start-dashboard.cmd live
```

### Docker

```powershell
# Build
docker build -t noerelay .

# Run
docker run --rm -p 8080:8080 noerelay

# Compose (NoeRelay + Ollama)
docker-compose up -d

# Compose (NoeRelay + Ollama + Open WebUI)
docker-compose -f docker-compose.yml -f docker-compose.openwebui.yml up -d
```

### Quick Smoke Test

```powershell
# Health
curl http://127.0.0.1:8080/health

# Models
curl http://127.0.0.1:8080/v1/models

# Chat
curl -X POST http://127.0.0.1:8080/v1/chat/completions `
  -H "Content-Type: application/json" `
  -d '{"model":"noerelay/epr-1","messages":[{"role":"user","content":"Hello"}]}'
```

### Key File Paths

| What | Path |
|---|---|
| Gateway entry point | [`reference/gateway/__main__.py`](reference/gateway/__main__.py) |
| Server routes | [`reference/gateway/server.py`](reference/gateway/server.py) |
| Request handlers | [`reference/gateway/handlers.py`](reference/gateway/handlers.py) |
| Pipeline | [`reference/gateway/pipeline.py`](reference/gateway/pipeline.py) |
| Configuration | [`reference/gateway/config.py`](reference/gateway/config.py) |
| OpenAPI spec | [`spec/openapi.json`](spec/openapi.json) |
| Routing policy | [`spec/routing-policy.json`](spec/routing-policy.json) |
| Test suite | [`tests/`](tests/) |
| Documentation | [`docs/`](docs/) |
| Dockerfile | [`Dockerfile`](Dockerfile) |
| Docker Compose | [`docker-compose.yml`](docker-compose.yml) |
| Env template | [`.env.example`](.env.example) |
| Gap analysis | [`docs/gap-analysis.md`](docs/gap-analysis.md) |
| Threat model | [`docs/threat-model.md`](docs/threat-model.md) |
| Product plan | [`docs/product-completion-plan.md`](docs/product-completion-plan.md) |

---

## Appendix A: File Creation Checklist

When executing this polish pass, create the following new files:

| # | File | Purpose |
|---|---|---|
| 1 | [`docker-compose.openwebui.yml`](docker-compose.openwebui.yml) | Open WebUI sidecar for docker-compose |
| 2 | [`docs/open-webui-setup.md`](docs/open-webui-setup.md) | Step-by-step Open WebUI setup guide |
| 3 | [`CHANGELOG.md`](CHANGELOG.md) | Project changelog (Keep a Changelog format) |
| 4 | [`examples/openai-sdk-test.py`](examples/openai-sdk-test.py) | OpenAI Python SDK integration test |
| 5 | [`examples/langchain-test.py`](examples/langchain-test.py) | LangChain integration test |
| 6 | [`examples/curl-test.sh`](examples/curl-test.sh) | curl-based smoke test script |
| 7 | [`docs/quickstart.md`](docs/quickstart.md) | 5-minute quick start (if missing) |
| 8 | [`docs/production-deployment.md`](docs/production-deployment.md) | Production deployment guide (if missing) |

## Appendix B: Existing Files That May Need Updates

| File | Possible Updates |
|---|---|
| [`README.md`](README.md) | Add badges, link to CHANGELOG, verify all commands work |
| [`docs/integration-guide.md`](docs/integration-guide.md) | Add LlamaIndex example, verify all examples |
| [`docs/api-reference.md`](docs/api-reference.md) | Verify all 62+ endpoints match implementation |
| [`docs/deployment.md`](docs/deployment.md) | Add Open WebUI compose instructions |
| [`docs/threat-model.md`](docs/threat-model.md) | Verify all mitigations are implemented |
| [`.env.example`](.env.example) | Add any missing environment variables |
| [`docker-compose.yml`](docker-compose.yml) | Verify healthcheck, volume paths |
| [`Dockerfile`](Dockerfile) | Verify COPY paths, PYTHONPATH |

---

*End of Codex Instructions. This document is self-contained — Codex should be able to read this alone and understand exactly what to do to polish NoeRelay to commercial release quality.*