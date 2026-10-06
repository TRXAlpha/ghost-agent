"""T06: empty-workspace mode for greenfield jobs."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import ghost_agent.orchestrator as orchestrator_module
from ghost_agent.autonomy import OutcomeStore
from ghost_agent.autonomy_improver import CapabilityImprover
from ghost_agent.orchestrator import Orchestrator, Task
from ghost_agent.proactive import ProactiveError, queue_sandbox_outcome


def _make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir(parents=True)
    (project / "existing_module.py").write_text("VALUE = 1\n", encoding="utf-8")
    tests = project / "tests"
    tests.mkdir()
    (tests / "test_existing.py").write_text("def test_existing_fails():\n    assert False\n",
                                            encoding="utf-8")
    return project


def _scripted_model(_model, messages, **kwargs):
    planning = "Create the plan now" in messages[-1]["content"]
    actions = ([{"tool": "write_file", "path": "plan.md", "content": "Implement"}]
               if planning else
               [{"tool": "write_file", "path": "app.py", "content": "answer = 42\n"},
                {"tool": "write_file", "path": "test_app.py",
                 "content": "from app import answer\ndef test_answer():\n    assert answer == 42\n"}])
    return json.dumps({"thought": "implement", "actions": actions})


def _run_job(tmp_path: Path, monkeypatch, constraints: dict) -> tuple[Path, Path]:
    """Run a scripted task via run_task_with_roots; return (project, artifacts)."""
    project = _make_project(tmp_path)
    artifacts = project / ".ghost" / "outcomes" / "job1"
    task = orchestrator_module.Task(
        "job1", "Feature", "Implement with tests", constraints, {})
    monkeypatch.setattr(orchestrator_module, "ollama_chat", _scripted_model)
    agent = Orchestrator(project, model="scripted")
    agent.run_task_with_roots(task, project, artifacts)
    return project, artifacts


def test_empty_mode_sandbox_has_no_project_files(tmp_path, monkeypatch):
    project, artifacts = _run_job(
        tmp_path, monkeypatch, {"workspace_mode": "empty", "iteration_limit": 3})
    sandbox = artifacts / "sandbox"
    assert not (sandbox / "existing_module.py").exists()
    assert not (sandbox / "tests").exists()
    assert (sandbox / "app.py").exists()
    state = json.loads((artifacts / "state.json").read_text(encoding="utf-8"))
    assert state["last_result"] == "tests_passed"


def test_project_mode_still_copies_project(tmp_path, monkeypatch):
    project, artifacts = _run_job(tmp_path, monkeypatch, {"iteration_limit": 3})
    sandbox = artifacts / "sandbox"
    assert (sandbox / "existing_module.py").exists()
    state = json.loads((artifacts / "state.json").read_text(encoding="utf-8"))
    assert state["last_result"] != "tests_passed"


def test_invalid_workspace_mode_rejected_before_creating_dirs(tmp_path, monkeypatch):
    project = _make_project(tmp_path)
    task = orchestrator_module.Task("bad_mode", "Feature", "Implement with tests",
                                    {"workspace_mode": "bogus"}, {})
    agent = Orchestrator(project, model="scripted")
    with pytest.raises(ValueError, match="workspace_mode"):
        agent.run_task_with_roots(task, project, project / ".ghost" / "outcomes" / "bad")
    assert not (project / ".ghost" / "outcomes" / "bad").exists()


def test_empty_mode_refuses_existing_target(tmp_path):
    project = _make_project(tmp_path)
    target = project / ".ghost" / "outcomes" / "job1" / "sandbox"
    target.mkdir(parents=True)
    agent = Orchestrator(project)
    with pytest.raises(FileExistsError):
        agent._create_empty_sandbox(target)


def test_empty_mode_refuses_escape(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    agent = Orchestrator(project)
    with pytest.raises(ValueError):
        agent._create_empty_sandbox(tmp_path.parent / "x")


def test_empty_mode_disables_existing_code_context(tmp_path):
    task = orchestrator_module.Task("t", "Fix the bug", "fix something",
                                    {"workspace_mode": "empty"}, {})
    assert Orchestrator._needs_existing_code_context(task) is False


def test_make_task_payload_copies_workspace_mode(tmp_path):
    store = OutcomeStore(tmp_path)
    job = store.create("Build a small calculator")
    job.acceptance_contract["workspace_mode"] = "empty"
    payload = store.make_task_payload(job, "research", 4)
    assert payload["constraints"]["workspace_mode"] == "empty"


def test_proactive_queue_defaults_to_empty(tmp_path):
    queue_sandbox_outcome(tmp_path, "build a small calculator", "scripted-model")
    store = OutcomeStore(tmp_path)
    job_ids = [path.parent.name for path in store.root.glob("*/outcome.json")]
    assert len(job_ids) == 1
    job = store.load(job_ids[0])
    assert job is not None
    assert job.acceptance_contract["workspace_mode"] == "empty"


def test_queue_rejects_bad_workspace_mode(tmp_path):
    with pytest.raises(ProactiveError):
        queue_sandbox_outcome(tmp_path, "build a small calculator", "scripted-model",
                              workspace_mode="x")


def test_capability_improver_queues_project_mode(tmp_path, monkeypatch):
    import ghost_agent.autonomy_improver as improver_module

    def fake_chat(model, messages, **kwargs):
        return '{"addressable": true, "risky": false, "reason": "add it"}'

    monkeypatch.setattr(improver_module, "ollama_chat", fake_chat)
    improver = CapabilityImprover(tmp_path, model="fake-model")
    improver.gaps.record_gap("Build adapter", "Missing adapter")
    improver.gaps.record_gap("Build adapter", "Missing adapter")

    gap_id = improver.try_build()
    assert gap_id is not None
    store = OutcomeStore(tmp_path)
    job_ids = [path.parent.name for path in store.root.glob("*/outcome.json")]
    assert len(job_ids) == 1
    job = store.load(job_ids[0])
    assert job is not None
    assert job.acceptance_contract["workspace_mode"] == "project"
