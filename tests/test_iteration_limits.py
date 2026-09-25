import pytest

from ghost_agent.orchestrator import Task


@pytest.mark.parametrize("key", ["iteration_limit", "time_budget_iters"])
@pytest.mark.parametrize("value", [None, False, True, 0, -1, 1001, 1.5, "8", float("nan"), float("inf")])
def test_invalid_iteration_limits_are_rejected(key, value):
    with pytest.raises(ValueError, match="integer"):
        Task("t", "title", "goal", {key: value}, {})


def test_default_alias_and_explicit_precedence():
    assert Task("t", "title", "goal", {}, {}).iteration_limit() == 8
    assert Task("t", "title", "goal", {"time_budget_iters": 3}, {}).iteration_limit() == 3
    assert Task("t", "title", "goal", {"time_budget_iters": 3, "iteration_limit": 4}, {}).iteration_limit() == 4


def test_mutated_limit_is_revalidated():
    task = Task("t", "title", "goal", {}, {})
    task.constraints["iteration_limit"] = float("nan")
    with pytest.raises(ValueError):
        task.iteration_limit()
