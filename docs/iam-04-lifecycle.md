# IAM-04 tenant data lifecycle

This package implements the local/test-only `single-region-org-v1-local-test`
profile. It is not a production retention or residency commitment.

## Inventory and deletion boundaries

The PostgreSQL inventory counts request, response, context, and other artifact
metadata; receipt and ledger rows; recommendation observations; exports; IAM
audit events; and usage records. Artifact byte size and legal holds come from
the authoritative artifact metadata. Cache, trace, application-log, and
third-party-provider locations are explicitly represented, but report zero in
this profile because no durable adapter exposes an authoritative count.

Deletion jobs are durable state machines. A job records its initial item count,
successful and failed work, and items skipped because an artifact legal hold is
active. Deleting payloads must also create a payload-free tombstone. Export
bundles are represented by an export request and, when complete, an artifact
reference so artifact retention and legal-hold rules apply to the bundle.

`cryptographic_delete` is a policy action for stores using per-tenant or
per-object envelope keys. The local artifact backend is unencrypted, so there
is no key to destroy in this named profile. Production remains blocked until a
KMS-backed artifact adapter can revoke the key, verify that ciphertext is no
longer decryptable, and retain only non-sensitive key-destruction evidence.

## Data that cannot disappear immediately

Ledger events, signed receipt digests, and tombstones are integrity proofs. A
payload can be removed or cryptographically shredded, but deleting or rewriting
its proof immediately would break hash-chain, signature, replay, and deletion
accountability guarantees. Policies therefore retain proof-only records for the
applicable audit period; proofs must not contain the deleted prompt or output.

Database and object-store backups are immutable recovery copies until their
configured backup expiry. Selective in-place deletion would invalidate backup
integrity and tested restoration. A deletion is applied to the live store,
recorded by tombstone, and allowed to age out of backups on the backup retention
schedule. Restoring an older backup requires replaying tombstones before the
system serves traffic. Legal hold suspends live deletion and backup expiry for
the held scope.

Third-party provider copies cannot be synchronously erased by this repository.
Their deletion receipt and provider-specific retention deadline must be
reconciled through a provider adapter. No such provider is enabled by the local
profile; production remains blocked until those contracts and adapters exist.

## Reconciliation and rollback

Inventory generation is transaction-scoped and tenant-isolated. Reconciliation
performs a fresh count in one transaction and stamps each returned entry with
the reconciliation time. External stores require adapter-specific reconciliation
before production use.

Migration rollback is non-destructive only before lifecycle records exist. Drop
`tombstones`, `export_requests`, `deletion_jobs`, and `lifecycle_policies` in
that order after exporting required evidence. Never roll back by deleting active
legal-hold or deletion-proof records without approved evidence retention and
backup procedures.
