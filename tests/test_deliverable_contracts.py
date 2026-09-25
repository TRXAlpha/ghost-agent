import pytest
import json

from ghost_agent.orchestrator import Orchestrator, Task


@pytest.mark.parametrize("required", ["app.py", {}, [None], [""], ["../outside"],
    ["C:\\outside.txt"], ["C:relative.txt"], ["/outside.txt"], ["a:stream"],
    ["a/../b"], ["a//b"], ["a\n.txt"], ["A.py", "a.py"], ["*.py"]])
def test_invalid_deliverable_contract_refused(required):
    with pytest.raises(ValueError):
        Task("t", "title", "goal", {"required_files": required}, {})


def test_required_directory_does_not_count_as_file(tmp_path):
    (tmp_path / "output").mkdir()
    task = Task("t", "title", "goal", {"required_files": ["output"]}, {})
    assert Orchestrator._missing_required_files(task, tmp_path) == ["output"]


def test_contract_revalidated_after_mutation(tmp_path):
    task = Task("t", "title", "goal", {}, {})
    task.constraints["required_files"] = "malformed"
    with pytest.raises(ValueError):
        Orchestrator._missing_required_files(task, tmp_path)


def test_relative_nested_deliverable(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x=1")
    task = Task("t", "title", "goal", {"required_files": ["src/app.py", "missing.py"]}, {})
    assert Orchestrator._missing_required_files(task, tmp_path) == ["missing.py"]


def test_passing_tests_cannot_remove_required_deliverable(tmp_path, monkeypatch):
    import ghost_agent.orchestrator as module
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps({"id": "removed_output", "title": "Feature",
        "goal": "Implement with tests", "context": {},
        "constraints": {"iteration_limit": 3, "required_files": ["app.py"]}}))

    def model(_model, messages, **kwargs):
        planning = "Create the plan now" in messages[-1]["content"]
        actions = [{"tool": "write_file", "path": "plan.md", "content": "Implement"}] if planning else [
            {"tool": "write_file", "path": "app.py", "content": "answer=42\n"},
            {"tool": "write_file", "path": "test_app.py", "content":
             "from pathlib import Path\ndef test_cleanup():\n    Path('app.py').unlink(missing_ok=True)\n"}]
        return json.dumps({"thought": "implement", "actions": actions})

    monkeypatch.setattr(module, "ollama_chat", model)
    agent = Orchestrator(tmp_path, model="scripted")
    agent.run_task(task_path)
    state = json.loads((tmp_path / "workspaces" / "removed_output" / "state.json").read_text())
    assert state["last_result"] != "tests_passed"
    gates = state["verification_gates"]
    assert [gate["status"] for gate in gates] == ["passed", "passed", "failed"]
    assert gates[-1]["stage"] == "deliverables"
    assert "app.py" in gates[-1]["result"]["output"]
