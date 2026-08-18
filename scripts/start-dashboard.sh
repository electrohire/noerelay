#!/usr/bin/env bash
# Start the NoeRelay gateway server with dashboard
# Usage: ./start-dashboard.sh [live|stub]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/../reference"

MODE="${1:-stub}"

if [ "$MODE" = "live" ]; then
    export NOERELAY_OPENROUTER_MODE=live
    export NOERELAY_CACHE_ENABLED=1
    echo "Starting NoeRelay gateway in LIVE mode..."
    echo "Dashboard: http://127.0.0.1:8080/dashboard"
    echo "Health:    http://127.0.0.1:8080/health"
    echo "Metrics:   http://127.0.0.1:8080/metrics"
    echo ""
    echo "Press Ctrl+C to stop."
    echo ""
    python -m gateway
else
    export NOERELAY_OPENROUTER_MODE=stub
    echo "Starting NoeRelay gateway in STUB mode (no network)..."
    echo "Dashboard: http://127.0.0.1:8080/dashboard"
    echo "Health:    http://127.0.0.1:8080/health"
    echo ""
    echo "Press Ctrl+C to stop."
    echo ""
    python -m gateway
fi