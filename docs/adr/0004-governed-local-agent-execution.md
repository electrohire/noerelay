# ADR-0004: Governed local-agent execution

**Status**: Proposed  
**Date**: 2026-09-03  
**Supersedes**: None  
**Superseded by**: None

## Context

NoeRelay currently routes all inference through OpenRouter (cloud). The integration mission requires supporting local model execution through governed agents. Local models must be accessed through registered, authenticated agents with immutable revision binding, capability limits, and independent verification — never through ad-hoc direct model calls.

## Decision

### Architecture

```
NoeRelay → AgentRevision → governed local runtime → vLLM/Ollama/SGLang
```

Local models are not directly addressable as route targets. They are accessed through registered agent revisions that enforce authentication, permissions, budgets, and evidence capture.

### Agent Registration

Each local agent is registered by immutable revision with:

| Field | Purpose |
|-------|---------|
| `endpoint` | Network address of the agent runtime |
| `trust_root` | Cryptographic trust anchor for agent authentication |
| `capabilities` | Declared capabilities (e.g., `["coding", "reasoning"]`) |
| `allowed_models` | Permitted model revisions this agent may use |
| `provider_family` | Provider family for verification independence checks |
| `maximum_data_class` | Maximum data classification this agent may handle |
| `permissions` | Filesystem, network, and tool permissions |
| `context_budget_tokens` | Maximum context window |
| `budget_ceiling_microusd` | Per-run budget ceiling |
| `runtime` | Runtime identifier (vLLM, Ollama, SGLang) |
| `delegation_limit` | Maximum delegation/fan-out depth |
| `cycle_limit` | Maximum repair/retry cycles |
| `verification_restrictions` | Required verifier families for this agent's output |

### Execution Guarantees

1. **Authenticated identity**: Every agent call is authenticated. Impersonation is rejected.
2. **Immutable revision binding**: Route decisions bind to a specific `AgentRevision`. Superseded or quarantined revisions cannot be dispatched.
3. **Capability enforcement**: The agent's declared capabilities are checked against task requirements before dispatch.
4. **Permission enforcement**: Filesystem, network, and tool permissions are enforced at the agent boundary.
5. **Delegation bounds**: Maximum fan-out and cycle depth are enforced. Recursive delegation without explicit authorization is blocked.
6. **Cancellation**: In-flight agent work can be cancelled. Partial results are recorded but not released.
7. **Idempotency**: Duplicate dispatch with the same idempotency key returns the cached result (if still valid) rather than re-executing.
8. **Evidence capture**: Raw stdout/stderr, exit codes, and artifact hashes are captured as immutable evidence. RTK-derived compact context is linked to raw evidence.
9. **Independent verification**: High-risk agent output requires verification by a different provider family or human authority.

### Tool Execution Boundary

At each tool execution, preserve:

```json
{
  "command": "cargo test --workspace --locked",
  "exit_code": 0,
  "raw_output": {"artifact_id": "...", "sha256": "..."},
  "compact_output": {
    "artifact_id": "...",
    "sha256": "...",
    "derived_from": "raw artifact id"
  },
  "filter": {
    "name": "rtk",
    "version": "...",
    "configuration_hash": "..."
  },
  "metrics": {
    "raw_estimated_tokens": 18420,
    "compact_estimated_tokens": 1910
  }
}
```

Raw stdout/stderr and exit status remain authoritative and immutable. RTK output is derived context. RTK failure falls back safely. Missing raw evidence cannot satisfy high-risk observed-evidence gates.

### RTK Naming Resolution

The repository's `rtk/` crate (`noerelay-rtk`) is an internal concept unrelated to the external Rust Token Killer. Before integrating external RTK, the internal crate will be renamed to `noerelay-compact` to avoid ambiguity.

## Consequences

### Positive
- Local models gain the same governance as cloud models
- Evidence capture is uniform across local and remote execution
- Agent revision binding prevents unauthorized model swaps
- Independent verification applies consistently

### Negative
- Additional latency for agent authentication and permission checks
- Requires agent runtime to implement the NoeRelay agent contract
- RTK naming migration requires coordinated changes

### Neutral
- Existing OpenRouter routing is unchanged
- Agent registry extends existing `AgentRevision` type