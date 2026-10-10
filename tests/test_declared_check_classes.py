"""TO2: declared verification classes (compile, smoke) as extra VERIFY gates."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import ghost_agent.orchestrator as orchestrator_module
from ghost_agent.orchestrator import Orchestrator, Task


def _scripted_model(_model, messages, **kwargs):
    planning = "Create the plan now" in messages[-1]["content"]
    actions = ([{"tool": "write_file", "path": "plan.md", "content": "Implement"}]
               if planning else
               [{"tool": "write_file", "path": "app.py", "content": "answer = 42\n"},
                {"tool": "write_file", "path": "test_app.py",
                 "content": "from app import answer\ndef test_answer():\n    assert answer == 42\n"}])
    return json.dumps({"thought": "implement", "actions": actions})


def test_compile_gate_catches_syntax_error_tests_did_not_import(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "broken.py").write_text("def broken(:\n")
    monkeypatch.setattr(orchestrator_module, "ollama_chat", _scripted_model)
    artifacts = project / ".ghost" / "outcomes" / "job1"
    task = Task("job1", "Feature", "Implement with tests",
                {"iteration_limit": 3, "required_checks": ["compile"]}, {})
    Orchestrator(project, model="scripted").run_task_with_roots(task, project, artifacts)
    state = json.loads((artifacts / "state.json").read_text())
    assert state["last_result"] != "tests_passed"
    stages = [(gate["stage"], gate["status"]) for gate in state["verification_gates"]]
    assert ("compile", "failed") in stages


def test_declared_checks_pass_when_code_is_valid(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(orchestrator_module, "ollama_chat", _scripted_model)
    artifacts = project / ".ghost" / "outcomes" / "job2"
    task = Task("job2", "Feature", "Implement with tests",
                {"iteration_limit": 3, "required_checks": ["compile", "smoke"],
                 "smoke_cmd": "python -c \"print('smoke ok')\""}, {})
    Orchestrator(project, model="scripted").run_task_with_roots(task, project, artifacts)
    state = json.loads((artifacts / "state.json").read_text())
    assert state["last_result"] == "tests_passed"
    stages = {gate["stage"]: gate["status"] for gate in state["verification_gates"]}
    assert stages.get("compile") == "passed"
    assert stages.get("smoke") == "passed"


def test_smoke_without_cmd_fails_the_gate(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(orchestrator_module, "ollama_chat", _scripted_model)
    artifacts = project / ".ghost" / "outcomes" / "job3"
    task = Task("job3", "Feature", "Implement with tests",
                {"iteration_limit": 3, "required_checks": ["smoke"]}, {})
    Orchestrator(project, model="scripted").run_task_with_roots(task, project, artifacts)
    state = json.loads((artifacts / "state.json").read_text())
    assert state["last_result"] != "tests_passed"
    failed = [gate for gate in state["verification_gates"]
              if gate["stage"] == "smoke" and gate["status"] == "failed"]
    assert failed
    assert failed[0]["result"]["output"] == "smoke check declared but no smoke_cmd given"


def test_unknown_check_class_raises():
    with pytest.raises(ValueError):
        Task("t", "title", "goal", {"required_checks": ["lint"]}, {})


def test_unit_check_and_defaults_are_unchanged():
    assert Task("t", "title", "goal", {"required_checks": ["unit"]}, {}).required_checks() == ["unit"]
    assert Task("t", "title", "goal", {}, {}).required_checks() == []


def test_compile_gate_runs_without_discoverable_test_commands(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    # Scripted model writes only source; no test files are ever created, so
    # _verification_commands finds nothing and the declared compile gate must
    # still run instead of falling through to verification_missing.
    def source_only_model(_model, messages, **kwargs):
        planning = "Create the plan now" in messages[-1]["content"]
        actions = ([{"tool": "write_file", "path": "plan.md", "content": "Implement"}]
                   if planning else
                   [{"tool": "write_file", "path": "app.py", "content": "answer = 42\n"}])
        return json.dumps({"thought": "implement", "actions": actions})
    monkeypatch.setattr(orchestrator_module, "ollama_chat", source_only_model)
    artifacts = project / ".ghost" / "outcomes" / "job5"
    task = Task("job5", "Feature", "Implement",
                {"iteration_limit": 3, "required_checks": ["compile"]}, {})
    Orchestrator(project, model="scripted").run_task_with_roots(task, project, artifacts)
    state = json.loads((artifacts / "state.json").read_text())
    stages = {gate["stage"]: gate["status"] for gate in state["verification_gates"]}
    assert stages.get("compile") == "passed"
    assert state["last_result"] == "tests_passed"


def test_smoke_cmd_runs_through_allowlisted_runner(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("print('smoke ok')\n")
    monkeypatch.setattr(orchestrator_module, "ollama_chat", _scripted_model)
    artifacts = project / ".ghost" / "outcomes" / "job4"
    task = Task("job4", "Feature", "Implement with tests",
                {"iteration_limit": 3, "required_checks": ["smoke"],
                 "smoke_cmd": "python app.py"}, {})
    Orchestrator(project, model="scripted").run_task_with_roots(task, project, artifacts)
    state = json.loads((artifacts / "state.json").read_text())
    stages = {gate["stage"]: gate["status"] for gate in state["verification_gates"]}
    assert stages.get("smoke") == "passed"
    assert state["last_result"] == "tests_passed"
