# NoeRelay Gateway — Compatibility Profile

## 1. Purpose

This document records the documented differences between the NoeRelay gateway
skeleton and a fully OpenAI-compatible endpoint, as required by EPR-API-002
("Standard OpenAI request fields MUST pass through without semantic
reinterpretation unless the compatibility profile documents the difference").

## 2. Advertised model

The gateway advertises a single virtual model:

- **`noerelay/epr-1`** — the EPR-1 evidence-governed portfolio runtime.

Any other `model` value in a request is rejected:

| Requested model | HTTP status | Meaning |
|---|---|---|
| `noerelay/epr-1` | 200 | Virtual model; routed by NoeRelay |
| `openai/*`, `openrouter/auto`, `api.openai.com` references | 403 | Denied by deterministic policy (EPR-API-007) |
| Anything else | 404 | Model not found |

The selected upstream model identity is **ledger-bound**, not response-bound.
The `model` field on success responses always echoes `noerelay/epr-1`. The
upstream route is discoverable via the evidence receipt at
`GET /v1/epr/runs/{run_id}`.

## 3. Standard field pass-through

The following standard OpenAI fields are carried verbatim into the OpenRouter
request payload without reinterpretation:

- `messages`
- `temperature`
- `max_tokens`
- `tools`
- `tool_choice`
- `response_format`

Additional unknown fields are also passed through. The gateway does not
validate, transform, or reject any of these fields.

## 4. Documented differences

### 4.1 Streaming (EPR-API-004)

**Status: implemented.**

Requests with `stream: true` receive `Content-Type: text/event-stream` and a
sequence of OpenAI-compatible `chat.completion.chunk` frames terminated by
`data: [DONE]`. The terminal chunk carries the `epr` metadata block so route
identity and receipt discoverability are preserved: `epr.evidence_receipt_url`
always points at `GET /v1/epr/runs/{run_id}`.

The pipeline runs to completion first (so the receipt is issued), then streams
the result in word-level chunks. Pipeline failures (e.g., 424 escalation, 422
governance error) are emitted as an SSE error event carrying `error` and `epr`
before `[DONE]`. True upstream token proxying is a future enhancement; the
skeleton performs a non-streaming upstream call and chunks the response.

### 4.2 `model` field semantics

The `model` field on success responses echoes the requested virtual model id
(`noerelay/epr-1`), not the upstream model id. This differs from OpenAI's
convention of returning the actual model that served the request. The upstream
route is disclosed via the `epr.route_decision_id` and the evidence receipt.

### 4.3 Token usage

The stub client reports zero token usage. When the live OpenRouter client is
enabled, usage fields will reflect the upstream response.

### 4.4 Authentication

The gateway skeleton does not perform authentication. The default bind address
is `127.0.0.1` (loopback only). Production deployment is responsible for
authentication and TLS termination.

## 5. Default governance profile (EPR-API-003)

When the `governance` object is absent from a request, the following
deterministic defaults apply:

| Field | Default | Rationale |
|---|---|---|
| `risk_class` | `low` | Lowest verification burden; policy `verification.low = [schema, policy]` |
| `data_policy` | `zdr` | Strictest available; matches `openrouter.provider_routing.zdr: true` |
| `max_cost_usd` | `$NOERELAY_DEFAULT_MAX_COST_USD` (0.25) | Configurable ceiling |
| `max_latency_ms` | `$NOERELAY_DEFAULT_MAX_LATENCY_MS` (60000) | Configurable ceiling |
| `required_acceptance_probability` | absent | Kernel applies policy floor for the risk class |
| `retention_class` | `ephemeral` | Least retention |
| `return_evidence_receipt` | `true` | Receipts are always issued |

## 6. Environment variables

### 6.1 New non-secret variables

| Variable | Default | Meaning |
|---|---|---|
| `NOERELAY_GATEWAY_HOST` | `127.0.0.1` | Bind address |
| `NOERELAY_GATEWAY_PORT` | `8080` | Bind port (0 = ephemeral) |
| `NOERELAY_OPENROUTER_MODE` | `stub` | `stub` or `live` |
| `NOERELAY_POLICY_PATH` | `spec/routing-policy.json` | Routing policy JSON |
| `NOERELAY_STATE_MACHINE_PATH` | `spec/verification-state-machine.json` | State machine JSON |
| `NOERELAY_PORTFOLIO_PATH` | `examples/candidate-actions.json` | Candidate action registry |
| `NOERELAY_DEFAULT_MAX_COST_USD` | `0.25` | Governance default |
| `NOERELAY_DEFAULT_MAX_LATENCY_MS` | `60000` | Governance default |
| `NOERELAY_EXTERNAL_BASE_URL` | `http://127.0.0.1:8080` | Used to render `evidence_receipt_url` |

### 6.2 Existing variables (unchanged)

See [`docs/environment.md`](environment.md) for `OPENROUTER_API_KEY`,
`OPENROUTER_BASE_URL`, `OPENROUTER_HTTP_REFERER`, `OPENROUTER_APP_TITLE`,
`NOERELAY_LIVE_TESTS`, and `HF_TOKEN`.

## 7. Skeleton limitations

1. **In-memory run registry.** Receipts do not survive process restart.
2. **Stub execution.** Model output is an echo marker (`[noerelay stub] ...`)
   in offline mode. Live mode (``NOERELAY_OPENROUTER_MODE=live``) uses the
   real OpenRouter HTTP client in [`openrouter.py`](reference/gateway/openrouter.py).
3. **Real verification DAG (implemented).** The verification engine in
   [`verification.py`](reference/gateway/verification.py) evaluates the DAG
   from the routing policy for each risk class (``schema``, ``policy``,
   ``deterministic_acceptance``, ``independent_family_review``,
   ``human_approval``). Evidence records conform to
   [`evidence.schema.json`](spec/schemas/evidence.schema.json).  See §9 for
   per-criterion status.
4. **Human approval loop (deferred).** For ``critical`` risk, the
   ``human_approval`` verification step returns ``not_run`` and blocks
   acceptance (fail-closed).  A human-in-the-loop review flow is a later
   phase.
5. **Streaming (implemented).** ``stream: true`` returns SSE with the terminal
   ``epr`` metadata chunk preserving route identity (see §4.1).
6. **No authentication.** Loopback bind default; deployment concern.
7. **Deterministic contract compilation by default.** An optional
   ``LLMContractProposer`` protocol (EPR-CON-001) may propose a task
   contract, but its output is merged with deterministic defaults and
   re-validated; deterministic compilation remains the fallback.
8. **Context compilation and compaction (implemented).** ``ContextCompiler``
   builds context packages by graph reachability (EPR-CTX-006), and
   ``ContextCompactor`` preserves authoritative L1 state while asserting the
   capsule invariants (EPR-CTX-002). Compaction is a no-op in the skeleton
   because in-memory runs stay below the size threshold.
9. **Claim lifecycle (implemented).** ``adjudicate_fact()`` is integrated
    through the :class:`EpistemicState` engine in [`epistemic.py`](reference/gateway/epistemic.py).
    Model assertions are classified as ``model_assertion`` evidence (EPR-EPI-002),
    corroboration requires non-model evidence (EPR-EPI-003), derived claims
    respect premise bounds (EPR-EPI-004), conflicted claims block high/critical
    risk acceptance (EPR-EPI-005), and model confidence is calibrated via
    :class:`CalibrationStore` (EPR-EPI-006).

## 8. Running the gateway

```bash
cd reference
python -m gateway
```

The server binds to ``127.0.0.1:8080`` by default. Example request:

```bash
curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"noerelay/epr-1","messages":[{"role":"user","content":"Hello"}]}'
```

## 9. Conformance checklist

| Requirement | Status | Artifact |
|---|---|---|
| EPR-API-001 | ✅ | `server.py` routes + `handlers.py`; chat + responses + models endpoints |
| EPR-API-002 | ✅ | `build_chat_payload()` verbatim pass-through; this document §3–4 |
| EPR-API-003 | ✅ | `governance.py` defaults + merge; this document §5 |
| EPR-API-004 | ✅ | `streaming.py` `SSEStreamer` + `server.py`/`handlers.py`; terminal chunk carries `epr` route identity |
| EPR-API-006 | ✅ | Boundary check; no `OPENAI_API_KEY`; stub default |
| EPR-API-007 | ✅ | 4-layer enforcement: startup portfolio, boundary, kernel, upstream payload |
| EPR-API-008 | ✅ | `fallback_plans` retained; `semantic_fallback_count` in epr metadata |
| EPR-LED-001 | ✅ | Every transition ledgered via `RunRegistry.ledger()` |
| EPR-LED-004 | ✅ | `issue_receipt()`; `GET /v1/epr/runs/{run_id}` |
| EPR-ROUTE-004 | ✅ | `candidate_audit` in ledger; redacted summary in 424 body |
| EPR-VFY-001 | ✅ | Verification DAG evaluation per risk class; `verification.py` |
| EPR-VFY-002 | ✅ | Real guard computation via `GuardEvaluator` in `statemachine.py` |
| EPR-VFY-003 | ✅ | Schema check validates OpenAI response shape |
| EPR-VFY-004 | ✅ | Policy check validates route admissibility |
| EPR-VFY-005 | ✅ | Deterministic acceptance checks observable criteria against response |
| EPR-VFY-006 | ✅ | Independent family review verifies verifier ≠ worker family |
| EPR-VFY-007 | ⏳ | Human approval fail-closed for critical risk (skeleton limitation) |
| EPR-VER-004 | ✅ | `test_independence.py` + `verification.py`; worker-generated-only tests fail high/critical risk |
| EPR-VER-006 | ✅ | `decoding.py` `DecodingPhaseManager`; separate tool/report phases unless conformance-tested |
| EPR-EPI-002 | ✅ | Model assertions classified as ``model_assertion`` evidence; ``epistemic.py`` |
| EPR-EPI-003 | ✅ | Corroboration requires non-model-assertion evidence; ``can_promote_by_corroboration()`` |
| EPR-EPI-004 | ✅ | Derived claims reference premises, bounded by weakest premise; ``add_derived_claim()`` |
| EPR-EPI-005 | ✅ | Conflicted claims block high/critical-risk acceptance; ``has_blocking_conflict()`` + guard |
| EPR-EPI-006 | ✅ | Calibration store with ECE, model flagging, conservative discount; ``CalibrationStore`` |
| EPR-MEM-001 | ✅ | ``validate_context_capsule`` called by ``ContextCompactor``; compaction preserves L1 state |
| EPR-CTX-001 | ✅ | Four memory levels in ``context.py``: ``MemoryLevel`` + ``CanonicalState`` |
| EPR-CTX-002 | ✅ | ``ContextCompactor`` asserts the three capsule invariants and preserves authoritative state |
| EPR-CTX-006 | ✅ | ``ContextCompiler`` compiles context packages by graph reachability from the current task |
| EPR-CON-001 | ✅ | Deterministic compiler is default/fallback; optional ``LLMContractProposer`` output re-validated |
| EPR-CON-002 | ✅ | ``classify_acceptance_criterion`` classifies executable/observable/judgmental/missing |
| EPR-CON-003 | ✅ | ``requires_clarification`` gates high/critical-risk missing acceptance; pipeline transitions to ``clarification_required`` |
| EPR-CON-004 | ✅ | ``StateVocabulary`` enforces eight distinct state vocabularies |
| EPR-LED-005 | ✅ | `provenance.py` `ProvenanceMapper`; W3C PROV + in-toto attestation mapping, `prov` enriched on evidence |
| EPR-ROUTE-005 | ✅ | `fallback.py` `FallbackRecorder`; provider/semantic fallbacks recorded separately via `fallback_triggered` ledger events |
| EPR-ROUTE-006 | ✅ | `online_learning.py` `CanaryTrafficRouter` + `PolicyVersionManager`; canary-only learning, `can_modify_production()` always false, signed-benchmark promotion |

## 10. Prometheus Metrics

The gateway exposes a Prometheus-compatible metrics endpoint at ``GET /metrics``
(see [`prometheus.py`](reference/gateway/prometheus.py)).  The endpoint defaults
to ``text/plain`` (Prometheus exposition format) and can return JSON when the
client sends ``Accept: application/json``.

### Available metrics

| Metric | Type | Labels |
|--------|------|--------|
| ``noerelay_runs_total`` | counter | — |
| ``noerelay_runs_accepted_total`` | counter | — |
| ``noerelay_runs_escalated_total`` | counter | — |
| ``noerelay_runs_rejected_total`` | counter | — |
| ``noerelay_active_runs`` | gauge | — |
| ``noerelay_cache_size`` | gauge | — |
| ``noerelay_local_models_count`` | gauge | — |
| ``noerelay_request_duration_seconds`` | histogram | — |
| ``noerelay_tokens_per_request`` | histogram | — |
| ``noerelay_cost_per_request_usd`` | histogram | — |
| ``noerelay_model_requests_total`` | counter | ``model_id`` |
| ``noerelay_model_tokens_total`` | counter | ``model_id`` |
| ``noerelay_model_cost_total`` | counter | ``model_id`` |
| ``noerelay_tenant_spend_total`` | counter | ``tenant_id`` |
| ``noerelay_risk_class_runs_total`` | counter | ``risk_class`` |

### Scraping

```yaml
scrape_configs:
  - job_name: 'noerelay'
    scrape_interval: 15s
    static_configs:
      - targets: ['noerelay:8080']
```

## 11. SIEM Integration

The SIEM integration module ([`siem.py`](reference/gateway/siem.py)) supports
log shipping and security event forwarding in multiple formats:

| Format | Use Case |
|--------|----------|
| JSON | Splunk HEC, Datadog Logs API |
| CEF | Splunk, ArcSight, QRadar |
| LEEF | IBM QRadar |
| Syslog | Any syslog-compatible SIEM |

### Event types forwarded

- **Audit events**: All API calls, authentication attempts, permission changes
- **Ledger events**: Every state transition in the evidence pipeline
- **Security events**: Failed auth, rate limit hits, policy violations

## 12. Docker & Kubernetes

### Docker

```bash
docker compose up -d
curl http://127.0.0.1:8080/health
```

See [`Dockerfile`](../Dockerfile) and [`docker-compose.yml`](../docker-compose.yml).

### Kubernetes

```bash
kubectl apply -f deploy/kubernetes/ -n noerelay
```

See [`deploy/kubernetes/`](../deploy/kubernetes/) for deployment, service, PVC,
and secret manifests.

## 13. CI/CD

GitHub Actions workflows in [`.github/workflows/`](../.github/workflows/):

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `ci.yml` | push, PR | Test matrix (Python 3.11, 3.12), Docker build |
| `benchmark.yml` | weekly, manual | Scheduled benchmark runs |

## 14. Additional Documentation

- **[`deployment.md`](deployment.md)** — Full deployment guide (Docker, K8s, TLS, backup, monitoring)
- **[`api-reference.md`](api-reference.md)** — Complete API reference for all 62+ endpoints
- **[`admin-guide.md`](admin-guide.md)** — Admin operations, tenant management, alerting, webhooks, secrets