"""Ghost bench: fixed benchmark of 8 non-toy goals with hidden acceptance tests.

Security note: hidden tests execute model-generated code on the host as the
current user, exactly like Ghost's normal verification. This is not containment.

Emergency stop limitation: emergency stop is checked between goals. Inside a
job, the orchestrator polls the run_root's stop file, not the owner's.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .autonomy import OutcomeStore
from .benchmark_goals import BenchGoal, BENCH_GOALS
from .proactive import queue_sandbox_outcome
from .runtime import EmergencyStop, ScheduledJob
from .utils.storage import atomic_json
from .worker import OutcomeWorker, WorkerResult

# Per-goal iteration budget; passed as CLI --iterations, which becomes job.iterations
_DEFAULT_ITERATIONS = 10

# Valid id set
_VALID_GOAL_IDS = frozenset(g.goal_id for g in BENCH_GOALS)


def _validate_args(model: str, goal_ids: Optional[list[str]], iterations: int) -> None:
    """Raise ValueError for invalid arguments."""
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model is required and may not be blank")
    if iterations != int(iterations) or not (1 <= iterations <= 50):
        raise ValueError("iterations must be an integer between 1 and 50")
    if goal_ids is not None:
        unknown = [gid for gid in goal_ids if gid not in _VALID_GOAL_IDS]
        if unknown:
            raise ValueError(f"Unknown goal id(s): {', '.join(sorted(unknown))}. Valid: {', '.join(sorted(_VALID_GOAL_IDS))}")


def _run_id() -> str:
    return time.strftime("%Y%m%d_%H%M%S") + "_" + str(time.process_time()).replace(".", "")[-6:]


def _goal_for(goal_id: str) -> BenchGoal:
    for g in BENCH_GOALS:
        if g.goal_id == goal_id:
            return g
    raise ValueError(f"No goal named {goal_id}")


def _run_hidden_test(sandbox_path: Path, goal: BenchGoal) -> tuple[bool, str]:
    """Copy sandbox, write hidden test, run pytest, return (accepted, detail)."""
    sandbox_path = Path(sandbox_path)
    if not sandbox_path.is_dir():
        return False, "sandbox path does not exist"

    tmpdir = tempfile.mkdtemp(prefix="ghost_bench_")
    try:
        copy_dest = Path(tmpdir) / "copy"
        shutil.copytree(
            sandbox_path,
            copy_dest,
            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
            dirs_exist_ok=False,
        )
        test_path = copy_dest / "test_hidden_acceptance.py"
        test_path.write_text(goal.hidden_test, encoding="utf-8")

        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
             "test_hidden_acceptance.py"],
            cwd=str(copy_dest),
            capture_output=True,
            text=True,
            timeout=120,
        )
        detail = (result.stdout + "\n" + result.stderr)[-20_000:]
        return result.returncode == 0, detail
    except subprocess.TimeoutExpired:
        return False, "hidden test timeout"
    except Exception as exc:
        return False, f"hidden test error: {exc}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def run_benchmark(
    project_root: Path,
    model: str,
    executor_factory: Callable[[Path], Callable[[ScheduledJob], WorkerResult]],
    *,
    goal_ids: Optional[list[str]] = None,
    iterations: int = _DEFAULT_ITERATIONS,
) -> dict:
    """Run the benchmark suite.

    Each goal goes through: queue_ -> OutcomeWorker. -> injected executor.
    Results (ghost tests + hidden acceptance) are written to report.json.
    """
    _validate_args(model, goal_ids, iterations)
    project_root = Path(project_root).resolve()
    run_id = _run_id()
    run_root = project_root / ".ghost" / "bench" / run_id
    run_root.mkdir(parents=True, exist_ok=True)

    # Owner root for emergency-stop checks (distinct from run_root)
    owner_root = project_root

    goals_to_run = [g for g in BENCH_GOALS if goal_ids is None or g.goal_id in goal_ids]
    started_at = time.time()
    goal_results: list[dict[str, Any]] = []

    for goal in goals_to_run:
        # Check emergency stop on the owner's project root between goals
        if EmergencyStop(owner_root).active():
            # Mark this and all remaining goals as skipped
            for rem in goals_to_run[goals_to_run.index(goal):]:
                goal_results.append({
                    "goal_id": rem.goal_id,
                    "job_id": "",
                    "status": "skipped_emergency_stop",
                    "result": "",
                    "tests_passed": False,
                    "accepted": False,
                    "acceptance_detail": "",
                    "duration_seconds": 0.0,
                })
            break

        job = queue_sandbox_outcome(
            run_root,
            goal.goal,
            model,
            source="benchmark",
            priority=50,
            workspace_mode="empty",
        )

        # Reload job and patch it
        store = OutcomeStore(run_root)
        _job = store.load(job.job_id)
        assert _job is not None, f"job {job.job_id} not found"
        job = _job
        job.skip_research = True
        job.acceptance_contract["required_files"] = [goal.module_file]
        job.iterations = iterations
        store.save(job)

        job_id = job.job_id
        worker = OutcomeWorker(run_root)
        executor = executor_factory(run_root)
        goal_start = time.time()
        try:
            result = worker.run_next(executor, job_id=job_id)
        except Exception as exc:
            result = WorkerResult(job_id, "failed", str(exc))
        goal_duration = time.time() - goal_start

        # Reload job for final state
        job = store.load(job_id)
        sandbox_path = Path(job.sandbox_path) if job.sandbox_path else None

        tests_passed = job.result == "tests_passed"
        accepted = False
        acceptance_detail = ""
        sandbox_path = Path(job.sandbox_path) if job.sandbox_path else None
        if sandbox_path and sandbox_path.exists():
            accepted, acceptance_detail = _run_hidden_test(sandbox_path, goal)
        elif not sandbox_path or not sandbox_path.exists():
            acceptance_detail = "sandbox path does not exist"

        goal_results.append({
            "goal_id": goal.goal_id,
            "job_id": job_id,
            "status": job.status,
            "result": job.result,
            "tests_passed": tests_passed,
            "accepted": accepted,
            "acceptance_detail": acceptance_detail,
            "duration_seconds": round(goal_duration, 3),
        })

    finished_at = time.time()
    attempted = [r for r in goal_results if r["status"] != "skipped_emergency_stop"]
    # AC: total counts only goals that were attempted (not skipped).
    # If emergency stop fired before any run, total is 0.
    total = len(attempted)
    tp_count = sum(1 for r in goal_results if r["tests_passed"])
    acc_count = sum(1 for r in goal_results if r["accepted"])

    report = {
        "run_id": run_id,
        "model": model,
        "iterations": iterations,
        "started_at": started_at,
        "finished_at": finished_at,
        "goals": goal_results,
        "summary": {
            "total": total,
            "tests_passed": tp_count,
            "accepted": acc_count,
            "ghost_pass_rate": round(tp_count / total, 3) if total else 0.0,
            "accepted_rate": round(acc_count / total, 3) if total else 0.0,
        },
    }

    report_path = run_root / "report.json"
    atomic_json(report_path, report)
    return report


def latest_report(project_root: Path) -> Optional[dict]:
    """Return the most recent report.json under .ghost/bench/, or None."""
    bench_root = Path(project_root).resolve() / ".ghost" / "bench"
    if not bench_root.is_dir():
        return None
    dirs = sorted(d for d in bench_root.iterdir() if d.is_dir() and (d / "report.json").is_file())
    if not dirs:
        return None
    return json.loads((dirs[-1] / "report.json").read_text(encoding="utf-8"))


def format_report(report: dict) -> str:
    """Return a plain-text benchmark table."""
    lines = []
    lines.append(f"{'goal_id':<14} {'result':<20} {'tests_passed':<14} {'accepted':<10} {'seconds'}")
    for g in report["goals"]:
        lines.append(
            f"{g['goal_id']:<14} {str(g.get('result', '')):<20} "
            f"{str(g.get('tests_passed', '')):<14} {str(g.get('accepted', '')):<10} "
            f"{g.get('duration_seconds', 0.0)}"
        )
    sm = report["summary"]
    lines.append(
        f"ghost_pass_rate={sm['ghost_pass_rate']:.3f}  accepted_rate={sm['accepted_rate']:.3f}  "
        f"({sm['total']} goals, model={report['model']})"
    )
    return "\n".join(lines)