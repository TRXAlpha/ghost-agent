from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ghost_agent.proactive import (
    OpportunityStore,
    ProactiveEngine,
    ProactiveError,
    idle_seconds,
    parse_proposal,
)
from ghost_agent.autonomy import OutcomeStore
from ghost_agent.runtime import Scheduler


def proposal(goal="Research how to turn my Rust lessons into a small practice game.", **overrides):
    value = {"title": "Rust practice experiment", "goal": goal,
             "rationale": "The project context mentions learning Rust.", "kind": "learning",
             "confidence": 0.9, "requires_user_input": False}
    value.update(overrides)
    return json.dumps(value)


def test_parse_and_rejects_model_authority_fields():
    parsed = parse_proposal(proposal())
    assert parsed["kind"] == "learning"
    with pytest.raises(ProactiveError):
        parse_proposal(proposal(grant_permission=True))
    with pytest.raises(ProactiveError):
        parse_proposal(proposal(confidence=2))
    with pytest.raises(ProactiveError):
        parse_proposal(proposal(kind="shell"))


def test_tick_queues_one_durable_sandbox_outcome(tmp_path: Path):
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *args, **kwargs: proposal())
    result = engine.tick()
    assert result["status"] == "queued"
    assert len(OutcomeStore(tmp_path).list()) == 1
    assert Scheduler(tmp_path).list()[0].status == "queued"
    job = OutcomeStore(tmp_path).list()[0]
    assert job.acceptance_contract["proactive"] is True
    assert job.acceptance_contract["promotion_requires_approval"] is True
    assert (tmp_path / ".ghost/events.jsonl").read_text()


def test_tick_is_deduplicated_and_second_tick_does_not_add_work(tmp_path: Path):
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *args, **kwargs: proposal())
    assert engine.tick()["status"] == "queued"
    assert engine.tick()["status"] == "duplicate"
    assert len(OutcomeStore(tmp_path).list()) == 1


def test_missing_input_and_unsafe_goal_are_not_queued(tmp_path: Path):
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *args, **kwargs: proposal(
        goal="Diagnose whether my symptoms mean I am sick.", kind="research"))
    result = engine.tick()
    assert result["status"] == "needs_input"
    assert not Scheduler(tmp_path).list()

    engine = ProactiveEngine(tmp_path / "other", "tiny", chat_fn=lambda *args, **kwargs: proposal(
        goal="Research whether a Huawei Health Kit connector is feasible.", kind="integration",
        requires_user_input=True))
    result = engine.tick()
    assert result["status"] == "needs_input"
    assert not Scheduler(tmp_path / "other").list()


def test_no_queue_option_only_records_proposal(tmp_path: Path):
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *args, **kwargs: proposal())
    result = engine.tick(auto_queue=False)
    assert result["status"] == "proposed"
    assert not Scheduler(tmp_path).list()


def test_idle_gate_defers_when_state_is_unknown_or_owner_active(tmp_path: Path):
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *args, **kwargs: proposal(), idle_fn=lambda: None)
    assert engine.tick(require_idle=True)["reason"] == "idle_state_unavailable"
    engine = ProactiveEngine(tmp_path / "active", "tiny", chat_fn=lambda *args, **kwargs: proposal(), idle_fn=lambda: 4)
    assert engine.tick(require_idle=True, idle_after=10)["reason"] == "owner_active"


def test_emergency_stop_blocks_before_model(tmp_path: Path):
    from ghost_agent.runtime import EmergencyStop
    EmergencyStop(tmp_path).engage("test")
    called = []
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *args, **kwargs: called.append(1))
    assert engine.tick()["status"] == "stopped"
    assert not called


def test_dismiss_is_persistent(tmp_path: Path):
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *args, **kwargs: proposal())
    result = engine.tick(auto_queue=False)
    opportunity_id = result["opportunity"]["opportunity_id"]
    assert engine.dismiss(opportunity_id).status == "dismissed"
    assert OpportunityStore(tmp_path).list()[0].status == "dismissed"


def test_explicit_context_file_reaches_planner_and_is_scoped(tmp_path: Path):
    notes = tmp_path / "desksentinel.md"
    notes.write_text("DeskSentinel is an ESP32 pan tilt prototype with two servos.", encoding="utf-8")
    seen = []

    def planner(model, messages, **kwargs):
        seen.append(json.loads(messages[-1]["content"]))
        return proposal(goal="Research a safe DeskSentinel simulator and write a sandbox prototype.", kind="integration")

    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=planner, context_paths=["desksentinel.md"])
    result = engine.tick(auto_queue=False)
    assert result["status"] == "proposed"
    assert seen[0]["provided_context"][0]["excerpt"].startswith("DeskSentinel")
    with pytest.raises(ProactiveError):
        ProactiveEngine(tmp_path, "tiny", chat_fn=planner, context_paths=["../outside.md"]).context()


def test_watch_runs_existing_worker_callback_after_queue(tmp_path: Path, monkeypatch):
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *args, **kwargs: proposal(), idle_fn=lambda: 1000)
    results = []
    def stop_after_sleep(_interval):
        raise KeyboardInterrupt
    monkeypatch.setattr("ghost_agent.proactive.time.sleep", stop_after_sleep)
    with pytest.raises(KeyboardInterrupt):
        engine.watch(interval=15, idle_after=10,
                     run_worker=lambda: (results.append("worker") or
                                         SimpleNamespace(status="completed", job_id="job", detail="ok")))
    assert results == ["worker"]


# -- OJ09: rejected-idea memory, feedback/stop conditions, bounded backlog -----

def test_dismissed_idea_is_suppressed_and_not_reproposed(tmp_path: Path):
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *a, **k: proposal())
    result = engine.tick(auto_queue=False)
    oid = result["opportunity"]["opportunity_id"]
    assert engine.dismiss(oid).status == "dismissed"
    # identical goal must not reappear as a new proposal
    again = engine.tick(auto_queue=False)
    assert again["status"] == "suppressed"
    assert again["opportunity"]["status"] == "dismissed"


def test_completed_idea_is_suppressed(tmp_path: Path):
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *a, **k: proposal())
    result = engine.tick(auto_queue=False)
    oid = result["opportunity"]["opportunity_id"]
    # simulate a user decision: mark completed via direct feedback helper
    from ghost_agent.proactive import Opportunity
    op = engine.store.find_any(oid)
    op.status = "queued"
    engine.store.save(op)
    engine.record_feedback(oid, "tests_passed")
    assert engine.store.find_any(oid).status == "completed"
    again = engine.tick(auto_queue=False)
    assert again["status"] == "suppressed"


def test_rejected_idea_is_suppressed(tmp_path: Path):
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *a, **k: proposal())
    result = engine.tick(auto_queue=False)
    oid = result["opportunity"]["opportunity_id"]
    op = engine.store.find_any(oid)
    op.status = "queued"
    engine.store.save(op)
    engine.record_feedback(oid, "tests_failed", evidence="flaky due to timing")
    assert engine.store.find_any(oid).status == "rejected"
    again = engine.tick(auto_queue=False)
    assert again["status"] == "suppressed"


def test_needs_input_idea_is_not_suppressed(tmp_path: Path):
    # requires_user_input is a legitimate defer, not a rejection — it may re-propose
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *a, **k: proposal(
        goal="Explain why the system is slow (report only).", requires_user_input=True))
    first = engine.tick(auto_queue=False)
    assert first["status"] == "needs_input"
    # a later tick can propose it again (status is no longer suppressed)
    op = engine.store.find_any(first["opportunity"]["opportunity_id"])
    assert op.status == "needs_input"
    assert engine.store.find_suppressed(op.opportunity_id) is None


def test_backlog_is_bounded(tmp_path: Path):
    # queue three distinct high-confidence proposals, then a fourth different one;
    # the backlog of non-queued proposals must stay <= max_proposed.
    engine = ProactiveEngine(tmp_path, "tiny")
    goals = [f"Research topic {i} and build a sandbox prototype." for i in range(4)]
    def planner(model, messages, **kwargs):
        # infer topic from the last queue maybe; simplest: return distinct goals
        idx = planner._idx
        planner._idx += 1
        return proposal(goal=goals[idx % len(goals)])
    planner._idx = 0
    engine.chat = planner
    statuses = [engine.tick(auto_queue=False)["status"] for _ in range(4)]
    assert all(s == "proposed" for s in statuses)
    # after capping, at most 3 remain proposed
    remaining = [o for o in engine.store.list(100) if o.status == "proposed"]
    assert len(remaining) <= 3
    # the evicted one is DISMISSED (rejected-idea memory), not deleted
    dismissed = [o for o in engine.store.list(100) if o.status == "dismissed"]
    assert len(dismissed) >= 1


def test_record_feedback_requires_queued_or_running(tmp_path: Path):
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *a, **k: proposal())
    oid = engine.tick(auto_queue=False)["opportunity"]["opportunity_id"]
    # proposed -> invalid for feedback
    with pytest.raises(ProactiveError, match="Cannot record feedback"):
        engine.record_feedback(oid, "tests_passed")
    # put it in a queued state, then an invalid outcome is rejected
    op = engine.store.find_any(oid)
    op.status = "queued"
    engine.store.save(op)
    with pytest.raises(ProactiveError, match="outcome"):
        engine.record_feedback(oid, "not-a-real-outcome")
