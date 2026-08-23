# NoeRelay five-minute quick start

This path runs the Rust authority gateway in deterministic stub mode. It makes no paid provider request, but still compiles a contract, selects an explicit model, reserves/reconciles budget, verifies the response envelope, appends ledger events, and issues an Ed25519-signed receipt.

## Docker Compose (recommended)

Docker Compose starts the Rust gateway and PostgreSQL durable authority store:

```powershell
$env:NOERELAY_API_KEY = "replace-with-at-least-32-characters"
$env:NOERELAY_RECEIPT_SIGNING_SEED_HEX = "replace-with-64-hex-characters"
docker compose up --build -d
```

Verify readiness and send an ordinary OpenAI-compatible request:

```powershell
$headers = @{ Authorization = "Bearer $env:NOERELAY_API_KEY"; "Content-Type" = "application/json" }
Invoke-RestMethod http://127.0.0.1:8080/ready
$response = Invoke-WebRequest -Method Post http://127.0.0.1:8080/v1/responses `
  -Headers $headers -Body '{"model":"noerelay/epr-1","input":"What is 2+2?"}'
$response.Headers["x-noerelay-run-id"]
$response.Headers["x-noerelay-receipt-hash"]
```

Retrieve the signed governance receipt using the returned run ID:

```powershell
$runId = $response.Headers["x-noerelay-run-id"]
Invoke-RestMethod "http://127.0.0.1:8080/v1/noerelay/runs/$runId/receipt" -Headers $headers
```

The Compose defaults are development-only. Replace the database password, API key, and signing seed before shared use.

## Native Rust process

Install Rust stable, start PostgreSQL separately, and configure the variables documented in `.env.example`. For ephemeral local state, omit `DATABASE_URL`:

```powershell
$env:NOERELAY_API_KEY = "replace-with-at-least-32-characters"
$env:NOERELAY_OPENROUTER_MODE = "stub"
$env:NOERELAY_ORGANIZATION_ID = "local-org"
$env:NOERELAY_PROJECT_ID = "local-project"
$env:NOERELAY_CANDIDATES_JSON = '[{"candidate_id":"stub","openrouter_model_id":"anthropic/claude-test","provider":"anthropic","available":true,"capabilities":["text"],"maximum_data_class":"confidential","cost":{"inference_microusd":1,"tools_microusd":0,"verification_microusd":0,"expected_retry_microusd":0,"expected_fallback_microusd":0,"infrastructure_microusd":0,"expected_human_review_microusd":0},"latency_p95_ms":1,"acceptance_lcb_ppm":999999,"supports_independent_verification":true}]'
cargo run -p noerelay-gateway
```

## Verification suites

```powershell
cargo test --workspace --locked
cargo clippy --workspace --all-targets -- -D warnings
Push-Location services/a2a-adapter
go test ./...
go vet ./...
Pop-Location
python -m unittest discover -s tests -v
```

For live inference, change `NOERELAY_OPENROUTER_MODE` to `live` and supply `OPENROUTER_API_KEY` from a secret manager. NoeRelay sends OpenRouter the explicit Rust-selected model; it does not use upstream automatic model routing.

See the [production deployment gate](production-deployment.md), [requirements](requirements.md), and [verification matrix](verification-matrix.md) before exposing the service.
