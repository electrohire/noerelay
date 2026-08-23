# Contributing to NoeRelay

NoeRelay is publicly visible but remains a proprietary ElectroHire project. Contributions are limited to authorized collaborators unless ElectroHire expressly invites otherwise.

## Development workflow

1. Create a short-lived branch from the default branch.
2. Keep changes scoped to one architectural, specification, or implementation concern.
3. Add or update schemas, examples, tests, and documentation together.
4. Run the complete conformance suite before opening a pull request.
5. Describe observable behavior, risk impact, compatibility impact, and evidence in the pull request.

```powershell
python -m unittest discover -s tests -v
```

Install `jsonschema` to run the optional standards-level validations:

```powershell
python -m pip install jsonschema
python -m unittest discover -s tests -v
```

## Normative changes

Changes to an `EPR-*` requirement, schema, routing rule, verification gate, epistemic transition, ledger contract, or compaction invariant must include:

- The reason for the change.
- The risk classes and task cohorts affected.
- Backward-compatibility analysis.
- Positive, negative, and fail-closed tests.
- Research, standards, or measured evaluation evidence where applicable.
- A versioned migration or promotion plan for production behavior.

Aggregate benchmark improvement does not justify a regression in a governed high-risk cohort.

## Commit and review expectations

- Do not commit credentials, tokens, private customer data, or regulated evidence.
- Prefer deterministic tests over model judgments whenever the criterion is executable.
- Record generated fixtures and their provenance.
- Keep worker and verifier independence requirements explicit.
- Treat model outputs as proposals or assertions, not observations.
- Require maintainer review before merging normative or security-sensitive changes.

## Reporting vulnerabilities

Do not open a public issue for a suspected vulnerability. Follow [SECURITY.md](SECURITY.md).

## Release notes template

Every draft or release candidate must update `CHANGELOG.md` and include release notes using this template:

```markdown
# NoeRelay <version> — <YYYY-MM-DD>

## Summary
<Who this is for and the outcome of the release.>

## Added
- <New user-visible capability.>

## Changed
- <Behavior or compatibility change.>

## Fixed
- <Defect and observable impact.>

## Security
- <Security control or advisory; omit sensitive exploit details until coordinated disclosure.>

## Migration
- <Required configuration, database, API, or deployment action; write “None” if not applicable.>

## Compatibility
- Python: <supported versions>
- API: <compatible, additive, or breaking>
- Ledger/schema: <compatibility statement>

## Known limitations
- <Unresolved limitation and safe workaround.>

## Verification evidence
- <Exact test, build, and smoke-test commands and results.>
```

Do not publish a release until the version and date agree across the changelog, README, package/runtime metadata, OpenAPI document, threat model, and release artifact names.
