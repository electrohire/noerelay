# NoeRelay Deployment Guide

> **Historical reference:** This document describes the legacy Python conformance gateway. It is not the Rust/PostgreSQL authority path. Use [production-deployment.md](production-deployment.md) and [quickstart.md](quickstart.md) for current deployment instructions.

## 1. Docker Deployment

### Prerequisites
- Docker 24+ and Docker Compose v2
- (Optional) NVIDIA Container Toolkit for GPU-accelerated local models

### Quick Start

For security assumptions and a production checklist, read [production-deployment.md](production-deployment.md) first. The Compose fallbacks are for local evaluation.

```bash
# Clone and build
git clone https://github.com/electrohire/noerelay.git
cd noerelay

# Start with Docker Compose
export NOERELAY_API_KEY="replace-with-a-random-key"
export NOERELAY_MASTER_KEY="replace-with-a-separate-random-key"
docker compose up -d

# Verify
curl http://127.0.0.1:8080/health
curl -H "Authorization: Bearer $NOERELAY_API_KEY" http://127.0.0.1:8080/v1/models
```

### Configuration

Set environment variables in `.env` or pass directly:

```bash
# Required for live mode
OPENROUTER_API_KEY=sk-or-v1-...
HF_TOKEN=hf_...

# Required for a non-loopback deployment
NOERELAY_AUTH_API_KEYS=replace-with-random-api-key
NOERELAY_MASTER_KEY=replace-with-separate-random-master-key
NOERELAY_CORS_ALLOWED_ORIGINS=https://chat.example.com

# Optional
NOERELAY_GATEWAY_PORT=8080
NOERELAY_LOG_LEVEL=INFO
NOERELAY_CACHE_ENABLED=1
NOERELAY_TLS_ENABLED=0
```

### Docker Compose Services

| Service | Port | Purpose |
|---------|------|---------|
| `noerelay` | 8080 | Gateway API (OpenAI-compatible) |
| `ollama` | 11434 | Local model inference (GPU-accelerated) |

### Volumes

| Volume | Mount | Purpose |
|--------|-------|---------|
| `noerelay-data` | `/data` | Persistent database, backups |
| `ollama-models` | `/root/.ollama` | Downloaded model weights |

### Health Checks

The gateway includes automatic health checks:
- **Interval**: 30s
- **Timeout**: 5s
- **Start period**: 10s
- **Retries**: 3

Health endpoint: `GET /health` returns `{"status": "healthy", "version": "0.1.0"}`

---

## 2. Kubernetes Deployment

### Prerequisites
- Kubernetes 1.27+
- kubectl configured
- (Optional) NVIDIA GPU operator for GPU workloads

### Deploy

```bash
# Create namespace
kubectl create namespace noerelay

# Set secrets (replace with actual values)
kubectl create secret generic noerelay-secrets \
  --from-literal=openrouter-api-key=sk-or-v1-... \
  --from-literal=hf-token=hf_... \
  --from-literal=noerelay-api-keys=replace-with-random-api-key \
  --from-literal=noerelay-master-key=replace-with-separate-random-master-key \
  --from-literal=metrics-api-key=replace-with-random-api-key \
  -n noerelay

# Apply manifests
kubectl apply -f deploy/kubernetes/ -n noerelay

# Verify
kubectl get pods -n noerelay
kubectl port-forward svc/noerelay 8080:80 -n noerelay
curl http://127.0.0.1:8080/health
```

### Manifests

| File | Resource | Purpose |
|------|----------|---------|
| `deployment.yaml` | Deployment | Gateway pod with health probes |
| `service.yaml` | Service | ClusterIP service on port 80 → 8080 |
| `pvc.yaml` | PersistentVolumeClaim | 10Gi persistent storage for data |
| `secret.yaml` | Secret | API keys and tokens |

### Resource Limits

| Resource | Request | Limit |
|----------|---------|-------|
| CPU | 250m | 1000m |
| Memory | 256Mi | 1Gi |

### Probes

| Probe | Path | Initial Delay | Period |
|-------|------|---------------|--------|
| Readiness | `/health` | 5s | 10s |
| Liveness | `/health` | 15s | 20s |

---

## 3. Configuration Reference

### Environment Variables

All configuration is done via environment variables prefixed with `NOERELAY_`.

#### Core Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `NOERELAY_GATEWAY_HOST` | `127.0.0.1` | Bind address |
| `NOERELAY_GATEWAY_PORT` | `8080` | Bind port (0 = ephemeral) |
| `NOERELAY_OPENROUTER_MODE` | `stub` | `stub` or `live` |
| `NOERELAY_EXTERNAL_BASE_URL` | `http://127.0.0.1:8080` | External URL for receipt URLs |

#### Policy & Portfolio

| Variable | Default | Description |
|----------|---------|-------------|
| `NOERELAY_POLICY_PATH` | `spec/routing-policy.json` | Routing policy JSON |
| `NOERELAY_STATE_MACHINE_PATH` | `spec/verification-state-machine.json` | State machine JSON |
| `NOERELAY_PORTFOLIO_PATH` | `examples/candidate-actions.json` | Candidate action registry |

#### Governance

| Variable | Default | Description |
|----------|---------|-------------|
| `NOERELAY_DEFAULT_MAX_COST_USD` | `0.25` | Default max cost per request |
| `NOERELAY_DEFAULT_MAX_LATENCY_MS` | `60000` | Default max latency |

#### Security

| Variable | Default | Description |
|----------|---------|-------------|
| `NOERELAY_AUTH_API_KEYS` | (none) | Comma-separated API keys for auth |
| `NOERELAY_AUTH_REQUIRED` | Automatic | Defaults to enabled for non-loopback binds |
| `NOERELAY_MASTER_KEY` | (none) | Required to enable managed secret encryption |
| `NOERELAY_CORS_ALLOWED_ORIGINS` | Loopback port 3000 | Comma-separated exact browser origins; no wildcard |
| `NOERELAY_MAX_REQUEST_BODY_BYTES` | `4194304` | Maximum JSON request body |
| `NOERELAY_RUN_RETENTION_MAX` | `10000` | Maximum retained run records |
| `NOERELAY_TLS_ENABLED` | `0` | Enable TLS (`0` or `1`) |
| `NOERELAY_TLS_CERT_PATH` | (none) | Path to TLS certificate |
| `NOERELAY_TLS_KEY_PATH` | (none) | Path to TLS private key |

#### Rate Limiting

| Variable | Default | Description |
|----------|---------|-------------|
| `NOERELAY_RATE_LIMIT_RATE` | `10.0` | Requests per second per key |
| `NOERELAY_RATE_LIMIT_BURST` | `20` | Burst capacity |

#### Database

| Variable | Default | Description |
|----------|---------|-------------|
| `NOERELAY_DATABASE_ENABLED` | `1` | Enable SQLite database |
| `NOERELAY_DATABASE_PATH` | `.noerelay/noerelay.db` | Database file path |

#### Caching

| Variable | Default | Description |
|----------|---------|-------------|
| `NOERELAY_CACHE_ENABLED` | `0` | Enable response cache |
| `NOERELAY_CACHE_MAX_SIZE` | `100` | Max cache entries |
| `NOERELAY_CACHE_TTL_SECONDS` | `3600` | Cache TTL |

#### Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `NOERELAY_LOG_LEVEL` | `INFO` | Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL) |
| `NOERELAY_LOG_OUTPUT` | `stdout` | Output destination (`stdout` or `file`) |
| `NOERELAY_LOG_FILE_PATH` | `.noerelay/noerelay.log` | Log file path |

#### Local Models

| Variable | Default | Description |
|----------|---------|-------------|
| `NOERELAY_LOCAL_MODEL_ENABLED` | `1` | Enable local model discovery |
| `NOERELAY_LOCAL_MODEL_URL` | `http://127.0.0.1:11434` | Ollama API URL |

#### Escalation

| Variable | Default | Description |
|----------|---------|-------------|
| `NOERELAY_ESCALATION_HIR_THRESHOLD` | `0.15` | Human intervention rate threshold |
| `NOERELAY_ESCALATION_RR_THRESHOLD` | `0.25` | Rework rate threshold |

#### External API Keys

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENROUTER_API_KEY` | (none) | OpenRouter API key |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | OpenRouter base URL |
| `OPENROUTER_HTTP_REFERER` | GitHub URL | HTTP referer header |
| `OPENROUTER_APP_TITLE` | `NoeRelay` | App title header |
| `HF_TOKEN` | (none) | HuggingFace Hub token |

#### System

| Variable | Default | Description |
|----------|---------|-------------|
| `NOERELAY_ENABLE_HEALTH_ENDPOINT` | `1` | Enable `/health` endpoint |
| `NOERELAY_ENABLE_METRICS_ENDPOINT` | `1` | Enable `/metrics` endpoint |
| `NOERELAY_LIVE_TESTS` | `0` | Enable live API tests |
| `NOERELAY_PERSISTENCE_DIR` | (none) | Directory for file-based persistence |

---

## 4. TLS Setup

### Generate Self-Signed Certificate (Development)

```bash
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem \
  -days 365 -nodes -subj "/CN=localhost"
```

### Configure TLS

```bash
export NOERELAY_TLS_ENABLED=1
export NOERELAY_TLS_CERT_PATH=/path/to/cert.pem
export NOERELAY_TLS_KEY_PATH=/path/to/key.pem
```

### Production TLS

For production, use a reverse proxy (nginx, Caddy, Traefik) with Let's Encrypt:
- Terminate TLS at the reverse proxy
- Forward plain HTTP to the NoeRelay gateway on localhost
- Set `NOERELAY_TLS_ENABLED=0` when using a reverse proxy

---

## 5. Backup and Restore

### Backup

```bash
# Via API
curl -X POST http://127.0.0.1:8080/v1/admin/backup

# Via Docker
docker compose exec noerelay python -c "
from gateway.database import Database
db = Database('/data/noerelay.db')
db.backup('/data/backup.db')
"

# Copy backup from container
docker compose cp noerelay:/data/backup.db ./backup.db
```

### Restore

```bash
# Via API
curl -X POST http://127.0.0.1:8080/v1/admin/restore \
  -H "Content-Type: application/json" \
  -d '{"backup_path": "/data/backup.db"}'

# Via Docker
docker compose cp ./backup.db noerelay:/data/backup.db
docker compose exec noerelay python -c "
from gateway.database import Database
db = Database('/data/noerelay.db')
db.restore('/data/backup.db')
"
```

### Export (JSON)

```bash
curl http://127.0.0.1:8080/v1/admin/export
```

---

## 6. Monitoring Setup

### Prometheus

The gateway exposes Prometheus metrics at `GET /metrics`:

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'noerelay'
    scrape_interval: 15s
    static_configs:
      - targets: ['noerelay:8080']
```

### Available Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `noerelay_runs_total` | Counter | Total runs processed |
| `noerelay_runs_accepted_total` | Counter | Runs accepted |
| `noerelay_runs_escalated_total` | Counter | Runs escalated |
| `noerelay_runs_rejected_total` | Counter | Runs rejected |
| `noerelay_active_runs` | Gauge | Currently active runs |
| `noerelay_cache_size` | Gauge | Cache entry count |
| `noerelay_local_models_count` | Gauge | Discovered local models |
| `noerelay_request_duration_seconds` | Histogram | Request latency |
| `noerelay_tokens_per_request` | Histogram | Tokens per request |
| `noerelay_cost_per_request_usd` | Histogram | Cost per request |
| `noerelay_model_requests_total` | Counter | Per-model requests |
| `noerelay_model_tokens_total` | Counter | Per-model tokens |
| `noerelay_model_cost_total` | Counter | Per-model cost |
| `noerelay_tenant_spend_total` | Counter | Per-tenant spend |
| `noerelay_risk_class_runs_total` | Counter | Per-risk-class runs |

### Grafana Dashboard

Import the Prometheus data source and create panels for:
- Request rate (QPS)
- Error rate (4xx, 5xx)
- P50/P95/P99 latency
- Cost per model and tenant
- Escalation rate
- Cache hit rate

### Health Check

```bash
curl http://127.0.0.1:8080/health
# {"status": "healthy", "version": "0.1.0"}
```

---

## 7. SIEM Integration

### Supported Formats

| Format | Use Case |
|--------|----------|
| JSON | Splunk HEC, Datadog Logs API |
| CEF | Splunk, ArcSight, QRadar |
| LEEF | IBM QRadar |
| Syslog | Any syslog-compatible SIEM |

### Configuration

```python
from gateway.siem import SIEMIntegration

siem = SIEMIntegration(
    endpoint="https://splunk-hec.example.com:8088/services/collector",
    api_key="Splunk-...",
    format="json",  # or "cef", "leef", "syslog"
)
```

### SIEM-Ready Events

- **Audit events**: All API calls, authentication attempts, permission changes
- **Ledger events**: Every state transition in the evidence pipeline
- **Security events**: Failed auth, rate limit hits, policy violations

---

## 8. API Key Management

### Create API Key

```bash
curl -X POST http://127.0.0.1:8080/v1/api-keys \
  -H "Content-Type: application/json" \
  -d '{
    "name": "production-key",
    "role": "operator",
    "rate_limit_rate": 10.0,
    "rate_limit_burst": 20,
    "tenant_id": "default"
  }'
```

### List API Keys

```bash
curl http://127.0.0.1:8080/v1/api-keys
```

### Rotate API Key

```bash
curl -X POST http://127.0.0.1:8080/v1/api-keys/{key_id}/rotate
```

### Revoke API Key

```bash
curl -X DELETE http://127.0.0.1:8080/v1/api-keys/{key_id}
```

---

## 9. Multi-Tenancy Setup

### Create Tenant

```bash
curl -X POST http://127.0.0.1:8080/v1/tenants \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "team-alpha",
    "name": "Team Alpha",
    "budget_daily_usd": 50.0,
    "budget_monthly_usd": 1000.0
  }'
```

### Check Tenant Budget

```bash
curl http://127.0.0.1:8080/v1/tenants/team-alpha/budget
```

### List Tenants

```bash
curl http://127.0.0.1:8080/v1/tenants
```

### Tenant Isolation

- Each tenant has independent API keys
- Budgets are enforced per-tenant
- Secrets are namespaced by tenant
- Webhooks are scoped to tenants
- Metrics include per-tenant spend tracking
