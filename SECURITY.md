# Security Policy

## Supported versions

NoeRelay is currently a pre-production draft (`0.1.0-draft`). Security fixes apply to the default branch until a formal release policy is published.

## Reporting a vulnerability

Report suspected vulnerabilities privately to the ElectroHire repository owners or through a private GitHub Security Advisory for this repository. Do not disclose the issue in a public issue, discussion, pull request, commit message, or external channel.

Include, when available:

- A concise description and affected component.
- Reproduction steps or a minimal proof of concept.
- Expected and observed behavior.
- Potential confidentiality, integrity, availability, policy, or epistemic impact.
- Suggested mitigation and any known workarounds.

Do not include real customer data, credentials, access tokens, proprietary prompts, or regulated evidence in the report.

## Security-sensitive areas

Particular care is required for changes involving permissions, provider credentials, data retention, prompt or tool injection, route-policy bypass, verifier independence, evidence integrity, ledger hashing, artifact binding, context compaction, and release gates.

## Threat model

The formal threat model is maintained in [`docs/threat-model.md`](docs/threat-model.md). It covers:

- Trust boundaries (client → gateway → OpenRouter → SQLite)
- Asset inventory and sensitivity classification
- Threat actors and capabilities
- 10 identified threats with mitigations and residual risk assessments
- Security controls summary
- Assumptions and future work for the Go production phase

All contributors should review the threat model before making changes to security-sensitive components.

## Security controls (current state)

| Control | Status |
|---|---|
| API key authentication | ✅ Implemented |
| Role-based access control | ✅ Implemented |
| Rate limiting (token bucket) | ✅ Implemented |
| Quota/budget enforcement | ✅ Implemented |
| Secret redaction in logs | ✅ Implemented |
| Non-root container user | ✅ Implemented |
| Multi-stage Docker build | ✅ Implemented |
| Zero runtime dependencies | ✅ Implemented |
| Hash-linked evidence ledger | ✅ Implemented |
| Fail-closed verification | ✅ Implemented |
| TLS support | ⚠️ Optional |
| OIDC/OAuth | ❌ Planned (Go phase) |
| Key hashing at rest | ❌ Planned (Go phase) |
| Container vulnerability scanning | ❌ Planned |
| SBOM generation | ❌ Planned |

## Responsible disclosure timeline

1. **Acknowledgment:** Within 48 hours of report receipt
2. **Triage:** Severity assessment within 5 business days
3. **Fix:** Patch developed and tested
4. **Disclosure:** Coordinated with reporter; advisory published after fix is available

## Security contacts

- Report vulnerabilities via GitHub Security Advisory: https://github.com/electrohire/noerelay/security/advisories/new
- For non-sensitive security questions, open a discussion in the repository