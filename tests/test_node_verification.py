import shutil
import json

import pytest

from ghost_agent.orchestrator import Orchestrator, Task
from ghost_agent.tools.cmd_tools import CmdError, join_command, run_cmd


def test_node_discovery_and_focused_selection(tmp_path):
    (tmp_path / "sum.test.cjs").write_text("// fixture")
    task = Task("t", "title", "goal", {}, {})
    assert Orchestrator._verification_command(task, tmp_path) == "node --test sum.test.cjs"
    state = {"files_touched": ["sum.test.cjs"]}
    assert Orchestrator._has_task_test_change(state)
    assert Orchestrator._focused_test_command(state) == "node --test sum.test.cjs"
    (tmp_path / "test_sum.py").write_text("# fixture")
    assert Orchestrator._verification_command(task, tmp_path) is None


@pytest.mark.skipif(not shutil.which("node"), reason="Node not installed")
@pytest.mark.parametrize("expected", [4, 5])
def test_real_node_gate(tmp_path, expected):
    path = tmp_path / "sum with spaces.test.cjs"
    path.write_text("const test = require('node:test'); const assert = require('node:assert/strict');"
                    f"test('addition', () => assert.equal(2+2, {expected}));")
    result = run_cmd(join_command(["node", "--test", path.name]), ".", tmp_path)
    assert (result["returncode"] == 0) is (expected == 4)
    assert "addition" in result["output"]


@pytest.mark.parametrize("command", ["node -e 1", "node --test", "node --test --watch", "npm test",
    "node --test missing.test.js", "node --test ../outside.test.js"])
def test_node_dispatch_is_scoped(tmp_path, command):
    with pytest.raises(CmdError):
        run_cmd(command, ".", tmp_path)


def test_node_modules_tests_are_not_default_verifiers(tmp_path):
    deps = tmp_path / "node_modules"
    deps.mkdir()
    (deps / "dependency.test.js").write_text("// fixture")
    assert Orchestrator._verification_command(Task("t", "title", "goal", {}, {}), tmp_path) is None


@pytest.mark.skipif(not shutil.which("node"), reason="Node not installed")
@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("node_pass", [False, True])
def test_orchestrator_completes_node_verification(tmp_path, monkeypatch, mixed, node_pass):
    import ghost_agent.orchestrator as module
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps({"id": "node_case", "title": "Node addition",
        "goal": "Implement addition with tests", "constraints": {"iteration_limit": 4}, "context": {}}))

    def model(_model, messages, **kwargs):
        planning = "Create the plan now" in messages[-1]["content"]
        actions = [{"tool": "write_file", "path": "plan.md", "content": "Implement and test addition"}] if planning else [
            {"tool": "write_file", "path": "sum.cjs", "content": "exports.add=(a,b)=>a+b;"},
            {"tool": "write_file", "path": "sum.test.cjs", "content":
             "const test=require('node:test'); const assert=require('node:assert/strict');"
             "const {add}=require('./sum.cjs'); test('add',()=>assert.equal(add(2,3),"
             + ("5" if node_pass else "99") + "));"}]
        if mixed and not planning:
            actions.append({"tool": "write_file", "path": "test_python.py",
                            "content": "def test_python(): assert 2+3 == 5\n"})
        return json.dumps({"thought": "implement", "actions": actions})

    monkeypatch.setattr(module, "ollama_chat", model)
    Orchestrator(tmp_path, model="scripted").run_task(task_path)
    state = json.loads((tmp_path / "workspaces" / "node_case" / "state.json").read_text())
    gates = state["verification_gates"]
    if not node_pass:
        assert state["last_result"] != "tests_passed"
        assert len(gates) == (2 if mixed else 1)
        assert all(gate["stage"] == "focused" for gate in gates)
        assert gates[-1]["result"]["returncode"] != 0
        return
    assert state["last_result"] == "tests_passed"
    assert len(gates) == (4 if mixed else 2)
    assert all(gate["result"]["returncode"] == 0 for gate in gates)
    if mixed:
        assert [gate["stage"] for gate in gates] == ["focused", "focused", "suite", "suite"]
        assert [gate["command"].split()[0] for gate in gates] == ["python", "node", "python", "node"]
    assert not (tmp_path / "sum.cjs").exists()
