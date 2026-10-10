"""Read-only turns must feed their results back (turns are otherwise stateless).

Live benchmark (GLM-5.3-Flash, 2026-10-10): the model read roman.py 9 times and
never wrote tests, because each read result was replaced by generic VERIFY
feedback before the next turn.
"""
import json

import ghost_agent.orchestrator as module
from ghost_agent.orchestrator import Orchestrator


def test_read_results_are_shown_and_job_can_finish(tmp_path, monkeypatch):
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps({"id": "ro", "title": "Feature", "goal": "Implement app with tests",
                                     "context": {}, "constraints": {"iteration_limit": 5}}))
    seen = []

    def model(_model, messages, **kwargs):
        last = messages[-1]["content"]
        seen.append(json.dumps(messages))
        if "Create the plan now" in last:
            return json.dumps({"thought": "plan", "actions": [{"tool": "write_file", "path": "plan.md", "content": "steps"}]})
        if len(seen) == 2:   # implement: write the module only
            return json.dumps({"thought": "impl", "actions": [{"tool": "write_file", "path": "app.py", "content": "ANSWER = 42\n"}]})
        if len(seen) == 3:   # repair: only read
            return json.dumps({"thought": "look", "actions": [{"tool": "read_file", "path": "app.py"}]})
        # next turn must have received the read result
        assert "ANSWER = 42" in seen[-1], "read result was not fed back"
        return json.dumps({"thought": "tests", "actions": [{"tool": "write_file", "path": "test_app.py",
                           "content": "from app import ANSWER\n\ndef test_answer():\n    assert ANSWER == 42\n"}]})

    monkeypatch.setattr(module, "ollama_chat", model)
    Orchestrator(tmp_path, model="scripted").run_task(task_path)
    state = json.loads((tmp_path / "workspaces" / "ro" / "state.json").read_text())
    assert state["last_result"] == "tests_passed"
    assert "read_only" in state.get("results_history", [])


def test_mixed_read_and_write_turn_still_verifies(tmp_path):
    orch = Orchestrator(tmp_path, model="scripted")
    from ghost_agent.schema import parse_action_response
    mixed = parse_action_response(json.dumps({"thought": "t", "actions": [
        {"tool": "read_file", "path": "a.py"}, {"tool": "write_file", "path": "b.py", "content": "x = 1\n"}]}))
    reads = parse_action_response(json.dumps({"thought": "t", "actions": [{"tool": "list_dir", "path": "."}]}))
    empty = parse_action_response(json.dumps({"thought": "t", "actions": []}))
    assert not orch._read_only_turn(mixed)
    assert orch._read_only_turn(reads)
    assert not orch._read_only_turn(empty)
