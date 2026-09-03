#!/usr/bin/env python
"""Dump raw pricing structure from OpenRouter."""
import os, json, urllib.request

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
with urllib.request.urlopen(req, timeout=20) as resp:
    data = json.loads(resp.read().decode())

# Dump first 3 models completely to see pricing structure
models = data.get('data', [])
for m in models[:3]:
    print(json.dumps(m, indent=2))
    print("---")

# Now find a model with actual pricing by looking at raw values
print("\n=== MODELS WITH NON-ZERO PRICING (raw) ===")
count = 0
for m in models:
    pricing = m.get('pricing', {})
    pp_raw = pricing.get('prompt', '0')
    cp_raw = pricing.get('completion', '0')
    pp = float(pp_raw) if pp_raw else 0
    cp = float(cp_raw) if cp_raw else 0
    if pp > 0 or cp > 0:
        count += 1
        if count <= 15:
            print(f"  {m['id']}: prompt={pp_raw!r} completion={cp_raw!r}")
print(f"\nTotal with non-zero pricing: {count}")