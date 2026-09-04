# NoeRelay API Reference

## Base URL

```
http://127.0.0.1:8080
```

## Authentication

API keys are passed via the `Authorization` header:

```
Authorization: Bearer noerelay_sk_...
```

When authentication is disabled (default), no header is required.

## Content Negotiation

- **Request**: `Content-Type: application/json`
- **Response**: `Content-Type: application/json`
- **Metrics**: `GET /metrics` returns `text/plain` (Prometheus) by default;
  pass `Accept: application/json` for JSON format.

---

## Endpoints

### OpenAI-Compatible

#### `POST /v1/chat/completions`

Chat completions endpoint (OpenAI-compatible).

**Request:**
```json
{
  "model": "axiovex-agni",
  "messages": [
    {"role": "user", "content": "Hello"}
  ],
  "governance": {
    "risk_class": "low",
    "max_cost_usd": 0.25
  },
  "stream": false
}
```

**Response (200):**
```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "model": "axiovex-agni",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "..."
    }
  }],
  "epr": {
    "run_id": "...",
    "trace_id": "...",
    "evidence_receipt_url": "http://127.0.0.1:8080/v1/epr/runs/{run_id}"
  }
}
```

**Streaming:** Set `"stream": true` for SSE (text/event-stream).

**Error Codes:**
| Status | Code | Meaning |
|--------|------|---------|
| 400 | `missing_field` | Required field missing |
| 403 | `model_denied_by_policy` | Model denied by routing policy |
| 404 | `model_not_found` | Model not in portfolio |
| 422 | `governance_validation_error` | Invalid governance |
| 424 | `escalation` | Execution escalated |

---

#### `POST /v1/responses`

OpenAI Responses API endpoint.

**Request:**
```json
{
  "model": "axiovex-agni",
  "input": "What is the capital of France?",
  "instructions": "Answer concisely.",
  "governance": {"risk_class": "low"},
  "stream": false
}
```

**Response (200):**
```json
{
  "id": "resp_...",
  "object": "response",
  "model": "axiovex-agni",
  "output": [{
    "type": "message",
    "role": "assistant",
    "content": [{"type": "output_text", "text": "Paris"}]
  }],
  "epr": {
    "run_id": "...",
    "evidence_receipt_url": "..."
  }
}
```

---

#### `GET /v1/models`

List available models (OpenAI-compatible).

**Response (200):**
```json
{
  "object": "list",
  "data": [
    {"id": "axiovex-agni", "object": "model", "owned_by": "axiovex"}
  ]
}
```

---

### System

#### `GET /health`

Health check endpoint.

**Response (200):**
```json
{"status": "healthy", "version": "0.1.0"}
```

---

#### `GET /metrics`

Prometheus metrics endpoint.

**Default (text/plain):**
```
# HELP noerelay_runs_total Total number of runs processed.
# TYPE noerelay_runs_total counter
noerelay_runs_total 680
...
```

**JSON (Accept: application/json):**
```json
{
  "runs_total": 680,
  "runs_accepted": 500,
  "runs_escalated": 45
}
```

---

#### `GET /cache/stats`

Response cache statistics.

**Response (200):**
```json
{
  "cache_enabled": true,
  "size": 42,
  "max_size": 100,
  "hit_rate": 0.85
}
```

---

### EPR (Evidence Pipeline Runtime)

#### `GET /v1/epr/runs/{run_id}`

Get evidence receipt for a run.

**Response (200):**
```json
{
  "run_id": "...",
  "trace_id": "...",
  "status": "accepted",
  "model_id": "qwen3:8b",
  "risk_class": "low",
  "verification_results": [...],
  "timestamp": "2026-01-01T00:00:00Z"
}
```

**Errors:** 404 `run_not_found`

---

#### `GET /v1/epr/runs/{run_id}/trace`

Get full decision trace for a run.

**Response (200):**
```json
{
  "run_id": "...",
  "trace_id": "...",
  "decision_trace": [...]
}
```

---

#### `GET /v1/epr/ledger/events`

Query ledger events with optional filters.

**Query Parameters:**
| Param | Type | Description |
|-------|------|-------------|
| `run_id` | string | Filter by run |
| `event_type` | string | Filter by event type |
| `actor` | string | Filter by actor ID |
| `from` | ISO 8601 | Start timestamp |
| `to` | ISO 8601 | End timestamp |

**Response (200):**
```json
{
  "object": "list",
  "data": [...],
  "count": 42
}
```

---

#### `GET /v1/epr/ledger/chain/{run_id}`

Get hash-linked chain for a run.

**Response (200):**
```json
{
  "run_id": "...",
  "chain": [...],
  "head_hash": "...",
  "event_count": 15
}
```

---

#### `POST /v1/epr/ledger/verify/{run_id}`

Verify chain integrity.

**Response (200):**
```json
{
  "run_id": "...",
  "valid": true,
  "message": "Chain integrity verified",
  "event_count": 15,
  "head_hash": "..."
}
```

---

#### `GET /v1/epr/ledger/export/{run_id}`

Export full chain as JSON.

**Response (200):**
```json
{
  "run_id": "...",
  "trace_id": "...",
  "exported_at": "...",
  "events": [...],
  "head_hash": "...",
  "event_count": 15
}
```

---

### Model Management

#### `GET /models/local`

List discovered local models (Ollama).

**Response (200):**
```json
{
  "object": "list",
  "data": [
    {"id": "qwen3:8b", "object": "model", "owned_by": "local"}
  ]
}
```

---

#### `GET /models/recommendations`

Get download and removal recommendations.

**Response (200):**
```json
{
  "object": "model_recommendations",
  "local_models": ["qwen3:8b"],
  "download_recommendations": [...],
  "removal_recommendations": [...]
}
```

---

#### `GET /models/cloud`

List OpenRouter cloud models.

**Response (200):**
```json
{
  "object": "list",
  "data": [...]
}
```

---

#### `GET /models/ranking`

Model performance ranking with true cost breakdown.

**Response (200):**
```json
{
  "object": "model_ranking",
  "ranked_models": [...],
  "total_models_tracked": 5,
  "cost_model_defaults": {...}
}
```

---

#### `POST /v1/models/pull`

Download a model via Ollama.

**Request:**
```json
{"model_name": "qwen3:8b"}
```

**Response (200):**
```json
{"status": "pulling", "model": "qwen3:8b", "result": {...}}
```

---

#### `DELETE /v1/models/{model_name}`

Remove a local model.

**Response (200):**
```json
{"status": "deleted", "model": "qwen3:8b", "result": {...}}
```

---

#### `POST /v1/models/register`

Register a custom model in the portfolio.

**Request:**
```json
{
  "model_id": "my-model",
  "provider_family": "custom",
  "inference_gateway": "ollama"
}
```

**Response (200):**
```json
{"status": "registered", "model": {...}}
```

---

### Benchmarks

#### `POST /v1/benchmarks/run`

Run a benchmark.

**Request:**
```json
{
  "cohort_name": "coding-tasks",
  "model_id": "qwen3:8b",
  "accuracy": 0.85,
  "total_tokens": 5000,
  "total_cost_usd": 0.05
}
```

---

#### `GET /v1/benchmarks/results`

List benchmark results.

**Query Parameters:**
| Param | Default | Description |
|-------|---------|-------------|
| `cohort` | (all) | Filter by cohort |
| `model_id` | (all) | Filter by model |
| `limit` | 50 | Page size |
| `offset` | 0 | Page offset |

---

#### `GET /v1/benchmarks/compare`

Compare models on benchmarks.

**Query Parameters:**
| Param | Description |
|-------|-------------|
| `model_ids` | Comma-separated model IDs |

---

### Governance

#### `GET /v1/governance/policy`

View current routing policy.

**Response (200):**
```json
{"policy": {...}}
```

---

#### `PUT /v1/governance/policy`

Update routing policy.

**Request:**
```json
{
  "inference": {...},
  "risk_gates": {...}
}
```

---

#### `GET /v1/governance/risk-classes`

List risk classes and their gates.

**Response (200):**
```json
{
  "risk_classes": {
    "low": {"name": "low", "gates": {...}},
    "medium": {...},
    "high": {...},
    "critical": {...}
  }
}
```

---

#### `PUT /v1/governance/risk-class/{class}`

Configure risk class gates.

**Request:**
```json
{
  "gates": {
    "schema": true,
    "policy": true,
    "deterministic_acceptance": true
  }
}
```

---

### Routing

#### `GET /v1/routing/portfolio`

View current model portfolio.

**Response (200):**
```json
{
  "object": "portfolio",
  "data": [...],
  "count": 5
}
```

---

#### `POST /v1/routing/candidates`

Add a candidate to the portfolio.

**Request:**
```json
{
  "candidate_id": "qwen3:8b",
  "action_kind": "inference"
}
```

---

#### `PUT /v1/routing/candidates/{id}`

Update a candidate.

---

#### `DELETE /v1/routing/candidates/{id}`

Remove a candidate.

---

### API Keys

#### `POST /v1/api-keys`

Create a new API key.

**Request:**
```json
{
  "name": "my-key",
  "role": "operator",
  "rate_limit_rate": 10.0,
  "rate_limit_burst": 20,
  "tenant_id": "default"
}
```

**Response (201):**
```json
{
  "key_id": "...",
  "name": "my-key",
  "key": "noerelay_sk_...",
  "role": "operator",
  "created_at": "..."
}
```

---

#### `GET /v1/api-keys`

List API keys.

**Query Parameters:**
| Param | Description |
|-------|-------------|
| `tenant_id` | Filter by tenant |

---

#### `POST /v1/api-keys/{id}/rotate`

Rotate an API key.

---

#### `DELETE /v1/api-keys/{id}`

Revoke an API key.

---

### Analytics

#### `GET /v1/analytics/cost`

Cost breakdown by model, risk class, time range.

**Query Parameters:**
| Param | Description |
|-------|-------------|
| `from` | Start timestamp |
| `to` | End timestamp |

---

#### `GET /v1/analytics/performance`

Model performance trends.

**Query Parameters:**
| Param | Description |
|-------|-------------|
| `model_id` | Filter by model |

---

#### `GET /v1/analytics/usage`

Request volume, tokens, peak usage.

---

#### `GET /v1/analytics/escalations`

Escalation analysis (HIR, RR, trends).

---

#### `GET /v1/analytics/audit`

Audit trail queries.

**Query Parameters:**
| Param | Description |
|-------|-------------|
| `actor_id` | Filter by actor |
| `action` | Filter by action |
| `from` | Start timestamp |
| `to` | End timestamp |
| `limit` | Page size |
| `offset` | Page offset |

---

### Export/Import

#### `GET /v1/export`

Export all data as JSON.

---

#### `POST /v1/import`

Import data from JSON.

**Request:**
```json
{"import_path": ".noerelay/export.json"}
```

---

### Admin

#### `POST /v1/admin/backup`

Backup the database.

**Response (200):**
```json
{"status": "ok", "backup_path": ".noerelay/backup.db"}
```

---

#### `POST /v1/admin/restore`

Restore from backup.

**Request:**
```json
{"backup_path": ".noerelay/backup.db"}
```

---

#### `GET /v1/admin/export`

Export database as JSON.

---

### Tenants

#### `GET /v1/tenants`

List all tenants.

---

#### `POST /v1/tenants`

Create a tenant.

**Request:**
```json
{
  "tenant_id": "team-alpha",
  "name": "Team Alpha",
  "budget_daily_usd": 50.0,
  "budget_monthly_usd": 1000.0
}
```

---

#### `PUT /v1/tenants/{id}`

Update a tenant.

---

#### `DELETE /v1/tenants/{id}`

Delete a tenant.

---

#### `GET /v1/tenants/{id}/budget`

Check tenant budget.

---

### Alerts

#### `GET /v1/alerts`

List alerts.

**Query Parameters:**
| Param | Description |
|-------|-------------|
| `severity` | Filter by severity |
| `acknowledged` | `true` or `false` |
| `limit` | Page size |

---

#### `POST /v1/alerts/rules`

Add an alert rule.

**Request:**
```json
{
  "name": "high-cost-alert",
  "alert_type": "cost_threshold",
  "condition": {"max_cost_usd": 1.0},
  "severity": "warning"
}
```

---

#### `POST /v1/alerts/{id}/acknowledge`

Acknowledge an alert.

**Request:**
```json
{"acknowledged_by": "admin"}
```

---

### Webhooks

#### `GET /v1/webhooks`

List webhooks.

**Query Parameters:**
| Param | Description |
|-------|-------------|
| `tenant_id` | Filter by tenant |

---

#### `POST /v1/webhooks`

Register a webhook.

**Request:**
```json
{
  "url": "https://example.com/webhook",
  "events": ["run.completed", "run.escalated"],
  "secret": "whsec_...",
  "tenant_id": "default"
}
```

---

#### `DELETE /v1/webhooks/{id}`

Delete a webhook.

---

### Config

#### `GET /v1/config`

Get all config values.

---

#### `PUT /v1/config/{key}`

Set a config value.

**Request:**
```json
{
  "value": "new-value",
  "updated_by": "admin"
}
```

---

### Secrets

#### `GET /v1/secrets`

List secrets.

**Query Parameters:**
| Param | Description |
|-------|-------------|
| `tenant_id` | Filter by tenant (default: "default") |

---

#### `POST /v1/secrets`

Store a secret.

**Request:**
```json
{
  "name": "my-secret",
  "value": "secret-value",
  "description": "Optional description",
  "tenant_id": "default"
}
```

---

#### `DELETE /v1/secrets/{name}`

Delete a secret.

---

## Error Format

All errors follow the OpenAI error format:

```json
{
  "error": {
    "message": "Human-readable error description",
    "type": "invalid_request_error",
    "param": "model",
    "code": "missing_field"
  }
}
```

### Error Types

| Type | Description |
|------|-------------|
| `invalid_request_error` | Malformed request |
| `governance_validation_error` | Invalid governance |
| `policy_denied_error` | Policy violation |
| `server_error` | Internal error |
| `upstream_error` | Upstream service failure |

### Error Codes

| Code | HTTP Status | Meaning |
|------|-------------|---------|
| `missing_field` | 400 | Required field missing |
| `invalid_json` | 400 | Invalid JSON body |
| `invalid_policy` | 400 | Invalid policy structure |
| `invalid_risk_class` | 400 | Unknown risk class |
| `model_denied_by_policy` | 403 | Model denied |
| `model_not_found` | 404 | Model not in portfolio |
| `run_not_found` | 404 | Run not in registry |
| `candidate_not_found` | 404 | Candidate not in portfolio |
| `key_not_found` | 404 | API key not found |
| `tenant_not_found` | 404 | Tenant not found |
| `alert_not_found` | 404 | Alert not found |
| `webhook_not_found` | 404 | Webhook not found |
| `secret_not_found` | 404 | Secret not found |
| `backup_not_found` | 404 | Backup file not found |
| `import_not_found` | 404 | Import file not found |
| `method_not_allowed` | 405 | HTTP method not supported |
| `governance_invalid` | 422 | Governance validation failed |
| `unsupported_input_item` | 400 | Unsupported input format |
| `database_not_enabled` | 501 | Database not enabled |
| `tenants_not_enabled` | 501 | Tenant manager not available |
| `alerts_not_enabled` | 501 | Alert manager not available |
| `webhooks_not_enabled` | 501 | Webhook manager not available |
| `config_not_enabled` | 501 | Config manager not available |
| `secrets_not_enabled` | 501 | Secret manager not available |
| `insufficient_permissions` | 403 | RBAC denied |
| `openrouter_unavailable` | 502 | OpenRouter API unavailable |
| `backup_failed` | 500 | Backup operation failed |
| `restore_failed` | 500 | Restore operation failed |
| `export_failed` | 500 | Export operation failed |
| `import_failed` | 500 | Import operation failed |
| `key_creation_failed` | 500 | API key creation failed |
| `key_list_failed` | 500 | API key listing failed |
| `key_revoke_failed` | 500 | API key revocation failed |
| `key_rotate_failed` | 500 | API key rotation failed |
| `analytics_failed` | 500 | Analytics query failed |
| `benchmark_failed` | 500 | Benchmark execution failed |
| `benchmark_list_failed` | 500 | Benchmark listing failed |
| `tenant_create_failed` | 500 | Tenant creation failed |
| `tenant_update_failed` | 500 | Tenant update failed |
| `tenants_failed` | 500 | Tenant listing failed |
| `escalation` | 424 | Execution escalated |

## Rate Limiting

Rate limits are enforced per API key using a token bucket algorithm:
- **Default rate**: 10 requests/second
- **Default burst**: 20 requests
- Configurable via `NOERELAY_RATE_LIMIT_RATE` and `NOERELAY_RATE_LIMIT_BURST`

Rate-limited responses return HTTP 429.

## Pagination

Paginated endpoints return:

```json
{
  "data": [...],
  "pagination": {
    "total": 100,
    "limit": 50,
    "offset": 0,
    "has_more": true
  }
}
