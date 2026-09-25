"""Tests for ghost_agent.parallel — task decomposition and parallel agents."""
from pathlib import Path

from ghost_agent.parallel import (
    SubTask,
    Decomposition,
    TaskDecomposer,
    ParallelRunner,
)


def test_decompose_single_task() -> None:
    """A simple task doesn't get decomposed."""
    result = TaskDecomposer.decompose("add a login function")
    assert len(result.sub_tasks) == 1
    assert result.sub_tasks[0].goal == "add a login function"


def test_decompose_numbered_list() -> None:
    """Numbered lists get decomposed."""
    goal = "1. Add user authentication\n2. Write tests for auth\n3. Update README"
    result = TaskDecomposer.decompose(goal)
    assert len(result.sub_tasks) >= 2


def test_decompose_with_and() -> None:
    """'and' conjunctions with action verbs get decomposed."""
    goal = "add user authentication and write tests for it"
    result = TaskDecomposer.decompose(goal)
    assert len(result.sub_tasks) >= 2


def test_decompose_semicolons() -> None:
    """Semicolons separate sub-tasks."""
    goal = "add auth to the API; write tests; update docs"
    result = TaskDecomposer.decompose(goal)
    assert len(result.sub_tasks) == 3


def test_decompose_dependencies() -> None:
    """Test tasks depend on implement tasks."""
    goal = "1. Add user authentication\n2. Write tests for auth"
    result = TaskDecomposer.decompose(goal)
    
    # The test task should depend on the implement task
    test_tasks = [st for st in result.sub_tasks if "test" in st.goal.lower()]
    impl_tasks = [st for st in result.sub_tasks if "add" in st.goal.lower()]
    
    if test_tasks and impl_tasks:
        assert len(test_tasks[0].depends_on) > 0


def test_decompose_merge_strategy() -> None:
    """Parallel tasks get parallel merge strategy."""
    goal = "add auth; write tests"  # This creates dependencies
    result = TaskDecomposer.decompose(goal)
    # Should be parallel or hybrid depending on dependencies
    assert result.merge_strategy in ("parallel", "hybrid")


def test_sub_task_dataclass() -> None:
    """SubTask dataclass works correctly."""
    st = SubTask(
        id="sub_001",
        goal="add feature",
        depends_on=["sub_000"],
        files_to_touch=["api.py"],
    )
    assert st.id == "sub_001"
    assert st.result is None
    assert st.success is False


def test_decomposition_dataclass() -> None:
    """Decomposition dataclass works correctly."""
    d = Decomposition(
        original_goal="complex task",
        sub_tasks=[SubTask(id="s1", goal="part 1")],
        merge_strategy="sequential",
    )
    assert d.original_goal == "complex task"
    assert len(d.sub_tasks) == 1


def test_extract_parts_complex() -> None:
    """Extract parts from complex goals."""
    # Multiple action verbs with "and"
    parts = TaskDecomposer._extract_parts(
        "add a login endpoint and create user registration"
    )
    assert len(parts) >= 2


def test_extract_parts_simple() -> None:
    """Simple goals return single part."""
    parts = TaskDecomposer._extract_parts("fix the bug")
    assert len(parts) == 1


def test_format_results() -> None:
    """Format parallel results."""
    completed = {
        "sub_001": SubTask(
            id="sub_001", goal="part 1", result="tests_passed",
            files_changed=["a.py"], iterations=3, success=True,
        ),
        "sub_002": SubTask(
            id="sub_002", goal="part 2", result="iteration_limit",
            files_changed=[], iterations=8, success=False, error="timed out",
        ),
    }
    output = ParallelRunner.format_results(completed)
    assert "1/2 sub-tasks succeeded" in output
    assert "[OK]" in output
    assert "[FAIL]" in output
    assert "timed out" in output
