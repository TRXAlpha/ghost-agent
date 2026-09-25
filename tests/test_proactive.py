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
