#!/usr/bin/env bash
# Stop the NoeRelay gateway server
# Usage: ./stop-dashboard.sh

echo "Stopping NoeRelay gateway..."

# Find and kill the process listening on port 8080
PID=$(lsof -ti :8080 2>/dev/null || true)

if [ -n "$PID" ]; then
    echo "Killing process $PID (listening on port 8080)"
    kill $PID 2>/dev/null || true
    sleep 1
    # Force kill if still running
    kill -9 $PID 2>/dev/null || true
    echo "NoeRelay gateway stopped."
else
    # Try pkill as fallback
    pkill -f "python -m gateway" 2>/dev/null && echo "NoeRelay gateway stopped." || echo "NoeRelay gateway was not running."
fi