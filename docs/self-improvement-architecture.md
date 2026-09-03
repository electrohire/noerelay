# Self-Improvement Architecture (EPR-SELF-IMPROVE-001)

## Overview

The NoeRelay self-improvement system cyclically runs benchmarks, analyzes results, applies safe improvements, and repeats until the stack converges ("dimension return").

## Architecture Diagram

```
+-------------------------------------------------------------------+
|                   SelfImprovementOrchestrator                       |
|               (scripts/noerelay_self_improve.py)                   |
|                                                                     |
|  Cycle Loop:                                                       |
|    1. Ensure Services -> ensure-services.ps1                        |
|    2. Health Probe   -> service_health_probe.py                     |
|    3. Run Benchmarks -> continuous_benchmark.py                     |
|    4. Analyze        -> improvement_analyzer.py                     |
|    5. Apply Actions  -> (restart, config_tune, model_swap)          |
|    6. Check Convergence -> score delta < threshold for N cycles     |
+-------------------------------------------------------------------+
         |                    |                         |
         v                    v                         v
+------------------+  +----------------+  +---------------------------+
| ContinuousBench- |  | Improvement-   |  | Service Health            |
| markPipeline     |  | Analyzer       |  | Probe                     |
|                  |  |                |  |                           |
| - Cohort registry|  | - Bottleneck   |  | - TCP probes               |
| - JSONL loading  |  |   detection    |  | - HTTP probes              |
| - HF dataset     |  | - Action gen   |  | - Docker checks            |
| - LiveBench      |  | - Pareto       |  | - Remote machine checks    |
| - Composite score|  |   frontier     |  | - SSH tunnel checks        |
| - Baseline mgmt  |  | - Convergence  |  |                           |
+------------------+  +----------------+  +---------------------------+
         |                    |
         v                    v
+------------------+  +----------------+
| BenchmarkRunner  |  | TrueCostModel   |
| (HTTP client)    |  |                 |
|                  |  | - TCO analysis  |
| - chat/completions|  | - Cost-quality  |
| - Multiple models |  |   tradeoffs    |
| - EPR metadata   |  | - Pareto calc   |
+------------------+  +----------------+
```

## File Inventory

| File | Role |
|------|------|
| [`scripts/noerelay_self_improve.py`](scripts/noerelay_self_improve.py) | Orchestrator — main cycle loop, state persistence, CLI |
| [`scripts/continuous_benchmark.py`](scripts/continuous_benchmark.py) | Benchmark pipeline — cohort registry, composite scoring, baseline management |
| [`scripts/improvement_analyzer.py`](scripts/improvement_analyzer.py) | Analysis engine — bottleneck detection, action generation, convergence assessment |
| [`scripts/service_health_probe.py`](scripts/service_health_probe.py) | Health probing — TCP/HTTP/Docker/SSH checks across all machines |
| [`scripts/run_benchmark.py`](scripts/run_benchmark.py) | Single-cohort benchmark CLI (legacy entry point) |
| [`scripts/model_lifecycle.py`](scripts/model_lifecycle.py) | Model discovery and lifecycle management CLI |
| [`reference/benchmark/runner.py`](reference/benchmark/runner.py) | HTTP benchmark runner (sends requests, evaluates responses) |
| [`reference/benchmark/metrics.py`](reference/benchmark/metrics.py) | Metric aggregation (accuracy, latency, cost, rates) |
| [`reference/benchmark/advanced_metrics.py`](reference/benchmark/advanced_metrics.py) | Advanced metrics (Brier score, selective risk, route regret) |
| [`reference/gateway/cost_model.py`](reference/gateway/cost_model.py) | True TCO cost model |
| [`reference/gateway/online_learning.py`](reference/gateway/online_learning.py) | Canary promotion governance |
| [`reference/gateway/local_portfolio.py`](reference/gateway/local_portfolio.py) | Local model catalog |

## Data Flow

### 1. Orchestrator (noerelay_self_improve.py)

The orchestrator runs a cycle loop:

```
for cycle in 1..max_cycles:
    1. ensure_services()        -> powershell ensure-services.ps1
    2. probe_health()           -> HealthMatrix
    3. run_benchmarks()         -> ContinuousBenchmarkPipeline.run_once()
    4. analyze()                -> ImprovementAnalyzer.analyze()
    5. apply_actions()          -> restart_service | config_tune | model_swap
    6. check_convergence()      -> score delta < 0.005 for 3 cycles
```

State is persisted in `evidence/self-improve/orchestrator_state.json`.

### 2. Benchmark Pipeline (continuous_benchmark.py)

Maps cohort names to dataset files and runs them:

```
COHORT_REGISTRY (name -> dataset path, evaluator, weight)
     |
     v
ContinuousBenchmarkPipeline.run_once(cohorts)
     |
     +--> _run_cohort_from_cfg() -> BenchmarkRunner.run_and_report()
     |
     +--> compute_composite_score(cohort_results, weights)
     |
     +--> _update_baseline(results)      # save best run
     |
     v
returns { timestamp, composite_score, cohorts, metadata }
```

### 3. Analysis Engine (improvement_analyzer.py)

Analyzes benchmark results and produces actions:

```
benchmark_results -> analyze()
     |
     +--> _detect_bottlenecks()     -> list[Bottleneck]
     +--> _generate_actions()       -> list[ImprovementAction]
     +--> _compute_pareto_frontier() -> list[pareto points]
     +--> _assess_convergence()     -> 0.0-1.0 progress
     |
     v
ImprovementReport { bottlenecks, actions, pareto_frontier, composite_score }
```

### 4. Action Categories

| Category | Auto-Apply | Description |
|----------|-----------|-------------|
| `restart_service` | Yes | Restart unhealthy services via ensure-services.ps1 |
| `config_tune` | Yes | Tune config parameters (context budget, fallbacks, verification) |
| `model_swap` | No (requires approval) | Swap cohort primary model to a stronger/faster alternative |
| `portfolio_add` | No (requires approval) | Add a new model to the local portfolio |
| `portfolio_remove` | No (requires approval) | Remove an underperforming model |

## Convergence ("Dimension Return")

Convergence is declared when ALL of:

1. **Score stability**: `|score[i] - score[i-1]| < 0.005` for 3 consecutive cycles
2. **No regression**: Scores are non-decreasing within the convergence window
3. **Minimum threshold**: All recent scores >= 0.70

Convergence detection is implemented in [`SelfImprovementOrchestrator._check_convergence()`](scripts/noerelay_self_improve.py:585).

## Usage Modes

```bash
# Dry-run: analyze but don't apply changes
python scripts/noerelay_self_improve.py --dry-run

# Auto-apply safe improvements
python scripts/noerelay_self_improve.py --auto-apply

# Quick mode: only quick-test cohort
python scripts/noerelay_self_improve.py --quick

# Full mode: all cohorts + LiveBench + HF datasets
python scripts/noerelay_self_improve.py --full

# Fixed number of cycles
python scripts/noerelay_self_improve.py --max-cycles 5

# Resume from previous state
python scripts/noerelay_self_improve.py --resume evidence/self-improve/orchestrator_state.json

# Run benchmarks standalone (without improvement loop)
python scripts/continuous_benchmark.py --once
python scripts/continuous_benchmark.py --once --cohorts quick-test,coding-tasks
python scripts/continuous_benchmark.py --watch --interval 600
python scripts/continuous_benchmark.py --list  # list registered cohorts

# Analyze a single benchmark run
python scripts/improvement_analyzer.py evidence/benchmarks/run-20240902-120000.json

# Probe health without improvement loop
python scripts/service_health_probe.py
python scripts/service_health_probe.py --json
```

## Extension Points

1. **Add a new cohort**: Add an entry to `COHORT_REGISTRY` in [`continuous_benchmark.py`](scripts/continuous_benchmark.py:58) with dataset path, evaluator, and weight.

2. **Add a new action category**: Add the category to [`AUTO_APPLY_CATEGORIES`](scripts/noerelay_self_improve.py:104) or [`APPROVAL_CATEGORIES`](scripts/noerelay_self_improve.py:107), implement execution in [`_execute_action()`](scripts/noerelay_self_improve.py:547), and recommendation generation in [`_generate_actions()`](scripts/improvement_analyzer.py:409).

3. **Add HuggingFace dataset cohorts**: Populate `HF_COHORTS` in [`continuous_benchmark.py`](scripts/continuous_benchmark.py:113) with `dataset_id`, `split`, and `evaluator`.

4. **Add LiveBench integration**: Implement [`_run_livebench_cohort()`](scripts/continuous_benchmark.py:400) with LiveBench API integration.

5. **Add custom thresholds**: Update [`COHORT_THRESHOLDS`](scripts/improvement_analyzer.py:138) with per-cohort accuracy, latency, and safety thresholds.