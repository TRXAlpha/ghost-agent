"""T10: Diagnostics recorded when a job hits the iteration limit."""
import json

import ghost_agent.orchestrator as module
from ghost_agent.orchestrator import Orchestrator


def _plan_actions():
    return [{"tool": "write_file", "path": "plan.md", "content": "Steps"}]


def test_limit_reason_records_tests_failed(tmp_path, monkeypatch):
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps({
        "id": "limit_reason", "title": "Limit Reason",
        "goal": "Implement with tests", "context": {},
        "constraints": {"iteration_limit": 2}}))

    def model(_model, messages, **kwargs):
        content = messages[-1]["content"]
        if "Create the plan now" in content:
            return json.dumps({"thought": "plan", "actions": _plan_actions()})
        return json.dumps({"thought": "implement", "actions": [
            {"tool": "write_file", "path": "app.py", "content": "x = 1\n"},
            {"tool": "write_file", "path": "test_app.py",
             "content": "import app\ndef test_x():\n    assert app.x == 42\n"},
        ]})

    monkeypatch.setattr(module, "ollama_chat", model)
    agent = Orchestrator(tmp_path, model="scripted")
    agent.run_task(task_path)

    state = json.loads((tmp_path / "workspaces" / "limit_reason" / "state.json").read_text())
    assert state["last_result"] == "iteration_limit"
    assert state["limit_reason"] in ("tests_failed", "focused_tests_failed")
    assert state["turns"]["IMPLEMENT"] == 1
    assert state["turns"]["REPAIR"] >= 1


def test_parse_errors_counted(tmp_path, monkeypatch):
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps({
        "id": "parse_errors", "title": "Parse Errors",
        "goal": "Implement", "context": {},
        "constraints": {"iteration_limit": 2}}))

    def model(_model, messages, **kwargs):
        if "Create the plan now" in messages[-1]["content"]:
            return json.dumps({"thought": "plan", "actions": _plan_actions()})
        return "not json"

    monkeypatch.setattr(module, "ollama_chat", model)
    agent = Orchestrator(tmp_path, model="scripted")
    agent.run_task(task_path)

    state = json.loads((tmp_path / "workspaces" / "parse_errors" / "state.json").read_text())
    assert state["parse_errors"] >= 2


def test_results_history_capped(tmp_path, monkeypatch):
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps({
        "id": "history_capped", "title": "History Capped",
        "goal": "Implement with tests", "context": {},
        "constraints": {"iteration_limit": 15}}))

    def model(_model, messages, **kwargs):
        content = messages[-1]["content"]
        if "Create the plan now" in content:
            return json.dumps({"thought": "plan", "actions": _plan_actions()})
        return json.dumps({"thought": "implement", "actions": [
            {"tool": "write_file", "path": "app.py", "content": "x = 1\n"},
            {"tool": "write_file", "path": "test_app.py",
             "content": "import app\ndef test_x():\n    assert app.x == 42\n"},
        ]})

    monkeypatch.setattr(module, "ollama_chat", model)
    agent = Orchestrator(tmp_path, model="scripted")
    agent.run_task(task_path)

    state = json.loads((tmp_path / "workspaces" / "history_capped" / "state.json").read_text())
    assert state["last_result"] == "iteration_limit"
    assert len(state["results_history"]) <= 20


def test_resume_old_checkpoint_without_new_keys(tmp_path, monkeypatch):
    agent = Orchestrator(tmp_path)
    workspace = agent._task_workspace("old_checkpoint")
    workspace.mkdir(parents=True)
    (workspace / "task.json").write_text(json.dumps(
        {"id": "old_checkpoint", "title": "Old", "goal": "Implement",
         "context": {}, "constraints": {"iteration_limit": 2}}))
    (workspace / "plan.md").write_text("Steps")
    (workspace / "state.json").write_text(json.dumps(dict(
        phase="IMPLEMENT", iteration=1, last_result="implement_ok",
        files_touched=[], last_feedback="")))
    monkeypatch.setattr(agent, "_init_workspace", lambda task: workspace)

    seen_turn = [0]

    def model(_model, messages, **kwargs):
        content = messages[-1]["content"]
        if "Create the plan now" in content:
            return json.dumps({"thought": "plan", "actions": _plan_actions()})
        seen_turn[0] += 1
        if seen_turn[0] == 1:  # IMPLEMENT: failing code
            return json.dumps({"thought": "implement", "actions": [
                {"tool": "write_file", "path": "app.py", "content": "x = 1\n"},
                {"tool": "write_file", "path": "test_app.py",
                 "content": "import app\ndef test_x():\n    assert app.x == 42\n"},
            ]})
        return json.dumps({"thought": "repair", "actions": [
            {"tool": "write_file", "path": "app.py", "content": "x = 42\n"},
        ]})

    monkeypatch.setattr(module, "ollama_chat", model)
    agent.resume_task("old_checkpoint")

    state = json.loads((workspace / "state.json").read_text())
    assert "turns" in state
    assert "parse_errors" in state
    assert "results_history" in state
    assert state["last_result"] == "tests_passed"
