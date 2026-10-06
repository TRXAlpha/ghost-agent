"""Tests for ghost bench (T11)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ghost_agent.benchmark import format_report, latest_report, run_benchmark
from ghost_agent.benchmark_goals import BENCH_GOALS, BenchGoal
from ghost_agent.proactive import queue_sandbox_outcome


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def fake_executor_factory(run_root: Path, *, write_correct_intervals: bool = False,
                          no_sandbox: bool = False, wrong_intervals: bool = False,
                          job_contract_check: bool = False):
    """Return a fake executor that simulates OutcomeWorker.run_next."""
    def fake_executor(scheduled):
        from ghost_agent.autonomy import OutcomeStore
        from ghost_agent.utils.storage import atomic_json
        store = OutcomeStore(run_root)
        job = store.load(scheduled.job_id)
        assert job is not None, f"job {scheduled.job_id} not found"

        if job_contract_check:
            assert job.skip_research is True, "skip_research should be True"
            assert job.acceptance_contract.get("workspace_mode") == "empty", \
                "workspace_mode should be empty"
            assert job.acceptance_contract.get("required_files") == ["intervals.py"], \
                f"required_files should be ['intervals.py'], got {job.acceptance_contract.get('required_files')}"
            assert job.iterations == 10, f"iterations should be 10, got {job.iterations}"

        if no_sandbox:
            job.sandbox_path = ""
            job.status = "completed"
            job.result = "tests_passed"
        else:
            outcomes_dir = run_root / ".ghost" / "outcomes"
            if not outcomes_dir.exists():
                outcomes_dir.mkdir(parents=True, exist_ok=True)
            sandbox = outcomes_dir / scheduled.job_id / "run" / "sandbox"
            sandbox.mkdir(parents=True, exist_ok=True)

            if write_correct_intervals:
                sandbox.mkdir(parents=True, exist_ok=True)
                (sandbox / "intervals.py").write_text(
                    "def merge(intervals):\n"
                    "    if not intervals:\n"
                    "        return []\n"
                    "    for s, e in intervals:\n"
                    "        if s > e:\n"
                    "            raise ValueError('start > end')\n"
                    "    sorted_ints = sorted(intervals, key=lambda x: x[0])\n"
                    "    result = [sorted_ints[0]]\n"
                    "    for start, end in sorted_ints[1:]:\n"
                    "        if start <= result[-1][1]:\n"
                    "            result[-1] = (result[-1][0], max(result[-1][1], end))\n"
                    "        else:\n"
                    "            result.append((start, end))\n"
                    "    return result\n",
                    encoding="utf-8",
                )
            elif wrong_intervals:
                sandbox.mkdir(parents=True, exist_ok=True)
                (sandbox / "intervals.py").write_text(
                    "def merge(intervals):\n"
                    "    return list(intervals)\n",
                    encoding="utf-8",
                )
            else:
                sandbox.mkdir(parents=True, exist_ok=True)
                (sandbox / "intervals.py").write_text(
                    "def merge(intervals):\n"
                    "    return intervals\n",
                    encoding="utf-8",
                )
            # write a test file too so tests_passed can be set
            (sandbox / "test_intervals.py").write_text(
                "from intervals import merge\ndef test_trivial():\n    assert merge([]) == []\n",
                encoding="utf-8",
            )
            job.sandbox_path = str(sandbox)
            job.status = "completed"
            job.result = "tests_passed"

        job.updated_at = job.updated_at
        store.save(job)
        from ghost_agent.worker import WorkerResult
        return WorkerResult(job.job_id, "completed")

    return fake_executor


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

def test_goals_well_formed():
    """8 goals, unique ids, every module_file ends with .py, every hidden
    test imports from its module, every goal text mentions the module file."""
    assert len(BENCH_GOALS) == 8, f"Expected 8 goals, got {len(BENCH_GOALS)}"
    ids = [g.goal_id for g in BENCH_GOALS]
    assert len(ids) == len(set(ids)), "Goal ids must be unique"
    for g in BENCH_GOALS:
        assert g.module_file.endswith(".py"), f"{g.goal_id}: module_file should end with .py"
        assert g.module_file in g.goal, f"{g.goal_id}: goal should mention {g.module_file}"
        # hidden test must import from the module
        mod_name = g.module_file[:-3]
        assert f"from {mod_name} import" in g.hidden_test or f"import {mod_name}" in g.hidden_test, \
            f"{g.goal_id}: hidden test should import from {mod_name}"


def test_run_isolated_from_owner_queue(tmp_path: Path, monkeypatch):
    """The run uses run_root's .ghost/bench/..., not the owner's queue."""
    owner_root = tmp_path / "owner"
    owner_root.mkdir()
    run_root = tmp_path / "run"
    run_root.mkdir()

    # Owner scheduler must not exist (or be empty)
    owner_scheduler = owner_root / ".ghost" / "scheduler.json"
    assert not owner_scheduler.exists(), "Owner scheduler should not exist before run"

    def fake_factory(rr):
        return fake_executor_factory(rr)

    report = run_benchmark(
        run_root,
        "tiny",
        fake_factory,
        goal_ids=["intervals"],
        iterations=10,
    )
    assert report["summary"]["total"] == 1
    # Owner root's scheduler was not touched
    assert not owner_scheduler.exists() or owner_scheduler.read_text() == "[]", \
        "Owner scheduler should not have been modified"
    # Run root has bench dir
    bench_dirs = list((run_root / ".ghost" / "bench").iterdir())
    assert len(bench_dirs) == 1, f"Expected 1 bench dir, got {bench_dirs}"


def test_accepted_when_hidden_test_passes(tmp_path: Path):
    """Correct intervals.py → hidden test passes → accepted=True."""
    def factory(rr):
        return fake_executor_factory(rr, write_correct_intervals=True)

    report = run_benchmark(tmp_path, "tiny", factory, goal_ids=["intervals"], iterations=10)
    assert report["summary"]["total"] == 1
    gr = report["goals"][0]
    assert gr["tests_passed"] is True
    assert gr["accepted"] is True, f"Hidden test should pass: {gr.get('acceptance_detail', '')}"
    assert report["summary"]["accepted_rate"] == 1.0


def test_not_accepted_when_hidden_test_fails(tmp_path: Path):
    """Wrong intervals.py + tests_passed=True → accepted=False (independent acceptance)."""
    def factory(rr):
        return fake_executor_factory(rr, wrong_intervals=True)

    report = run_benchmark(tmp_path, "tiny", factory, goal_ids=["intervals"], iterations=10)
    assert report["summary"]["total"] == 1
    gr = report["goals"][0]
    assert gr["tests_passed"] is True, "Fake sets tests_passed=True"
    assert gr["accepted"] is False, "Hidden test should fail for wrong implementation"
    assert report["summary"]["accepted_rate"] == 0.0


def test_missing_sandbox_not_accepted(tmp_path: Path):
    """No sandbox_path → accepted=False, run does not crash."""
    def factory(rr):
        return fake_executor_factory(rr, no_sandbox=True)

    report = run_benchmark(tmp_path, "tiny", factory, goal_ids=["intervals"], iterations=10)
    assert report["summary"]["total"] == 1
    gr = report["goals"][0]
    assert gr["accepted"] is False, "No sandbox → not accepted"
    assert "sandbox path does not exist" in gr.get("acceptance_detail", "")


def test_emergency_stop_skips_remaining(tmp_path: Path):
    """EmergencyStop engaged on owner root skips remaining goals."""
    owner_root = tmp_path / "owner"
    owner_root.mkdir()
    from ghost_agent.runtime import EmergencyStop
    EmergencyStop(owner_root).engage("test")

    call_count = [0]

    def counting_factory(rr):
        def executor(scheduled):
            call_count[0] += 1
            from ghost_agent.worker import WorkerResult
            return WorkerResult(scheduled.job_id, "completed")
        return executor

    report = run_benchmark(owner_root, "tiny", counting_factory,
                           goal_ids=["intervals", "roman"], iterations=10)
    assert all(g["status"] == "skipped_emergency_stop" for g in report["goals"]), \
        "All goals should be skipped"
    assert call_count[0] == 0, "Executor should never be called"
    assert report["summary"]["total"] == 0, "No goals attempted"


def test_invalid_arguments():
    """Unknown goal id, iterations=0, iterations=51, model=' ' each raise ValueError."""
    from ghost_agent.benchmark import _validate_args
    with pytest.raises(ValueError, match="Unknown goal id"):
        _validate_args("tiny", ["nonexistent_goal"], 10)
    with pytest.raises(ValueError, match="iterations must be"):
        _validate_args("tiny", None, 0)
    with pytest.raises(ValueError, match="iterations must be"):
        _validate_args("tiny", None, 51)
    with pytest.raises(ValueError, match="model is required"):
        _validate_args("  ", None, 10)


def test_job_contract_set(tmp_path: Path):
    """Loaded job has skip_research=True, workspace_mode='empty',
    required_files=['intervals.py'], and the requested iterations."""
    contract_check_results = []

    def factory(rr):
        def executor(scheduled):
            from ghost_agent.autonomy import OutcomeStore
            from ghost_agent.worker import WorkerResult
            store = OutcomeStore(rr)
            job = store.load(scheduled.job_id)
            assert job is not None
            results = {
                "skip_research": job.skip_research,
                "workspace_mode": job.acceptance_contract.get("workspace_mode"),
                "required_files": job.acceptance_contract.get("required_files"),
                "iterations": job.iterations,
            }
            contract_check_results.append(results)
            # write a minimal sandbox
            sandbox = rr / ".ghost" / "outcomes" / scheduled.job_id / "run" / "sandbox"
            sandbox.mkdir(parents=True, exist_ok=True)
            (sandbox / "intervals.py").write_text("def merge(x): return x\n", encoding="utf-8")
            (sandbox / "test_intervals.py").write_text(
                "from intervals import merge\ndef test(): pass\n", encoding="utf-8")
            job.sandbox_path = str(sandbox)
            job.status = "completed"
            job.result = "tests_passed"
            store.save(job)
            return WorkerResult(job.job_id, "completed")
        return executor

    report = run_benchmark(tmp_path, "tiny", factory,
                           goal_ids=["intervals"], iterations=10)
    assert len(contract_check_results) == 1
    cr = contract_check_results[0]
    assert cr["skip_research"] is True
    assert cr["workspace_mode"] == "empty"
    assert cr["required_files"] == ["intervals.py"]
    assert cr["iterations"] == 10


def test_latest_report_and_format(tmp_path: Path):
    """latest_report returns the most recent run's report; format_report includes accepted_rate."""
    def factory(rr):
        return fake_executor_factory(rr, write_correct_intervals=True)

    report = run_benchmark(tmp_path, "tiny", factory, goal_ids=["intervals"], iterations=10)
    stored = latest_report(tmp_path)
    assert stored is not None
    assert stored["run_id"] == report["run_id"]
    formatted = format_report(stored)
    assert "accepted_rate=" in formatted
    assert "ghost_pass_rate=" in formatted


def test_queue_accepts_benchmark_source(tmp_path: Path):
    """queue_sandbox_outcome accepts source='benchmark'."""
    job = queue_sandbox_outcome(tmp_path, "x task", "tiny", source="benchmark")
    assert job.job_id
    assert job.acceptance_contract["source"] == "benchmark"