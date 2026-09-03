#!/usr/bin/env python
"""Dump raw pricing structure from OpenRouter to file."""
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

models = data.get('data', [])

output_lines = []

# Dump first 3 models completely
for m in models[:3]:
    output_lines.append(json.dumps(m, indent=2))
    output_lines.append("---")

# Now find models with actual pricing
output_lines.append("\n=== MODELS WITH NON-ZERO PRICING (sorted by total) ===")
priced = []
for m in models:
    pricing = m.get('pricing', {})
    pp_raw = pricing.get('prompt', '0')
    cp_raw = pricing.get('completion', '0')
    pp = float(pp_raw) if pp_raw else 0
    cp = float(cp_raw) if cp_raw else 0
    total = pp + cp
    if total > 0:
        priced.append((total, pp, cp, m['id'], m.get('name',''), m.get('context_length',0)))

priced.sort(key=lambda x: x[0], reverse=True)

output_lines.append(f"\nTotal with non-zero pricing: {len(priced)}")
output_lines.append(f"\nTop 40:")
for total, pp, cp, mid, name, ctx in priced[:40]:
    output_lines.append(f"  ${total:.4f}/M | prompt=${pp:.4f} compl=${cp:.4f} | {mid} ctx={ctx}")

output_lines.append(f"\n=== BUDGET TIER (priced but cheap) ===")
budget = [x for x in priced if x[0] < 1.0]
budget.sort(key=lambda x: x[0])
for total, pp, cp, mid, name, ctx in budget[:30]:
    output_lines.append(f"  ${total:.4f}/M | {mid} ctx={ctx}")

with open('evidence/portfolio/pricing_debug.txt', 'w') as f:
    f.write('\n'.join(output_lines))

print("Done. Check evidence/portfolio/pricing_debug.txt")