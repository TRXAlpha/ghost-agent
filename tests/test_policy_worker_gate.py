"""TO3: re-check policy when the worker claims a job.

Drives the real production handler `_execute_queued_outcome` (via the real
`OutcomeWorker.run_next`) with a stubbed orchestrator, mirroring
tests/test_proactive_feedback.py. Asserts: a job from a non-owner source is
paused (not started, no sandbox) when policy is off, with one durable
`policy-paused-<job_id>` notification; it resumes when policy is back on;
owner jobs (no source, or telegram) are unaffected.
"""
from __future__ import annotations

import json
from pathlib import Path

from ghost_agent.autonomy import OutcomeStore
from ghost_agent.cli import _execute_queued_outcome
from ghost_agent.kernel import GoalStore
from ghost_agent.policy import PolicyStore
from ghost_agent.proactive import ProactiveEngine
from ghost_agent.runtime import AttentionManager, ScheduledJob, Scheduler
from ghost_agent.worker import OutcomeWorker


class FakeOrchestrator:
    """Stub that records construction and writes a passing state.json."""

    built = False

    def __init__(self, *_a, **_k) -> None:
        FakeOrchestrator.built = True

    def run_task_with_roots(self, _task, _workspace, artifacts_root) -> None:
        Path(artifacts_root).mkdir(parents=True, exist_ok=True)
        (Path(artifacts_root) / "state.json").write_text(
            json.dumps({"last_result": "tests_passed"}), encoding="utf-8")


def _queue_proactive_job(tmp_path: Path) -> str:
    """Queue one proactive job through the real path; make it skip research."""
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *a, **k: json.dumps(
        {"title": "Rust practice experiment",
         "goal": "Research how to turn my Rust lessons into a small practice game.",
         "rationale": "Project context mentions learning Rust.", "kind": "learning",
         "confidence": 0.9, "requires_user_input": False}))
    assert engine.tick()["status"] == "queued"
    job = OutcomeStore(tmp_path).list()[-1]
    job.skip_research = True
    OutcomeStore(tmp_path).save(job)
    return job.job_id


def _run_worker(tmp_path: Path):
    return OutcomeWorker(tmp_path).run_next(
        lambda scheduled: _execute_queued_outcome(tmp_path, scheduled))


def _make_owner_job(tmp_path: Path, source: str | None) -> str:
    """Create an owner job with no source, or with source=telegram."""
    goal = GoalStore(tmp_path).create("Manual build task")
    store = OutcomeStore(tmp_path)
    job = store.create("Manual build task")
    if source:
        job.acceptance_contract["source"] = source
    job.goal_id, job.skip_research, job.coding_model = goal.goal_id, True, "local"
    store.save(job)
    Scheduler(tmp_path).enqueue(ScheduledJob(job.job_id, goal.goal_id))
    return job.job_id


def test_queued_proactive_job_is_paused_when_build_off(tmp_path, monkeypatch):
    job_id = _queue_proactive_job(tmp_path)
    PolicyStore(tmp_path).set("build", False)
    notified = []
    monkeypatch.setattr(AttentionManager, "notify",
                        lambda self, n: notified.append(n))

    result = _run_worker(tmp_path)

    assert result and result.status == "paused"
    job = OutcomeStore(tmp_path).load(job_id)
    assert job.status == "paused"
    assert any("paused" in e for e in job.errors)
    assert len(notified) == 1
    assert notified[0].notification_id == f"policy-paused-{job_id}"
    assert notified[0].priority == "normal"
    # sandbox never created
    assert not (OutcomeStore(tmp_path).path_for(job) / "run").exists()


def test_paused_proactive_job_resumes_when_build_back_on(tmp_path, monkeypatch):
    job_id = _queue_proactive_job(tmp_path)
    PolicyStore(tmp_path).set("build", False)
    monkeypatch.setattr(AttentionManager, "notify", lambda self, n: None)
    first = _run_worker(tmp_path)
    assert first and first.status == "paused"
    assert OutcomeStore(tmp_path).load(job_id).status == "paused"

    # build back on, then resume via the real resume path (paused -> queued),
    # mirroring tests/test_cli_outcomes.py and tests/test_scheduler_recovery.py.
    PolicyStore(tmp_path).set("build", True)
    FakeOrchestrator.built = False
    monkeypatch.setattr("ghost_agent.cli.Orchestrator", FakeOrchestrator)
    Scheduler(tmp_path).resume(job_id)
    result = OutcomeWorker(tmp_path).run_next(
        lambda scheduled: _execute_queued_outcome(tmp_path, scheduled))

    assert result and result.status == "completed"
    assert OutcomeStore(tmp_path).load(job_id).status == "completed"
    assert FakeOrchestrator.built


def test_owner_job_without_source_unaffected(tmp_path, monkeypatch):
    _make_owner_job(tmp_path, None)
    PolicyStore(tmp_path).set("build", False)
    monkeypatch.setattr("ghost_agent.cli.Orchestrator", FakeOrchestrator)

    result = _run_worker(tmp_path)

    assert result and result.status == "completed"


def test_owner_job_telegram_source_unaffected(tmp_path, monkeypatch):
    _make_owner_job(tmp_path, "telegram")
    PolicyStore(tmp_path).set("build", False)
    monkeypatch.setattr("ghost_agent.cli.Orchestrator", FakeOrchestrator)

    result = _run_worker(tmp_path)

    assert result and result.status == "completed"
