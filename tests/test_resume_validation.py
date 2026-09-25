import json

import pytest

from ghost_agent.orchestrator import Orchestrator


@pytest.mark.parametrize("field,value", [("iteration", True), ("iteration", -1),
    ("prior_iterations", "3"), ("files_touched", [None]), ("phase", []),
    ("last_feedback", {}), ("last_result", False)])
def test_corrupt_checkpoint_refused(field, value):
    state = dict(phase="VERIFY", iteration=1, files_touched=[])
    state[field] = value
    before = dict(state)
    with pytest.raises(ValueError, match="checkpoint"):
        Orchestrator._prepare_resume(state)
    assert state == before


def test_legacy_resume_reverifies_completed_state(tmp_path, monkeypatch):
    agent = Orchestrator(tmp_path)
    workspace = agent._task_workspace("resume_case")
    workspace.mkdir(parents=True)
    (workspace / "task.json").write_text(json.dumps({"id": "resume_case", "goal": "goal"}))
    (workspace / "state.json").write_text(json.dumps(dict(phase="DONE", iteration=5,
        last_result="tests_passed", files_touched=["test_app.py"])))
    seen = []
    monkeypatch.setattr(agent, "_run_loop", lambda task, root, state: seen.append(state))
    agent.resume_task("resume_case")
    assert seen[0]["phase"] == "VERIFY"
    assert seen[0]["iteration"] == 0
    assert seen[0]["prior_iterations"] == 5


@pytest.mark.parametrize("path", ["../test_outside.py", "C:\\test_outside.py", "/test_outside.py", "test_x.py:stream"])
def test_checkpoint_test_paths_are_workspace_relative(path):
    state = dict(phase="VERIFY", iteration=2, files_touched=[path])
    with pytest.raises(ValueError):
        Orchestrator._prepare_resume(state)
    assert state["iteration"] == 2
