#!/usr/bin/env bash
# =============================================================================
# NoeRelay — curl-based Smoke Test Script
# =============================================================================
# This script tests the NoeRelay gateway using only curl and basic shell tools.
# It verifies:
#   1. Health endpoint
#   2. Model listing
#   3. Non-streaming chat completion
#   4. Streaming chat completion
#   5. Error handling
#
# Usage:
#   bash examples/curl-test.sh
#   NOERELAY_BASE_URL=http://localhost:8080 bash examples/curl-test.sh
#
# Environment variables:
#   NOERELAY_BASE_URL  — NoeRelay base URL (default: http://127.0.0.1:8080)
#   NOERELAY_API_KEY   — NoeRelay API key (default: noerelay, Compose dev key)
#   NOERELAY_MODEL     — Model ID to use (default: axiovex-agni)
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_URL="${NOERELAY_BASE_URL:-http://127.0.0.1:8080}"
API_KEY="${NOERELAY_API_KEY:-noerelay}"
MODEL="${NOERELAY_MODEL:-axiovex-agni}"
TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/noerelay-smoke.XXXXXX")
SCRIPT_BASHPID=$BASHPID
cleanup() {
    if [ "$BASHPID" -eq "$SCRIPT_BASHPID" ]; then
        rm -rf -- "$TEMP_DIR"
    fi
}
trap cleanup EXIT HUP INT TERM
HEALTH_FILE="$TEMP_DIR/health.json"
MODELS_FILE="$TEMP_DIR/models.json"
CHAT_FILE="$TEMP_DIR/chat.json"
STREAM_FILE="$TEMP_DIR/stream.txt"
ERROR_FILE="$TEMP_DIR/error.json"
ERROR2_FILE="$TEMP_DIR/error2.json"
GOV_FILE="$TEMP_DIR/governance.json"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

PASSED=0
FAILED=0

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
pass_test() {
    echo -e "${GREEN}✅ PASS${NC} — $1"
    PASSED=$((PASSED + 1))
}

fail_test() {
    echo -e "${RED}❌ FAIL${NC} — $1"
    FAILED=$((FAILED + 1))
}

section() {
    echo ""
    echo -e "${BLUE}============================================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}============================================================${NC}"
}

# ---------------------------------------------------------------------------
# Check prerequisites
# ---------------------------------------------------------------------------
check_prereqs() {
    section "Prerequisites Check"

    if ! command -v curl &> /dev/null; then
        echo -e "${RED}❌ curl is not installed. Please install curl.${NC}"
        exit 1
    fi
    pass_test "curl is installed"

    # Check if python is available for JSON formatting
    if command -v python3 &> /dev/null; then
        PYTHON_BIN="python3"
        JSON_PRETTY="python3 -m json.tool"
        pass_test "python3 available for JSON formatting"
    elif command -v python &> /dev/null; then
        PYTHON_BIN="python"
        JSON_PRETTY="python -m json.tool"
        pass_test "python available for JSON formatting"
    else
        PYTHON_BIN=""
        JSON_PRETTY="cat"
        echo -e "${YELLOW}⚠️  python not found — JSON output will not be formatted${NC}"
    fi
}

# ---------------------------------------------------------------------------
# Test 1: Health Check
# ---------------------------------------------------------------------------
test_health() {
    section "Test 1: Health Check"

    HTTP_CODE=$(curl -s -o "$HEALTH_FILE" -w "%{http_code}" \
        --connect-timeout 5 \
        "$BASE_URL/health" 2>/dev/null || true)
    HTTP_CODE="${HTTP_CODE:-000}"

    if [ "$HTTP_CODE" = "200" ]; then
        pass_test "Health endpoint returned 200"
        echo "   Response: $($JSON_PRETTY < "$HEALTH_FILE")"
    else
        fail_test "Health endpoint returned $HTTP_CODE (expected 200)"
        echo "   Is NoeRelay running at $BASE_URL?"
        echo "   Start it with: cd reference && python -m gateway"
        return 1
    fi
}

# ---------------------------------------------------------------------------
# Test 2: List Models
# ---------------------------------------------------------------------------
test_list_models() {
    section "Test 2: List Models"

    HTTP_CODE=$(curl -s -o "$MODELS_FILE" -w "%{http_code}" \
        --connect-timeout 5 \
        -H "Authorization: Bearer $API_KEY" \
        "$BASE_URL/v1/models" 2>/dev/null || true)
    HTTP_CODE="${HTTP_CODE:-000}"

    if [ "$HTTP_CODE" = "200" ]; then
        pass_test "Models endpoint returned 200"
        echo "   Response:"
        $JSON_PRETTY < "$MODELS_FILE"

        # Check if expected model is in the list
        if grep -q "$MODEL" "$MODELS_FILE" 2>/dev/null; then
            pass_test "Expected model '$MODEL' found in model list"
        else
            echo -e "${YELLOW}   ⚠️  Expected model '$MODEL' not found in list${NC}"
        fi
    else
        fail_test "Models endpoint returned $HTTP_CODE (expected 200)"
    fi
}

# ---------------------------------------------------------------------------
# Test 3: Non-Streaming Chat Completion
# ---------------------------------------------------------------------------
test_chat_completion() {
    section "Test 3: Non-Streaming Chat Completion"

    HTTP_CODE=$(curl -s -o "$CHAT_FILE" -w "%{http_code}" \
        --connect-timeout 10 \
        --max-time 30 \
        -X POST "$BASE_URL/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $API_KEY" \
        -d "{
            \"model\": \"$MODEL\",
            \"messages\": [
                {\"role\": \"system\", \"content\": \"You are a helpful assistant. Answer concisely.\"},
                {\"role\": \"user\", \"content\": \"What is 2+2?\"}
            ],
            \"temperature\": 0.7,
            \"max_tokens\": 100
        }" 2>/dev/null || true)
    HTTP_CODE="${HTTP_CODE:-000}"

    if [ "$HTTP_CODE" = "200" ]; then
        pass_test "Chat completion returned 200"
        echo "   Response:"
        $JSON_PRETTY < "$CHAT_FILE"

        # Extract and display the content
        if [ -n "$PYTHON_BIN" ]; then
            CONTENT=$(NOERELAY_RESPONSE_FILE="$CHAT_FILE" "$PYTHON_BIN" -c "
import json, sys
import os
try:
    with open(os.environ['NOERELAY_RESPONSE_FILE'], encoding='utf-8') as response_file:
        data = json.load(response_file)
    print(data['choices'][0]['message']['content'])
except (KeyError, OSError, TypeError, ValueError):
    print('[could not parse]')
" 2>/dev/null || echo "[parse error]")
        else
            CONTENT="[python unavailable]"
        fi
        echo ""
        echo "   Content: $CONTENT"

        # Check for EPR metadata
        if grep -q '"epr"' "$CHAT_FILE" 2>/dev/null; then
            pass_test "EPR metadata present in response"
        else
            echo -e "${YELLOW}   ⚠️  No EPR metadata found in response${NC}"
        fi
    else
        fail_test "Chat completion returned $HTTP_CODE (expected 200)"
        echo "   Response:"
        cat "$CHAT_FILE" 2>/dev/null || echo "   [no response body]"
    fi
}

# ---------------------------------------------------------------------------
# Test 4: Streaming Chat Completion
# ---------------------------------------------------------------------------
test_streaming() {
    section "Test 4: Streaming Chat Completion"

    echo "   Sending streaming request..."

    # Use a temp file for streaming output
    HTTP_CODE=$(curl -s -o "$STREAM_FILE" -w "%{http_code}" \
        --connect-timeout 10 \
        --max-time 30 \
        -X POST "$BASE_URL/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $API_KEY" \
        -d "{
            \"model\": \"$MODEL\",
            \"messages\": [
                {\"role\": \"user\", \"content\": \"Count from 1 to 5, one per line.\"}
            ],
            \"stream\": true,
            \"max_tokens\": 100
        }" 2>/dev/null || true)
    HTTP_CODE="${HTTP_CODE:-000}"

    if [ "$HTTP_CODE" = "200" ]; then
        pass_test "Streaming returned 200"

        # Count data chunks
        CHUNK_COUNT=$(grep -c '^data:' "$STREAM_FILE" 2>/dev/null || echo "0")
        echo "   Chunks received: $CHUNK_COUNT"

        # Check for [DONE] marker
        if grep -q '\[DONE\]' "$STREAM_FILE" 2>/dev/null; then
            pass_test "Stream terminated with [DONE] marker"
        else
            echo -e "${YELLOW}   ⚠️  No [DONE] marker found in stream${NC}"
        fi

        # Show first few chunks
        echo "   First 3 chunks:"
        grep '^data:' "$STREAM_FILE" 2>/dev/null | head -3 | while read -r line; do
            echo "   $line"
        done
    else
        fail_test "Streaming returned $HTTP_CODE (expected 200)"
    fi
}

# ---------------------------------------------------------------------------
# Test 5: Error Handling
# ---------------------------------------------------------------------------
test_error_handling() {
    section "Test 5: Error Handling"

    # Test with invalid model
    HTTP_CODE=$(curl -s -o "$ERROR_FILE" -w "%{http_code}" \
        --connect-timeout 5 \
        -X POST "$BASE_URL/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $API_KEY" \
        -d '{
            "model": "nonexistent-model-xyz",
            "messages": [{"role": "user", "content": "Hello"}]
        }' 2>/dev/null || true)
    HTTP_CODE="${HTTP_CODE:-000}"

    if [ "$HTTP_CODE" != "200" ]; then
        pass_test "Invalid model returned error code $HTTP_CODE"
        echo "   Response:"
        $JSON_PRETTY < "$ERROR_FILE" 2>/dev/null || echo "   [no response body]"
    else
        echo -e "${YELLOW}   ⚠️  Invalid model returned 200 (may be OK in stub mode)${NC}"
    fi

    # Test with missing required field
    HTTP_CODE=$(curl -s -o "$ERROR2_FILE" -w "%{http_code}" \
        --connect-timeout 5 \
        -X POST "$BASE_URL/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $API_KEY" \
        -d '{
            "model": "axiovex-agni"
        }' 2>/dev/null || true)
    HTTP_CODE="${HTTP_CODE:-000}"

    if [ "$HTTP_CODE" != "200" ]; then
        pass_test "Missing messages field returned error code $HTTP_CODE"
    else
        echo -e "${YELLOW}   ⚠️  Missing messages returned 200 (may be OK in stub mode)${NC}"
    fi
}

# ---------------------------------------------------------------------------
# Test 6: Governance Parameters
# ---------------------------------------------------------------------------
test_governance() {
    section "Test 6: Governance Parameters"

    HTTP_CODE=$(curl -s -o "$GOV_FILE" -w "%{http_code}" \
        --connect-timeout 10 \
        --max-time 30 \
        -X POST "$BASE_URL/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $API_KEY" \
        -d "{
            \"model\": \"$MODEL\",
            \"messages\": [
                {\"role\": \"user\", \"content\": \"Hello\"}
            ],
            \"max_tokens\": 50,
            \"governance\": {
                \"risk_class\": \"low\",
                \"data_policy\": \"zdr\",
                \"max_cost_usd\": 0.25,
                \"max_latency_ms\": 60000
            }
        }" 2>/dev/null || true)
    HTTP_CODE="${HTTP_CODE:-000}"

    if [ "$HTTP_CODE" = "200" ]; then
        pass_test "Governance request returned 200"
    else
        echo -e "${YELLOW}   ⚠️  Governance request returned $HTTP_CODE${NC}"
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    echo "NoeRelay — curl Smoke Test"
    echo "Time: $(date '+%Y-%m-%d %H:%M:%S')"
    echo ""
    echo "Base URL: $BASE_URL"
    echo "Model: $MODEL"
    echo "API Key: [set; value not displayed]"

    check_prereqs

    # Run tests (continue even if health check fails, but note it)
    test_health || echo -e "${RED}⚠️  Health check failed — subsequent tests may also fail${NC}"
    test_list_models
    test_chat_completion
    test_streaming
    test_error_handling
    test_governance

    # Summary
    section "Test Summary"
    TOTAL=$((PASSED + FAILED))
    echo -e "${GREEN}Passed: $PASSED${NC}"
    echo -e "${RED}Failed: $FAILED${NC}"
    echo "Total:  $TOTAL"

    if [ "$FAILED" -gt 0 ]; then
        echo ""
        echo -e "${YELLOW}⚠️  Some tests failed. Check the output above for details.${NC}"
        echo "   This may be expected if running in stub mode or without"
        echo "   an OpenRouter API key configured."
        exit 1
    else
        echo ""
        echo -e "${GREEN}🎉 All tests passed! NoeRelay is operational.${NC}"
        exit 0
    fi
}

main "$@"
