#!/usr/bin/env python
"""Discover current frontier models on OpenRouter (September 2026)."""
import os, json, urllib.request, sys

env = {}
with open('.env') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip()

api_key = env.get('OPENROUTER_API_KEY', '')
req = urllib.request.Request('https://openrouter.ai/api/v1/models')
req.add_header('Authorization', f'Bearer {api_key}')
req.add_header('HTTP-Referer', 'https://noerelay.dev')

try:
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode())
    models = data.get('data', [])
    sys.stderr.write(f'Total models on OpenRouter: {len(models)}\n')
    
    priced = []
    for m in models:
        mid = m.get('id', '')
        name = m.get('name', '')
        pricing = m.get('pricing', {})
        pp = float(pricing.get('prompt', '0'))
        cp = float(pricing.get('completion', '0'))
        ctx = m.get('context_length', 0)
        priced.append((mid, name, pp, cp, ctx))
    
    priced.sort(key=lambda x: x[2] + x[3], reverse=True)
    
    sys.stderr.write('\n=== TOP 50 MODELS BY PRICE (frontier indicators) ===\n')
    for mid, name, pp, cp, ctx in priced[:50]:
        sys.stderr.write(f'  ${pp+cp:.1f}/M | {mid}  ctx={ctx}\n')
    
    # Also show notable budget models
    sys.stderr.write('\n=== NOTABLE BUDGET MODELS (sorted by price) ===\n')
    budget_keywords = ['llama-3.2', 'llama-4', 'gemma', 'qwen-3', 'qwen3', 'mistral-small', 'phi-4', 'deepseek-chat']
    budget = [(mid, name, pp, cp, ctx) for mid, name, pp, cp, ctx in priced if any(kw in mid.lower() for kw in budget_keywords)]
    budget.sort(key=lambda x: x[2] + x[3])
    for mid, name, pp, cp, ctx in budget[:20]:
        sys.stderr.write(f'  ${pp+cp:.3f}/M | {mid}  ctx={ctx}\n')

except Exception as e:
    sys.stderr.write(f'Error: {e}\n')
    sys.exit(1)