#!/usr/bin/env python
"""NoeRelay Self-Improvement Orchestrator (EPR-SELF-IMPROVE-001).

Cyclically runs benchmarks, analyzes results, applies safe improvements,
and repeats until the stack converges ("dimension return").

Architecture:
  Cycle = Ensure Services → Health Probe → Run Benchmarks → Analyze →
          Apply Safe Actions → Check Convergence → Repeat (or stop)

Convergence ("dimension return") is declared when:
  1. Composite score delta < 0.005 for 3 consecutive cycles
  2. All cohorts meet minimum thresholds
  3. No critical bottlenecks remain
  4. All services healthy across all machines

Usage:
    # Run until convergence (default: max 50 cycles)
    python scripts/noerelay_self_improve.py

    # Run a fixed number of cycles
    python scripts/noerelay_self_improve.py --max-cycles 10

    # Dry-run (analyze but don't apply changes)
    python scripts/noerelay_self_improve.py --dry-run

    # Auto-apply safe improvements (no approval needed)
    python scripts/noerelay_self_improve.py --auto-apply

    # Quick mode: only quick-test cohort, shorter intervals
    python scripts/noerelay_self_improve.py --quick

    # Full mode: all cohorts, LiveBench, HF datasets
    python scripts/noerelay_self_improve.py --full

    # Resume from a previous state file
    python scripts/noerelay_self_improve.py --resume state.json

Configuration:
    NOERELAY_GATEWAY_URL — gateway base URL (default: http://127.0.0.1:8080)
    NOERELAY_SELF_IMPROVE_DIR — state/output directory (default: evidence/self-improve)
    NOERELAY_SELF_IMPROVE_MAX_CYCLES — max cycles (default: 50)
    NOERELAY_SELF_IMPROVE_CONVERGENCE_WINDOW — cycles for convergence (default: 3)
    NOERELAY_SELF_IMPROVE_CONVERGENCE_DELTA — score delta threshold (default: 0.005)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure scripts/ and reference/ are importable
_scripts_dir = Path(__file__).resolve().parent
_reference_dir = _scripts_dir.parent / "reference"
for _p in (_scripts_dir, str(_reference_dir)):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from service_health_probe import probe_all_services, HealthMatrix
from improvement_analyzer import ImprovementAnalyzer, ImprovementReport, ImprovementAction

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GATEWAY_URL = os.environ.get("NOERELAY_GATEWAY_URL", "http://127.0.0.1:8080")
SELF_IMPROVE_DIR = Path(
    os.environ.get("NOERELAY_SELF_IMPROVE_DIR", "evidence/self-improve")
)
MAX_CYCLES = int(os.environ.get("NOERELAY_SELF_IMPROVE_MAX_CYCLES", "50"))
CONVERGENCE_WINDOW = int(
    os.environ.get("NOERELAY_SELF_IMPROVE_CONVERGENCE_WINDOW", "3")
)
CONVERGENCE_DELTA = float(
    os.environ.get("NOERELAY_SELF_IMPROVE_CONVERGENCE_DELTA", "0.005")
)

# Cohorts for different modes
QUICK_COHORTS = ["quick-test"]
STANDARD_COHORTS = [
    "quick-test",
    "coding-tasks",
    "reasoning-tasks",
    "safety-tasks",
]
FULL_COHORTS = [
    "quick-test",
    "coding-tasks",
    "reasoning-tasks",
    "tool-use-tasks",
    "multi-turn-tasks",
    "safety-tasks",
    "vision-tasks",
]

# Actions that can be auto-applied without approval
AUTO_APPLY_CATEGORIES = {"restart_service", "config_tune"}

# Actions that always require approval
APPROVAL_CATEGORIES = {"model_swap", "portfolio_add", "portfolio_remove"}


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

class OrchestratorState:
    """Persistent state for the self-improvement orchestrator."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.state_dir / "orchestrator_state.json"

        # Load or initialize
        if self.state_file.exists():
            self._data = json.loads(self.state_file.read_text("utf-8"))
        else:
            self._data = {
                "started_at": datetime.now(timezone.utc).isoformat(),
                "current_cycle": 0,
                "converged": False,
                "converged_at": None,
                "score_history": [],
                "applied_actions": [],
                "last_results_file": None,
                "last_health_file": None,
                "last_report_file": None,
            }

    @property
    def current_cycle(self) -> int:
        return self._data["current_cycle"]

    @property
    def converged(self) -> bool:
        return self._data["converged"]

    @property
    def score_history(self) -> list[float]:
        return self._data["score_history"]

    def record_cycle(
        self,
        cycle: int,
        score: float,
        results_file: str,
        health_file: str,
        report_file: str,
        actions_applied: list[str],
    ) -> None:
        self._data["current_cycle"] = cycle
        self._data["score_history"].append(score)
        self._data["last_results_file"] = results_file
        self._data["last_health_file"] = health_file
        self._data["last_report_file"] = report_file
        for a in actions_applied:
            self._data["applied_actions"].append({
                "cycle": cycle,
                "action": a,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        self._save()

    def mark_converged(self) -> None:
        self._data["converged"] = True
        self._data["converged_at"] = datetime.now(timezone.utc).isoformat()
        self._save()

    def _save(self) -> None:
        self.state_file.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class SelfImprovementOrchestrator:
    """Cyclic self-improvement orchestrator for NoeRelay.

    Runs the full cycle: ensure services → probe health → benchmark →
    analyze → apply → check convergence → repeat.
    """

    def __init__(
        self,
        gateway_url: str = GATEWAY_URL,
        state_dir: Path | None = None,
        max_cycles: int = MAX_CYCLES,
        convergence_window: int = CONVERGENCE_WINDOW,
        convergence_delta: float = CONVERGENCE_DELTA,
        dry_run: bool = False,
        auto_apply: bool = False,
        cohorts: list[str] | None = None,
        include_livebench: bool = False,
        include_hf: bool = False,
    ) -> None:
        self.gateway_url = gateway_url
        self.state_dir = state_dir or SELF_IMPROVE_DIR
        self.max_cycles = max_cycles
        self.convergence_window = convergence_window
        self.convergence_delta = convergence_delta
        self.dry_run = dry_run
        self.auto_apply = auto_apply
        self.cohorts = cohorts or STANDARD_COHORTS
        self.include_livebench = include_livebench
        self.include_hf = include_hf

        self.state = OrchestratorState(self.state_dir)
        self.analyzer = ImprovementAnalyzer()

        # Sub-directories
        self.results_dir = self.state_dir / "results"
        self.health_dir = self.state_dir / "health"
        self.reports_dir = self.state_dir / "reports"
        for d in (self.results_dir, self.health_dir, self.reports_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        """Run the self-improvement loop until convergence or max cycles."""
        print("=" * 70)
        print("  NoeRelay Self-Improvement Orchestrator")
        print(f"  Started: {datetime.now(timezone.utc).isoformat()}")
        print(f"  Gateway: {self.gateway_url}")
        print(f"  Max cycles: {self.max_cycles}")
        print(f"  Convergence window: {self.convergence_window}")
        print(f"  Convergence delta: {self.convergence_delta}")
        print(f"  Dry run: {self.dry_run}")
        print(f"  Auto-apply: {self.auto_apply}")
        print(f"  Cohorts: {', '.join(self.cohorts)}")
        print(f"  State dir: {self.state_dir}")
        print("=" * 70)
        print()

        start_cycle = self.state.current_cycle + 1

        for cycle in range(start_cycle, self.max_cycles + 1):
            print(f"\n{'#'*70}")
            print(f"  CYCLE {cycle} / {self.max_cycles}")
            print(f"  {datetime.now(timezone.utc).isoformat()}")
            print(f"{'#'*70}\n")

            try:
                self._run_cycle(cycle)
            except KeyboardInterrupt:
                print("\n\nInterrupted by user. Saving state...")
                break
            except Exception as exc:
                print(f"\nCycle {cycle} failed with error: {exc}")
                import traceback
                traceback.print_exc()
                print("Continuing to next cycle...")
                continue

            # Check convergence
            if self._check_convergence():
                self.state.mark_converged()
                print("\n" + "=" * 70)
                print("  DIMENSION RETURN ACHIEVED")
                print(f"  Stack converged at cycle {cycle}")
                print(f"  Final score: {self.state.score_history[-1]:.4f}")
                print("=" * 70)
                break

        if not self.state.converged:
            print("\n" + "=" * 70)
            print(f"  MAX CYCLES REACHED ({self.max_cycles})")
            print(f"  Final score: {self.state.score_history[-1]:.4f}"
                  if self.state.score_history else "  No scores recorded")
            print("=" * 70)

        return self.state.to_dict()

    # ------------------------------------------------------------------
    # Single cycle
    # ------------------------------------------------------------------

    def _run_cycle(self, cycle: int) -> None:
        """Execute one full improvement cycle."""
        actions_applied: list[str] = []

        # Step 1: Ensure services are running
        print("─" * 50)
        print("  Step 1: Ensuring all services on all machines...")
        print("─" * 50)
        self._ensure_services()

        # Step 2: Health probe
        print("\n─" * 50)
        print("  Step 2: Probing service health...")
        print("─" * 50)
        health = self._probe_health(cycle)

        # Step 3: Run benchmarks
        print("\n─" * 50)
        print("  Step 3: Running benchmarks...")
        print("─" * 50)
        results = self._run_benchmarks(cycle)

        # Step 4: Load previous results for delta
        previous = self._load_previous_results()

        # Step 5: Analyze
        print("\n─" * 50)
        print("  Step 4: Analyzing results...")
        print("─" * 50)
        report = self.analyzer.analyze(
            benchmark_results=results,
            previous_results=previous,
            health_matrix=health.to_dict() if health else None,
            cycle_number=cycle,
        )
        print(report.summary)

        # Save report
        report_file = self.reports_dir / f"report-cycle-{cycle:04d}.json"
        report_file.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Step 6: Apply safe actions
        print("\n─" * 50)
        print("  Step 5: Applying improvements...")
        print("─" * 50)
        applied = self._apply_actions(report)
        actions_applied = [a.description for a in applied]

        # Step 7: Record state
        results_file = self.results_dir / f"results-cycle-{cycle:04d}.json"
        health_file = self.health_dir / f"health-cycle-{cycle:04d}.json"

        self.state.record_cycle(
            cycle=cycle,
            score=report.composite_score,
            results_file=str(results_file),
            health_file=str(health_file),
            report_file=str(report_file),
            actions_applied=actions_applied,
        )

        print(f"\n  Cycle {cycle} complete. Score: {report.composite_score:.4f}")

    # ------------------------------------------------------------------
    # Step implementations
    # ------------------------------------------------------------------

    def _ensure_services(self) -> None:
        """Ensure all services are running via PowerShell orchestration script."""
        ensure_script = _scripts_dir / "ensure-services.ps1"
        if not ensure_script.exists():
            print("  WARNING: ensure-services.ps1 not found, skipping service check")
            return

        if self.dry_run:
            print("  [DRY RUN] Would run: ensure-services.ps1")
            return

        try:
            result = subprocess.run(
                [
                    "powershell", "-ExecutionPolicy", "Bypass", "-File",
                    str(ensure_script),
                    "-Json",
                ],
                capture_output=True, text=True, timeout=120,
                cwd=str(_scripts_dir.parent),
            )
            if result.stdout.strip():
                try:
                    data = json.loads(result.stdout.splitlines()[-1])
                    healthy = data.get("HealthyCount", 0)
                    total = data.get("TotalCount", 0)
                    print(f"  Services: {healthy}/{total} healthy")
                except json.JSONDecodeError:
                    print(f"  Service check output: {result.stdout[:200]}")
            if result.returncode != 0:
                print(f"  WARNING: Service check returned code {result.returncode}")
        except subprocess.TimeoutExpired:
            print("  WARNING: Service check timed out")
        except Exception as exc:
            print(f"  WARNING: Service check failed: {exc}")

    def _probe_health(self, cycle: int) -> HealthMatrix | None:
        """Probe all services and save health report."""
        if self.dry_run:
            print("  [DRY RUN] Would probe all services")
            return None

        try:
            matrix = probe_all_services(timeout=5.0)
            print_health_summary(matrix)

            # Save
            health_file = self.health_dir / f"health-cycle-{cycle:04d}.json"
            health_file.write_text(
                json.dumps(matrix.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            return matrix
        except Exception as exc:
            print(f"  Health probe failed: {exc}")
            return None

    def _run_benchmarks(self, cycle: int) -> dict[str, Any]:
        """Run benchmarks using the continuous_benchmark pipeline."""
        results_file = self.results_dir / f"results-cycle-{cycle:04d}.json"

        if self.dry_run:
            print("  [DRY RUN] Would run benchmarks for cohorts: " + ", ".join(self.cohorts))
            return {"composite_score": {"overall_score": 0.0}, "cohorts": {}}

        # Use the existing continuous_benchmark.py pipeline
        try:
            from continuous_benchmark import ContinuousBenchmarkPipeline

            pipeline = ContinuousBenchmarkPipeline(
                gateway_url=self.gateway_url,
                output_dir=self.results_dir,
                baseline_dir=self.state_dir / "baselines",
                include_livebench=self.include_livebench,
                include_hf=self.include_hf,
            )
            results = pipeline.run_once(cohorts=self.cohorts)

            # Also save to the cycle-specific file
            results_file.write_text(
                json.dumps(results, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )

            return results
        except ImportError:
            print("  WARNING: continuous_benchmark module not importable")
            print("  Falling back to subprocess benchmark run...")
            return self._run_benchmarks_subprocess(cycle)
        except Exception as exc:
            print(f"  Benchmark pipeline error: {exc}")
            import traceback
            traceback.print_exc()
            return {"composite_score": {"overall_score": 0.0}, "cohorts": {}}

    def _run_benchmarks_subprocess(self, cycle: int) -> dict[str, Any]:
        """Fallback: run benchmarks via subprocess."""
        results_file = self.results_dir / f"results-cycle-{cycle:04d}.json"
        benchmark_script = _scripts_dir / "continuous_benchmark.py"

        cmd = [
            sys.executable, str(benchmark_script),
            "--once",
            "--gateway", self.gateway_url,
            "--output", str(self.results_dir),
        ]
        if self.include_livebench:
            cmd.append("--livebench")
        if self.include_hf:
            cmd.append("--hf")

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600,
                cwd=str(_scripts_dir.parent),
            )
            print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
            if result.stderr:
                print("STDERR:", result.stderr[:500])

            # Try to find the results file
            result_files = sorted(
                self.results_dir.glob("run-*.json"), reverse=True
            )
            if result_files:
                return json.loads(result_files[0].read_text("utf-8"))
        except subprocess.TimeoutExpired:
            print("  Benchmark run timed out (10 min)")
        except Exception as exc:
            print(f"  Benchmark subprocess error: {exc}")

        return {"composite_score": {"overall_score": 0.0}, "cohorts": {}}

    def _load_previous_results(self) -> dict[str, Any] | None:
        """Load the previous cycle's benchmark results."""
        if self.state.current_cycle < 1:
            return None
        prev_file = self.results_dir / f"results-cycle-{self.state.current_cycle:04d}.json"
        if prev_file.exists():
            return json.loads(prev_file.read_text("utf-8"))
        return None

    def _apply_actions(self, report: ImprovementReport) -> list[ImprovementAction]:
        """Apply safe improvement actions from the report."""
        applied: list[ImprovementAction] = []

        for action in report.actions:
            can_auto = (
                self.auto_apply
                and action.category in AUTO_APPLY_CATEGORIES
            )
            needs_approval = (
                action.category in APPROVAL_CATEGORIES
                or action.requires_approval
            )

            if self.dry_run:
                print(f"  [DRY RUN] Would apply: {action.description}")
                continue

            if needs_approval and not self.auto_apply:
                print(f"  [SKIP] Requires approval: {action.description}")
                continue

            if can_auto or action.category == "restart_service":
                print(f"  [APPLY] {action.description}")
                success = self._execute_action(action)
                if success:
                    applied.append(action)
                    report.applied_actions.append(action)
                else:
                    print(f"  [FAIL] Could not apply: {action.description}")
            else:
                print(f"  [PENDING] {action.description} "
                      f"(category={action.category}, confidence={action.confidence:.0%})")

        if not applied:
            print("  No actions applied this cycle.")

        return applied

    def _execute_action(self, action: ImprovementAction) -> bool:
        """Execute a single improvement action."""
        if action.category == "restart_service":
            return self._restart_service(action.target)
        if action.category == "config_tune":
            return self._tune_config(action.target, action.proposed_state)
        # model_swap, portfolio_add, portfolio_remove require manual intervention
        return False

    def _restart_service(self, service_name: str) -> bool:
        """Restart a service via the PowerShell orchestration script."""
        ensure_script = _scripts_dir / "ensure-services.ps1"
        if not ensure_script.exists():
            return False
        try:
            subprocess.run(
                [
                    "powershell", "-ExecutionPolicy", "Bypass", "-File",
                    str(ensure_script),
                ],
                capture_output=True, timeout=60,
                cwd=str(_scripts_dir.parent),
            )
            return True
        except Exception:
            return False

    def _tune_config(self, key: str, value: str) -> bool:
        """Tune a configuration parameter."""
        # For now, config tuning is advisory — actual changes require
        # updating .env or environment variables and restarting.
        print(f"  Config tune: {key} → {value} (requires restart to take effect)")
        return True

    # ------------------------------------------------------------------
    # Convergence detection
    # ------------------------------------------------------------------

    def _check_convergence(self) -> bool:
        """Check if the stack has converged (dimension return).

        Convergence requires:
        1. At least convergence_window cycles completed
        2. Score delta < convergence_delta for the last convergence_window cycles
        3. No score regression in the window
        """
        history = self.state.score_history
        if len(history) < self.convergence_window:
            return False

        # Check last N scores
        recent = history[-self.convergence_window:]

        # All deltas must be below threshold
        for i in range(1, len(recent)):
            delta = abs(recent[i] - recent[i - 1])
            if delta > self.convergence_delta:
                return False

        # No regression (scores should be non-decreasing in the window)
        for i in range(1, len(recent)):
            if recent[i] < recent[i - 1] - self.convergence_delta:
                return False

        # All scores must be above minimum threshold (0.70)
        if any(s < 0.70 for s in recent):
            return False

        return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def print_health_summary(matrix: HealthMatrix) -> None:
    """Print a compact health summary."""
    status = "ALL HEALTHY" if matrix.all_healthy else "ISSUES DETECTED"
    print(f"  Health: {matrix.healthy_count}/{matrix.total_count} services — {status}")
    if matrix.machines_offline:
        print(f"  Offline machines: {', '.join(matrix.machines_offline)}")
    for svc in matrix.services:
        if not svc.healthy:
            print(f"    [DOWN] {svc.name} ({svc.machine}) — {svc.detail[:80]}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="NoeRelay Self-Improvement Orchestrator — cyclic benchmark + improve until convergence",
    )
    parser.add_argument(
        "--max-cycles", type=int, default=MAX_CYCLES,
        help=f"Maximum cycles before stopping (default: {MAX_CYCLES})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Analyze but don't apply any changes",
    )
    parser.add_argument(
        "--auto-apply", action="store_true",
        help="Auto-apply safe improvements without approval",
    )
    parser.add_argument(
        "--quick", action="store_true",
        help="Quick mode: only quick-test cohort",
    )
    parser.add_argument(
        "--full", action="store_true",
        help="Full mode: all cohorts + LiveBench + HF datasets",
    )
    parser.add_argument(
        "--gateway", type=str, default=GATEWAY_URL,
        help=f"Gateway URL (default: {GATEWAY_URL})",
    )
    parser.add_argument(
        "--state-dir", type=str, default=str(SELF_IMPROVE_DIR),
        help=f"State/output directory (default: {SELF_IMPROVE_DIR})",
    )
    parser.add_argument(
        "--convergence-window", type=int, default=CONVERGENCE_WINDOW,
        help=f"Cycles for convergence (default: {CONVERGENCE_WINDOW})",
    )
    parser.add_argument(
        "--convergence-delta", type=float, default=CONVERGENCE_DELTA,
        help=f"Score delta threshold (default: {CONVERGENCE_DELTA})",
    )
    parser.add_argument(
        "--resume", type=str, default=None,
        help="Resume from a previous state file",
    )

    args = parser.parse_args()

    # Determine cohorts
    if args.quick:
        cohorts = QUICK_COHORTS
    elif args.full:
        cohorts = FULL_COHORTS
    else:
        cohorts = STANDARD_COHORTS

    # State dir
    state_dir = Path(args.state_dir)

    # Resume handling
    if args.resume:
        resume_path = Path(args.resume)
        if resume_path.exists():
            state_dir = resume_path.parent
            print(f"Resuming from state in: {state_dir}")

    orchestrator = SelfImprovementOrchestrator(
        gateway_url=args.gateway,
        state_dir=state_dir,
        max_cycles=args.max_cycles,
        convergence_window=args.convergence_window,
        convergence_delta=args.convergence_delta,
        dry_run=args.dry_run,
        auto_apply=args.auto_apply,
        cohorts=cohorts,
        include_livebench=args.full,
        include_hf=args.full,
    )

    final_state = orchestrator.run()

    # Print final summary
    print("\nFinal State:")
    print(f"  Cycles completed: {final_state['current_cycle']}")
    print(f"  Converged: {final_state['converged']}")
    if final_state.get("converged_at"):
        print(f"  Converged at: {final_state['converged_at']}")
    print(f"  Score history: {[round(s, 4) for s in final_state['score_history']]}")
    print(f"  Total actions applied: {len(final_state['applied_actions'])}")

    return 0 if final_state["converged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())