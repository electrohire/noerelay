# Open WebUI Setup Guide for NoeRelay

**Version:** 1.0
**Last updated:** 2026-08-20

This guide walks through setting up [Open WebUI](https://github.com/open-webui/open-webui) to use NoeRelay as its OpenAI-compatible backend. Open WebUI provides a ChatGPT-like web interface that connects to NoeRelay's governed model routing.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Quick Start (Docker Compose)](#2-quick-start-docker-compose)
3. [Manual Setup (Existing Open WebUI)](#3-manual-setup-existing-open-webui)
4. [Verification](#4-verification)
5. [Configuration Reference](#5-configuration-reference)
6. [Architecture](#6-architecture)
7. [Troubleshooting](#7-troubleshooting)
8. [Advanced Configuration](#8-advanced-configuration)

---

## 1. Prerequisites

- **Docker** and **Docker Compose** installed
- **NoeRelay** repository cloned
- Optional: **OpenRouter API key** (for live mode; stub mode works without it)
- Optional: **Ollama** (for local model routing)

---

## 2. Quick Start (Docker Compose)

The easiest way to get started is using the provided Docker Compose files:

### Step 1: Start the Stack

```bash
# From the NoeRelay repository root
docker-compose -f docker-compose.yml -f docker-compose.openwebui.yml up -d
```

This starts three services:
- **noerelay** — The NoeRelay gateway on port 8080
- **ollama** — Local model provider on port 11434 (optional, for local models)
- **open-webui** — Web chat interface on port 3000

### Step 2: Wait for Services to be Healthy

```bash
# Check NoeRelay health
curl http://localhost:8080/health
# Expected: {"status": "healthy", "version": "0.1.0"}

# Check Open WebUI health
curl http://localhost:3000
# Expected: HTML response (the Open WebUI login page)
```

### Step 3: Access Open WebUI

1. Open your browser to **http://localhost:3000**
2. On first access, create an admin account (email + password)
3. After login, you'll see the chat interface

### Step 4: Verify the Connection

The connection to NoeRelay is pre-configured via environment variables. To verify:

1. Click the model selector dropdown (top-left of chat input)
2. You should see **`noerelay/epr-1`** in the model list
3. Select it and send a test message: "Hello! What is 2+2?"
4. You should receive a response routed through NoeRelay's governance pipeline

### Step 5: Check EPR Metadata

NoeRelay includes EPR (Epistemic Process Recording) metadata in every response. In Open WebUI:

1. Send a message
2. The response includes standard OpenAI fields plus NoeRelay governance metadata
3. You can inspect run details via the NoeRelay dashboard at **http://localhost:8080/dashboard**

---

## 3. Manual Setup (Existing Open WebUI)

If you already have Open WebUI running and want to connect it to NoeRelay:

### Step 1: Start NoeRelay

```bash
# From the NoeRelay repository
cd reference
# Windows PowerShell:
$env:NOERELAY_OPENROUTER_MODE='stub'
python -m gateway

# Linux/macOS:
NOERELAY_OPENROUTER_MODE=stub python -m gateway
```

NoeRelay is now running at `http://127.0.0.1:8080`.

### Step 2: Configure Open WebUI

1. Open Open WebUI in your browser
2. Go to **Settings** (gear icon, bottom-left)
3. Navigate to **Connections** → **OpenAI API**
4. Fill in the connection details:

   | Field | Value |
   |---|---|
   | **Base URL** | `http://127.0.0.1:8080/v1` |
   | **API Key** | `any-value` (or your NoeRelay API key if auth is enabled) |

5. Click **"Test Connection"** — you should see a success message
6. Click **Save**

### Step 3: Select the Model

1. Return to the chat interface
2. Click the model selector dropdown
3. Select **`noerelay/epr-1`**
4. Start chatting!

---

## 4. Verification

### Verify Model Listing

```bash
# Direct API call to verify models
curl http://127.0.0.1:8080/v1/models
```

Expected response:
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

### Verify Chat Completion

```bash
curl -X POST http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "noerelay/epr-1",
    "messages": [{"role": "user", "content": "Hello! What is 2+2?"}]
  }'
```

Expected: A JSON response with `choices[0].message.content` containing the answer.

### Verify Streaming

```bash
curl -X POST http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "noerelay/epr-1",
    "messages": [{"role": "user", "content": "Count from 1 to 5."}],
    "stream": true
  }'
```

Expected: SSE stream with `data: {...}` chunks ending with `data: [DONE]`.

### Verify EPR Metadata

In the chat completion response, look for the `epr` block:

```json
{
  "epr": {
    "run_id": "run-abc123",
    "receipt_id": "r-xyz789",
    "status": "accepted",
    "model_used": "openai/gpt-4o-mini",
    "gateway": "openrouter",
    "cost_usd": 0.000015,
    "latency_ms": 450,
    "risk_class": "low",
    "hir": false,
    "rr": false,
    "chain_hash": "sha256:abc123..."
  }
}
```

---

## 5. Configuration Reference

### Docker Compose Environment Variables

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_BASE_URL` | `http://noerelay:8080/v1` | NoeRelay API endpoint |
| `OPENAI_API_KEY` | `noerelay` | API key for NoeRelay |
| `WEBUI_NAME` | `NoeRelay Chat` | Display name in the UI |
| `WEBUI_URL` | `http://localhost:3000` | Public URL of the WebUI |
| `ENABLE_SIGNUP` | `true` | Allow new account creation |
| `DEFAULT_MODELS` | `noerelay/epr-1` | Pre-selected model |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

### NoeRelay Environment Variables

| Variable | Default | Description |
|---|---|---|
| `NOERELAY_OPENROUTER_MODE` | `stub` | `stub` (no API key) or `live` (needs key) |
| `NOERELAY_GATEWAY_HOST` | `127.0.0.1` | Bind address |
| `NOERELAY_GATEWAY_PORT` | `8080` | Listen port |
| `NOERELAY_DATABASE_ENABLED` | `0` | Enable SQLite persistence |
| `NOERELAY_LOG_LEVEL` | `INFO` | Log level |

---

## 6. Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Browser (http://localhost:3000)                         │
│  Open WebUI — ChatGPT-like chat interface                │
└──────────────────────┬──────────────────────────────────┘
                       │ OpenAI-compatible API calls
                       ▼
┌─────────────────────────────────────────────────────────┐
│  NoeRelay Gateway (http://noerelay:8080/v1)              │
│  ┌──────────────────────────────────────────────────┐   │
│  │  Contract → Policy → Route → Execute → Verify    │   │
│  └──────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────┐   │
│  │  EPR Ledger + Evidence Receipts                   │   │
│  └──────────────────────────────────────────────────┘   │
└────────┬──────────────────────────────┬─────────────────┘
         │                              │
         ▼                              ▼
┌─────────────────────┐    ┌─────────────────────────────┐
│  Ollama (local)      │    │  OpenRouter (cloud)          │
│  localhost:11434     │    │  openrouter.ai/api/v1        │
│  - qwen3:8b          │    │  - openai/gpt-4o-mini        │
│  - llama3.2          │    │  - anthropic/claude-3.5      │
│  - ...               │    │  - google/gemini-flash       │
└─────────────────────┘    └─────────────────────────────┘
```

**Request flow:**
1. User types a message in Open WebUI
2. Open WebUI sends an OpenAI-compatible request to NoeRelay
3. NoeRelay compiles a task contract, applies governance policy
4. NoeRelay routes to the best model (local Ollama first, cloud OpenRouter fallback)
5. Response is verified through the verification DAG
6. Evidence receipt is generated and stored in the hash-linked ledger
7. Response (with EPR metadata) is returned to Open WebUI
8. Open WebUI displays the response to the user

---

## 7. Troubleshooting

### Connection Refused

**Symptom:** Open WebUI shows "Connection refused" or "Failed to fetch"

**Solutions:**
1. Verify NoeRelay is running: `curl http://localhost:8080/health`
2. Check Docker network: `docker network ls` — ensure both containers are on the same network
3. Check NoeRelay logs: `docker logs noerelay-noerelay-1`
4. Verify the base URL in Open WebUI settings is `http://noerelay:8080/v1` (not `localhost`)

### Model Not Found

**Symptom:** "Model not found" or model list is empty

**Solutions:**
1. Verify models endpoint: `curl http://localhost:8080/v1/models`
2. Check NoeRelay logs for startup errors
3. Ensure the portfolio file exists: `examples/candidate-actions.json`
4. Restart NoeRelay: `docker-compose restart noerelay`

### Authentication Error

**Symptom:** 401 Unauthorized

**Solutions:**
1. By default, NoeRelay auth is disabled on loopback. If you enabled auth, use a valid API key.
2. Create an API key: `curl -X POST http://localhost:8080/v1/api-keys -H "Content-Type: application/json" -d '{"name":"open-webui"}'`
3. Use the returned key in Open WebUI settings

### Streaming Not Working

**Symptom:** Non-streaming works but streaming doesn't

**Solutions:**
1. Verify streaming works directly: `curl -X POST http://localhost:8080/v1/chat/completions -H "Content-Type: application/json" -d '{"model":"noerelay/epr-1","messages":[{"role":"user","content":"Hi"}],"stream":true}'`
2. Check Open WebUI logs: `docker logs noerelay-openwebui`
3. Some Open WebUI versions have streaming issues with custom endpoints — try updating Open WebUI

### Empty Responses

**Symptom:** Response is empty or contains only EPR metadata

**Solutions:**
1. In stub mode, responses are simulated. Switch to live mode for real responses.
2. Set `NOERELAY_OPENROUTER_MODE=live` and provide `OPENROUTER_API_KEY`
3. Check that OpenRouter has available models

---

## 8. Advanced Configuration

### Using a Custom API Key

If you've enabled authentication in NoeRelay:

```bash
# Create an API key
curl -X POST http://localhost:8080/v1/api-keys \
  -H "Content-Type: application/json" \
  -d '{"name":"open-webui","role":"admin"}'

# Use the returned key in docker-compose
# docker-compose.openwebui.yml:
#   OPENAI_API_KEY=sk-your-key-here
```

### Enabling Live Mode with OpenRouter

```bash
# Set your OpenRouter API key
export OPENROUTER_API_KEY=sk-or-v1-your-key

# Start with live mode
docker-compose -f docker-compose.yml -f docker-compose.openwebui.yml up -d
```

Or in `.env`:
```
OPENROUTER_API_KEY=sk-or-v1-your-key
NOERELAY_OPENROUTER_MODE=live
```

### Using Local Ollama Models

The docker-compose already includes Ollama. To use local models:

```bash
# Pull a model into Ollama
docker exec noerelay-ollama-1 ollama pull qwen3:8b

# NoeRelay will automatically discover and prefer local models
```

### Governance Configuration

Add governance constraints to requests through Open WebUI by using the API directly:

```bash
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "noerelay/epr-1",
    "messages": [{"role": "user", "content": "Write a function"}],
    "governance": {
      "risk_class": "high",
      "data_policy": "zdr",
      "max_cost_usd": 0.50,
      "max_latency_ms": 120000
    }
  }'
```

### Accessing the NoeRelay Dashboard

While using Open WebUI, you can also access the NoeRelay dashboard:

- **Dashboard:** http://localhost:8080/dashboard
- **Metrics:** http://localhost:8080/metrics
- **API Reference:** http://localhost:8080/v1/models

---

## Next Steps

- Read the [Integration Guide](integration-guide.md) for other client tools
- Read the [API Reference](api-reference.md) for all 62+ endpoints
- Read the [Architecture](architecture.md) for EPR-1 design details
- Read the [Admin Guide](admin-guide.md) for tenant management and operations