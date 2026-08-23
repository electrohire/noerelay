# NoeRelay Integration Guide

## Connecting OpenAI-Compatible Tools

NoeRelay exposes an OpenAI-compatible API at `http://127.0.0.1:8080/v1`. Any tool
that supports custom OpenAI API endpoints can connect to NoeRelay.

---

### 1. Open WebUI

Open WebUI (formerly Ollama WebUI) is a popular web interface for LLMs. To connect
it to NoeRelay:

1. Start NoeRelay:

   ```bash
   scripts/start-dashboard.cmd live
   ```

2. In Open WebUI settings:
   - Go to **Settings → Connections → OpenAI API**
   - Set **Base URL**: `http://127.0.0.1:8080/v1`
   - Set **API Key**: (any value if auth is disabled, or your NoeRelay API key)
   - Click **"Test Connection"**

3. The model `noerelay/epr-1` will appear in the model dropdown.

4. All requests through Open WebUI will be routed through NoeRelay's governance
   pipeline:
   - Contract compilation
   - Policy checking (OpenAI exclusion, data policy)
   - Route selection (local Ollama first, cloud OpenRouter fallback)
   - Verification DAG
   - Evidence receipt generation
   - Hash-linked ledger

---

### 2. Zoo-Code (Single Agent Mode)

Zoo-Code uses OpenRouter for model routing. To use NoeRelay as the routing layer:

1. Start NoeRelay in live mode:

   ```bash
   scripts/start-dashboard.cmd live
   ```

2. In your `.zoo-code/` configuration, set the OpenRouter base URL to NoeRelay:

   ```json
   {
     "providers": {
       "fast": {
         "base_url": "http://127.0.0.1:8080/v1",
         "api_key": "your-noerelay-api-key",
         "model": "noerelay/epr-1"
       }
     }
   }
   ```

3. Zoo-Code will send requests to NoeRelay, which will:
   - Route to local Ollama models first (free, fast)
   - Escalate to cloud OpenRouter models if local fails
   - Apply governance (risk class, data policy, verification)
   - Track cost, HIR, RR, and produce evidence receipts

4. The single Zoo-Code agent is responsible for routing to other agents — NoeRelay
   handles the model routing within each agent's request.

---

### 3. Any OpenAI-Compatible Client

For any client that supports custom OpenAI endpoints:

- **Base URL**: `http://127.0.0.1:8080/v1`
- **API Key**: Your NoeRelay API key (create one via `POST /v1/api-keys` or use any
  value if auth is disabled)
- **Model**: `noerelay/epr-1`
- **Streaming**: Supported (`stream: true` returns SSE)
- **Governance**: Add a `governance` object to the request body:

  ```json
  {
    "model": "noerelay/epr-1",
    "messages": [
      {"role": "user", "content": "Write a function to sort a list"}
    ],
    "governance": {
      "risk_class": "low",
      "data_policy": "zdr",
      "max_cost_usd": 0.25,
      "max_latency_ms": 60000
    }
  }
  ```

---

### 4. Python SDK Example

```python
import urllib.request
import json

url = "http://127.0.0.1:8080/v1/chat/completions"
data = json.dumps({
    "model": "noerelay/epr-1",
    "messages": [{"role": "user", "content": "Hello"}],
    "governance": {"risk_class": "low"}
}).encode()

req = urllib.request.Request(url, data=data, method="POST", headers={
    "Content-Type": "application/json",
    "Authorization": "Bearer your-api-key"
})

with urllib.request.urlopen(req) as resp:
    result = json.loads(resp.read())
    print(result["choices"][0]["message"]["content"])
    print(result["epr"])  # Evidence receipt metadata
```

---

### 5. cURL Example

```bash
curl -X POST http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-api-key" \
  -d '{
    "model": "noerelay/epr-1",
    "messages": [{"role": "user", "content": "What is 2+2?"}],
    "governance": {"risk_class": "low", "data_policy": "zdr"}
  }'
```

---

### 6. LangChain Integration

```python
import os

from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url="http://127.0.0.1:8080/v1",
    api_key=os.environ.get("NOERELAY_API_KEY", "any-value"),
    model="noerelay/epr-1",
)

response = llm.invoke("Hello!")
print(response.content)
```

---

### 7. LlamaIndex Integration

Install the optional client integration with `python -m pip install llama-index-llms-openai`, then point it at NoeRelay:

```python
import os

from llama_index.llms.openai import OpenAI

llm = OpenAI(
    api_base="http://127.0.0.1:8080/v1",
    api_key=os.environ.get("NOERELAY_API_KEY", "any-value"),
    model="noerelay/epr-1",
)

response = llm.complete("What is 2+2?")
print(response.text)
```

The imported package is an optional client dependency, not a NoeRelay runtime dependency. The class name reflects the compatible protocol adapter; NoeRelay still rejects OpenAI upstream model routes.

---

### 8. Docker Compose with Open WebUI

```yaml
# docker-compose.override.yml
services:
  open-webui:
    image: ghcr.io/open-webui/open-webui:main
    ports:
      - "3000:8080"
    environment:
      - OPENAI_API_BASE_URL=http://noerelay:8080/v1
      - OPENAI_API_KEY=your-api-key
    depends_on:
      - noerelay
```

---

### 9. API Endpoints Reference

The endpoints in `spec/openapi.json` are the current Rust gateway contract. The additional routes below belong to the legacy Python reference server and may evolve or be replaced before a production release.

| Endpoint | Method | Description |
|---|---|---|
| `/v1/chat/completions` | POST | OpenAI-compatible chat completions |
| `/v1/models` | GET | List available models |
| `/v1/api-keys` | GET/POST | List or create API keys |
| `/v1/api-keys/{id}` | DELETE | Revoke an API key |
| `/v1/api-keys/{id}/rotate` | POST | Rotate an API key |
| `/v1/tenants` | GET/POST | List or create tenants |
| `/v1/tenants/{id}` | DELETE | Delete a tenant |
| `/v1/tenants/{id}/budget` | PUT | Update tenant budget |
| `/v1/alerts` | GET | List alerts |
| `/v1/alerts/{id}/acknowledge` | POST | Acknowledge an alert |
| `/v1/webhooks` | GET/POST | List or register webhooks |
| `/v1/webhooks/{id}` | DELETE | Delete a webhook |
| `/v1/config` | GET | Get configuration |
| `/v1/config/{key}` | PUT | Update a config value |
| `/v1/routing/portfolio` | GET | Get routing portfolio |
| `/v1/routing/candidates` | POST | Add a candidate |
| `/v1/routing/candidates/{id}` | DELETE | Remove a candidate |
| `/v1/governance/policy` | GET | Get routing policy |
| `/v1/governance/risk-classes` | GET | Get risk classes |
| `/v1/benchmarks/run` | POST | Run a benchmark |
| `/v1/benchmarks/results` | GET | Get benchmark results |
| `/v1/benchmarks/datasets` | GET | List available datasets |
| `/v1/benchmarks/compare` | GET | Compare model performance |
| `/v1/analytics/cost` | GET | Cost analytics |
| `/v1/analytics/performance` | GET | Performance analytics |
| `/v1/analytics/usage` | GET | Usage analytics |
| `/v1/analytics/escalations` | GET | Escalation analytics |
| `/v1/analytics/dashboard` | GET | Dashboard summary data |
| `/v1/analytics/audit` | GET | Audit log entries |
| `/v1/epr/ledger/events` | GET | Ledger events |
| `/v1/epr/ledger/verify/{id}` | POST | Verify chain integrity |
| `/v1/epr/ledger/export/{id}` | GET | Export ledger |
| `/v1/epr/runs/{id}` | GET | Get run details |
| `/v1/epr/runs/{id}/trace` | GET | Get decision trace |
| `/v1/admin/backup` | POST | Create backup |
| `/v1/admin/restore` | POST | Restore from backup |
| `/v1/export` | GET | Export all data |
| `/v1/import` | POST | Import data |
| `/health` | GET | Health check |
| `/metrics` | GET | Prometheus metrics |
| `/dashboard` | GET | Dashboard HTML |
| `/cache/stats` | GET | Cache statistics |
| `/models/local` | GET | Local models |
| `/models/cloud` | GET | Cloud models |
| `/models/ranking` | GET | Model ranking |
| `/models/recommendations` | GET | Download/removal recommendations |
| `/v1/models/pull` | POST | Pull a model |
| `/v1/models/{id}` | DELETE | Remove a model |

---

### 10. Governance Fields

When sending a chat completion request, you can include a `governance` object:

| Field | Type | Default | Description |
|---|---|---|---|
| `risk_class` | string | `"low"` | Risk classification: `low`, `medium`, `high`, `critical` |
| `data_policy` | string | `"zdr"` | Data policy: `zdr` (zero data retention), `retain` |
| `max_cost_usd` | number | `0.25` | Maximum cost in USD for this request |
| `max_latency_ms` | number | `60000` | Maximum latency in milliseconds |

---

### 11. Response Format

A successful chat completion response includes standard OpenAI fields plus
NoeRelay-specific metadata:

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1700000000,
  "model": "noerelay/epr-1",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "The answer is 4."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 5,
    "total_tokens": 15
  },
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
