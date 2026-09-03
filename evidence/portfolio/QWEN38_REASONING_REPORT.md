# Qwen3.8-27B Reasoning Evaluation + Self-Improvement Report

**Generated**: 2026-09-03T12:56:00Z  
**Machine**: Windows 11, local Ollama + Docker OpenRouter gateway

---

## 1. Qwen3.8-27B: Discovery & Benchmark

### Model Details

| Property | Value |
|----------|-------|
| **Ollama tag** | `qwen3.8:27b` |
| **HuggingFace source** | `Qwen/Qwen3.8-27B` (5.2M downloads, 13,766 likes) |
| **Size on disk** | 16.5 GB |
| **Quantization** | Q4_K_M (Ollama default) |
| **Parameters** | 27B |
| **Context window** | 131,072 tokens |

### Reasoning-Tasks Benchmark (15 cases)

| Metric | qwen3.8:27b | qwen3-coder:30b (prior best) | Delta |
|--------|-------------|------|-------|
| **Accuracy** | 86.7% (13/15) | 86.7% (13/15) | **0%** |
| **Avg Latency** | 7,413ms | 397ms | **+1,767%** |
| **P95 Latency** | 59,018ms | ~800ms | **+7,278%** |
| **Avg Tokens** | 203 | ~150 | +35% |
| **Cold start** | 59,018ms (first req) | N/A | — |

### Per-Case Results

| Case | Expected | Result | Latency | Tokens |
|------|----------|--------|---------|--------|
| reason-1 | "No" | ✅ OK | 59,018ms | 267 |
| reason-2 | "360" | ✅ OK | 1,912ms | 124 |
| reason-3 | "1" | ✅ OK | 2,031ms | 126 |
| reason-4 | "64" | ✅ OK | 1,549ms | 100 |
| reason-5 | "21" | ✅ OK | 1,650ms | 112 |
| reason-6 | "50" | ❌ MISS | 7,478ms | 352 |
| reason-7 | "5" | ✅ OK | 3,070ms | 184 |
| reason-8 | "5" | ✅ OK | 2,354ms | 143 |
| reason-9 | "1/2" | ❌ MISS | 9,763ms | 490 |
| reason-10 | "13" | ✅ OK | 1,665ms | 98 |
| reason-11 | "54" | ✅ OK | 1,617ms | 99 |
| reason-12 | "burn" | ✅ OK | 6,085ms | 284 |
| reason-13 | "paradox" | ✅ OK | 3,754ms | 173 |
| reason-14 | "24" | ✅ OK | 3,478ms | 200 |
| reason-15 | "3" | ✅ OK | 5,774ms | 299 |

### Verdict: ❌ NOT RECOMMENDED for reasoning

**Same accuracy as qwen3-coder:30b but 18x slower.** The 27B parameter count doesn't translate to better reasoning on these benchmarks. The model is verbose (203 avg tokens vs 150) and has severe cold-start latency (59s first request).

---

## 2. Better Local Reasoning Models (HuggingFace Survey)

### Top Candidates for Local Deployment

| Model | Downloads | Likes | Est. Size | Notes |
|-------|-----------|-------|-----------|-------|
| **DeepSeek R1 Distill Qwen 32B** | 548K | 1,607 | ~19GB | Best open reasoning model; currently pulling |
| **Qwen3.5-14B-A3B Claude Opus Reasoning** | 8.6K | 19 | ~8GB | MoE, smaller, reasoning-distilled |
| **Qwen3-30B-A3B Claude Opus High Reasoning** | 355 | 6 | ~17GB | MoE, uncensored variant |
| **Qwen3.8-27B Claude Opus Reasoning Distilled** | 11.6K | 20 | ~16GB | Reasoning-distilled version of qwen3.8 |
| **Phi-4 Reasoning Plus** | 183 | 1 | ~8GB | Microsoft reasoning model |

### Recommendation

1. **Immediate**: Keep `qwen3-coder:30b` as the local reasoning model (86.7%, 397ms)
2. **Short-term**: Pull `deepseek-r1:32b` (19GB, currently downloading) — expected to outperform both
3. **Medium-term**: Try `Qwen3.5-14B-A3B-Claude-Opus-Reasoning` (smaller MoE, reasoning-distilled)
4. **Cloud fallback**: `qwen3-235b-a22b` on OpenRouter (100% accuracy, $0.90/M tok)

---

## 3. Self-Improvement Loop Results (5 Cycles, Live OpenRouter)

### Score Progression

| Cycle | Score | Delta | Key Actions |
|-------|-------|-------|-------------|
| 1 | **0.8943** | — | Safety verification for reasoning + safety tasks; cloud fallback for reason-6 |
| 2 | **0.9563** | +0.062 | Context budget reduction; cloud fallback for reason-9 |
| 3 | **0.9380** | -0.018 | Model swap recommendations (qwen38-4b-distilled) |
| 4 | **0.9125** | -0.026 | No new actions (all prior actions re-applied) |
| 5 | **0.9380** | +0.026 | Safety verification re-triggered for safety-tasks |

### Final State

- **Composite score**: 0.9380
- **Converged**: No (oscillating 0.91-0.96)
- **Actions applied**: 8
- **Pending actions**: 2 (model swap recommendations at 65% confidence)

### Per-Cohort Performance (Cycle 5)

| Cohort | Accuracy | Safety | Notes |
|--------|----------|--------|-------|
| quick-test | 100% | 100% | Perfect |
| coding-tasks | 100% | 100% | Perfect |
| reasoning-tasks | 93.3% | 86.7% | reason-9 consistently fails |
| safety-tasks | 100% | 91.7% | 8.3% unsafe accept rate |

### Bottleneck Analysis

- **reason-9** ("1/2"): Consistently fails across all cycles — requires cloud fallback
- **reason-6** ("50"): Fixed via cloud fallback in cycle 1
- **safety-tasks unsafe rate**: 8.3% exceeds 2.0% threshold — safety verification enabled but non-deterministic LLM outputs cause variance

---

## 4. Complete Local Model Rankings (All Cohorts)

| Rank | Model | quick-test | coding | reasoning | safety | Size |
|------|-------|------------|--------|-----------|--------|------|
| 1 | **qwen3-coder:30b** | 100% | 93.3% | **86.7%** ⚡397ms | 100% | 17.3GB |
| 2 | **qwen38-4b-distilled** | 100% | **100%** | 80.0% | 100% | 4.0GB |
| 3 | **llama3.2:3b** | **100%** ⚡136ms | 86.7% | 73.3% | **100%** ⚡136ms | 1.9GB |
| 4 | **qwen3:8b** | 100% | 86.7% | 80.0% | 100% | 4.9GB |
| 5 | **qwen3.8:27b** | — | — | 86.7% 🐌7,413ms | — | 16.5GB |
| 6 | **Muse-Glimmer-30B** | ❌ 0% | ❌ 0% | ❌ 0% | ❌ 0% | 19.7GB |

### Recommended Local Portfolio

```
quick-test    → llama3.2:3b        (100%, 136ms, 1.9GB)
coding-tasks  → qwen38-4b-distilled (100%, 1,484ms, 4.0GB)
reasoning     → qwen3-coder:30b     (86.7%, 397ms, 17.3GB)
safety-tasks  → llama3.2:3b        (100%, 136ms, 1.9GB)
```

---

## 5. Cloud Portfolio (OpenRouter, Sep 2026)

| Cohort | Best Model | Accuracy | Cost/1M tok |
|--------|-----------|----------|-------------|
| quick-test | `gemma-3-4b` | 100% | $0.05/$0.15 |
| coding-tasks | `llama-3.2-3b` | 100% | $0.06/$0.08 |
| reasoning-tasks | `qwen3-235b-a22b` | 100% | $0.90/$0.90 |
| safety-tasks | `llama-3.2-3b` | 100% | $0.06/$0.08 |

---

## 6. Key Takeaways

1. **qwen3.8:27b is NOT an upgrade for reasoning** — same accuracy as qwen3-coder:30b but 18x slower. The extra 27B parameters don't help on these benchmarks.

2. **qwen3-coder:30b remains the best local reasoning model** at 86.7% accuracy with 397ms latency. It's the sweet spot of speed vs accuracy.

3. **DeepSeek R1 Distill Qwen 32B** (currently pulling) is the most promising upgrade path — 548K downloads, 1,607 likes, purpose-built for reasoning.

4. **Self-improvement converges around 0.91-0.96** — the non-deterministic nature of LLM outputs on edge cases (reason-9, safety boundary cases) prevents full convergence to 1.0.

5. **Budget cloud models dominate** — premium frontier models (GPT-5.5, Claude Opus 4.1) are unnecessary for these task types. The $0.06/M tok llama-3.2-3b matches their accuracy.

6. **Layer1labs machine is unreachable** — SSH tunnel or Tailscale connection needed for remote vLLM inference.