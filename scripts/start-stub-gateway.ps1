$ErrorActionPreference = "Stop"

$env:NOERELAY_API_KEY = "noerelay-local-development-key-0001"
$env:NOERELAY_OPENROUTER_MODE = "stub"
$env:NOERELAY_ORGANIZATION_ID = "local-org"
$env:NOERELAY_PROJECT_ID = "local-project"
$env:NOERELAY_ENVIRONMENT_ID = "development"
$env:NOERELAY_CANDIDATES_JSON = '[{"candidate_id":"deepseek-v4-pro","openrouter_model_id":"deepseek/deepseek-v4-pro","provider":"deepseek","available":true,"capabilities":["text"],"maximum_data_class":"confidential","cost":{"inference_microusd":1,"tools_microusd":0,"verification_microusd":0,"expected_retry_microusd":0,"expected_fallback_microusd":0,"infrastructure_microusd":0,"expected_human_review_microusd":0},"latency_p95_ms":1,"acceptance_lcb_ppm":999999,"supports_independent_verification":true}]'
$env:NOERELAY_BUDGET_LIMIT_MICROUSD = "10000000000"
$env:NOERELAY_CONTEXT_BUDGET_TOKENS = "131072"
$env:NOERELAY_RECEIPT_SIGNING_SEED_HEX = "0000000000000000000000000000000000000000000000000000000000000000"
$env:NOERELAY_RECEIPT_SIGNING_KEY_ID = "local-development-key"
$env:NOERELAY_BIND = "127.0.0.1:8080"

$gateway = Join-Path (Join-Path (Join-Path $PSScriptRoot "..") "target") "debug"
$gateway = Join-Path $gateway "noerelay-gateway.exe"
if (-not (Test-Path $gateway)) {
    Write-Error "Gateway binary not found at $gateway"
    exit 1
}

Write-Host "Starting stub gateway at http://127.0.0.1:8080 ..."
& $gateway
