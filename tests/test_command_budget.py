import json
import subprocess
import sys

import pytest

from ghost_agent.command_budget import CommandBudget


def test_calls_persist_across_instances(tmp_path):
    path = tmp_path / "budget.json"
    with CommandBudget(path, calls=1).slot() as timeout:
        assert timeout == 30
    with pytest.raises(ValueError, match="exhausted"):
        with CommandBudget(path, calls=1).slot():
            pytest.fail("Budget bypass")


def test_reservation_and_refund(tmp_path, monkeypatch):
    ticks = iter([10, 12])
    monkeypatch.setattr("ghost_agent.command_budget.time.monotonic", lambda: next(ticks))
    path = tmp_path / "budget.json"
    with CommandBudget(path, seconds=5).slot() as timeout:
        assert timeout == 5
        assert json.loads(path.read_text())["used_seconds"] == 5
    assert json.loads(path.read_text())["used_seconds"] == 2


def test_no_refund_after_simulated_crash(tmp_path):
    path = tmp_path / "budget.json"
    path.write_text(json.dumps(dict(seconds=5, calls=2, used_seconds=5, used_calls=1)))
    with pytest.raises(ValueError, match="exhausted"):
        with CommandBudget(path, seconds=5, calls=2).slot():
            pytest.fail("Crash reservation bypass")


def test_actual_process_crash_preserves_reservation(tmp_path):
    path = tmp_path / "budget.json"
    script = (
        "import os,sys; from pathlib import Path; "
        "from ghost_agent.command_budget import CommandBudget\n"
        "with CommandBudget(Path(sys.argv[1]), seconds=5, calls=2).slot():\n"
        "    os._exit(73)\n"
    )
    result = subprocess.run([sys.executable, "-c", script, str(path)],
                            capture_output=True, timeout=10)
    assert result.returncode == 73
    data = json.loads(path.read_text())
    assert data["used_calls"] == 1
    assert data["used_seconds"] == 5
    with pytest.raises(ValueError, match="exhausted"):
        with CommandBudget(path, seconds=5, calls=2).slot():
            pytest.fail("Crash reset allowance")


@pytest.mark.parametrize("raw", ["{", "[]", '{"used_calls": 0}'])
def test_corrupt_state_preserved(tmp_path, raw):
    path = tmp_path / "budget.json"
    path.write_text(raw)
    with pytest.raises(ValueError):
        with CommandBudget(path).slot():
            pytest.fail("Corrupt budget bypass")
    assert path.read_text() == raw


def test_resume_cannot_raise_budget_silently(tmp_path):
    path = tmp_path / "budget.json"
    with CommandBudget(path, calls=1).slot():
        pass
    with pytest.raises(ValueError, match="changed"):
        with CommandBudget(path, calls=2).slot():
            pytest.fail("Budget changed")


def test_concurrent_admission_refused(tmp_path):
    path = tmp_path / "budget.json"
    with CommandBudget(path).slot():
        with pytest.raises(BlockingIOError):
            with CommandBudget(path).slot():
                pytest.fail("Concurrent admission")


def test_orchestrator_commands_share_budget(tmp_path, monkeypatch):
    from ghost_agent.orchestrator import Orchestrator
    import ghost_agent.orchestrator as module
    agent = object.__new__(Orchestrator)
    agent.repo_root = tmp_path
    agent._command_budget = CommandBudget(tmp_path / "budget.json", calls=1)
    calls = []
    monkeypatch.setattr(module.cmd_tools, "run_cmd", lambda *args, **kwargs:
                        calls.append(kwargs) or {"returncode": 0, "output": "ok"})
    agent._run_command("python -V", ".", tmp_path)
    with pytest.raises(ValueError, match="exhausted"):
        agent._run_command("python -V", ".", tmp_path)
    assert len(calls) == 1


def test_exhaustion_stops_before_further_model_repairs(tmp_path, monkeypatch):
    import ghost_agent.orchestrator as module
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps({"id": "budget_stop", "title": "Feature",
        "goal": "Implement with tests", "context": {},
        "constraints": {"iteration_limit": 8, "command_budget_calls": 1}}))
    model_calls = []

    def model(_model, messages, **kwargs):
        model_calls.append(messages[-1]["content"])
        assert len(model_calls) <= 2, "Exhausted budget must not enter model repair"
        planning = "Create the plan now" in model_calls[-1]
        return json.dumps({"thought": "implement", "actions": [{"tool": "write_file",
            "path": "plan.md" if planning else "test_example.py",
            "content": "Plan" if planning else "def test_example(): assert True\n"}]})

    monkeypatch.setattr(module, "ollama_chat", model)
    executions = []
    monkeypatch.setattr(module.cmd_tools, "run_cmd", lambda *args, **kwargs:
                        executions.append(args) or {"returncode": 0, "output": "ok"})
    module.Orchestrator(tmp_path, model="scripted").run_task(task_path)
    state = json.loads((tmp_path / "workspaces" / "budget_stop" / "state.json").read_text())
    assert state["phase"] == "DONE"
    assert state["last_result"] == "command_budget_exhausted"
    assert len(model_calls) == 2
    assert len(executions) == 1
