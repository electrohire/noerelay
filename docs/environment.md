# Environment and secret configuration

NoeRelay uses OpenRouter for model inference and Hugging Face Hub for benchmark acquisition. The public NoeRelay API may retain an OpenAI-compatible wire format, but that compatibility does not authorize OpenAI-hosted models.

## Keys

| Name | Secret | Required | Purpose |
|---|---:|---:|---|
| `OPENROUTER_API_KEY` | Yes | For live inference tests | Calls explicitly selected, non-OpenAI model IDs through OpenRouter. |
| `HF_TOKEN` | Yes | For gated/private datasets; recommended for CI | Downloads benchmark snapshots and optionally publishes private evaluation artifacts. Use a fine-grained read token unless publishing is required. |

Do not create or configure `OPENAI_API_KEY`. NoeRelay does not use it. An OpenRouter management or provisioning key is also unnecessary unless a separate key-provisioning service is built later.

Use different OpenRouter and Hugging Face tokens for local development and CI. Give the CI OpenRouter key an expiration and a small spending limit. Give the CI Hugging Face token read-only access to only the benchmark repositories it needs.

## GitHub `test` environment

In the private GitHub repository:

1. Open **Settings → Environments → New environment** and create `test`.
2. Under **Environment secrets**, add `OPENROUTER_API_KEY` and `HF_TOKEN`.
3. Under **Environment variables**, add the non-secret values below.

| Variable | Value |
|---|---|
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` |
| `OPENROUTER_HTTP_REFERER` | `https://github.com/electrohire/noerelay` |
| `OPENROUTER_APP_TITLE` | `NoeRelay` |
| `NOERELAY_LIVE_TESTS` | `1` only in a budget-limited live-test job; omit or set `0` for ordinary tests |

Offline conformance tests require no keys. The remote smoke workflow declares `environment: Test`, uses `secrets.OPENROUTER_API_KEY` and `secrets.HF_TOKEN`, and never prints either value.

The repository includes two GitHub Actions workflows:

- `Conformance` runs secret-free schema and reference-kernel tests for pull requests, pushes to `main`, and manual dispatches.
- `Test Environment Smoke` is manual-only, accepts only `main`, requires the exact confirmation `RUN TEST`, declares the credential-bearing `Test` environment only after those guards pass, validates both credentials, and confirms access to the initial Hugging Face benchmark repositories. It does not make a paid inference request.

Environment names are case-sensitive in workflow configuration. The remote environment is named `Test`.

The current GitHub billing plan does not support native required reviewers or deployment-branch policies for this private repository. GitHub rejected both rules through its environment API. Until the plan supports them, the manual-only trigger, pre-environment `main` guard, explicit confirmation, read-only workflow permissions, immutable action pins, and separation from pull-request CI are compensating controls. Native independent approval remains the preferred control before any paid generation or deployment job is enabled.

## Windows local environment

Use User variables so the secrets do not enter the repository:

1. Open Start and search for **Edit environment variables for your account**.
2. Under **User variables**, select **New** for each entry.
3. Add `OPENROUTER_API_KEY` with a local OpenRouter key and `HF_TOKEN` with a separate Hugging Face token.
4. Add the non-secret variables below.
5. Close and restart PowerShell, Codex, and any IDE that should inherit the new values.

```text
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_HTTP_REFERER=https://github.com/electrohire/noerelay
OPENROUTER_APP_TITLE=NoeRelay
NOERELAY_LIVE_TESTS=0
HF_HOME=C:\Users\<your-user>\.cache\huggingface
```

Confirm only that the variables exist; do not print token values into logs:

```powershell
@("OPENROUTER_API_KEY", "HF_TOKEN") | ForEach-Object {
    if ([Environment]::GetEnvironmentVariable($_, "User")) { "$_ is set" } else { "$_ is missing" }
}
```

The committed [`.env.example`](../.env.example) is a names-only template. Real `.env` files, keys, and local Hugging Face caches are ignored by Git.
