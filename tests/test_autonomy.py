from pathlib import Path

import json
import pytest

from ghost_agent import orchestrator as orchestrator_module
from ghost_agent.identity import IdentityStore
from ghost_agent.runtime import EmergencyStop
from ghost_agent.orchestrator import Orchestrator, Task


def test_sandbox_is_project_snapshot_and_never_mutates_source(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (project / ".venv").mkdir()
    (project / ".venv" / "cache.txt").write_text("ignore", encoding="utf-8")
    agent = Orchestrator(project)

    sandbox = agent._create_sandbox(project, project / "workspaces" / "run_1")
    (sandbox / "app.py").write_text("VALUE = 2\n", encoding="utf-8")

    assert (sandbox / "app.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert (project / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not (sandbox / ".venv").exists()


def test_snapshot_excludes_conventional_secret_files(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    for name in (".env", ".env.local", ".ENV.production", "server.key", "identity.pem"):
        (project / name).write_text("synthetic private data", encoding="utf-8")
    (project / "app.py").write_text("value = 1", encoding="utf-8")
    agent = Orchestrator(project)
    sandbox = agent._create_sandbox(project, project / "workspaces" / "safe")
    assert sorted(path.name for path in sandbox.iterdir()) == ["app.py"]
    assert (project / ".env").read_text() == "synthetic private data"


@pytest.mark.parametrize("task_id", ["..", "../outside", "a/b", "a\\b", "/absolute"])
def test_resume_rejects_path_like_task_ids(tmp_path, task_id):
    with pytest.raises(ValueError, match="task ID"):
        Orchestrator(tmp_path).resume_task(task_id)


def test_verification_requires_explicit_contract_or_detected_tests(tmp_path: Path) -> None:
    task = Task("t", "title", "goal", {"test_cmd": "pytest -q tests"}, {})
    assert Orchestrator._verification_command(task, tmp_path) == "pytest -q tests"

    task.constraints = {}
    (tmp_path / "test_app.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    assert Orchestrator._verification_command(task, tmp_path) == "python -m pytest -q"

    (tmp_path / "test_app.py").unlink()
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    assert Orchestrator._verification_command(task, tmp_path) is None


def test_greenfield_tasks_do_not_receive_irrelevant_repository_context() -> None:
    fresh = Task("t", "Create a game", "Create a snake game with tests.", {}, {})
    repair = Task("t", "Fix game", "Fix an existing game regression.", {}, {})

    assert not Orchestrator._needs_existing_code_context(fresh)
    assert Orchestrator._needs_existing_code_context(repair)


def test_explicit_test_requirement_needs_a_test_file_written_this_run() -> None:
    task = Task("t", "Feature", "Add a feature and pytest tests.", {}, {})

    assert Orchestrator._requires_focused_tests(task)
    assert not Orchestrator._has_task_test_change({"files_touched": ["app.py"]})
    assert Orchestrator._has_task_test_change({"files_touched": ["tests/test_app.py"]})
    assert Orchestrator._has_task_test_change({"files_touched": ["app_test.py"]})
    assert Orchestrator._focused_test_command({"files_touched": ["tests/test_app.py"]}) == (
        "python -m pytest -q -- tests/test_app.py"
    )


def test_required_file_contract_reports_missing_deliverables(tmp_path: Path) -> None:
    task = Task("t", "Feature", "Create files", {"required_files": ["app.py", "test_app.py"]}, {})
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")

    assert Orchestrator._missing_required_files(task, tmp_path) == ["test_app.py"]


@pytest.mark.parametrize("separate_artifacts", [False, True])
def test_agent_repairs_failing_code_inside_sandbox(tmp_path: Path, monkeypatch, separate_artifacts) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "test_calculator.py").write_text(
        "from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    task_path = project / "task.json"
    task_path.write_text(json.dumps({
        "id": "repair_eval",
        "title": "Calculator repair",
        "goal": "Implement add and prove it with tests.",
        "constraints": {"test_cmd": "python -m pytest -q", "iteration_limit": 5},
        "context": {},
    }), encoding="utf-8")

    def scripted_model(_model, messages, **_kwargs):
        instruction = messages[-1]["content"]
        if "Create the plan now" in instruction:
            return json.dumps({"thought": "plan", "actions": [{
                "tool": "write_file", "path": "plan.md", "content": "Implement and test add."
            }]})
        if "Implement the task" in instruction:
            return json.dumps({"thought": "initial implementation", "actions": [
                {"tool": "write_file", "path": "calculator.py", "content": "def add(a, b):\n    return a - b\n"},
                {"tool": "write_file", "path": "test_calculator.py", "content": "from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"},
            ]})
        if "Previous step failed" in instruction:
            return json.dumps({"thought": "repair", "actions": [{
                "tool": "write_file", "path": "calculator.py", "content": "def add(a, b):\n    return a + b\n"
            }]})
        raise AssertionError(f"Unexpected instruction: {instruction}")

    monkeypatch.setattr(orchestrator_module, "ollama_chat", scripted_model)
    agent = Orchestrator(project, model="scripted")
    if separate_artifacts:
        artifacts = project / ".ghost" / "outcomes" / "repair_eval" / "run"
        agent.run_task_with_roots(agent._load_task(task_path), project, artifacts)
        sandbox = artifacts / "sandbox"
    else:
        agent.run_task(task_path)
        sandbox = artifacts = project / "workspaces" / "repair_eval"
    state = json.loads((artifacts / "state.json").read_text(encoding="utf-8"))
    assert state["last_result"] == "tests_passed"
    assert "return a + b" in (sandbox / "calculator.py").read_text(encoding="utf-8")
    assert not (project / "calculator.py").exists()


@pytest.mark.parametrize("gate", ["stop", "policy", "kernel", "corrupt_stop", "corrupt_identity"])
def test_verification_cannot_bypass_safety_gates(tmp_path, monkeypatch, gate):
    agent = Orchestrator(tmp_path)
    if gate == "stop":
        EmergencyStop(tmp_path).engage("test")
    elif gate == "policy":
        agent.identity.add_prohibition("Do not run commands")
    elif gate == "kernel":
        agent.kernel.manifests.pop("command.execute")
    elif gate == "corrupt_stop":
        EmergencyStop(tmp_path).path.write_text("{broken", encoding="utf-8")
    else:
        agent.identity.path.write_text("{broken", encoding="utf-8")

    calls = []
    monkeypatch.setattr(orchestrator_module.cmd_tools, "run_cmd", lambda *a, **kw: calls.append(a))
    result = agent._run_verification_command(
        "python -V", tmp_path, orchestrator_module.JsonlLogger(tmp_path / "actions.log")
    )
    assert calls == []
    assert result["returncode"] != 0
    assert result["output"]


def test_verification_records_tool_provenance(tmp_path):
    agent = Orchestrator(tmp_path)
    result = agent._run_verification_command(
        "python -V", tmp_path, orchestrator_module.JsonlLogger(tmp_path / "actions.log")
    )
    assert result["returncode"] == 0
    records = agent.provenance.all()
    assert records[-1].kind == "tool.action"
    assert records[-1].payload["input"]["cmd"] == "python -V"


def test_missing_kernel_fails_closed(tmp_path):
    agent = Orchestrator(tmp_path)
    del agent.kernel
    action = orchestrator_module.ActionResponse.model_validate({
        "actions": [{"tool": "list_dir", "path": "."}],
    }).actions[0]
    assert agent._authorize_action(action, tmp_path) is not None


def test_rejected_secret_write_does_not_leak_into_action_audit(tmp_path):
    agent = Orchestrator(tmp_path)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    secret = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcde"
    response = orchestrator_module.ActionResponse.model_validate({
        "actions": [{"tool": "write_file", "path": "credentials.txt", "content": secret}],
    })
    logger = orchestrator_module.JsonlLogger(tmp_path / "actions.log")
    results, touched = agent._execute_actions(response, sandbox, logger)
    assert "error" in results[0]
    assert not touched
    assert not (sandbox / "credentials.txt").exists()
    assert secret not in logger.path.read_text(encoding="utf-8")
    assert secret not in agent.provenance.path.read_text(encoding="utf-8")


def test_emergency_stop_halts_before_model_inference(tmp_path, monkeypatch):
    agent = Orchestrator(tmp_path)
    EmergencyStop(tmp_path).engage("test")
    calls = []
    monkeypatch.setattr(orchestrator_module, "ollama_chat", lambda *a, **kw: calls.append(a))
    artifacts = tmp_path / ".ghost" / "outcomes" / "stopped" / "run"
    agent.run_task_with_roots(Task("stopped", "Create", "Create a file", {}, {}), tmp_path, artifacts)
    assert calls == []
    assert json.loads((artifacts / "state.json").read_text())["last_result"] == "emergency_stop"


def test_focused_test_paths_with_spaces_are_quoted():
    path = "tests/test_some feature.py"
    command = Orchestrator._focused_test_command({"files_touched": [path]})
    assert orchestrator_module.cmd_tools._split_cmd(command)[-1] == path


@pytest.mark.parametrize("kind", ["sandbox", "artifacts"])
@pytest.mark.parametrize("destination", ["project", "outside", "existing"])
def test_run_initialization_never_erases_existing_data(tmp_path, kind, destination):
    project = tmp_path / "project"
    project.mkdir()
    agent = Orchestrator(project)
    target = {"project": project, "outside": tmp_path / "outside",
              "existing": project / "workspaces" / "existing"}[destination]
    target.mkdir(parents=True, exist_ok=True)
    sentinel = target / "user.txt"
    sentinel.write_text("preserve me", encoding="utf-8")
    with pytest.raises((ValueError, FileExistsError)):
        if kind == "sandbox":
            agent._create_sandbox(project, target)
        else:
            agent._init_artifacts_root(Task("t", "t", "t", {}, {}), target)
    assert sentinel.read_text(encoding="utf-8") == "preserve me"


def test_executor_enforces_persistent_user_prohibition(tmp_path: Path) -> None:
    IdentityStore(tmp_path).add_prohibition("Do not run commands")
    agent = Orchestrator(tmp_path)
    response = orchestrator_module.ActionResponse.model_validate({
        "thought": "test", "actions": [{"tool": "run_cmd", "cmd": "python -V", "cwd": "."}],
    })

    results, _ = agent._execute_actions(
        response, tmp_path, orchestrator_module.JsonlLogger(tmp_path / "actions.log")
    )

    assert "Blocked by user policy" in results[0]["error"]


def test_executor_enforces_emergency_stop(tmp_path: Path) -> None:
    agent = Orchestrator(tmp_path)
    EmergencyStop(tmp_path).engage("test")
    response = orchestrator_module.ActionResponse.model_validate({
        "thought": "write", "actions": [{"tool": "write_file", "path": "a.py", "content": "x = 1"}],
    })

    results, _ = agent._execute_actions(
        response, tmp_path, orchestrator_module.JsonlLogger(tmp_path / "actions.log")
    )

    assert "Emergency stop is engaged" in results[0]["error"]


def test_executor_capability_kernel_denies_workspace_escape(tmp_path: Path) -> None:
    agent = Orchestrator(tmp_path)
    response = orchestrator_module.ActionResponse.model_validate({
        "thought": "escape", "actions": [{"tool": "write_file", "path": "../outside.py", "content": "x = 1"}],
    })

    results, _ = agent._execute_actions(
        response, tmp_path, orchestrator_module.JsonlLogger(tmp_path / "actions.log")
    )

    assert "Capability denied" in results[0]["error"]
