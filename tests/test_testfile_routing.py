import pytest

from ghost_agent.orchestrator import Orchestrator, ActionResponse


@pytest.mark.parametrize("path", ["test_sum.test.cjs", "test_assets/readme.md", "test_notes.md"])
def test_nonpython_test_named_files_do_not_require_pytest(path):
    agent = object.__new__(Orchestrator)
    agent._goal_prints = False
    action = ActionResponse.model_validate({"actions": [{"tool": "write_file", "path": path,
                                                         "content": "Non-Python content"}]}).actions[0]
    assert agent._validate_action_inputs(action) is None


@pytest.mark.parametrize("path", ["test_app.py", "app_test.py"])
def test_both_python_test_conventions_receive_same_gate(path):
    agent = object.__new__(Orchestrator)
    agent._goal_prints = False
    action = ActionResponse.model_validate({"actions": [{"tool": "write_file", "path": path,
                                                         "content": "x = 1"}]}).actions[0]
    assert "pytest-style" in agent._validate_action_inputs(action)
