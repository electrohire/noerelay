# Hugging Face benchmark acquisition

Hugging Face Hub is NoeRelay's default catalog and distribution layer for public, gated, and private benchmark artifacts. It is not the authority for whether a model is promoted: deterministic scorers, cohort gates, provenance, and the evidence ledger retain that authority.

## Acquisition contract

Every benchmark run must record:

- Hub dataset ID, configuration, and split;
- the resolved immutable repository commit, never only `main`;
- file hashes, upstream source, license, and any transformation provenance;
- evaluator and metric versions;
- model ID, OpenRouter routing constraints, policy version, random seed, and execution-environment hash;
- raw sample-level results or a governed reference to them.

Use `datasets.load_dataset(..., revision=<commit>)` for compatible datasets. Use `huggingface_hub.snapshot_download(..., revision=<commit>)` when a benchmark ships its own evaluator or non-Datasets file layout. Remote dataset code must remain disabled unless it has been reviewed and its revision and code hash are ledgered.

Hugging Face Lighteval is appropriate for its supported task catalog and custom metrics, including API-backed model adapters. Benchmarks such as SWE-bench and BFCL still require their official execution/scoring harnesses; acquiring their artifacts from the Hub does not replace those evaluators.

## Initial Hub sources

| Cohort | Hub dataset |
|---|---|
| Governed software engineering | `SWE-bench/SWE-bench_Verified` |
| Agentic tool calling | `gorilla-llm/Berkeley-Function-Calling-Leaderboard` |

Internal hidden suites should use private dataset repositories or controlled artifact storage. A model under evaluation must not receive hidden labels, judge outputs, or promotion thresholds in its task context.

## Cache and credentials

`HF_TOKEN` authenticates Hub downloads. `HF_HOME` places the local cache outside tracked project content. CI and local development must use separate fine-grained tokens as described in [environment.md](environment.md).
