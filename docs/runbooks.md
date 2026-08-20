# NoeRelay Operator Runbooks

**Version:** 0.1.0-draft
**Last updated:** 2026-08-20

---

## Runbook Index

1. [Starting and Stopping the Gateway](#1-starting-and-stopping-the-gateway)
2. [Health Check and Monitoring](#2-health-check-and-monitoring)
3. [Backup and Restore](#3-backup-and-restore)
4. [Tenant Management](#4-tenant-management)
5. [API Key Management](#5-api-key-management)
6. [Model Lifecycle](#6-model-lifecycle)
7. [Alert Response](#7-alert-response)
8. [Incident Response](#8-incident-response)

---

## 1. Starting and Stopping the Gateway

### Start (Docker)

```bash
docker compose up -d
```

### Start (bare metal)

```powershell
$env:NOERELAY_GATEWAY_HOST = "0.0.0.0"
$env:NOERELAY_OPENROUTER_MODE = "live"
$env:OPENROUTER_API_KEY = "<your-key>"
python -m gateway
```

### Stop gracefully

```bash
# Docker
docker compose down

# Bare metal: send SIGTERM (Ctrl+C)
# The gateway drains in-flight requests before shutting down (up to 30s)
```

### Verify running

```bash
curl http://127.0.0.1:8080/health
# Expected: {"status": "healthy", "version": "0.1.0"}
```

---

## 2. Health Check and Monitoring

### Health endpoint

```
GET /health
→ {"status": "healthy", "version": "0.1.0"}
```

### Metrics endpoint

```
GET /metrics
→ Prometheus-format metrics (15 metric types)
```

Key metrics to monitor:
- `noerelay_requests_total` — request volume
- `noerelay_request_duration_seconds` — latency distribution
- `noerelay_fallback_total` — fallback events (provider and semantic)
- `noerelay_escalation_total` — escalations requiring human review
- `noerelay_cost_usd_total` — cumulative cost
- `noerelay_rate_limit_hits_total` — rate limit enforcement

### Alert thresholds

| Alert | Condition | Severity |
|---|---|---|
| High error rate | >5% of requests over 5min | Warning |
| Escalation spike | >10 escalations in 5min | Critical |
| Cost anomaly | >2x baseline spend in 1hr | Warning |
| Health check failure | 3 consecutive failures | Critical |
| Rate limit surge | >100 rate limit hits in 1min | Warning |

---

## 3. Backup and Restore

### Backup (SQLite)

```bash
# Via API
curl -X POST http://127.0.0.1:8080/v1/admin/backup \
  -H "Authorization: Bearer $NOERELAY_ADMIN_KEY"

# Manual file copy
cp /data/noerelay.db /backups/noerelay-$(date -I).db
```

### Restore

```bash
# Via API
curl -X POST http://127.0.0.1:8080/v1/admin/restore \
  -H "Authorization: Bearer $NOERELAY_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"backup_id": "<backup-id>"}'

# Manual file restore (gateway must be stopped)
cp /backups/noerelay-2026-08-20.db /data/noerelay.db
```

### Backup schedule

- **Frequency:** Daily automated backup
- **Retention:** 30 days rolling
- **Verification:** Restore to staging weekly

---

## 4. Tenant Management

### Create tenant

```bash
curl -X POST http://127.0.0.1:8080/v1/admin/tenants \
  -H "Authorization: Bearer $NOERELAY_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "acme-corp",
    "name": "Acme Corporation",
    "daily_budget_usd": 100.0,
    "monthly_budget_usd": 2000.0
  }'
```

### List tenants

```bash
curl http://127.0.0.1:8080/v1/admin/tenants \
  -H "Authorization: Bearer $NOERELAY_ADMIN_KEY"
```

### Update tenant quotas

```bash
curl -X PATCH http://127.0.0.1:8080/v1/admin/tenants/acme-corp \
  -H "Authorization: Bearer $NOERELAY_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"daily_budget_usd": 200.0}'
```

### Delete tenant

```bash
curl -X DELETE http://127.0.0.1:8080/v1/admin/tenants/acme-corp \
  -H "Authorization: Bearer $NOERELAY_ADMIN_KEY"
```

---

## 5. API Key Management

### Create API key

```bash
curl -X POST http://127.0.0.1:8080/v1/admin/api-keys \
  -H "Authorization: Bearer $NOERELAY_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "acme-corp",
    "name": "production-key-1",
    "role": "user"
  }'
```

### List keys for tenant

```bash
curl http://127.0.0.1:8080/v1/admin/tenants/acme-corp/api-keys \
  -H "Authorization: Bearer $NOERELAY_ADMIN_KEY"
```

### Rotate key

```bash
curl -X POST http://127.0.0.1:8080/v1/admin/api-keys/{key_id}/rotate \
  -H "Authorization: Bearer $NOERELAY_ADMIN_KEY"
```

### Revoke key

```bash
curl -X DELETE http://127.0.0.1:8080/v1/admin/api-keys/{key_id} \
  -H "Authorization: Bearer $NOERELAY_ADMIN_KEY"
```

---

## 6. Model Lifecycle

### List registered models

```bash
curl http://127.0.0.1:8080/v1/admin/models \
  -H "Authorization: Bearer $NOERELAY_ADMIN_KEY"
```

### Add model to portfolio

```bash
curl -X POST http://127.0.0.1:8080/v1/admin/models \
  -H "Authorization: Bearer $NOERELAY_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model_id": "anthropic/claude-3.5-sonnet",
    "provider": "anthropic",
    "capabilities": ["text"],
    "cost_per_1k_input": 0.003,
    "cost_per_1k_output": 0.015
  }'
```

### Disable model (emergency)

```bash
curl -X POST http://127.0.0.1:8080/v1/admin/models/anthropic/claude-3.5-sonnet/disable \
  -H "Authorization: Bearer $NOERELAY_ADMIN_KEY"
```

### Enable model

```bash
curl -X POST http://127.0.0.1:8080/v1/admin/models/anthropic/claude-3.5-sonnet/enable \
  -H "Authorization: Bearer $NOERELAY_ADMIN_KEY"
```

---

## 7. Alert Response

### Cost anomaly detected

1. Check `/v1/admin/analytics/cost` for spending breakdown by tenant and model
2. Identify the tenant and model causing the spike
3. Review recent runs for that tenant: `/v1/epr/runs?tenant_id={id}&limit=50`
4. If malicious: revoke API key immediately (see §5)
5. If legitimate but unexpected: contact tenant, consider temporary quota reduction

### Escalation spike

1. Check `/v1/admin/analytics/escalations` for escalation reasons
2. Review escalated runs: `/v1/epr/runs?status=escalated&limit=20`
3. Common causes:
   - Missing high-risk acceptance criteria → tenant needs governance update
   - Verification failures → model may be producing invalid output
   - No admissible route → portfolio may need expansion
4. If systemic: consider disabling the problematic model (see §6)

### Health check failure

1. Verify the gateway process is running: `docker ps` or `ps aux | grep gateway`
2. Check logs: `docker logs noerelay` or tail the log file
3. Check disk space: `df -h /data`
4. Check SQLite integrity: `sqlite3 /data/noerelay.db "PRAGMA integrity_check;"`
5. Restart if necessary (see §1)

### Rate limit surge

1. Check `/v1/admin/analytics/rate-limits` for affected keys
2. Identify the tenant
3. If legitimate traffic increase: raise the tenant's rate limit
4. If abusive: revoke the key (see §5)
5. If DDoS: engage network-level protection

---

## 8. Incident Response

### Severity classification

| Severity | Definition | Response time |
|---|---|---|
| Sev1 — Critical | Gateway unavailable, data loss, security breach | Immediate (15 min) |
| Sev2 — High | Major feature broken, cost anomaly >5x | 1 hour |
| Sev3 — Medium | Minor feature degraded, single tenant affected | 4 hours |
| Sev4 — Low | Cosmetic issue, non-blocking | Next business day |

### Incident response steps

1. **Declare:** Acknowledge the incident and assign severity
2. **Contain:** Revoke compromised keys, disable affected models, apply rate limits
3. **Investigate:** Review logs, metrics, audit trail, and ledger events
4. **Mitigate:** Apply fix or workaround
5. **Recover:** Verify health, restore from backup if needed
6. **Post-mortem:** Document root cause, timeline, impact, and preventive measures

### Emergency contacts

- **Repository owners:** See GitHub repository settings
- **Security vulnerabilities:** Follow [SECURITY.md](../SECURITY.md)
- **OpenRouter incidents:** https://status.openrouter.ai

### Recovery commands

```bash
# Full restart
docker compose down
docker compose up -d

# Restore from backup (see §3)
# Verify health
curl http://127.0.0.1:8080/health

# Verify metrics
curl http://127.0.0.1:8080/metrics | head -20
```

---

## Appendix: Environment Variables Reference

| Variable | Default | Description |
|---|---|---|
| `NOERELAY_GATEWAY_HOST` | `127.0.0.1` | Bind address |
| `NOERELAY_GATEWAY_PORT` | `8080` | Listen port |
| `NOERELAY_OPENROUTER_MODE` | `stub` | `stub` or `live` |
| `OPENROUTER_API_KEY` | — | OpenRouter API key |
| `NOERELAY_DATABASE_ENABLED` | `0` | Enable SQLite persistence |
| `NOERELAY_DATABASE_PATH` | `:memory:` | SQLite database path |
| `NOERELAY_LOG_LEVEL` | `INFO` | Log level |
| `NOERELAY_LOG_OUTPUT` | `stdout` | `stdout` or `file` |
| `NOERELAY_CACHE_ENABLED` | `0` | Enable response cache |
| `NOERELAY_TLS_ENABLED` | `0` | Enable TLS |
| `NOERELAY_DEFAULT_MAX_COST_USD` | `0.25` | Default cost ceiling |
| `NOERELAY_DEFAULT_MAX_LATENCY_MS` | `60000` | Default latency ceiling |
| `HF_TOKEN` | — | Hugging Face token (benchmarks only) |