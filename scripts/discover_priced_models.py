#!/usr/bin/env python
"""Get raw pricing from OpenRouter list API."""
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

# Find models with non-zero pricing
priced = []
for m in data.get('data', []):
    pricing = m.get('pricing', {})
    pp = float(pricing.get('prompt', 0))
    cp = float(pricing.get('completion', 0))
    if pp > 0 or cp > 0:
        priced.append((m.get('id',''), m.get('name',''), pp, cp, m.get('context_length',0)))

priced.sort(key=lambda x: x[2]+x[3], reverse=True)

with open('evidence/portfolio/frontier_pricing_v2.json', 'w') as f:
    out = [{"id": r[0], "name": r[1], "prompt_per_M": r[2], "completion_per_M": r[3], "total_per_M": r[2]+r[3], "ctx": r[4]} for r in priced[:60]]
    json.dump(out, f, indent=2)

print(f"Found {len(priced)} models with non-zero pricing")
print(f"Top 20:")
for r in priced[:20]:
    print(f"  ${r[2]+r[3]:.1f}/M | {r[0]}")