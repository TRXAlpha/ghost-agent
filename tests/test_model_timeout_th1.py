"""TH1: a model timeout fails the turn, not the whole job."""

import json

import pytest
import requests

import ghost_agent.orchestrator as module
from ghost_agent.orchestrator import Orchestrator


def _write_task(tmp_path, iterations=8):
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps({
        "id": "example", "title": "Feature",
        "goal": "Implement with tests", "context": {},
        "constraints": {"iteration_limit": iterations},
    }))
    return task_path


def _state(tmp_path):
    return json.loads(
        (tmp_path / "workspaces" / "example" / "state.json").read_text()
    )


def test_one_timeout_costs_one_turn_job_still_passes(tmp_path, monkeypatch):
    task_path = _write_task(tmp_path)
    calls = {"n": 0}

    def model(_model, messages, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.exceptions.ReadTimeout("timed out")
        planning = "Create the plan now" in messages[-1]["content"]
        if planning:
            actions = [{"tool": "write_file", "path": "plan.md",
                        "content": "Implement"}]
        else:
            actions = [
                {"tool": "write_file", "path": "app.py",
                 "content": "answer = 42\n"},
                {"tool": "write_file", "path": "test_app.py",
                 "content": "from app import answer\ndef test_answer():\n"
                            "    assert answer == 42\n"},
            ]
        return json.dumps({"thought": "implement", "actions": actions})

    monkeypatch.setattr(module, "ollama_chat", model)
    agent = Orchestrator(tmp_path, model="scripted")
    agent.run_task(task_path)
    state = _state(tmp_path)
    assert state["last_result"] == "tests_passed"
    assert state["model_failures"] == 0  # reset by the successful turn


def test_three_consecutive_timeouts_end_job_as_model_unavailable(
    tmp_path, monkeypatch
):
    task_path = _write_task(tmp_path)

    def model(_model, messages, **kwargs):
        raise requests.exceptions.ReadTimeout("timed out")

    monkeypatch.setattr(module, "ollama_chat", model)
    agent = Orchestrator(tmp_path, model="scripted")
    agent.run_task(task_path)
    state = _state(tmp_path)
    assert state["last_result"] == "model_unavailable"
    assert state["phase"] == "DONE"
    assert state["model_failures"] == 3


def test_value_error_from_chat_still_propagates(tmp_path, monkeypatch):
    task_path = _write_task(tmp_path)

    def model(_model, messages, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(module, "ollama_chat", model)
    agent = Orchestrator(tmp_path, model="scripted")
    with pytest.raises(ValueError):
        agent.run_task(task_path)
