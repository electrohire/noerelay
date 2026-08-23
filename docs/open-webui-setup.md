# Open WebUI setup for NoeRelay

**NoeRelay version:** `0.1.0-draft`
**Last updated:** 2026-08-20

Open WebUI can use NoeRelay as a custom OpenAI-wire-compatible backend. “OpenAI-compatible” refers only to the client protocol: NoeRelay routes through explicitly permitted non-OpenAI models via OpenRouter or local Ollama and rejects OpenAI model families.

## Prerequisites

- Docker Engine and Docker Compose v2
- This repository
- Optional: `OPENROUTER_API_KEY` for live cloud inference
- Optional: enough disk and memory for an Ollama model

## Docker Compose quick start

Choose local secrets before shared use. The literal defaults below are development-only:

```bash
export NOERELAY_API_KEY="replace-with-a-random-key"
export NOERELAY_MASTER_KEY="replace-with-a-separate-random-key"
export WEBUI_SECRET_KEY="replace-with-a-random-session-key"
docker compose -f docker-compose.yml -f docker-compose.openwebui.yml up --build -d
```

PowerShell:

```powershell
$env:NOERELAY_API_KEY = "replace-with-a-random-key"
$env:NOERELAY_MASTER_KEY = "replace-with-a-separate-random-key"
$env:WEBUI_SECRET_KEY = "replace-with-a-random-session-key"
docker compose -f docker-compose.yml -f docker-compose.openwebui.yml up --build -d
```

Compose places both containers on its default network. Open WebUI connects internally to `http://noerelay:8080/v1` and uses the same `NOERELAY_API_KEY` passed to the gateway.

Verify the services:

```bash
curl http://localhost:8080/health
curl -H "Authorization: Bearer $NOERELAY_API_KEY" http://localhost:8080/v1/models
curl http://localhost:3000
```

Open [http://localhost:3000](http://localhost:3000), create the initial administrator, select `noerelay/epr-1`, and send a message. Disable `ENABLE_SIGNUP` after the initial account is created when the deployment is not a disposable local environment.

## Connect an existing Open WebUI installation

Start NoeRelay and set an API key when it is reachable beyond loopback. In Open WebUI:

1. Open **Admin Panel → Settings → Connections** (the label may differ by Open WebUI version).
2. Add an OpenAI-compatible connection.
3. Set the base URL to the address Open WebUI can reach, ending in `/v1`.
4. Set the API key to a value accepted by `NOERELAY_AUTH_API_KEYS` or a database-managed NoeRelay key.
5. Save or test the connection, then select `noerelay/epr-1`.

Use `http://host.docker.internal:8080/v1` when Open WebUI runs in Docker Desktop and NoeRelay runs directly on the Windows or macOS host. On Linux, use an explicit host-gateway mapping or a shared Docker network. `127.0.0.1` inside the Open WebUI container refers to that container, not the host.

## Verify completion and EPR metadata

Open WebUI renders the assistant content but may not surface arbitrary top-level response extensions. Verify the full NoeRelay response directly:

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer $NOERELAY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"noerelay/epr-1","messages":[{"role":"user","content":"What is 2+2?"}]}'
```

The returned `epr.run_id` can be used with the authenticated run endpoint:

```bash
curl -H "Authorization: Bearer $NOERELAY_API_KEY" \
  http://localhost:8080/v1/noerelay/runs/RUN_ID/receipt
```

Streaming uses server-sent events and ends with `data: [DONE]`. The terminal metadata chunk contains the EPR extension.

## Live OpenRouter mode

Add these values to the ignored local `.env` file or inject them through your secret manager:

```dotenv
NOERELAY_OPENROUTER_MODE=live
OPENROUTER_API_KEY=replace-with-your-openrouter-key
```

Then recreate the gateway. No `OPENAI_API_KEY` is accepted or required by NoeRelay; the similarly named Open WebUI connection variable is only how Open WebUI labels custom compatible backends.

## Local Ollama models

The base Compose file starts Ollama, but it does not download model weights automatically:

```bash
docker compose exec ollama ollama pull qwen3:8b
```

Only routes admitted by the configured portfolio and policy are eligible. Downloading a model does not by itself promote it into every governed cohort.

## Architecture

```text
Browser
  │
  ▼
Open WebUI :3000
  │  compatible HTTP + API key
  ▼
NoeRelay :8080
  ├── contract → policy → route → execute → verify
  ├── evidence receipt + hash-linked ledger
  ├──► local Ollama (optional)
  └──► OpenRouter → explicit non-OpenAI model IDs (optional live mode)
```

## Troubleshooting

**Open WebUI reports 401.** Ensure `NOERELAY_API_KEY` has the same value in both Compose services, then recreate them. The health endpoint is intentionally public, so a healthy response does not prove the model route is authenticated.

**The model list is empty.** Run the authenticated `/v1/models` curl command above. Check `docker compose logs noerelay` for startup configuration errors.

**Connection refused.** From one Compose project, use `http://noerelay:8080/v1`, not `localhost`. Confirm both services with `docker compose ps`.

**Browser origin is rejected.** Add the exact scheme, host, and port to `NOERELAY_CORS_ALLOWED_ORIGINS`; wildcard origins are deliberately rejected.

**No real provider answer appears.** Stub mode is deterministic and makes no provider call. Configure live mode and a valid OpenRouter key, or install an eligible Ollama route.

**The dashboard URL returns 401.** Operational routes are protected when authentication is enabled. Use authenticated API calls or place an identity-aware reverse proxy in front of operator surfaces; do not expose them publicly.

For deployment controls, backups, TLS, and monitoring, see [production deployment](production-deployment.md). For other clients, see the [integration guide](integration-guide.md).
