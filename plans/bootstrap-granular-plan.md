# Bootstrap Self-Improvement + CI Fixes — Granular Implementation Plan

**Date**: 2026-09-03  
**Status**: Ready for implementation

---

## Sub-Task Breakdown

Each sub-task is independently implementable and verifiable. Execute in order.

---

### Phase 1: CI Fixes (6 sub-tasks)

#### 1.1 Fix `cargo fmt` formatting
- **File**: All `.rs` files (already ran `cargo fmt --all`)
- **Verify**: `cargo fmt --all -- --check` exits 0
- **Risk**: None — purely cosmetic

#### 1.2 Fix unused import `ModelRevision` in `route_target.rs`
- **File**: `crates/noerelay-core/src/route_target.rs`
- **Change**: Remove `ModelRevision` from `use crate::registry::{AgentRevision, ModelRevision};`
- **Verify**: `cargo clippy -p noerelay-core` no longer warns about unused import

#### 1.3 Fix dead code `inner: Router` in `StagedRouter`
- **File**: `crates/noerelay-core/src/routing.rs`
- **Change**: Remove `inner: Router` field from `StagedRouter` struct. The struct doesn't need it — `select_with_ranking()` is self-contained.
- **Verify**: `cargo clippy -p noerelay-core` no longer warns about dead code

#### 1.4 Fix `cloned_ref_to_slice_refs` clippy warning
- **File**: `crates/noerelay-core/src/routing.rs` (staged_tests)
- **Change**: Replace `.cloned()` on a reference with direct reference usage
- **Verify**: `cargo clippy -p noerelay-core` no longer warns

#### 1.5 Verify full CI pipeline passes locally
- **Commands**:
  - `cargo fmt --all -- --check`
  - `cargo clippy --workspace --all-targets -- -D warnings`
  - `cargo test --workspace --locked`
- **Verify**: All three exit 0

#### 1.6 Create CodeQL workflow
- **File**: `.github/workflows/codeql.yml`
- **Content**: Standard CodeQL analysis for Python, Go, Rust
- **Trigger**: push to main, PR to main, weekly schedule
- **Verify**: File exists with correct syntax

---

### Phase 2: Bootstrap Orchestrator (4 sub-tasks)

#### 2.1 Create `scripts/bootstrap_improve.py`
- **Purpose**: Standalone orchestrator that runs self-improvement cycles with heartbeat
- **Key features**:
  - Reads plan from `evidence/bootstrap/plan.json`
  - Writes heartbeat to `evidence/bootstrap/heartbeat.json` every 5 seconds
  - On failure, writes rollback plan to `evidence/bootstrap/rollback.json`
  - Manages gateway lifecycle (start/stop Docker with correct env vars)
  - Each cycle has 10-minute timeout
  - Uses existing `SelfImprovementOrchestrator` from `noerelay_self_improve.py`
- **Verify**: Script runs without import errors

#### 2.2 Create `scripts/agent_bootstrap.py`
- **Purpose**: Agent-facing wrapper with three commands
- **Commands**:
  - `--start`: Write plan file, spawn `bootstrap_improve.py`, return immediately
  - `--status`: Read heartbeat file, print current state
  - `--recover`: Read rollback plan, apply rollback actions, allow agent to fix and resume
- **Verify**: `python scripts/agent_bootstrap.py --status` works (even if no run in progress)

#### 2.3 Create `evidence/bootstrap/` directory
- **Files**: `.gitkeep`, initial `plan.json` template
- **Verify**: Directory exists with template files

#### 2.4 Test bootstrap orchestrator locally
- **Steps**:
  1. Start gateway in live mode
  2. Run `agent_bootstrap.py --start --mode live --max-cycles 2 --auto-apply`
  3. Monitor `agent_bootstrap.py --status`
  4. Verify heartbeat updates
  5. Verify results written to `evidence/bootstrap/results/`
- **Verify**: At least 1 cycle completes successfully

---

### Phase 3: Run & Iterate (3 sub-tasks)

#### 3.1 Run full self-improvement loop
- **Command**: `python scripts/agent_bootstrap.py --start --mode live --max-cycles 5 --auto-apply`
- **Monitor**: `python scripts/agent_bootstrap.py --status` every 30 seconds
- **Verify**: All 5 cycles complete or convergence reached

#### 3.2 Handle failures (if any)
- **If failure**: Read `evidence/bootstrap/rollback.json`
- **Apply rollback**: Restart services, revert config changes
- **Fix root cause**: Diagnose and fix the issue
- **Resume**: `python scripts/agent_bootstrap.py --recover`
- **Verify**: Loop continues from last successful cycle

#### 3.3 Iterate until satisfied
- **Review**: Check `evidence/bootstrap/results/` for final scores
- **If score < 0.90**: Investigate bottlenecks, adjust thresholds, re-run
- **If score >= 0.90**: Accept and finalize
- **Verify**: Final composite score meets threshold

---

### Phase 4: Finalize (1 sub-task)

#### 4.1 Commit & push all changes
- **Files**: All CI fixes, bootstrap scripts, CodeQL workflow, results
- **Verify**: `git push` succeeds, CI is green on GitHub

---

## Execution Order

```
1.1 → 1.2 → 1.3 → 1.4 → 1.5 → 1.6  (CI fixes)
    ↓
2.1 → 2.2 → 2.3 → 2.4  (Bootstrap orchestrator)
    ↓
3.1 → 3.2 → 3.3  (Run & iterate)
    ↓
4.1  (Commit & push)
```

Each phase is gated on the previous phase passing verification.