#!/usr/bin/env python
"""Quick benchmark of qwen3.8:27b on reasoning-tasks."""
import json, time, urllib.request, sys

cases = []
with open("benchmarks/reasoning-tasks.jsonl", "r") as f:
    for line in f:
        line = line.strip()
        if line:
            cases.append(json.loads(line))

print(f"Loaded {len(cases)} reasoning cases\n")

model = "deepseek-r1:32b"
correct = 0
total_latency = 0.0
total_tokens = 0
latencies = []

for i, case in enumerate(cases):
    input_data = case.get("input", {})
    messages = input_data.get("messages", [{"role": "user", "content": str(input_data)}])
    expected = case.get("expected_output", "")

    payload = json.dumps({
        "model": model,
        "messages": messages,
        "stream": False
    }).encode("utf-8")

    req = urllib.request.Request(
        "http://127.0.0.1:11434/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        latency = (time.perf_counter() - start) * 1000
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        tokens = result.get("usage", {}).get("total_tokens", 0)

        is_correct = expected.lower() in content.lower() if expected else True
        if is_correct:
            correct += 1

        total_latency += latency
        total_tokens += tokens
        latencies.append(latency)

        status = "OK" if is_correct else "MISS"
        print(f"  [{i+1}/{len(cases)}] {case.get('id','?')}: {status} | {latency:.0f}ms | {tokens}t | expected=\"{expected[:50]}\"")
    except Exception as e:
        latency = (time.perf_counter() - start) * 1000
        print(f"  [{i+1}/{len(cases)}] {case.get('id','?')}: ERR | {latency:.0f}ms | {str(e)[:80]}")

n = len(cases)
latencies.sort()
p95 = latencies[int(n * 0.95)] if latencies else 0

print(f"\n=== qwen3.8:27b on reasoning-tasks ===")
print(f"Accuracy: {correct}/{n} = {correct/n*100:.1f}%")
print(f"Avg latency: {total_latency/n:.0f}ms")
print(f"P95 latency: {p95:.0f}ms")
print(f"Avg tokens: {total_tokens/n:.0f}")