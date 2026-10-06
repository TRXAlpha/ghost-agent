import json

import pytest

from ghost_agent.orchestrator import Orchestrator


@pytest.mark.parametrize("record", [
    {"returncode": False, "output": "ok"},
    {"returncode": 0.0, "output": "ok"},
    {"returncode": "0", "output": "ok"},
    {"returncode": 0},
    {"returncode": 0, "output": None},
    {"returncode": 0, "output": "ok", "error": "interrupted"},
    {"returncode": 125, "output": "launcher refused"},
])
def test_invalid_or_failed_command_never_succeeds(record):
    assert not Orchestrator._command_succeeded(record)
    assert object.__new__(Orchestrator)._has_errors([record])


@pytest.mark.parametrize("records", [[], [None], [{}, {}],
    [{"returncode": False, "output": "ok"}],
    [{"returncode": 0, "output": 7}], [{"error": "timeout"}]])
def test_verification_gate_refuses_malformed_results(tmp_path, records):
    agent = object.__new__(Orchestrator)
    agent._execute_actions = lambda *args: (records, [])
    result = agent._run_verification_command("python -V", tmp_path, None)
    assert result["returncode"] == 1
    assert result["output"]


def test_real_integer_success_is_accepted(tmp_path):
    agent = object.__new__(Orchestrator)
    record = {"returncode": 0, "output": ""}
    agent._execute_actions = lambda *args: ([record], [])
    assert agent._run_verification_command("python -V", tmp_path, None) == record
    assert agent._command_succeeded(record)


@pytest.mark.parametrize("focused_code,expected,calls_expected", [
    # T08: VERIFY no longer consumes iterations, so extra VERIFY (and gate)
    # calls run before the limit stops the loop. old -> new: 2 -> 4, 1 -> 3.
    (0, "tests_failed", 4), (1, "focused_tests_failed", 3),
])
def test_failure_identifies_the_actual_gate(tmp_path, monkeypatch, focused_code, expected, calls_expected):
    import ghost_agent.orchestrator as module
    task = tmp_path / "task.json"
    task.write_text(json.dumps({"id": "gate_failure", "title": "Gate failure",
        "goal": "Implement and test a function", "context": {},
        "constraints": {"test_cmd": "python -m pytest -q", "iteration_limit": 3}}))

    def model(_model, messages, **kwargs):
        planning = "Create the plan now" in messages[-1]["content"]
        return json.dumps({"thought": "test", "actions": [{"tool": "write_file",
            "path": "plan.md" if planning else "test_example.py",
            "content": "Test the behavior" if planning else "def test_example(): assert True\n"}]})

    monkeypatch.setattr(module, "ollama_chat", model)
    agent = Orchestrator(tmp_path, model="scripted")
    calls = []

    def gate(command, *args):
        calls.append(command)
        return {"returncode": focused_code if len(calls) == 1 else 1, "output": "gate output"}

    monkeypatch.setattr(agent, "_run_verification_command", gate)
    states = []
    write_state = agent._write_state

    def capture(path, state, *args):
        states.append(state["last_result"])
        return write_state(path, state, *args)

    monkeypatch.setattr(agent, "_write_state", capture)
    agent.run_task(task)
    assert expected in states
    assert len(calls) == calls_expected


@pytest.mark.parametrize("path,expected", [
    ("test_notes.md", False), ("test_asset.json", False),
    ("app_test.py", True), ("tests/test_app.py", True), ("app.py", False),
])
def test_focused_gate_only_selects_python_test_files(path, expected):
    state = {"files_touched": [path]}
    assert Orchestrator._has_task_test_change(state) is expected
    assert (Orchestrator._focused_test_command(state) is not None) is expected


def test_source_only_run_cannot_finish_as_tests_passed(tmp_path, monkeypatch):
    import ghost_agent.orchestrator as module
    task = tmp_path / "task.json"
    task.write_text(json.dumps({"id": "source_only", "title": "Function",
        "goal": "Implement a function", "context": {},
        "constraints": {"iteration_limit": 3}}))

    def model(_model, messages, **kwargs):
        planning = "Create the plan now" in messages[-1]["content"]
        return json.dumps({"thought": "implement", "actions": [{"tool": "write_file",
            "path": "plan.md" if planning else "app.py",
            "content": "Implement function" if planning else "def add(a,b): return a-b\n"}]})

    monkeypatch.setattr(module, "ollama_chat", model)
    agent = Orchestrator(tmp_path, model="scripted")
    states = []
    write_state = agent._write_state

    def capture(path, state, *args):
        states.append(state["last_result"])
        return write_state(path, state, *args)

    monkeypatch.setattr(agent, "_write_state", capture)
    monkeypatch.setattr(agent, "_run_verification_command",
                        lambda *args: pytest.fail("Source-only project must not run an implicit syntax gate"))
    agent.run_task(task)
    assert "verification_missing" in states
    assert "tests_passed" not in states


def test_gate_checkpoints_survive_interruption_and_redact_output(tmp_path, monkeypatch):
    import ghost_agent.orchestrator as module
    task = tmp_path / "task.json"
    task.write_text(json.dumps({"id": "interrupted_gates", "title": "Feature",
        "goal": "Implement with tests", "context": {},
        "constraints": {"iteration_limit": 4}}))

    def model(_model, messages, **kwargs):
        planning = "Create the plan now" in messages[-1]["content"]
        return json.dumps({"thought": "implement", "actions": [{"tool": "write_file",
            "path": "plan.md" if planning else "test_example.py",
            "content": "Plan" if planning else "def test_example(): assert True\n"}]})

    monkeypatch.setattr(module, "ollama_chat", model)
    agent = Orchestrator(tmp_path, model="scripted")
    calls = []
    secret = "sk-" + "x" * 30
    state_path = tmp_path / "workspaces" / "interrupted_gates" / "state.json"

    def gate(command, *args):
        calls.append(command)
        persisted = json.loads(state_path.read_text())
        assert persisted["verification_gates"][-1]["status"] == "running"
        if len(calls) == 1:
            return {"returncode": 0, "output": secret}
        raise KeyboardInterrupt("simulated interruption")

    monkeypatch.setattr(agent, "_run_verification_command", gate)
    with pytest.raises(KeyboardInterrupt):
        agent.run_task(task)
    saved = state_path.read_text()
    assert secret not in saved
    state = json.loads(saved)
    assert state["phase"] == "VERIFY"
    assert state["last_result"] != "tests_passed"
    assert [gate["status"] for gate in state["verification_gates"]] == ["passed", "running"]
    assert "result" not in state["verification_gates"][-1]
