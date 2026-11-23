"""Iteration budget awareness and verification timeout (jobs looped to the limit, 2026-11-23)."""
import json

import ghost_agent.orchestrator as module
from ghost_agent.orchestrator import Orchestrator


def test_budget_note_counts_down_and_flags_repeats():
    note = Orchestrator._budget_note
    state = {"iteration": 3}
    assert note(state, 30, "boom") == "Budget: iteration 4 of 30."
    state["iteration"] = 4
    note(state, 30, "boom")
    state["iteration"] = 5
    assert "failed 3 times with the same result" in note(state, 30, "boom")
    state["iteration"] = 6
    assert "WARNING" not in note(state, 30, "something new") and state["feedback_repeats"] == 0
    state["iteration"] = 28
    late = note(state, 30, "x")
    assert "Only 2 iteration(s) left" in late and "NOTES.md" in late


def test_model_sees_the_budget_every_turn(tmp_path, monkeypatch):
    task = tmp_path / "task.json"
    task.write_text(json.dumps({"id": "example", "title": "T", "goal": "Implement with tests", "context": {},
                                "constraints": {"iteration_limit": 8}}))
    seen = []

    def model(_m, messages, **kw):
        seen.append(" ".join(m["content"] for m in messages if m["role"] == "user"))
        if "Create the plan now" in messages[-1]["content"]:
            acts = [{"tool": "write_file", "path": "plan.md", "content": "x"}]
        else:
            acts = [{"tool": "write_file", "path": "app.py", "content": "a = 1\n"},
                    {"tool": "write_file", "path": "test_app.py",
                     "content": "from app import a\ndef test_a():\n    assert a == 1\n"}]
        return json.dumps({"thought": "t", "actions": acts})

    monkeypatch.setattr(module, "ollama_chat", model)
    Orchestrator(tmp_path, model="scripted").run_task(task)
    assert any("Budget: iteration" in s and "of 8" in s for s in seen)


def test_verification_gate_gets_a_long_timeout(tmp_path, monkeypatch):
    agent = Orchestrator(tmp_path, model="scripted")
    agent._command_timeout = 30
    seen = []

    def fake_execute(response, workspace, log):
        seen.append(agent._command_timeout)
        return [{"returncode": 0, "output": "ok"}], []

    monkeypatch.setattr(agent, "_execute_actions", fake_execute)
    assert agent._run_verification_command("python -m pytest -q", tmp_path, None)["returncode"] == 0
    assert seen == [module.VERIFY_COMMAND_TIMEOUT] and agent._command_timeout == 30  # model's own commands keep 30 s
