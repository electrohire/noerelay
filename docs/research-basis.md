# Research and Standards Basis

This file records the primary sources that motivated EPR-1. Preprints are research evidence, not production guarantees; their reported results require local reproduction.

## Routing and harnesses

- [LLMRouter: Unified Infrastructure for Developing, Evaluating, and Deploying LLM Routers](https://arxiv.org/abs/2608.06867), 7 August 2026 — sequential routing formulation across generic, memory, vision, time-series, and personalized tasks.
- [Agentic Routing: The Harness-Native Data Flywheel](https://arxiv.org/abs/2607.11399), 13 July 2026 — step-level routing conditioned on harness state and environment-labeled outcomes.
- [WISERouter](https://arxiv.org/abs/2607.23765), 26 July 2026 — workload-constrained contextual-bandit routing.
- [R2-Router](https://arxiv.org/abs/2602.02823), revised 29 May 2026 — joint selection of model and output-length budget.
- [Switchcraft](https://arxiv.org/abs/2605.07112), 8 May 2026 — lightweight routing for agentic tool calling.
- [The Scaffold Effect in Coding Agents](https://arxiv.org/abs/2607.22585), 8 June 2026 — evidence that harness-model pairs, rather than model names alone, determine cost and behavior.

## Evidence and epistemic control

- [LEDGERMIND](https://arxiv.org/abs/2607.28374), 30 July 2026 — provenance-constrained multimodal state and provenance non-amplification.
- [Evidence-Ledger Adjudication](https://arxiv.org/abs/2607.26512), 29 July 2026 — claim/evidence packets and explicit routing of unsupported or conflicting claims.
- [Trusted Uncertainty in Large Language Models](https://arxiv.org/abs/2509.01455), 1 September 2025 — heterogeneous evidence fusion and risk-controlled refusal.
- [Uncertainty-Aware Abstention with Provable Alignment Guarantees](https://arxiv.org/abs/2607.04430), 5 July 2026 — confidence-interval-based selective answering.

## Verification

- [The Prover Is the Judge](https://arxiv.org/abs/2607.14340), 15 July 2026 — verifier-driven coding, specification scope, and agent gaming of weak checks.
- [Coding Agents as Test-Suite Auditors](https://arxiv.org/abs/2608.01715), 3 August 2026 — adversarial tests and independent certification chains.
- [Are Coding Agents Generating Over-Mocked Tests?](https://arxiv.org/abs/2602.00409), 30 January 2026 — empirical evidence motivating independent test-adequacy controls.
- [Event-B Agent](https://arxiv.org/abs/2605.17475), 17 May 2026 — interleaving natural-language model synthesis with formal verification feedback.
- [Constraint Tax in Open-Weight LLMs](https://arxiv.org/abs/2606.25605), 24 June 2026 — tool-call suppression under simultaneous grammar-constrained structured output.

## Memory and multimodality

- [Memex(RL)](https://arxiv.org/abs/2603.04257), 4 March 2026 — indexed summaries with recoverable full-fidelity evidence.
- [Parallel Context Compaction](https://arxiv.org/abs/2605.23296), 22 May 2026 — predictable, parallel context compaction.
- [MM-ToolSandBox](https://arxiv.org/abs/2607.11818), 13 July 2026 — evaluation of visually grounded tool-calling agents and evidence that current systems remain brittle.

## Base-model candidate

- [Qwen3.6-35B-A3B official release](https://qwen.ai/blog?id=qwen3.6-35b-a3b), 15 April 2026 — open-weight, native multimodal MoE with approximately 3B active parameters and an agentic coding focus.
- [Qwen3.6-35B-A3B model repository](https://huggingface.co/Qwen/Qwen3.6-35B-A3B) — checkpoint, license, and serving information.

The model is a candidate, not a normative dependency. EPR-1 routes by measured cohort outcomes and permits replacement without changing the public virtual-model identity.

## Interoperability standards

- [W3C PROV](https://www.w3.org/TR/prov-primer/) — provenance entities, activities, agents, and derivations.
- [in-toto specification](https://github.com/in-toto/docs/blob/master/in-toto-spec.md) — authenticated software-supply-chain steps and materials/products.
- [OMG ReqIF](https://www.omg.org/reqif/) — requirements interchange.
- [OpenTelemetry GenAI agent semantic conventions](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md) — model, workflow, and tool spans.
