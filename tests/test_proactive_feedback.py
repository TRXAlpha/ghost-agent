"""OJ09-FeedbackLoop: production worker closes the proactive opportunity loop.

These tests drive the REAL production handler (`_execute_queued_outcome`) through
`OutcomeWorker.run_next` and assert that the linked Opportunity is closed durably
and idempotently, plus restart reconciliation. The orchestrator is stubbed so the
handler reaches a deterministic result WITHOUT a live model, but the worker and
handler code under test are the real production path.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ghost_agent.autonomy import OutcomeStore
from ghost_agent.cli import _execute_queued_outcome
from ghost_agent.proactive import (
    OpportunityStore,
    ProactiveEngine,
    ProactiveError,
    close_opportunity_feedback,
    feedback_outcome_for_job,
)
from ghost_agent.runtime import ScheduledJob, Scheduler
from ghost_agent.worker import OutcomeWorker


def _proposal(goal="Research how to turn my Rust lessons into a small practice game."):
    return json.dumps({"title": "Rust practice experiment", "goal": goal,
                       "rationale": "The project context mentions learning Rust.",
                       "kind": "learning", "confidence": 0.9, "requires_user_input": False})


def _queue_one_opportunity(tmp_path: Path) -> ProactiveEngine:
    """Queue exactly one proactive sandbox opportunity via the real tick() path."""
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *a, **k: _proposal())
    result = engine.tick()
    assert result["status"] == "queued"
    return engine


class FakeOrchestrator:
    """Writes a deterministic last_result; used to script the handler outcome."""

    def __init__(self, result: str = "tests_passed", *, raises: bool = False) -> None:
        self.result = result
        self.raises = raises

    def __call__(self, *_args, **_kwargs):
        return self

    def run_task_with_roots(self, _task, _workspace, artifacts_root) -> None:
        if self.raises:
            raise RuntimeError("sandbox failed")
        Path(artifacts_root).mkdir(parents=True, exist_ok=True)
        (Path(artifacts_root) / "state.json").write_text(
            json.dumps({"last_result": self.result}), encoding="utf-8")


def _prepare_proactive_job(tmp_path: Path, engine: ProactiveEngine) -> str:
    """Set the queued opportunity's job to skip_research and return its job_id."""
    op = engine.store.list(100)[-1]
    assert op.status == "queued" and op.job_id
    job = OutcomeStore(tmp_path).load(op.job_id)
    job.skip_research = True
    OutcomeStore(tmp_path).save(job)
    return op.job_id


def _run_worker(tmp_path: Path):
    return OutcomeWorker(tmp_path).run_next(
        lambda scheduled: _execute_queued_outcome(tmp_path, scheduled))


# ---- Regression: production worker closes the loop (the original defect) ----

def test_worker_completes_proactive_opportunity_when_tests_pass(tmp_path, monkeypatch):
    engine = _queue_one_opportunity(tmp_path)
    job_id = _prepare_proactive_job(tmp_path, engine)
    monkeypatch.setattr("ghost_agent.cli.Orchestrator", FakeOrchestrator("tests_passed"))

    result = _run_worker(tmp_path)
    assert result and result.status == "completed"

    op = engine.store.list(100)[-1]
    assert op.status == "completed"          # <-- the loop is now closed by the worker
    assert op.result == "tests_passed"
    assert "job=" in op.evidence and job_id in op.evidence  # real, job-derived evidence


def test_worker_rejects_proactive_opportunity_when_tests_fail(tmp_path, monkeypatch):
    engine = _queue_one_opportunity(tmp_path)
    _prepare_proactive_job(tmp_path, engine)
    monkeypatch.setattr("ghost_agent.cli.Orchestrator", FakeOrchestrator("tests_failed"))

    result = _run_worker(tmp_path)
    assert result and result.status == "failed"

    op = engine.store.list(100)[-1]
    assert op.status == "rejected"
    assert op.result == "tests_failed"


def test_worker_rejects_proactive_opportunity_on_worker_error(tmp_path, monkeypatch):
    engine = _queue_one_opportunity(tmp_path)
    _prepare_proactive_job(tmp_path, engine)
    monkeypatch.setattr("ghost_agent.cli.Orchestrator", FakeOrchestrator(raises=True))

    result = _run_worker(tmp_path)
    assert result and result.status == "failed"

    op = engine.store.list(100)[-1]
    assert op.status == "rejected"
    assert op.result == "error"


# ---- Non-proactive jobs are untouched ----

def test_non_proactive_job_without_opportunity_link_is_safe(tmp_path, monkeypatch):
    from ghost_agent.kernel import GoalStore
    goal = GoalStore(tmp_path).create("Manual build task")
    store = OutcomeStore(tmp_path)
    job = store.create("Manual build task")
    job.goal_id, job.skip_research, job.coding_model = goal.goal_id, True, "local"
    store.save(job)
    Scheduler(tmp_path).enqueue(ScheduledJob(job.job_id, goal.goal_id))

    monkeypatch.setattr("ghost_agent.cli.Orchestrator", FakeOrchestrator("tests_passed"))
    result = _run_worker(tmp_path)
    assert result and result.status == "completed"
    # No opportunity existed; nothing must crash and the manual job completed.
    assert OpportunityStore(tmp_path).list(100) == []


# ---- Idempotency ----

def test_close_opportunity_feedback_is_idempotent(tmp_path):
    engine = _queue_one_opportunity(tmp_path)
    op = engine.store.list(100)[-1]
    job = OutcomeStore(tmp_path).load(op.job_id)
    job.skip_research = True
    job.status = "completed"
    job.result = "tests_passed"
    OutcomeStore(tmp_path).save(job)

    first = close_opportunity_feedback(tmp_path, job)
    assert first is not None and first.status == "completed"

    # Calling again on the now-finalized opportunity is a safe no-op (no corruption).
    again = close_opportunity_feedback(tmp_path, job)
    assert again is None
    assert engine.store.find_any(op.opportunity_id).status == "completed"
    assert len(OpportunityStore(tmp_path).list(100)) == 1


def test_close_opportunity_feedback_noop_for_non_terminal_job(tmp_path):
    engine = _queue_one_opportunity(tmp_path)
    op = engine.store.list(100)[-1]
    job = OutcomeStore(tmp_path).load(op.job_id)
    job.status = "researching"  # still being worked -> no terminal verdict
    job.result = ""
    OutcomeStore(tmp_path).save(job)

    assert feedback_outcome_for_job(job) is None
    assert close_opportunity_feedback(tmp_path, job) is None
    assert engine.store.find_any(op.opportunity_id).status == "queued"


def test_outcome_mapping_completed_is_tests_passed(tmp_path):
    op = _queue_one_opportunity(tmp_path).store.list(100)[-1]
    job = OutcomeStore(tmp_path).load(op.job_id)
    job.status, job.result = "completed", "tests_passed"
    assert feedback_outcome_for_job(job) == "tests_passed"

    job.result = "tests_failed"
    assert feedback_outcome_for_job(job) == "tests_failed"

    job.status, job.result = "failed", ""
    assert feedback_outcome_for_job(job) == "error"


# ---- Restart reconciliation ----

def test_reconcile_closes_opportunity_when_job_reached_terminal_state(tmp_path):
    engine = _queue_one_opportunity(tmp_path)
    op = engine.store.list(100)[-1]
    job = OutcomeStore(tmp_path).load(op.job_id)
    job.skip_research = True
    job.status = "completed"
    job.result = "tests_passed"
    OutcomeStore(tmp_path).save(job)
    # Simulate the worker finishing while the proactive loop was NOT watching:
    # the opportunity remained queued.
    assert engine.store.find_any(op.opportunity_id).status == "queued"

    closed = engine.reconcile()
    assert closed == 1
    assert engine.store.find_any(op.opportunity_id).status == "completed"


def test_reconcile_closes_opportunity_from_scheduler_terminal_state(tmp_path):
    engine = _queue_one_opportunity(tmp_path)
    op = engine.store.list(100)[-1]
    job = OutcomeStore(tmp_path).load(op.job_id)
    # Outcome record still says created/researching, but the scheduler marks the
    # job terminal (cancelled) -> reconcile closes with error outcome.
    Scheduler(tmp_path).mark(op.job_id, "cancelled", expected={"queued"})
    assert engine.store.find_any(op.opportunity_id).status == "queued"

    closed = engine.reconcile()
    assert closed == 1
    assert engine.store.find_any(op.opportunity_id).status == "rejected"
    assert engine.store.find_any(op.opportunity_id).result == "error"


def test_reconcile_leaves_non_terminal_jobs_untouched(tmp_path):
    engine = _queue_one_opportunity(tmp_path)
    op = engine.store.list(100)[-1]
    assert engine.store.find_any(op.opportunity_id).status == "queued"

    closed = engine.reconcile()
    assert closed == 0
    assert engine.store.find_any(op.opportunity_id).status == "queued"


def test_reconcile_is_idempotent(tmp_path):
    engine = _queue_one_opportunity(tmp_path)
    op = engine.store.list(100)[-1]
    job = OutcomeStore(tmp_path).load(op.job_id)
    job.status, job.result = "completed", "tests_passed"
    OutcomeStore(tmp_path).save(job)

    assert engine.reconcile() == 1
    assert engine.reconcile() == 0
    assert engine.store.find_any(op.opportunity_id).status == "completed"


# ---- Negative control: record_feedback validation unchanged ----

def test_record_feedback_still_rejects_invalid_states(tmp_path):
    engine = _queue_one_opportunity(tmp_path)
    op = engine.store.list(100)[-1]
    op.status = "proposed"  # proposed is not queued/running
    engine.store.save(op)
    with pytest.raises(ProactiveError, match="Cannot record feedback"):
        engine.record_feedback(op.opportunity_id, "tests_passed")
