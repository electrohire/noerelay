"""Zoo-code bootstrapping verification script."""
import requests
import json
import time
import sys

BASE = "http://127.0.0.1:8080"

def do_step(name, method, path, **kw):
    print(f"\n{'='*60}")
    print(f"STEP: {name}")
    print(f"{method} {path}")
    url = f"{BASE}{path}"
    start = time.time()
    r = requests.request(method, url, timeout=60, **kw)
    elapsed = time.time() - start
    print(f"Status: {r.status_code} ({elapsed:.1f}s)")
    try:
        data = r.json()
        print(json.dumps(data, indent=2, default=str)[:3000])
        return data
    except Exception:
        print(f"Raw: {r.text[:2000]}")
        return None

# Step 3: zoo-code style request
do_step("Step 3: zoo-code style request (system+user, temperature, max_tokens)", "POST", "/v1/chat/completions",
    json={
        "model": "noerelay/epr-1",
        "messages": [
            {"role": "system", "content": "You are a helpful coding assistant."},
            {"role": "user", "content": "Write a Python function that reverses a string."}
        ],
        "temperature": 0.1,
        "max_tokens": 4096
    },
    headers={"Authorization": "Bearer test-key"}
)

# Step 4: Simple model test
do_step("Step 4: Simple model test", "POST", "/v1/chat/completions",
    json={
        "model": "noerelay/epr-1",
        "messages": [{"role": "user", "content": "What is the capital of France?"}]
    }
)

# Step 5: Streaming
print(f"\n{'='*60}")
print("STEP 5: Streaming")
url = f"{BASE}/v1/chat/completions"
r = requests.post(url, json={
    "model": "noerelay/epr-1",
    "messages": [{"role": "user", "content": "Count from 1 to 5"}],
    "stream": True
}, stream=True, timeout=30)
print(f"Status: {r.status_code}")
chunk_count = 0
for line in r.iter_lines():
    if line:
        line_str = line.decode("utf-8")
        if line_str.startswith("data: "):
            chunk_count += 1
            data_str = line_str[6:]
            if data_str != "[DONE]":
                try:
                    d = json.loads(data_str)
                    delta = d.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        print(f"  Chunk {chunk_count}: {content[:100]}")
                except Exception:
                    pass
        if chunk_count > 20:
            print("  ... (truncated after 20 chunks)")
            break
print(f"Total chunks received: {chunk_count}")

# Step 7: Quick rate limit test (5 rapid requests)
print(f"\n{'='*60}")
print("STEP 7: Rate limit test (5 rapid requests)")
for i in range(1, 6):
    try:
        r = requests.post(f"{BASE}/v1/chat/completions", json={
            "model": "noerelay/epr-1",
            "messages": [{"role": "user", "content": f"test {i}"}]
        }, timeout=10)
        print(f"  Request {i}: {r.status_code}")
    except Exception as e:
        print(f"  Request {i}: ERROR - {e}")

# Step 6: Ledger
do_step("Step 6: EPR Ledger Events", "GET", "/v1/epr/ledger/events")

# Step 8: Error cases
do_step("Step 8a: Invalid model", "POST", "/v1/chat/completions",
    json={"model": "invalid-model", "messages": [{"role": "user", "content": "test"}]}
)
do_step("Step 8b: Empty messages", "POST", "/v1/chat/completions",
    json={"model": "noerelay/epr-1", "messages": []}
)

print("\nDone with HTTP tests.")