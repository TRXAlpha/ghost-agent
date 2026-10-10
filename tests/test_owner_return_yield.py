"""TO1: unattended jobs yield to the owner when they return to the PC."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import ghost_agent.orchestrator as orchestrator_module
import ghost_agent.proactive as proactive_module
from ghost_agent.autonomy import OutcomeStore
from ghost_agent.cli import _execute_queued_outcome
from ghost_agent.orchestrator import Orchestrator, Task
from ghost_agent.policy import PolicyStore
from ghost_agent.proactive import queue_sandbox_outcome
from ghost_agent.runtime import ScheduledJob, Scheduler
from ghost_agent.worker import OutcomeWorker


def _scripted_model(_model, messages, **kwargs):
    planning = "Create the plan now" in messages[-1]["content"]
    actions = ([{"tool": "write_file", "path": "plan.md", "content": "Implement"}]
               if planning else
               [{"tool": "write_file", "path": "app.py", "content": "answer = 42\n"},
                {"tool": "write_file", "path": "test_app.py",
                 "content": "from app import answer\ndef test_answer():\n    assert answer == 42\n"}])
    return json.dumps({"thought": "implement", "actions": actions})


def _make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir(parents=True)
    return project


def test_unattended_job_yields_when_owner_returns(tmp_path, monkeypatch):
    project = _make_project(tmp_path)
    monkeypatch.setattr(orchestrator_module, "ollama_chat", _scripted_model)
    monkeypatch.setattr(orchestrator_module, "_owner_idle_seconds", lambda: 5.0)
    artifacts = project / ".ghost" / "outcomes" / "job1"
    task = Task("job1", "Feature", "Implement with tests",
                {"iteration_limit": 3, "yield_on_owner_return": True}, {})
    agent = Orchestrator(project, model="scripted")
    agent.run_task_with_roots(task, project, artifacts)
    state = json.loads((artifacts / "state.json").read_text())
    assert state["last_result"] == "yielded_to_owner"
    assert state["yield"]["idle_s"] == 5.0
    assert state["yield"]["latency_s"] >= 0.0


def test_owner_job_never_yields(tmp_path, monkeypatch):
    project = _make_project(tmp_path)
    monkeypatch.setattr(orchestrator_module, "ollama_chat", _scripted_model)
    monkeypatch.setattr(orchestrator_module, "_owner_idle_seconds", lambda: 5.0)
    artifacts = project / ".ghost" / "outcomes" / "job2"
    task = Task("job2", "Feature", "Implement with tests",
                {"iteration_limit": 3, "yield_on_owner_return": False}, {})
    agent = Orchestrator(project, model="scripted")
    agent.run_task_with_roots(task, project, artifacts)
    state = json.loads((artifacts / "state.json").read_text())
    assert state["last_result"] == "tests_passed"
    assert "yield" not in state


def test_unknown_idle_never_yields(tmp_path, monkeypatch):
    project = _make_project(tmp_path)
    monkeypatch.setattr(orchestrator_module, "ollama_chat", _scripted_model)
    monkeypatch.setattr(orchestrator_module, "_owner_idle_seconds", lambda: None)
    artifacts = project / ".ghost" / "outcomes" / "job3"
    task = Task("job3", "Feature", "Implement with tests",
                {"iteration_limit": 3, "yield_on_owner_return": True}, {})
    agent = Orchestrator(project, model="scripted")
    agent.run_task_with_roots(task, project, artifacts)
    state = json.loads((artifacts / "state.json").read_text())
    assert state["last_result"] == "tests_passed"
    assert "yield" not in state


def test_queue_sandbox_outcome_sets_yield_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(PolicyStore, "require", lambda self, *_a, **_k: None)
    job = queue_sandbox_outcome(tmp_path, "Build a small experiment", "scripted",
                                source="proactive", opportunity_id="opp1")
    assert job.acceptance_contract["yield_on_owner_return"] is True
    payload = OutcomeStore(tmp_path).make_task_payload(job, "", 4)
    assert payload["constraints"]["yield_on_owner_return"] is True


def test_worker_maps_yield_to_paused(tmp_path, monkeypatch):
    project = _make_project(tmp_path)
    store = OutcomeStore(project)
    job = store.create("Build yield test")
    job.coding_model = "scripted"
    job.skip_research = True
    job.acceptance_contract["yield_on_owner_return"] = True
    store.save(job)
    scheduler = Scheduler(project)
    scheduler.enqueue(ScheduledJob(job.job_id, "scheduler-own"))
    worker = OutcomeWorker(project)
    monkeypatch.setattr(orchestrator_module, "_owner_idle_seconds", lambda: 5.0)

    class YieldOrchestrator:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def run_task_with_roots(self, _task, _workspace_root, artifacts_root) -> None:
            Path(artifacts_root).mkdir(parents=True, exist_ok=True)
            (Path(artifacts_root) / "state.json").write_text(
                json.dumps({"last_result": "yielded_to_owner"}), encoding="utf-8")

    monkeypatch.setattr("ghost_agent.cli.Orchestrator", YieldOrchestrator)
    result = worker.run_next(lambda scheduled: _execute_queued_outcome(project, scheduled))
    assert result and result.status == "paused"
    assert store.load(job.job_id).status == "paused"
    assert Scheduler(project).list()[0].status == "paused"


def test_resume_continues_from_checkpoint_after_yield(tmp_path, monkeypatch):
    project = _make_project(tmp_path)
    monkeypatch.setattr(orchestrator_module, "ollama_chat", _scripted_model)
    artifacts = project / ".ghost" / "outcomes" / "job5"
    task = Task("job5", "Feature", "Implement with tests",
                {"iteration_limit": 6, "yield_on_owner_return": True}, {})
    monkeypatch.setattr(orchestrator_module, "_owner_idle_seconds", lambda: 5.0)
    agent = Orchestrator(project, model="scripted")
    agent.run_task_with_roots(task, project, artifacts)
    state = json.loads((artifacts / "state.json").read_text())
    assert state["last_result"] == "yielded_to_owner"

    # Owner is away again (idle unknown never yields); resume from the
    # preserved checkpoint and finish.
    monkeypatch.setattr(orchestrator_module, "_owner_idle_seconds", lambda: None)
    agent2 = Orchestrator(project, model="scripted")
    agent2.resume_task_with_roots(task, artifacts)
    state2 = json.loads((artifacts / "state.json").read_text())
    assert state2["last_result"] == "tests_passed"
