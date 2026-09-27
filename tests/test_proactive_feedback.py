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
    Opportunity,
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


# ---- F1: reconcile is reachable from PRODUCTION wiring (watch loop), not a direct call ----

def test_reconcile_runs_from_production_watch_loop(tmp_path, monkeypatch):
    """F1 regression: restart reconciliation is driven by watch() itself.

    The opportunity's job reached a terminal state while the loop was 'away';
    starting the real watch() loop (production wiring) must close it via
    reconcile() BEFORE the first tick, without the test calling reconcile()
    directly.
    """
    import ghost_agent.proactive as proactive_mod
    engine = _queue_one_opportunity(tmp_path)
    op = engine.store.list(100)[-1]
    job = OutcomeStore(tmp_path).load(op.job_id)
    job.status, job.result = "completed", "tests_passed"
    OutcomeStore(tmp_path).save(job)
    assert engine.store.find_any(op.opportunity_id).status == "queued"

    # Short-circuit the infinite watch loop: tick needs no model, and the first
    # time.sleep raises SystemExit to break the loop after one iteration.
    calls = {"tick": 0}
    def fake_tick(**kwargs):
        calls["tick"] += 1
        return {"status": "deferred"}
    monkeypatch.setattr(engine, "tick", fake_tick)
    def stop_loop(_seconds):
        raise SystemExit
    monkeypatch.setattr(proactive_mod.time, "sleep", stop_loop)

    with pytest.raises(SystemExit):
        engine.watch(interval=15)

    # reconcile() ran inside watch()'s production wiring and closed the loop.
    assert engine.store.find_any(op.opportunity_id).status == "completed"
    assert calls["tick"] == 1  # reconciliation happened before the only tick


def test_reconcile_error_does_not_break_watch_loop(tmp_path, monkeypatch):
    """F1 regression: a reconcile failure is logged and swallowed, never halting the loop."""
    import ghost_agent.proactive as proactive_mod
    from ghost_agent.runtime import EventBus
    engine = _queue_one_opportunity(tmp_path)

    def boom():
        raise RuntimeError("reconcile exploded")
    monkeypatch.setattr(engine, "reconcile", boom)

    calls = {"tick": 0}
    def fake_tick(**kwargs):
        calls["tick"] += 1
        return {"status": "deferred"}
    monkeypatch.setattr(engine, "tick", fake_tick)
    monkeypatch.setattr(proactive_mod.time, "sleep", lambda _s: (_ for _ in ()).throw(SystemExit))

    with pytest.raises(SystemExit):
        engine.watch(interval=15)

    assert calls["tick"] == 1  # loop survived the reconcile failure
    # The failure was recorded durably as an event, not silently dropped.
    assert any(e.kind == "proactive.reconcile_error" for e in EventBus(tmp_path).recent(50))


def test_reconcile_safe_swallows_errors(tmp_path, monkeypatch):
    from ghost_agent.runtime import EventBus
    engine = _queue_one_opportunity(tmp_path)
    monkeypatch.setattr(engine, "reconcile", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    engine._reconcile_safe()  # must not raise
    assert any(e.kind == "proactive.reconcile_error" for e in EventBus(tmp_path).recent(50))


# ---- P3: reconcile scans the full ledger, not just list(100) ----

def test_reconcile_scans_opportunities_beyond_tail_window(tmp_path):
    """P3: an old stuck queued opportunity beyond the list(100) window is reconciled."""
    engine = _queue_one_opportunity(tmp_path)
    first = engine.store.list_all()[0]
    # Build >100 filler opportunities so the stuck one falls outside list(100).
    for i in range(105):
        op = Opportunity(
            opportunity_id=f"filler_{i}", title=f"fill {i}", goal=f"fill goal {i}",
            rationale="x", kind="learning", confidence=0.5, requires_user_input=False,
            status="dismissed",
        )
        engine.store.save(op)
    assert first not in engine.store.list(100) and first in engine.store.list_all()

    job = OutcomeStore(tmp_path).load(first.job_id)
    job.status, job.result = "completed", "tests_passed"
    OutcomeStore(tmp_path).save(job)

    closed = engine.reconcile()
    assert closed == 1
    assert engine.store.find_any(first.opportunity_id).status == "completed"


# ---- Reviewer A P3: active job with non-empty result stays OPEN under reconcile ----

def test_reconcile_leaves_open_when_result_nonempty_but_status_active(tmp_path):
    """P3: a non-empty result on a still queued/running job must NOT be treated as
    terminal feedback, so reconcile leaves the opportunity open (scheduler branch
    decides, and it is not terminal here)."""
    engine = _queue_one_opportunity(tmp_path)
    op = engine.store.list(100)[-1]
    job = OutcomeStore(tmp_path).load(op.job_id)
    job.status = "queued"       # scheduler still active
    job.result = "tests_passed" # stale/partial non-empty result, not a verdict
    OutcomeStore(tmp_path).save(job)

    assert feedback_outcome_for_job(job) is None  # NOT a premature 'error'
    assert engine.reconcile() == 0
    assert engine.store.find_any(op.opportunity_id).status == "queued"  # still open


# ---- F2: terminal early-returns close the linked opportunity via the REAL handler ----

def _handler_for(tmp_path, job) -> None:
    """Drive the real production handler with a scheduled job (no worker claim gate)."""
    from ghost_agent.runtime import ScheduledJob
    return _execute_queued_outcome(tmp_path, ScheduledJob(job.job_id, job.goal_id))


def test_emergency_stop_before_research_closes_opportunity(tmp_path):
    """F2 (b): pre-research emergency stop returns 'cancelled' and durably closes
    the linked opportunity through the real _execute_queued_outcome handler."""
    from ghost_agent.runtime import EmergencyStop
    engine = _queue_one_opportunity(tmp_path)
    _prepare_proactive_job(tmp_path, engine)
    op = engine.store.list(100)[-1]
    EmergencyStop(tmp_path).engage("review hold")
    try:
        result = _handler_for(tmp_path, OutcomeStore(tmp_path).load(op.job_id))
        assert result and result.status == "cancelled"
        final = engine.store.find_any(op.opportunity_id)
        assert final.status == "rejected"           # durably closed
        assert final.result == "error"
        job = OutcomeStore(tmp_path).load(op.job_id)
        assert job.status == "failed" and job.result == "cancelled"  # real terminal state
        assert "cancelled" in final.evidence        # real job-derived evidence
    finally:
        EmergencyStop(tmp_path).clear()


def test_emergency_stop_after_research_closes_opportunity_via_worker(tmp_path, monkeypatch):
    """F2 (d): an emergency stop that engages DURING research (i.e. after the
    worker has claimed the job) is reached through OutcomeWorker.run_next and
    durably closes the linked opportunity."""
    from ghost_agent.runtime import EmergencyStop
    engine = _queue_one_opportunity(tmp_path)
    op = engine.store.list(100)[-1]
    job = OutcomeStore(tmp_path).load(op.job_id)
    job.skip_research = False  # exercise the research step
    OutcomeStore(tmp_path).save(job)
    EmergencyStop(tmp_path).clear()

    def research_engages_stop(*_a, **_k):
        EmergencyStop(tmp_path).engage("engaged during research")
        return "research report"
    monkeypatch.setattr("ghost_agent.cli._research_directly", research_engages_stop)
    try:
        result = _run_worker(tmp_path)
        assert result and result.status == "cancelled"
        final = engine.store.find_any(op.opportunity_id)
        assert final.status == "rejected"
        assert final.result == "error"
        stored = OutcomeStore(tmp_path).load(op.job_id)
        assert stored.status == "failed" and stored.result == "cancelled"
    finally:
        EmergencyStop(tmp_path).clear()


def test_route_mismatch_closes_opportunity_via_worker(tmp_path, monkeypatch):
    """F2 (c): an inference-route mismatch is reached through OutcomeWorker.run_next
    and durably closes the linked opportunity; no provider is contacted."""
    monkeypatch.setenv("GHOST_INFERENCE_BACKEND", "legacy")  # deterministic queued route
    engine = _queue_one_opportunity(tmp_path)
    _prepare_proactive_job(tmp_path, engine)
    op = engine.store.list(100)[-1]
    # The queued job recorded inference_route == {"backend":"legacy"}; the runtime
    # now resolves to a different backend -> the handler must close the loop.
    monkeypatch.setattr("ghost_agent.models.chat.route_identity",
                        lambda: {"backend": "openjarvis", "engine": "x", "endpoint": "y", "allow_remote": False})

    result = _run_worker(tmp_path)
    assert result and result.status == "failed"
    final = engine.store.find_any(op.opportunity_id)
    assert final.status == "rejected"
    assert final.result == "error"
    stored = OutcomeStore(tmp_path).load(op.job_id)
    assert stored.status == "failed" and stored.result == "route_mismatch"
    assert "route_mismatch" in final.evidence
