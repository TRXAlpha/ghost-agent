"""T08: Only IMPLEMENT and REPAIR consume iterations; PLAN and VERIFY do not."""
import json
import pytest
from ghost_agent.orchestrator import Orchestrator


def _make_model(plan_actions, implement_actions):
    """Build a scripted model that distinguishes PLAN from IMPLEMENT/REPAIR turns."""
    call_count = [0]

    def model(_model, messages, **kwargs):
        call_count[0] += 1
        if call_count[0] > 20:
            raise AssertionError(f"Model called {call_count[0]} times — loop detected")
        content = messages[-1]["content"]
        if "Create the plan now" in content:
            return json.dumps({"thought": "plan", "actions": plan_actions})
        return json.dumps({"thought": "implement", "actions": implement_actions})

    return model, call_count


def test_plan_and_verify_do_not_consume_iterations(tmp_path, monkeypatch):
    """With limit=1, PLAN should not consume the only iteration.

    Under the old rule, PLAN incremented and left IMPLEMENT without a slot,
    producing iteration_limit. Under the new rule, the first IMPLEMENT runs
    and its result is verified.
    """
    import ghost_agent.orchestrator as module

    model, _ = _make_model(
        plan_actions=[{"tool": "write_file", "path": "plan.md", "content": "Steps"}],
        implement_actions=[
            {"tool": "write_file", "path": "app.py", "content": "answer = 42\n"},
            {"tool": "write_file", "path": "test_app.py",
             "content": "from app import answer\ndef test_answer():\n    assert answer == 42\n"},
        ],
    )
    monkeypatch.setattr(module, "ollama_chat", model)

    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps({
        "id": "plan_verify_count", "title": "Plan+Verify",
        "goal": "Return a constant", "context": {},
        "constraints": {"iteration_limit": 1}
    }))

    agent = Orchestrator(tmp_path, model="scripted")
    agent.run_task(task_path)

    state = json.loads((tmp_path / "workspaces" / "plan_verify_count" / "state.json").read_text())
    assert state["last_result"] == "tests_passed", \
        f"Expected tests_passed with limit=1, got {state['last_result']}"


def test_final_repair_is_verified(tmp_path, monkeypatch):
    """The last IMPLEMENT/REPAIR attempt is always verified, even at the limit.

    With limit=2: IMPLEMENT writes a failing test → VERIFY → REPAIR writes the
    fix → VERIFY → tests_passed.  The REPAIR (iteration 2) is not blocked by the
    limit check because the limit check is skipped while in VERIFY.
    """
    import ghost_agent.orchestrator as module

    turn = [0]

    def fix_model(_m, messages, **kwargs):
        turn[0] += 1
        content = messages[-1]["content"]
        if "Create the plan now" in content:
            return json.dumps({"thought": "plan", "actions": [
                {"tool": "write_file", "path": "plan.md", "content": "Steps"}
            ]})
        if turn[0] == 2:  # first non-plan turn = IMPLEMENT: broken code
            return json.dumps({"thought": "implement", "actions": [
                {"tool": "write_file", "path": "broken.py", "content": "x = 1 / 0\n"},
                {"tool": "write_file", "path": "test_broken.py",
                 "content": "import broken\ndef test_nonzero():\n    assert broken.x == 42\n"},
            ]})
        # later turn = REPAIR: fix the code
        return json.dumps({"thought": "repair", "actions": [
            {"tool": "write_file", "path": "broken.py", "content": "x = 42\n"},
        ]})

    monkeypatch.setattr(module, "ollama_chat", fix_model)

    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps({
        "id": "final_repair_verified", "title": "Repair Verified",
        "goal": "Implement", "context": {},
        "constraints": {"iteration_limit": 2}
    }))

    agent = Orchestrator(tmp_path, model="scripted")
    agent.run_task(task_path)

    state = json.loads((tmp_path / "workspaces" / "final_repair_verified" / "state.json").read_text())
    assert state["last_result"] == "tests_passed", \
        f"Expected tests_passed, got {state['last_result']}"
    assert state["iteration"] == 2, f"Expected iteration=2, got {state['iteration']}"


def test_iteration_limit_still_enforced(tmp_path, monkeypatch):
    """Iterations are still capped at the limit."""
    import ghost_agent.orchestrator as module

    model, call_count = _make_model(
        plan_actions=[{"tool": "write_file", "path": "plan.md", "content": "Steps"}],
        implement_actions=[
            {"tool": "write_file", "path": "app.py", "content": "x=1\n"},
            {"tool": "write_file", "path": "test_app.py",
             "content": "import app\ndef test_x():\n    assert app.x == 42\n"},
        ],
    )
    monkeypatch.setattr(module, "ollama_chat", model)

    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps({
        "id": "limit_enforced", "title": "Limit Test",
        "goal": "Return a constant", "context": {},
        "constraints": {"iteration_limit": 3}
    }))

    agent = Orchestrator(tmp_path, model="scripted")
    agent.run_task(task_path)

    state = json.loads((tmp_path / "workspaces" / "limit_enforced" / "state.json").read_text())
    assert state["last_result"] == "iteration_limit", \
        f"Expected iteration_limit, got {state['last_result']}"
    assert state["iteration"] == 3, f"Expected iteration=3, got {state['iteration']}"
    # PLAN (1) + 3 IMPLEMENT/REPAIR turns = at most 4 total calls
    assert call_count[0] <= 4, f"Too many model calls: {call_count[0]}"


def test_plan_failure_does_not_loop(tmp_path, monkeypatch):
    """A plan parse error is treated as a REPAIR; the run terminates cleanly."""
    import ghost_agent.orchestrator as module

    call_count = [0]

    def model(_model, messages, **kwargs):
        call_count[0] += 1
        if call_count[0] > 20:
            raise AssertionError(f"Model called {call_count[0]} times — loop detected")
        return "not valid json at all"  # parse error

    monkeypatch.setattr(module, "ollama_chat", model)

    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps({
        "id": "plan_fail", "title": "Plan Fail",
        "goal": "Implement", "context": {},
        "constraints": {"iteration_limit": 5}
    }))

    agent = Orchestrator(tmp_path, model="scripted")
    agent.run_task(task_path)

    state = json.loads((tmp_path / "workspaces" / "plan_fail" / "state.json").read_text())
    assert state["last_result"] != "tests_passed"
    # PLAN (1) + 5 REPAIR attempts (REPAIR does not increment on failure)
    assert call_count[0] <= 6, f"Expected ≤6 calls, got {call_count[0]}"