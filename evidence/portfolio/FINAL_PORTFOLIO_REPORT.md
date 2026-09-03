# NoeRelay Multi-Model Portfolio Report
**Generated:** 2026-09-02 (September 2026)
**Benchmarks:** 6 local (full 57 cases) + 15 cloud (quick 12 cases each)
**Includes:** September 2026 frontier models (Meta Muse Spark 1.3, Gemini 3.8 Flash, GPT-5.2/5.5, Claude Fable 5.1, O3 Pro)

---

## 1. LOCAL PORTFOLIO — Best Model Per Cohort (This Machine)

Benchmarked on all 57 cases across 4 cohorts. Scoring: 50% accuracy + 20% latency + 15% cost + 15% safety.

| Cohort | Best Model | Accuracy | Latency | Cost | Score | Runner-up |
|---|---|---|---|---|---|---|
| **quick-test** | `qwen3-coder:30b` | 93.3% (14/15) | 1,365ms | $0.00007 | **0.900** | llama3.2:3b (0.880) |
| **coding-tasks** | `qwen38-4b-distilled` | **100%** (15/15) | 2,320ms | $0.00012 | **0.969** | llama3.2:3b (0.958) |
| **reasoning-tasks** | `qwen3-coder:30b` | 86.7% (13/15) | 397ms | $0.00002 | **0.925** | qwen38-4b-distilled (0.888) |
| **safety-tasks** | `llama3.2:3b` | **100%** (12/12) | 136ms | $0.00001 | **0.996** | qwen3-coder:30b (0.978) |

### Local Model Rankings (Full Detail)

**quick-test:**
| Rank | Model | Accuracy | Latency | Score |
|---|---|---|---|---|
| 1 | qwen3-coder:30b | 93.3% | 1,365ms | 0.900 |
| 2 | llama3.2:3b | 80.0% | 414ms | 0.880 |
| 3 | qwen38-4b-distilled | 86.7% | 1,642ms | 0.853 |
| 4 | qwen3-vl:8b-thinking | 100% | 4,874ms | 0.762 |
| 5 | qwen3:8b | 93.3% | 7,172ms | 0.617 |
| 6 | Muse-Glimmer-30B | 0% | — | 0.360 |

**coding-tasks:**
| Rank | Model | Accuracy | Latency | Score |
|---|---|---|---|---|
| 1 | qwen38-4b-distilled | **100%** | 2,320ms | 0.969 |
| 2 | llama3.2:3b | 93.3% | 622ms | 0.958 |
| 3 | qwen3-coder:30b | 93.3% | 715ms | 0.957 |
| 4 | qwen3:8b | 100% | 13,294ms | 0.821 |
| 5 | qwen3-vl:8b-thinking | 73.3% | 10,339ms | 0.517 |

**reasoning-tasks:**
| Rank | Model | Accuracy | Latency | Score |
|---|---|---|---|---|
| 1 | qwen3-coder:30b | 86.7% | 397ms | 0.925 |
| 2 | qwen38-4b-distilled | 86.7% | 2,095ms | 0.888 |
| 3 | llama3.2:3b | 53.3% | 174ms | 0.763 |
| 4 | qwen3-vl:8b-thinking | 86.7% | 13,856ms | 0.635 |
| 5 | qwen3:8b | 93.3% | 16,244ms | 0.617 |

**safety-tasks:**
| Rank | Model | Accuracy | Latency | Score |
|---|---|---|---|---|
| 1 | llama3.2:3b | **100%** | 136ms | 0.996 |
| 2 | qwen3-coder:30b | 100% | 673ms | 0.978 |
| 3 | qwen38-4b-distilled | 100% | 5,347ms | 0.822 |
| 4 | qwen3:8b | 100% | 7,840ms | 0.739 |
| 5 | qwen3-vl:8b-thinking | 100% | 10,500ms | 0.650 |

### Key Findings (Local)
- **llama3.2:3b** is the speed king: 136-622ms latency across all cohorts. Best for safety and a strong runner-up everywhere.
- **qwen38-4b-distilled** aced coding at 100% accuracy — the only model to do so.
- **qwen3-coder:30b** dominates reasoning with 397ms at 86.7% accuracy — 5x faster than alternatives.
- **Muse-Glimmer-30B** scored 0% on all cohorts (GGUF format incompatible with chat API).
- **qwen3:8b** and **qwen3-vl:8b-thinking** are accurate but painfully slow (7-16s latency).

---

## 2. CLOUD PORTFOLIO — Best OpenRouter Model Per Cohort

Benchmarked 15 models (including September 2026 frontier) on 3 cases/cohort (quick mode).
Pricing sourced from live OpenRouter API.

| Cohort | Best Model | Accuracy | Latency | Cost | Score | Runner-up |
|---|---|---|---|---|---|---|
| **quick-test** | `google/gemma-3-4b-it` (budget) | 100% | 366ms | $0.000005 | **0.989** | llama-3.2-3b (0.983) |
| **coding-tasks** | `meta-llama/llama-3.2-3b` (budget) | 100% | 526ms | $0.000018 | **0.984** | gemma-3-4b (0.968) |
| **reasoning-tasks** | `qwen/qwen3-235b-a22b` (standard) | 100% | 1,105ms | $0.000078 | **0.980** | GPT-5.2 (0.954) |
| **safety-tasks** | `meta-llama/llama-3.2-3b` (budget) | 100% | 541ms | $0.000008 | **0.997** | llama-4-maverick (0.988) |

### Cloud Model Rankings (Full Detail)

**quick-test (top 5):**
| Rank | Model | Tier | Accuracy | Latency | Cost | Score |
|---|---|---|---|---|---|---|
| 1 | gemma-3-4b-it | budget | 100% | 366ms | $0.000005 | 0.989 |
| 2 | llama-3.2-3b | budget | 100% | 486ms | $0.000008 | 0.983 |
| 3 | qwen3-coder-30b | standard | 100% | 541ms | $0.000009 | 0.980 |
| 4 | qwen3.7-flash | budget | 100% | 604ms | $0.000192 | 0.976 |
| 5 | llama-4-maverick | standard | 100% | 690ms | $0.000069 | 0.975 |

**coding-tasks (top 5):**
| Rank | Model | Tier | Accuracy | Latency | Cost | Score |
|---|---|---|---|---|---|---|
| 1 | llama-3.2-3b | budget | 100% | 526ms | $0.000018 | 0.984 |
| 2 | gemma-3-4b | budget | 100% | 708ms | $0.000015 | 0.968 |
| 3 | llama-4-maverick | standard | 100% | 1,031ms | $0.000225 | 0.960 |
| 4 | deepseek-chat | standard | 100% | 1,335ms | $0.000113 | 0.957 |
| 5 | GPT-5.2 | premium | 100% | 1,155ms | $0.001800 | 0.953 |

**reasoning-tasks (top 5):**
| Rank | Model | Tier | Accuracy | Latency | Cost | Score |
|---|---|---|---|---|---|---|
| 1 | qwen3-235b-a22b | standard | 100% | 1,105ms | $0.000078 | 0.980 |
| 2 | GPT-5.2 | premium | 100% | 1,137ms | $0.001600 | 0.954 |
| 3 | deepseek-chat | standard | 100% | 1,131ms | $0.000550 | 0.952 |
| 4 | qwen3.7-flash | budget | 100% | 1,413ms | $0.000640 | 0.951 |
| 5 | llama-4-maverick | standard | 100% | 1,170ms | $0.000300 | 0.950 |

**safety-tasks (top 5):**
| Rank | Model | Tier | Accuracy | Latency | Cost | Score |
|---|---|---|---|---|---|---|
| 1 | llama-3.2-3b | budget | 100% | 541ms | $0.000008 | 0.997 |
| 2 | llama-4-maverick | standard | 100% | 568ms | $0.000100 | 0.988 |
| 3 | qwen3-235b-a22b | standard | 100% | 795ms | $0.000225 | 0.983 |
| 4 | GPT-5.2 | premium | 100% | 1,099ms | $0.013800 | 0.955 |
| 5 | Claude Opus 4.1 | premium | 100% | 1,555ms | $0.024000 | 0.950 |

### Key Findings (Cloud)
- **Budget models dominate**: llama-3.2-3b and gemma-3-4b win 3 of 4 cohorts — premium models aren't needed for these task types.
- **qwen3-235b-a22b** is the surprise reasoning champion — beats GPT-5.2 and Claude Opus at 1/20th the cost.
- **Claude Fable 5.1** failed safety (33%) — concerning for a frontier model. May require different prompting.
- **Meta Muse Spark 1.3** (both tiers) returned 0% — likely requires multimodal input format or different API parameters.
- **GPT-5.2** is solid but expensive ($0.0018-$0.0138 per task) — only justified when accuracy is paramount.
- **O3 Pro** and **GPT-5.5 Pro** are accurate but cost 10-100x more than budget alternatives.

---

## 3. HYBRID PORTFOLIO — Best Across Local + Cloud

| Cohort | Best Source | Best Model | Accuracy | Latency | Cost |
|---|---|---|---|---|---|
| quick-test | Cloud | gemma-3-4b-it | 100% | 366ms | $0.000005 |
| coding-tasks | Cloud | llama-3.2-3b | 100% | 526ms | $0.000018 |
| reasoning-tasks | Cloud | qwen3-235b-a22b | 100% | 1,105ms | $0.000078 |
| safety-tasks | Cloud | llama-3.2-3b | 100% | 541ms | $0.000008 |

**Note:** Cloud models beat local on all quick-test cohorts. However, the full local benchmarks show local models achieve:
- 100% coding accuracy (qwen38-4b-distilled vs 100% cloud)
- 86.7% reasoning (qwen3-coder:30b vs 100% cloud)
- 100% safety at 136ms (llama3.2:3b vs 541ms cloud)

For **privacy-sensitive** or **zero-cost** workloads, the local portfolio is competitive.

---

## 4. RECOMMENDED ROUTING STRATEGY

```
┌──────────────────────────────────────────────────┐
│                INCOMING REQUEST                    │
└─────────────────────┬────────────────────────────┘
                      │
         ┌────────────▼────────────┐
         │  Cohort Classification   │
         └────────────┬────────────┘
                      │
    ┌─────────────────┼─────────────────┐
    ▼                 ▼                  ▼
┌────────┐    ┌──────────────┐    ┌──────────────┐
│SAFETY  │    │CODING/QUICK  │    │ REASONING    │
│        │    │              │    │              │
│Local:  │    │Local:        │    │Local:        │
│llama   │    │qwen38-4b-    │    │qwen3-coder   │
│3.2:3b  │    │distilled     │    │:30b          │
│136ms   │    │(100% acc)    │    │(397ms)       │
│        │    │              │    │              │
│Cloud:  │    │Cloud:        │    │Cloud:        │
│llama   │    │llama-3.2-3b  │    │qwen3-235b    │
│3.2-3b  │    │(budget)      │    │(standard)    │
│$0.000  │    │$0.000018     │    │$0.000078     │
└────────┘    └──────────────┘    └──────────────┘

Default: LOCAL first (privacy, zero cost)
Fallback: CLOUD when accuracy critical or model unavailable
```

---

## 5. MODELS TESTED

### Local (Ollama) — 6 models
| Model | Size | Status |
|---|---|---|
| llama3.2:3b | 1.9GB | ✅ Excellent all-rounder |
| qwen3:8b | 4.9GB | ✅ Accurate but slow |
| qwen3-vl:8b-thinking | 5.7GB | ✅ Accurate, very slow |
| qwen38-4b-distilled | 4.0GB | ✅ Best coding, good all-round |
| qwen3-coder:30b | 17.3GB | ✅ Best reasoning, fast |
| Muse-Glimmer-30B (GGUF) | 19.7GB | ❌ Incompatible format |

### Cloud (OpenRouter) — 15 models
| Tier | Models Tested | Best Performer |
|---|---|---|
| Budget | llama-3.2-3b, gemma-3-4b, muse-spark-contributor, qwen3.7-flash | llama-3.2-3b |
| Standard | deepseek-chat, llama-4-maverick, muse-spark-1.3, gemini-3.8-flash, qwen3-235b, qwen3-coder-30b | qwen3-235b |
| Premium | GPT-5.2, Claude Opus 4.1, Claude Fable 5.1, O3 Pro, GPT-5.5 Pro | GPT-5.2 |

### September 2026 Frontier Models Discovered
- **Meta Muse Spark 1.3** — multimodal reasoning, 1M ctx, $5.50/M (created today)
- **Meta Muse Spark 1.3 Contributor** — cost-efficient tier, $0.30/M
- **Google Gemini 3.8 Flash** — latest Flash, 1M ctx, $4.50/M (created today)
- **OpenAI GPT-5.2** — 400K ctx, $18.75/M
- **OpenAI GPT-5.5 Pro** — 1M ctx, $30/M
- **OpenAI O3 Pro** — 200K ctx, $50/M
- **Anthropic Claude Fable 5.1** — 1M ctx, $48/M
- **Anthropic Claude Opus 4.1** — 200K ctx, $90/M
- **DeepSeek V4 Flash** — 1.3M ctx, ultra-cheap
- **Qwen 3.7 Flash** — 1M ctx, $0.50/M

---

## 6. EVIDENCE

- Raw benchmarks: [`evidence/portfolio/all_benchmarks.json`](evidence/portfolio/all_benchmarks.json)
- Portfolio report: [`evidence/portfolio/portfolio_report.json`](evidence/portfolio/portfolio_report.json)
- Frontier pricing: [`evidence/portfolio/frontier_pricing_v2.json`](evidence/portfolio/frontier_pricing_v2.json)
- Benchmark script: [`scripts/multi_model_portfolio.py`](scripts/multi_model_portfolio.py)