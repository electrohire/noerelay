# Production deployment gate

NoeRelay `0.1.0-draft` is not currently approved for general organizational production use. This guide defines the minimum supported Rust/PostgreSQL topology and the evidence still required; it is not a compliance certification.

## Required topology and secrets

- Run the Rust `noerelay-gateway` image behind an identity-aware TLS ingress.
- Use supported PostgreSQL with encrypted storage, point-in-time recovery, connection TLS, a least-privilege application role, and independently protected backups.
- Set `NOERELAY_PRODUCTION_MODE=1`. Startup then rejects stub routing, a missing `DATABASE_URL`, a missing live `OPENROUTER_API_KEY`, or a missing receipt signing seed.
- Inject distinct high-entropy values for `NOERELAY_API_KEY`, `OPENROUTER_API_KEY`, the PostgreSQL credential, and `NOERELAY_RECEIPT_SIGNING_SEED_HEX`. The signing seed is exactly 32 random bytes encoded as 64 hexadecimal characters.
- Give every signing key a stable `NOERELAY_RECEIPT_SIGNING_KEY_ID`; retain retired public keys for offline receipt verification.
- Configure an explicit reviewed `NOERELAY_CANDIDATES_JSON`. Every OpenRouter request contains the selected model ID; `openrouter/auto` is rejected.
- Set an integer `NOERELAY_BUDGET_LIMIT_MICROUSD`. Floating-point money is prohibited in authority and ledger records.

The current gateway authenticates one configured service/API key and one configured organization/project scope per deployment. A shared multi-organization GA deployment is blocked until the Rust API-key registry, RBAC administration, quota partitioning, and cross-tenant adversarial suite are complete.

## Persistence behavior

Every prepare, completion, rejection, and receipt transition is staged in Rust and committed to PostgreSQL with optimistic versioning before live state advances. The database stores the versioned authority snapshot, append-only hash-chain events, signed receipts, and integer cost records. Row-level security is forced on tenant tables and ledger update/delete operations are rejected.

Only one gateway replica is supported today. Although PostgreSQL is durable, version-conflict reload/retry and distributed work leasing are not complete, so horizontal replicas could reject concurrent transitions. The Kubernetes HPA intentionally remains capped at one.

## Kubernetes

The manifests in `deploy/kubernetes` are fail-closed templates. Before applying them:

1. Replace the empty candidate list with a reviewed candidate registry.
2. Create `noerelay-secrets` out of band with `api-key`, `openrouter-api-key`, `database-url`, and `receipt-signing-seed-hex`.
3. Set the ingress hostname, issuer, network policy destinations, organization/project IDs, resource limits, and signing key ID.
4. Confirm `/ready` checks both the model-plane configuration and PostgreSQL connectivity.

Example secret creation:

```bash
kubectl -n noerelay create secret generic noerelay-secrets \
  --from-literal=api-key="$NOERELAY_API_KEY" \
  --from-literal=openrouter-api-key="$OPENROUTER_API_KEY" \
  --from-literal=database-url="$DATABASE_URL" \
  --from-literal=receipt-signing-seed-hex="$NOERELAY_RECEIPT_SIGNING_SEED_HEX"
```

## Mandatory release evidence

Do not label a build GA until the [verification matrix](verification-matrix.md) contains observed evidence for all MUST requirements, including:

- independent security review with no unresolved critical/high findings;
- tenant-crossover, SSRF, injection, quota, signing-key, ledger-splice, and A2A adversarial tests;
- load/soak/fault results meeting the published SLOs;
- backup, restore, ledger verification, and signing-key recovery drills meeting declared RPO/RTO;
- OpenAI client compatibility across chat, Responses, tools, multimodal input, errors, and streaming;
- product, engineering, evaluation, security, and operations sign-off;
- legal/privacy review for each claimed organizational compliance profile.

Until those gates pass, use NoeRelay only in controlled evaluation or a narrowly reviewed pilot. See the [threat model](threat-model.md), [requirements](requirements.md), and [runbooks](runbooks.md).
