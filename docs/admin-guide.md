# NoeRelay Admin Guide

## 1. Overview

This guide covers administrative operations for the NoeRelay gateway, including
backup/restore, tenant management, alerting, webhooks, secrets, configuration,
and model lifecycle management.

---

## 2. Backup and Restore

### Backup

```bash
# Via API
curl -X POST http://127.0.0.1:8080/v1/admin/backup

# Response
{"status": "ok", "backup_path": ".noerelay/backup.db"}
```

### Restore

```bash
curl -X POST http://127.0.0.1:8080/v1/admin/restore \
  -H "Content-Type: application/json" \
  -d '{"backup_path": ".noerelay/backup.db"}'
```

### Export (JSON)

```bash
curl http://127.0.0.1:8080/v1/admin/export
```

### Import (JSON)

```bash
curl -X POST http://127.0.0.1:8080/v1/import \
  -H "Content-Type: application/json" \
  -d '{"import_path": ".noerelay/export.json"}'
```

### Automated Backups

Set up a cron job for periodic backups:

```bash
# Daily backup at 2 AM
0 2 * * * curl -X POST http://127.0.0.1:8080/v1/admin/backup
```

---

## 3. Tenant Management

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

### Update Tenant

```bash
curl -X PUT http://127.0.0.1:8080/v1/tenants/team-alpha \
  -H "Content-Type: application/json" \
  -d '{"budget_daily_usd": 100.0}'
```

### Delete Tenant

```bash
curl -X DELETE http://127.0.0.1:8080/v1/tenants/team-alpha
```

### Check Budget

```bash
curl http://127.0.0.1:8080/v1/tenants/team-alpha/budget
```

### Tenant Budget Enforcement

- **Daily budget**: Resets at midnight UTC
- **Monthly budget**: Resets on the 1st of each month
- When budget is exceeded, requests are rejected with status 402
- Budget alerts are raised at 80% and 100% thresholds

---

## 4. Alert Management

### List Alerts

```bash
# All alerts
curl http://127.0.0.1:8080/v1/alerts

# Filtered
curl "http://127.0.0.1:8080/v1/alerts?severity=critical&acknowledged=false"
```

### Add Alert Rule

```bash
curl -X POST http://127.0.0.1:8080/v1/alerts/rules \
  -H "Content-Type: application/json" \
  -d '{
    "name": "high-cost-alert",
    "alert_type": "cost_threshold",
    "condition": {"max_cost_usd": 1.0},
    "severity": "warning"
  }'
```

### Acknowledge Alert

```bash
curl -X POST http://127.0.0.1:8080/v1/alerts/{alert_id}/acknowledge \
  -H "Content-Type: application/json" \
  -d '{"acknowledged_by": "admin@example.com"}'
```

### Alert Severity Levels

| Severity | Meaning | Response |
|----------|---------|----------|
| `info` | Informational | No action required |
| `warning` | Potential issue | Monitor and investigate |
| `critical` | Immediate action needed | Block affected operations |

---

## 5. Webhook Management

### Register Webhook

```bash
curl -X POST http://127.0.0.1:8080/v1/webhooks \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com/webhook",
    "events": ["run.completed", "run.escalated", "alert.raised"],
    "secret": "whsec_your_webhook_secret",
    "tenant_id": "default"
  }'
```

### List Webhooks

```bash
curl http://127.0.0.1:8080/v1/webhooks
curl "http://127.0.0.1:8080/v1/webhooks?tenant_id=team-alpha"
```

### Delete Webhook

```bash
curl -X DELETE http://127.0.0.1:8080/v1/webhooks/{webhook_id}
```

### Webhook Event Types

| Event | Description |
|-------|-------------|
| `run.started` | Inference run started |
| `run.completed` | Run completed successfully |
| `run.escalated` | Run escalated to human |
| `run.rejected` | Run rejected by policy |
| `alert.raised` | New alert triggered |
| `alert.acknowledged` | Alert acknowledged |
| `budget.exceeded` | Tenant budget exceeded |
| `key.created` | API key created |
| `key.revoked` | API key revoked |

### Webhook Payload Format

```json
{
  "event": "run.completed",
  "timestamp": "2026-01-01T00:00:00Z",
  "data": {
    "run_id": "...",
    "status": "accepted",
    "model_id": "qwen3:8b"
  },
  "signature": "sha256=..."
}
```

Webhook signatures use HMAC-SHA256 with the webhook secret.

---

## 6. Secret Management

### Store Secret

```bash
curl -X POST http://127.0.0.1:8080/v1/secrets \
  -H "Content-Type: application/json" \
  -d '{
    "name": "openrouter-key-backup",
    "value": "sk-or-v1-...",
    "description": "Backup OpenRouter API key",
    "tenant_id": "default"
  }'
```

### List Secrets

```bash
curl "http://127.0.0.1:8080/v1/secrets?tenant_id=default"
```

### Delete Secret

```bash
curl -X DELETE "http://127.0.0.1:8080/v1/secrets/openrouter-key-backup?tenant_id=default"
```

### Secret Scoping

- Secrets are namespaced by tenant
- Secret values are never returned in list responses
- Only the secret name and metadata are exposed

---

## 7. Configuration Management

### View All Config

```bash
curl http://127.0.0.1:8080/v1/config
```

### Set Config Value

```bash
curl -X PUT http://127.0.0.1:8080/v1/config/routing_policy \
  -H "Content-Type: application/json" \
  -d '{
    "value": {"inference": {"default": "auto"}},
    "updated_by": "admin"
  }'
```

### Configurable Keys

Environment variables are the primary configuration mechanism. The config
management API stores runtime-overridable values that persist in the database:

- `routing_policy` — Runtime policy overrides
- Custom keys as needed by deployment

---

## 8. Model Lifecycle Management

### Discover Local Models

```bash
curl http://127.0.0.1:8080/models/local
```

### Pull Model

```bash
curl -X POST http://127.0.0.1:8080/v1/models/pull \
  -H "Content-Type: application/json" \
  -d '{"model_name": "qwen3:8b"}'
```

### Remove Model

```bash
curl -X DELETE http://127.0.0.1:8080/v1/models/qwen3:8b
```

### Get Recommendations

```bash
curl http://127.0.0.1:8080/models/recommendations
```

### Register Custom Model

```bash
curl -X POST http://127.0.0.1:8080/v1/models/register \
  -H "Content-Type: application/json" \
  -d '{
    "model_id": "my-custom-model",
    "provider_family": "custom",
    "inference_gateway": "ollama"
  }'
```

### Model Ranking

```bash
curl http://127.0.0.1:8080/models/ranking
```

### Model Lifecycle States

| State | Description |
|-------|-------------|
| `discovered` | Found via Ollama/HF but not in portfolio |
| `registered` | Added to portfolio |
| `active` | Available for routing |
| `deprecated` | Marked for removal, no new routes |
| `removed` | Deleted from local storage |

---

## 9. API Key Management

### Create Key

```bash
curl -X POST http://127.0.0.1:8080/v1/api-keys \
  -H "Content-Type: application/json" \
  -d '{
    "name": "admin-key",
    "role": "admin",
    "rate_limit_rate": 100.0,
    "rate_limit_burst": 200,
    "tenant_id": "default"
  }'
```

### Rotate Key

```bash
curl -X POST http://127.0.0.1:8080/v1/api-keys/{key_id}/rotate
```

### Revoke Key

```bash
curl -X DELETE http://127.0.0.1:8080/v1/api-keys/{key_id}
```

### Key Roles

| Role | Permissions |
|------|-------------|
| `admin` | Full access to all endpoints |
| `operator` | Manage models, view analytics |
| `viewer` | Read-only access |

---

## 10. Governance Configuration

### View Policy

```bash
curl http://127.0.0.1:8080/v1/governance/policy
```

### Update Policy

```bash
curl -X PUT http://127.0.0.1:8080/v1/governance/policy \
  -H "Content-Type: application/json" \
  -d '{
    "inference": {"default": "auto"},
    "risk_gates": {
      "low": {"schema": true, "policy": true},
      "high": {"schema": true, "policy": true, "deterministic_acceptance": true}
    }
  }'
```

### Configure Risk Class

```bash
curl -X PUT http://127.0.0.1:8080/v1/governance/risk-class/high \
  -H "Content-Type: application/json" \
  -d '{
    "gates": {
      "schema": true,
      "policy": true,
      "deterministic_acceptance": true,
      "independent_family_review": true
    }
  }'
```

### Risk Classes

| Class | Default Gates | Description |
|-------|---------------|-------------|
| `low` | schema, policy | Lowest verification burden |
| `medium` | schema, policy, deterministic_acceptance | Standard verification |
| `high` | schema, policy, deterministic_acceptance, independent_family_review | Cross-family verification |
| `critical` | All gates + human_approval | Full verification with human sign-off |

---

## 11. Monitoring and Observability

### Prometheus Metrics

```bash
# Prometheus text format
curl http://127.0.0.1:8080/metrics

# JSON format
curl -H "Accept: application/json" http://127.0.0.1:8080/metrics
```

### Health Check

```bash
curl http://127.0.0.1:8080/health
```

### Cache Stats

```bash
curl http://127.0.0.1:8080/cache/stats
```

### Analytics Endpoints

```bash
# Cost analytics
curl http://127.0.0.1:8080/v1/analytics/cost

# Performance analytics
curl "http://127.0.0.1:8080/v1/analytics/performance?model_id=qwen3:8b"

# Usage analytics
curl http://127.0.0.1:8080/v1/analytics/usage

# Escalation analytics
curl http://127.0.0.1:8080/v1/analytics/escalations

# Audit trail
curl "http://127.0.0.1:8080/v1/analytics/audit?limit=100"
```

---

## 12. SIEM Integration

### Configuration

```python
from gateway.siem import SIEMIntegration

# Splunk HEC
siem = SIEMIntegration(
    endpoint="https://splunk-hec:8088/services/collector",
    api_key="Splunk-...",
    format="json"
)

# IBM QRadar (LEEF)
siem = SIEMIntegration(
    endpoint="https://qradar:514",
    format="leef"
)

# Syslog
siem = SIEMIntegration(
    endpoint="syslog://127.0.0.1:514",
    format="syslog"
)
```

### Event Types Shipped to SIEM

- Audit events (API calls, auth, permission changes)
- Ledger events (state transitions, verification results)
- Security events (failed auth, rate limit hits)

---

## 13. Troubleshooting

### Common Issues

**Gateway won't start:**
- Check `NOERELAY_GATEWAY_PORT` is not in use
- Verify `OPENROUTER_API_KEY` is set for `live` mode
- Check log output for `ConfigError` messages

**Database errors:**
- Verify `NOERELAY_DATABASE_PATH` directory is writable
- Check disk space on the data volume
- Try restoring from backup

**Local models not found:**
- Verify Ollama is running: `curl http://127.0.0.1:11434/api/tags`
- Check `NOERELAY_LOCAL_MODEL_URL` is correct
- Ensure `NOERELAY_LOCAL_MODEL_ENABLED=1`

**High latency:**
- Check cache hit rate: `GET /cache/stats`
- Enable caching: `NOERELAY_CACHE_ENABLED=1`
- Review model ranking for faster alternatives

### Logging

Set `NOERELAY_LOG_LEVEL=DEBUG` for detailed logs:

```bash
NOERELAY_LOG_LEVEL=DEBUG NOERELAY_LOG_OUTPUT=stdout python -m gateway
```

### Database Inspection

```bash
sqlite3 .noerelay/noerelay.db ".tables"
sqlite3 .noerelay/noerelay.db "SELECT COUNT(*) FROM runs;"