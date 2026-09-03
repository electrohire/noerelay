#!/usr/bin/env python
"""Discover frontier model pricing details from OpenRouter."""
import os, json, urllib.request, sys

env = {}
with open('.env') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip()

api_key = env.get('OPENROUTER_API_KEY', '')

# Top frontier model IDs to check
frontier_ids = [
    "openai/gpt-5.2",
    "openai/gpt-5.2-codex",
    "openai/gpt-5.4",
    "openai/gpt-5.5",
    "openai/gpt-5.5-pro",
    "openai/o3-pro",
    "openai/o1-pro",
    "anthropic/claude-opus-5",
    "anthropic/claude-opus-4.8",
    "anthropic/claude-fable-5.1",
    "anthropic/claude-sonnet-4.6",
    "anthropic/claude-sonnet-4.5",
    "moonshotai/kimi-k3",
    "sakana/fugu-ultra",
    "google/gemini-2.5-pro",
    "google/gemma-4-31b-it",
    "google/gemma-4-26b-a4b-it",
    "qwen/qwen3-235b-a22b-2507",
    "qwen/qwen3.7-flash",
    "qwen/qwen3-coder-30b-a3b-instruct",
    "meta-llama/llama-4-maverick",
    "meta-llama/llama-4-scout",
    "deepseek/deepseek-chat",
    "mistralai/mistral-small-3.2-24b-instruct",
    "microsoft/phi-4",
]

results = []
for mid in frontier_ids:
    try:
        req = urllib.request.Request(f'https://openrouter.ai/api/v1/models/{mid}')
        req.add_header('Authorization', f'Bearer {api_key}')
        req.add_header('HTTP-Referer', 'https://noerelay.dev')
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        pricing = data.get('pricing', {})
        pp = float(pricing.get('prompt', 0))
        cp = float(pricing.get('completion', 0))
        ctx = data.get('context_length', 0)
        total = pp + cp
        results.append((mid, total, pp, cp, ctx))
    except Exception as e:
        results.append((mid, 0, 0, 0, 0, str(e)))

results.sort(key=lambda x: x[1], reverse=True)

with open('evidence/portfolio/frontier_pricing.json', 'w') as f:
    out = []
    for r in results:
        if len(r) == 6:
            out.append({"id": r[0], "total_per_M": r[1], "prompt": r[2], "completion": r[3], "ctx": r[4], "error": r[5]})
        else:
            out.append({"id": r[0], "total_per_M": r[1], "prompt": r[2], "completion": r[3], "ctx": r[4]})
    json.dump(out, f, indent=2)

print("Done. Check evidence/portfolio/frontier_pricing.json")