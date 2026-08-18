@echo off
REM Start the NoeRelay gateway server with dashboard
REM Usage: start-dashboard.cmd [live|stub]

setlocal

set MODE=%1
if "%MODE%"=="" set MODE=stub

cd /d "%~dp0\..\reference"

if "%MODE%"=="live" (
    set NOERELAY_OPENROUTER_MODE=live
    set NOERELAY_CACHE_ENABLED=1
    echo Starting NoeRelay gateway in LIVE mode...
    echo Dashboard: http://127.0.0.1:8080/dashboard
    echo Health:    http://127.0.0.1:8080/health
    echo Metrics:   http://127.0.0.1:8080/metrics
    echo.
    echo Press Ctrl+C to stop.
    echo.
    python -m gateway
) else (
    set NOERELAY_OPENROUTER_MODE=stub
    echo Starting NoeRelay gateway in STUB mode (no network)...
    echo Dashboard: http://127.0.0.1:8080/dashboard
    echo Health:    http://127.0.0.1:8080/health
    echo.
    echo Press Ctrl+C to stop.
    echo.
    python -m gateway
)