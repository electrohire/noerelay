# NoeRelay Threat Model

**Version:** 0.1.0-draft
**Last updated:** 2026-08-20
**Status:** Legacy reference-kernel model; Rust authority delta in Section 1.1

---

## 1. Scope

This threat model originally covered the Python reference kernel. ADR-0001 now assigns release authority to Rust. Its existing findings remain useful for the legacy oracle, but it is not release-complete until the Rust gateway/core, PostgreSQL persistence, A2A/MCP adapters, and tool sandbox receive independent review.

### 1.1 Rust authority delta

The Rust boundary now includes canonical request scope, task-contract hashing, constraint-first model selection, budget reservation, tool authorization, four-valued claims, protected context manifests, verification DAG decisions, usage rollups, recommendation uncertainty, and hash-chain verification. New risks include cross-language canonicalization drift, binding privilege confusion, adapter-to-core authorization, async cancellation, stream truncation, registry/config authenticity, and persistence transaction boundaries. The release tests for these risks are normative in [`verification-matrix.md`](verification-matrix.md).

### In scope

- Gateway HTTP server (Python, stdlib `http.server`)
- API key authentication and RBAC
- Rate limiting and quota enforcement
- Task contract compilation and governance validation
- Deterministic routing and portfolio selection
- Verification DAG execution
- Epistemic state management and evidence ledger
- SQLite persistence layer
- Structured logging and metrics endpoints
- Docker container and Kubernetes deployment

### Out of scope (future phases)

- Durable Rust production persistence and worker plane
- PostgreSQL persistence
- A2A agent interoperability
- MCP tool integration
- Tool sandbox execution
- Multimodal adapters (vision, image generation)
- TypeScript operator console
- OpenTelemetry distributed tracing

---

## 2. Trust Boundaries

```
┌─────────────────────────────────────────────────────┐
│  External Client (API consumer)                      │
│  - Sends requests with API key                       │
│  - Receives responses and evidence receipts          │
└──────────────────┬──────────────────────────────────┘
                   │ HTTPS (TLS optional in reference)
                   ▼
┌─────────────────────────────────────────────────────┐
│  NoeRelay Gateway (trust boundary)                   │
│  ┌───────────────┐  ┌──────────────┐  ┌───────────┐ │
│  │ Auth / RBAC   │  │ Rate Limiter │  │ Tenancy   │ │
│  └───────┬───────┘  └──────┬───────┘  └─────┬─────┘ │
│          ▼                 ▼                 ▼       │
│  ┌───────────────────────────────────────────────┐  │
│  │  Pipeline: Contract → Route → Execute → Verify │  │
│  └───────────────────┬───────────────────────────┘  │
│                      │                               │
│  ┌───────────────────▼───────────────────────────┐  │
│  │  Epistemic Ledger + Evidence Receipts          │  │
│  └───────────────────┬───────────────────────────┘  │
│                      │                               │
│  ┌───────────────────▼───────────────────────────┐  │
│  │  SQLite (file-based persistence)               │  │
│  └───────────────────────────────────────────────┘  │
└──────────────────┬──────────────────────────────────┘
                   │ HTTPS
                   ▼
┌─────────────────────────────────────────────────────┐
│  OpenRouter API (external, untrusted)                │
│  - Model inference provider                          │
│  - Model outputs are proposals, not observations     │
└─────────────────────────────────────────────────────┘
```

### Key trust boundaries

1. **Client → Gateway:** API key authentication boundary. Unauthenticated requests are rejected.
2. **Gateway → OpenRouter:** Model outputs cross a trust boundary. All model outputs are treated as proposals subject to verification.
3. **Gateway → SQLite:** File-system boundary. Database file permissions must restrict access to the gateway process.
4. **Tenant → Tenant:** Cross-tenant isolation boundary. One tenant must not access another tenant's runs, keys, or data.

---

## 3. Assets

| Asset | Sensitivity | Storage | Protection |
|---|---|---|---|
| API keys | High | SQLite / process configuration | Database-managed keys are SHA-256 hashed; plaintext environment keys are process secrets; neither is logged |
| OpenRouter API key | Critical | Environment variable | Never logged; masked in errors |
| Run data (inputs, outputs, costs) | Medium | SQLite / in-memory | Tenant-scoped access |
| Evidence receipts | Medium | SQLite / in-memory | Hash-linked integrity |
| Epistemic claims | Medium | In-memory | Immutable ledger |
| Managed secrets | Critical | SQLite | Versioned authenticated encryption; master key held separately |
| Governance policies | High | File system | Read-only after load |
| Portfolio configuration | High | File system | Read-only after load |
| Hugging Face token | Critical | Environment variable | Never logged; masked in errors |

---

## 4. Threat Actors

| Actor | Motivation | Capability |
|---|---|---|
| External attacker (unauthenticated) | Data theft, DoS | Network access to gateway port |
| Malicious tenant | Cross-tenant access, quota bypass | Valid API key |
| Compromised model provider | Inject malicious output | Controls upstream model responses |
| Insider (operator) | Misconfiguration, data exfiltration | Access to deployment environment |

---

## 5. Threats and Mitigations

### T1: Unauthenticated API Access

**Threat:** Attacker accesses gateway endpoints without valid credentials.

**Mitigation:**
- API key authentication required for all non-health endpoints
- RBAC enforces role-based access per endpoint
- Default bind address is `127.0.0.1` (loopback only)
- Health endpoint (`GET /health`) is intentionally unauthenticated

**Residual risk:** Low for the default network posture. Auth may be disabled only on a loopback listener unless an operator explicitly overrides the requirement; non-loopback startup without keys fails closed.

### T2: API Key Exposure in Logs

**Threat:** API keys or secrets appear in structured log output.

**Mitigation:**
- Structured logger applies content-aware redaction for known secret patterns
- API keys are never included in log `extra` fields
- OpenRouter API key is masked in error messages
- `.gitignore` prevents `.env` from being committed

**Residual risk:** Low. The redaction filter covers known patterns; custom log calls should use the `StructuredLogger` API.

### T3: Cross-Tenant Data Access

**Threat:** Tenant A accesses Tenant B's runs, keys, or configuration.

**Mitigation:**
- Compatibility run, receipt, trace, ledger, and response-cache access is scoped to the authenticated tenant
- API key management is tenant-scoped
- RBAC checks verify tenant ownership before data access
- Negative tests verify cross-tenant isolation

**Residual risk:** Medium. The reference implementation has no database-level row security, so isolation still depends on handler and query correctness. Administrative analytics are intentionally privileged rather than tenant self-service.

### T4: Model Output Injection

**Threat:** A compromised or malicious model provider returns output designed to bypass verification or inject harmful content.

**Mitigation:**
- All model outputs are classified as `model_assertion` evidence (not observations)
- Verification DAG runs deterministic checks before acceptance
- Independent verifier family requirement for high-risk tasks
- Corroboration requires non-model evidence
- Fail-closed: unverifiable outputs trigger repair/fallback/escalation

**Residual risk:** Medium. The verification DAG is only as strong as its checks. No adversarial test suite exists yet. The Go production implementation will add pluggable verifier nodes and hidden test suites.

### T5: Denial of Service

**Threat:** Attacker exhausts gateway resources through high-volume requests.

**Mitigation:**
- Token-bucket rate limiting per API key
- Quota/budget enforcement per tenant (daily/monthly)
- A configurable maximum JSON request-body size
- Bounded in-memory runs, alerts, webhooks, SIEM records, and caches
- Graceful shutdown with request draining
- K8s resource limits (CPU, memory)

**Residual risk:** Medium. No distributed rate limiting across instances. No WAF or DDoS protection at the network layer.

### T6: SQLite Injection

**Threat:** Malicious input causes SQL injection in database queries.

**Mitigation:**
- Parameterized queries used throughout the database module
- No string concatenation for SQL construction
- Input validation before persistence

**Residual risk:** Low. The database module uses parameterized queries exclusively.

### T7: Ledger Tampering

**Threat:** Attacker modifies evidence ledger entries to falsify provenance.

**Mitigation:**
- SHA-256 hash-linked chain (each event references previous hash)
- `verify_chain()` validates integrity on read
- Evidence receipts bind inputs, route, artifacts, verification, cost, claims, and ledger head
- Tampering is detectable through hash verification

**Residual risk:** Low for integrity detection, but a local attacker who can modify both records and all downstream receipts can recompute an unsigned chain. External signed checkpoints are not implemented.

### T8: Configuration Tampering

**Threat:** Attacker modifies routing policy, portfolio, or governance configuration.

**Mitigation:**
- Policy and portfolio files are loaded at startup and treated as read-only
- Configuration validation on load
- File permissions should restrict write access to operators only

**Residual risk:** Medium. No runtime integrity checking of configuration files. No signed configuration artifacts.

### T9: Secret Exposure via Environment

**Threat:** Secrets in environment variables are exposed through process listing, debug endpoints, or error messages.

**Mitigation:**
- Secrets are never included in `/health` or `/metrics` responses
- Error responses mask secret values
- Structured logger redacts known secret patterns
- K8s secrets are mounted as files or env vars with `secretKeyRef`

**Residual risk:** Low. Environment variable exposure is a platform concern; the application does not intentionally expose them.

Managed application secrets stored through the API use versioned authenticated encryption with a required master key. Ciphertext tampering fails closed, list operations never return secret values, and key rotation is transactional. The master key remains a critical deployment secret and must be backed up separately from SQLite.

### T10: Dependency Supply Chain

**Threat:** Compromised Python package introduces malicious code.

**Mitigation:**
- Reference kernel is dependency-free at runtime (stdlib only)
- `jsonschema` and `pytest` are test-only dependencies
- Production Docker image does not install pip packages
- Pinned dependency versions in CI workflows

**Residual risk:** Low. The zero-dependency runtime design eliminates most supply chain risk for the reference kernel.

---

## 6. Security Controls Summary

| Control | Status | Notes |
|---|---|---|
| API key authentication | ✅ Implemented | Disabled by default (loopback bind) |
| RBAC | ✅ Implemented | Role-based access per endpoint |
| Rate limiting | ✅ Implemented | Token bucket per API key |
| Quota enforcement | ✅ Implemented | Daily/monthly budgets per tenant |
| Input validation | ✅ Implemented | Governance, contract, and schema validation |
| Secret redaction in logs | ✅ Implemented | Content-aware redaction filter |
| Non-root container user | ✅ Implemented | Multi-stage Docker build |
| Health endpoint (no auth) | ✅ Implemented | K8s probe compatible |
| TLS support | ⚠️ Optional | Requires manual cert provisioning |
| OIDC/OAuth | ❌ Not implemented | Planned for Go production phase |
| API-key hashing at rest | ✅ Implemented | Database-managed keys stored as SHA-256 digests |
| Managed-secret encryption | ✅ Implemented | Encrypt-then-MAC with explicit master key |
| Exact-origin CORS | ✅ Implemented | Explicit allowlist; wildcard rejected |
| Request body limit | ✅ Implemented | Configurable; 4 MiB default |
| Container vulnerability scanning | ❌ Not implemented | Planned for CI enhancement |
| SBOM generation | ❌ Not implemented | Planned for CI enhancement |
| Distributed tracing | ❌ Not implemented | Planned for Go production phase |
| Kill switches | ❌ Not implemented | Planned for Go production phase |
| Adversarial test suite | ❌ Not implemented | Planned for Go production phase |

---

## 7. Assumptions and Residual Risks

1. **The network boundary is trusted.** The reference implementation assumes the gateway runs behind a reverse proxy or within a trusted network. TLS termination is the responsibility of the ingress controller.

2. **The host filesystem is trusted.** SQLite database files and configuration files are protected by host filesystem permissions.

3. **OpenRouter is a transport, not a trust anchor.** Model outputs are always treated as proposals. The verification DAG is the trust anchor.

4. **Single-instance deployment.** The reference implementation does not support horizontal scaling with shared state. Cross-instance attacks are not modeled.

5. **No tool execution.** Tool sandbox threats are out of scope until governed tools are implemented.

---

## 8. Future Work

The Go production control plane will require a significantly expanded threat model covering:

- PostgreSQL row-level security and connection pooling
- Protobuf message validation and fuzzing
- A2A agent authentication, authorization, and delegation depth limits
- MCP session isolation and credential scoping
- Tool sandbox escape prevention
- Distributed rate limiting and DDoS protection
- OpenTelemetry trace correlation and sensitive span redaction
- Kill switch propagation and circuit breaker design
- Signed release artifacts and SBOM attestation
- Formal verification of routing policy and epistemic transitions
