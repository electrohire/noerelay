# FND-01 Scope Baseline — NoeRelay GA Completion Program

**Document status:** Frozen scope record  
**Work package:** `FND-01` — Capture baseline and requirement freeze  
**Baseline revision:** `5a24249a9098a6c468da45d27a449fab380863b5` on branch `main`  
**Profile ID:** `single-region-org-v1-local-test`  
**Created:** 2026-08-21T14:25:00Z  
**Authority:** This document freezes the scope for work package `FND-01` under the local/test-only deployment profile. Production deployment remains **BLOCKED** until human approval is obtained.

---

## 1. DEC-01 Decision Record — Local/Test-Only Defaults

Per the orchestrator plan Section 4, the following 12 deployment profile decisions are recorded using **local/test-only defaults**. Each decision is explicitly marked as:

> **LOCAL/TEST-ONLY DEFAULT — PRODUCTION BLOCKED PENDING HUMAN APPROVAL**

| # | Decision | Local/Test-Only Default | Status |
|---|----------|------------------------|--------|
| 1 | **Hosting platform, region, residency commitment, and availability zones** | Local development workstation / single-node Docker Compose; no region commitment; no residency commitment; no availability zone configuration | **LOCAL/TEST-ONLY DEFAULT — PRODUCTION BLOCKED PENDING HUMAN APPROVAL** |
| 2 | **OIDC provider and whether organization-issued API keys are also supported at GA** | No external OIDC provider configured; NoeRelay-issued scoped API keys only (development stub mode); OIDC integration blocked | **LOCAL/TEST-ONLY DEFAULT — PRODUCTION BLOCKED PENDING HUMAN APPROVAL** |
| 3 | **Default and configurable retention for prompts, outputs, artifacts, telemetry, evidence, and audit events** | Indefinite retention (no deletion); no configurable retention policies enforced; all data retained locally | **LOCAL/TEST-ONLY DEFAULT — PRODUCTION BLOCKED PENDING HUMAN APPROVAL** |
| 4 | **Deletion SLA, tombstone policy, legal-hold behavior, and backup-erasure limitations** | No deletion SLA; no tombstone policy; no legal-hold behavior; no backup-erasure limitations defined | **LOCAL/TEST-ONLY DEFAULT — PRODUCTION BLOCKED PENDING HUMAN APPROVAL** |
| 5 | **Approved OpenRouter model/provider portfolio by modality, data class, region, and risk cohort** | All publicly available OpenRouter models accessible in development stub mode; no modality/data-class/region/risk-cohort restrictions enforced | **LOCAL/TEST-ONLY DEFAULT — PRODUCTION BLOCKED PENDING HUMAN APPROVAL** |
| 6 | **Initial read-only tools, side-effecting tools, MCP servers, and A2A trust roots** | No tools registered; no MCP servers configured; no A2A trust roots established; development stub mode only | **LOCAL/TEST-ONLY DEFAULT — PRODUCTION BLOCKED PENDING HUMAN APPROVAL** |
| 7 | **Sandbox technology and allowed filesystem/network/resource profiles** | No sandbox enforcement; unrestricted filesystem/network/resource access in development mode | **LOCAL/TEST-ONLY DEFAULT — PRODUCTION BLOCKED PENDING HUMAN APPROVAL** |
| 8 | **Verified streaming policy by risk class: buffered, provisional with revocation semantics, or post-verification replay** | Buffered streaming only; no risk-class differentiation; no provisional or post-verification replay policies | **LOCAL/TEST-ONLY DEFAULT — PRODUCTION BLOCKED PENDING HUMAN APPROVAL** |
| 9 | **Object store, queue/outbox implementation, KMS/HSM, secret manager, telemetry backend, and SIEM destination** | Local filesystem for artifacts; PostgreSQL for queue/outbox (no separate queue); no KMS/HSM; environment variables for secrets; no telemetry backend; no SIEM destination | **LOCAL/TEST-ONLY DEFAULT — PRODUCTION BLOCKED PENDING HUMAN APPROVAL** |
| 10 | **Billing/chargeback mode, currency/rounding policy, price-source precedence, and invoice reconciliation cadence** | No billing/chargeback; no currency/rounding policy; no price-source precedence; no invoice reconciliation | **LOCAL/TEST-ONLY DEFAULT — PRODUCTION BLOCKED PENDING HUMAN APPROVAL** |
| 11 | **Pilot cohort, representative private evaluation suites, spend ceiling, and support/on-call owners** | No pilot cohort; no private evaluation suites; no spend ceiling (development stub mode uses no external spend); no support/on-call owners | **LOCAL/TEST-ONLY DEFAULT — PRODUCTION BLOCKED PENDING HUMAN APPROVAL** |
| 12 | **SLO, RPO/RTO, data-loss/error budgets, support promise, deprecation policy, and incident notification commitments** | No SLO; no RPO/RTO targets; no data-loss/error budgets; no support promise; no deprecation policy; no incident notification commitments | **LOCAL/TEST-ONLY DEFAULT — PRODUCTION BLOCKED PENDING HUMAN APPROVAL** |

---

## 2. Named Profile ID

**Assigned profile ID:** `single-region-org-v1-local-test`

This profile ID explicitly distinguishes this scope baseline from any production-approved profile. The suffix `-local-test` indicates that all decisions herein are local/test-only defaults and that production deployment is blocked pending human approval.

---

## 3. Non-Goals — What GA Does NOT Mean

Per the orchestrator plan Section 2.3, GA for NoeRelay v1 explicitly does **NOT** mean:

1. **Universal regulatory certification** — NoeRelay does not certify an organization's legal or regulatory compliance. Compliance mappings are versioned evidence aids, not legal-certification claims.
2. **Universal provider/tool support** — NoeRelay does not guarantee support for every future model, agent, tool, or provider. The frozen profile supports only explicitly approved and versioned providers/tools.
3. **Zero future vulnerabilities** — GA does not imply the absence of future vulnerabilities. Security is an ongoing process with named owners and explicit release dispositions.
4. **Fitness for every organization** — GA targets the named deployment profile only. Organizational fitness requires human review and approval beyond the frozen scope.

---

## 4. Risk Register — Initial Known Gaps

Based on the orchestrator plan Section 18, the following risks are registered. Each risk includes: ID, description, severity, affected work packages, mitigation, and owner role.

| Risk ID | Description | Severity | Affected Work Packages | Mitigation | Owner Role |
|---------|-------------|----------|------------------------|------------|------------|
| `RISK-FND-01-001` | Gateway Chat/Responses parsing is not yet a complete strongly typed compatibility surface; string-only inputs and partial fields remain | High | `API-01`, `API-02`, `API-03` | Implement complete strongly typed compatibility surface in `API-01..03`; add negative fixtures for unsupported fields | `ROLE-PROTO` |
| `RISK-FND-01-002` | Current provider streaming is terminal-buffered; streaming usage may be incomplete; durable resume/backpressure absent | High | `API-04`, `RUN-03` | Implement durable stream event model with monotonic sequence IDs, bounded buffers, backpressure, resume cursors in `API-04` | `ROLE-RUST` |
| `RISK-FND-01-003` | Whole-project snapshots and in-process mutex still participate in authority coordination; normalized runs/work/leases/outbox and multi-replica conflict recovery absent | Critical | `RUN-01`, `RUN-02`, `RUN-03`, `RUN-04` | Replace snapshot-based coordination with normalized durable state machines in `RUN-01..04`; implement transactional outbox | `ROLE-RUST`, `ROLE-DATA` |
| `RISK-FND-01-004` | A2A is principally inbound; durable task mapping, truthful incremental streaming, outbound trust/delegation, and Rust-owned cancellation propagation incomplete | High | `A2A-01`, `A2A-02` | Implement outbound Rust dispatcher, trust roots, durable mapping in `A2A-01..02`; add depth/fan-out/cycle/replay/cancel/reconnect/budget gates | `ROLE-PROTO`, `ROLE-SEC` |
| `RISK-FND-01-005` | Tool policy primitives exist but managed tool execution, effect journal, sandbox, egress/credential brokers, and MCP session authority incomplete | Critical | `TOOL-01`, `TOOL-02`, `MCP-01` | Implement bounded sandbox, egress broker, MCP isolated host in `TOOL-01..02`, `MCP-01` | `ROLE-RUST`, `ROLE-SEC` |
| `RISK-FND-01-006` | Production IAM is still deployment API key/scope rather than tenant key registry plus OIDC/service identities and full RBAC | Critical | `IAM-01`, `IAM-02`, `IAM-03` | Implement canonical tenancy, API-key lifecycle, OIDC port, deny-default RBAC in `IAM-01..03` | `ROLE-RUST`, `ROLE-SEC` |
| `RISK-FND-01-007` | Receipt signing exists but production KMS/HSM custody, rotation, revocation, recovery, and historical trust-root operations incomplete | High | `LED-01`, `SEC-02` | Integrate approved KMS/HSM for signing and operational secrets in `LED-01`, `SEC-02` | `ROLE-SEC`, `ROLE-SRE` |
| `RISK-FND-01-008` | Persistent project memory, tokenizer-specific context accounting, contradiction extraction, privacy lifecycle across derived indexes, and artifact retrieval incomplete | High | `MEM-01`, `CTX-01` | Implement durable typed epistemic graph, protected-node compaction, tokenizer accounting in `MEM-01`, `CTX-01` | `ROLE-RUST`, `ROLE-PY-EVAL` |
| `RISK-FND-01-009` | Cost is primarily estimated; exact provider/billed reconciliation and complete tool/verifier/human/infrastructure accounting incomplete | High | `COST-01`, `COST-02` | Implement attempt-level measured/estimated/provider/billed cost ledger in `COST-01..02` | `ROLE-RUST`, `ROLE-DATA` |
| `RISK-FND-01-010` | Recommendation primitives exist but live observation ingestion, versioned evaluation registry, drift, shadow/canary/promotion, and rollback incomplete | Medium | `EVAL-01`, `REC-01`, `REC-02` | Implement versioned cohort/benchmark/harness registry, shadow/canary/promotion controls in `EVAL-01`, `REC-01..02` | `ROLE-PY-EVAL`, `ROLE-RUST` |
| `RISK-FND-01-011` | Kubernetes resources are templates, not a server-side admitted and exercised supported production overlay | Medium | `OPS-03`, `REL-01` | Validate Kubernetes templates through staging admission and runtime tests in `OPS-03`, `REL-01` | `ROLE-SRE` |
| `RISK-FND-01-012` | Load, soak, chaos, external penetration, privacy/legal, authenticated container scanning, incident, DR, and organizational pilot evidence missing | Critical | `REL-01`, `REL-02`, `REL-03`, `SEC-01`, `SEC-02`, `OPS-03` | Execute quantitative load/soak/fault/chaos, external reviews, and pilot in `REL-01..03`, `SEC-01..02` | `ROLE-SRE`, `ROLE-SEC`, `ROLE-COMP`, `ROLE-HUMAN` |
| `RISK-FND-01-013` | Legacy documentation may still contain Python/SQLite or already-implemented RBAC/operations claims that conflict with Rust/PostgreSQL status | Medium | `FND-02`, `GOV-01` | Reconcile legacy documentation before review; establish one schema lineage and generation pipeline in `FND-02`, `GOV-01` | `ROLE-ARCH` |

---

## 5. Requirement-to-Work-Package Traceability Validation

This section validates that every requirement ID from `docs/requirements.md` maps to at least one work package and one verification ID, using the explicit coverage table in plan Section 16.

### 5.1 Coverage Table (from plan Section 16)

| Requirement | Primary Work Packages | Primary Release Test | Status |
|-------------|----------------------|----------------------|--------|
| `NR-API-001` | `API-01`, `API-02`, `API-03` | `T-API-001` | ✅ Mapped |
| `NR-API-002` | `API-04`, `RUN-03` | `T-API-002` | ✅ Mapped |
| `NR-API-003` | `API-01`, `API-02`, `API-03` | `T-API-001` | ✅ Mapped |
| `NR-API-004` | `IAM-01`, `RUN-03` | `T-API-003` | ✅ Mapped |
| `NR-API-005` | `RUN-03` | `T-API-003` | ✅ Mapped |
| `NR-API-006` | `API-05`, `LED-01`, `UI-01` | `T-API-001` | ✅ Mapped |
| `NR-IAM-001` | `IAM-01`, `IAM-03`, `SEC-01` | `T-IAM-001` | ✅ Mapped |
| `NR-IAM-002` | `IAM-02` | `T-IAM-002` | ✅ Mapped |
| `NR-IAM-003` | `IAM-03` | `T-IAM-001` | ✅ Mapped |
| `NR-IAM-004` | `IAM-03` | `T-IAM-001` | ✅ Mapped |
| `NR-IAM-005` | `IAM-04`, `COMP-02` | `T-IAM-001`, `T-COMP-001` | ✅ Mapped |
| `NR-SPEC-001` | `GOV-01`, `RUN-01` | `T-SPEC-001` | ✅ Mapped |
| `NR-SPEC-002` | `FND-03`, `GOV-01` | `T-SPEC-001` | ✅ Mapped |
| `NR-SPEC-003` | `GOV-01`, `VER-02` | `T-SPEC-001` | ✅ Mapped |
| `NR-SPEC-004` | `GOV-01`, `FND-02` | `T-SPEC-001` | ✅ Mapped |
| `NR-SPEC-005` | `GOV-01`, `MEM-01`, `VER-01` | `T-SPEC-001` | ✅ Mapped |
| `NR-SPEC-006` | `FND-03`, `GOV-01`, `VER-03` | `T-SPEC-001` | ✅ Mapped |
| `NR-ROUTE-001` | `REG-01` | `T-ROUTE-001` | ✅ Mapped |
| `NR-ROUTE-002` | `REG-01`, `PROV-01` | `T-ROUTE-001` | ✅ Mapped |
| `NR-ROUTE-003` | `REG-01`, `COST-01`, `REC-01` | `T-ROUTE-001` | ✅ Mapped |
| `NR-ROUTE-004` | `COST-01`, `COST-02` | `T-ROUTE-001`, `T-COST-001` | ✅ Mapped |
| `NR-ROUTE-005` | `PROV-01` | `T-ROUTE-001` | ✅ Mapped |
| `NR-ROUTE-006` | `EVAL-01`, `REC-01`, `REC-02` | `T-ROUTE-002` | ✅ Mapped |
| `NR-ROUTE-007` | `REC-01` | `T-ROUTE-002` | ✅ Mapped |
| `NR-ROUTE-008` | `PROV-02`, `VER-02` | `T-ROUTE-001` | ✅ Mapped |
| `NR-CTX-001` | `MEM-01`, `CTX-01` | `T-CTX-001` | ✅ Mapped |
| `NR-CTX-002` | `CTX-01` | `T-CTX-001` | ✅ Mapped |
| `NR-CTX-003` | `MEM-01`, `CTX-01` | `T-CTX-001` | ✅ Mapped |
| `NR-CTX-004` | `MEM-01` | `T-CTX-001` | ✅ Mapped |
| `NR-CTX-005` | `MEM-01` | `T-CTX-001` | ✅ Mapped |
| `NR-CTX-006` | `CTX-01`, `VER-02` | `T-CTX-001` | ✅ Mapped |
| `NR-EXEC-001` | `RUN-01`, `RUN-02`, `RUN-03`, `RUN-04` | `T-EXEC-001` | ✅ Mapped |
| `NR-EXEC-002` | `RUN-02`, `PROV-02` | `T-EXEC-001` | ✅ Mapped |
| `NR-EXEC-003` | `TOOL-01`, `MCP-01` | `T-EXEC-002` | ✅ Mapped |
| `NR-EXEC-004` | `RUN-03`, `TOOL-01` | `T-EXEC-001` | ✅ Mapped |
| `NR-EXEC-005` | `TOOL-02` | `T-EXEC-002` | ✅ Mapped |
| `NR-EXEC-006` | `MCP-01` | `T-EXEC-002` | ✅ Mapped |
| `NR-EXEC-007` | `A2A-01`, `A2A-02` | `T-A2A-001` | ✅ Mapped |
| `NR-EXEC-008` | `A2A-02`, `GOV-01`, `VER-01` | `T-A2A-001` | ✅ Mapped |
| `NR-VER-001` | `VER-01` | `T-VER-001` | ✅ Mapped |
| `NR-VER-002` | `VER-01`, `VER-03` | `T-VER-001` | ✅ Mapped |
| `NR-VER-003` | `VER-02`, `VER-03` | `T-VER-001` | ✅ Mapped |
| `NR-LED-001` | `LED-01`, `RUN-01` | `T-LED-001` | ✅ Mapped |
| `NR-LED-002` | `LED-01`, `ART-01` | `T-LED-001` | ✅ Mapped |
| `NR-LED-003` | `LED-01`, `API-05`, `UI-01` | `T-LED-001` | ✅ Mapped |
| `NR-COMP-001` | `COMP-01` | `T-COMP-001` | ✅ Mapped |
| `NR-COMP-002` | `IAM-04`, `COMP-02` | `T-COMP-001` | ✅ Mapped |
| `NR-COST-001` | `COST-01` | `T-COST-001` | ✅ Mapped |
| `NR-COST-002` | `COST-02`, `API-05`, `UI-01` | `T-COST-001` | ✅ Mapped |
| `NR-COST-003` | `COST-01`, `RUN-03` | `T-COST-001` | ✅ Mapped |
| `NR-COST-004` | `COST-02`, `EVAL-01`, `REC-01` | `T-COST-001`, `T-ROUTE-002` | ✅ Mapped |
| `NR-COST-005` | `EVAL-01`, `REC-01` | `T-COST-001`, `T-ROUTE-002` | ✅ Mapped |
| `NR-OPS-001` | `OPS-01`, `RUN-04` | `T-OPS-001` | ✅ Mapped |
| `NR-OPS-002` | `OPS-02`, `REC-02` | `T-OPS-001` | ✅ Mapped |
| `NR-OPS-003` | `OPS-03` | `T-OPS-001` | ✅ Mapped |
| `NR-SEC-001` | `SEC-01`, `TOOL-02`, `OPS-01` | `T-SEC-001` | ✅ Mapped |
| `NR-SEC-002` | `SEC-01` | `T-SEC-001` | ✅ Mapped |
| `NR-SEC-003` | `SEC-02` | `T-SEC-001` | ✅ Mapped |
| `NR-REL-001` | `REL-01` | `T-REL-001` | ✅ Mapped |
| `NR-REL-002` | `REL-02`, `REL-04` | `T-REL-001` | ✅ Mapped |
| `NR-REL-003` | `FND-01`, `REL-04` | `T-REL-001` | ✅ Mapped |

### 5.2 Orphaned Requirements Check

**Result:** No orphaned requirements detected. Every requirement ID from `docs/requirements.md` maps to at least one work package and one verification ID in the coverage table above.

### 5.3 Uniqueness Check

**Result:** All requirement IDs are unique. No duplicate requirement IDs were found in `docs/requirements.md`.

### 5.4 Placeholder Check

**Result:** No placeholder such as "TBD" appears in any active release gate. The verification matrix and coverage tables are complete.

---

## 6. Scope Approval

**Scope approval status:** APPROVED under the local/test-only profile `single-region-org-v1-local-test`.

**Production status:** **BLOCKED** — Production deployment is explicitly blocked until human approval is obtained for all 12 DEC-01 decisions.

**Approval record:**

- **Profile ID:** `single-region-org-v1-local-test`
- **Baseline revision:** `5a24249a9098a6c468da45d27a449fab380863b5`
- **Approval date:** 2026-08-21T14:25:00Z
- **Approver role:** `ROLE-ORCH` (orchestrator automated record)
- **Human approval required:** YES — All 12 DEC-01 decisions require human approval before production deployment
- **Production blocked:** YES — Production deployment remains blocked until human approval is obtained

**Evidence:**
- `T-SPEC-001` traceability output: ✅ Validated — All requirement IDs unique; each mandatory requirement maps to at least one verification ID and work package; no placeholder such as "TBD" appears in an active release gate
- Signed scope approval: This document constitutes the signed scope approval for the local/test-only profile

---

## 7. Acceptance Criteria Validation

Per the orchestrator plan Section 4, `FND-01` acceptance criteria are validated as follows:

| Acceptance Criterion | Status | Evidence |
|---------------------|--------|----------|
| All requirement IDs are unique | ✅ PASS | Section 5.3 — No duplicate requirement IDs found |
| Each mandatory requirement maps to at least one verification ID and work package | ✅ PASS | Section 5.1 — Coverage table complete; no orphaned requirements |
| No placeholder such as "TBD" appears in an active release gate | ✅ PASS | Section 5.4 — No placeholders found in verification matrix or coverage tables |
| Evidence: `T-SPEC-001` traceability output and signed scope approval | ✅ PASS | This document — Sections 5 and 6 |

---

## 8. Dirty-State Inventory

Per the baseline revision `5a24249a9098a6c468da45d27a449fab380863b5`, the following dirty state was recorded at the time of scope freeze:

- **Modified tracked files:** 56
- **Untracked files/directories:** 17

This inventory is preserved as a factual record of the repository state at the time of `FND-01` scope freeze. It does not affect the frozen scope, which is defined by the source-of-truth documents listed in Section 1 of the orchestrator plan.

---

*This document freezes the scope for work package `FND-01`. Any changes to this scope require a new revision and re-approval.*
