# Compliance Registry — CMMC, EU AI Act, GDPR, US Federal

**Status**: Template — populate per deployment  
**Date**: 2026-09-03  
**Mission**: [`NOERELAY_INTEGRATION_MISSION.md`](C:\Users\trist\Downloads\NOERELAY_INTEGRATION_MISSION.md) §17

---

## 1. Authoritative Compliance Registry

Each entry binds:

```text
jurisdiction and authority
framework/regulation/clause/control/article
official source URL and retrieved content hash
publication, effective, transition, and repeal dates
applicability predicates
regulated actor role
system/use-case/data/contract scope
obligation and prohibited action
required evidence and retention
assessment or reporting authority
implementation owner and approver
status, exception, POA&M/corrective action, and expiry
registry schema and mapping revision
```

Only reviewed, signed registry revisions may become active policy. Automated monitoring may propose changes; a designated legal/compliance owner must approve them.

---

## 2. CMMC Level 2 Target

### 2.1 Boundary Package

- Authoritative CUI category and marking guidance
- Contracts and clauses
- Data-flow diagrams, network diagrams, trust boundaries
- Complete asset inventory (CUI assets, security-protection assets, contractor-risk-managed assets, specialized assets, out-of-scope assets)
- System Security Plan tied to exact boundary and configuration baseline
- Approved change process that re-evaluates scope before any model, agent, integration, route, region, storage, telemetry, or support-access change

### 2.2 Router Labels

The router must carry:
- `data_classification`
- `cui_categories`
- `dissemination_controls`
- `contract_id`
- `enclave_id`
- `authorized_regions`
- `authorized_provider/service revisions`
- `export_control`

CUI/FCI may route only through currently authorized assets within the assessed boundary.

### 2.3 Control Families

| Family | Coverage |
|--------|----------|
| Access Control | Least privilege, separation of duties, MFA, PAW, session control, remote access, periodic recertification |
| Identification/Authentication | Device and service identity, secrets and key management, certificate rotation, immediate revocation |
| Cryptography | FIPS-validated modules, encryption in transit/at rest, customer/tenant key separation |
| Configuration Management | Baselines, secure builds, change control, allowlisting, patching, vulnerability remediation, drift detection |
| Audit | Event coverage, protected time sync, centralized collection, tamper-evident retention, alerting, review, ledger correlation |
| Media | Marking, access, transport, sanitization, destruction, removable-media restrictions, backup protection |
| Incident Response | Detection, triage, containment, eradication, recovery, exercises, evidence preservation, contractual reporting |
| Personnel | Screening, termination/transfer, role training, CUI handling, security awareness, AI literacy |
| Physical | Access control, visitor/media records for in-scope facilities |
| Risk Assessment | Continuous vulnerability scanning, penetration testing, supplier risk, remediation governance |
| Security Assessment | Every applicable objective, continuous monitoring, SSP maintenance, management review |
| System/Communications Protection | Segmentation, deny-by-default egress, DNS/web controls, boundary protection, secure remote maintenance |
| System/Information Integrity | Malware protection, flaw remediation, integrity checking, secure provenance, prompt/tool-output attack controls |

### 2.4 Assessment Objectives

Record each as `MET`, `NOT_MET`, or `NOT_APPLICABLE` with:
- Assessor identity
- Scope
- Timestamp
- Procedure
- Immutable evidence references

`NOT_APPLICABLE` requires signed rationale. Do not infer `MET` from a policy document alone.

### 2.5 POA&M Rules

- Follow current 32 CFR 170.21 eligibility, scoring, closeout, and time limits
- Prevent prohibited requirements from being placed on a POA&M
- Calculate conditional status from governing revision
- Alert before deadlines
- Block any representation that a conditional assessment is final
- Annual affirmations and required reassessments must be explicit human-authorized, ledgered workflows

---

## 3. EU AI Act

### 3.1 Role Classification

For each release and deployment, classify:
- Provider, deployer, importer, distributor, product manufacturer, authorized representative, GPAI provider, or downstream provider
- Territorial scope
- Excluded military/defense/national-security purpose
- Prohibited practice check
- High-risk category
- Transparency duty
- GPAI duty

### 3.2 AI System Inventory

Maintain signed record containing:
- Intended purpose
- Reasonably foreseeable misuse
- Affected persons
- Prohibited-use checks
- Annex I/III analysis
- Provider/deployer role
- Model and data lineage
- Substantial-modification analysis
- Jurisdictions
- Effective dates
- Decision owner
- Legal review
- Next reassessment trigger

### 3.3 High-Risk Requirements (Articles 8-15)

- Continuous risk-management system
- Documented data governance, provenance, relevance, representativeness, quality, bias analysis
- Annex IV technical documentation
- Automatic logs with protected retention and deployer export
- Meaningful transparency, limitations, accuracy metrics, known failure modes
- Human oversight with stop/override capability
- Accuracy, robustness, resilience, cybersecurity, fallback, safe failure
- Quality-management system, regulatory records, corrective action
- Conformity assessment, EU declaration, CE marking, registration
- Deployer monitoring, input-data controls, worker/representative notices
- Fundamental-rights impact assessment and data-protection impact assessment
- Post-market monitoring, serious-incident detection/reporting, corrective action

---

## 4. GDPR

Where personal data is involved:
- Controller/processor/joint-controller roles
- Purpose, lawful basis, data categories, data subjects, sources, recipients, transfers, retention
- Processor terms, records of processing
- Data-subject rights (access, correction, deletion, restriction, objection, portability)
- Security, breach workflow, DPIA
- Automated-decision safeguards
- Data minimization, purpose limitation, configurable retention
- Consent withdrawal where consent is used
- Transfer-mechanism records

---

## 5. US Federal Procurement (OMB M-25-21, M-25-22)

- AI system/use-case inventory, accountable owner, impact classification
- Risk assessment, testing, monitoring, incident response
- Appeals/recourse, accessibility, privacy, civil-rights review
- Substantiated capability, safety, accuracy, bias, cost, and security claims
- Protected-class impact testing for consequential decisions
- Notice, explanation, human review, contestability, correction, audit, retention, disclosure
- Government-data restrictions, IP/data rights, no-training commitments
- Interoperability/portability, subcontractor disclosure, version notification
- Independent evaluation, rollback, sunset/exit support

---

## 6. Compliance Acceptance Gates

Before a regulated deployment or release:
1. Current applicability and role classification
2. Approved system/data/contract boundary
3. No prohibited use
4. All mandatory controls implemented and evidenced
5. Risk, privacy, civil-rights/fundamental-rights, security, and impact assessments complete
6. Required independent assessment/conformity/registration/status current
7. Documentation, notices, instructions, disclosures, and contracts ready
8. Monitoring, incident, appeal, correction, rollback, and withdrawal processes tested
9. Supplier, model, data, and external-service obligations verified
10. Open POA&M/corrective actions permitted and within current limits
11. Legal/compliance/security/product owner sign-off recorded
12. Receipt bound to the ledger head and compliance-registry revision

No automated agent may sign an affirmation of continuous compliance, legal opinion, CMMC assessment, declaration of conformity, or regulator submission.