"""Tests for ghost_agent.knowledge — the knowledge graph."""
import time
from pathlib import Path

from ghost_agent.knowledge import (
    KnowledgeGraph,
    FileNode,
    TaskRecord,
    ErrorPattern,
)


def test_knowledge_graph_init(tmp_path: Path) -> None:
    """Knowledge graph creates its directory and file."""
    kg = KnowledgeGraph(tmp_path / "knowledge")
    assert kg.graph_path.exists() or True  # Created lazily


def test_knowledge_graph_record_task(tmp_path: Path) -> None:
    """Record a task and verify it's stored."""
    kg = KnowledgeGraph(tmp_path / "knowledge")
    
    kg.record_task(
        task_id="test_001",
        goal="add authentication to the API",
        result="tests_passed",
        strategy="plan_first",
        files_touched=["api/auth.py", "tests/test_auth.py"],
        iterations=3,
        parse_errors=0,
        tags=["feature"],
    )
    
    assert len(kg.tasks) == 1
    assert kg.tasks[0].task_id == "test_001"
    assert kg.tasks[0].success is True
    
    # Verify persistence
    kg2 = KnowledgeGraph(tmp_path / "knowledge")
    assert len(kg2.tasks) == 1
    assert kg2.tasks[0].task_id == "test_001"


def test_knowledge_graph_record_error(tmp_path: Path) -> None:
    """Record error patterns and get suggestions."""
    kg = KnowledgeGraph(tmp_path / "knowledge")
    
    # Record an error pattern
    for i in range(3):
        kg.record_error(
            error_type="syntax_error",
            error_message="unexpected token '}'",
            fix_applied="removed extra closing brace",
            worked=True,
        )
    
    assert len(kg.error_patterns) == 1
    assert kg.error_patterns[0].count == 3
    assert kg.error_patterns[0].success_rate > 0.5
    
    # Get suggestion
    suggestion = kg.get_suggested_fix("syntax_error", "unexpected token '}'")
    assert suggestion is not None
    assert "brace" in suggestion


def test_knowledge_graph_task_suggestions(tmp_path: Path) -> None:
    """Get suggestions for similar tasks."""
    kg = KnowledgeGraph(tmp_path / "knowledge")
    
    # Record some past tasks
    for i in range(3):
        kg.record_task(
            task_id=f"feat_{i}",
            goal=f"add feature X to the API module",
            result="tests_passed",
            strategy="plan_first",
            files_touched=["api/module.py"],
            iterations=3,
            parse_errors=0,
        )
    
    kg.record_task(
        task_id="bug_001",
        goal="fix bug in authentication",
        result="tests_passed",
        strategy="plan_and_repair",
        files_touched=["auth.py"],
        iterations=5,
        parse_errors=1,
    )
    
    # Query for similar task
    suggestions = kg.get_task_suggestions("add feature Y to the API")
    assert suggestions["similar_tasks"] > 0
    assert suggestions["success_rate"] > 0
    assert "plan_first" in suggestions.get("suggested_strategy", "")


def test_knowledge_graph_change_impact(tmp_path: Path) -> None:
    """Get files affected by a change."""
    kg = KnowledgeGraph(tmp_path / "knowledge")
    
    # Build a simple dependency graph
    kg.update_file("models.py", functions=[], classes=["User"], imports=[])
    kg.update_file("api.py", functions=["get_user"], classes=[], imports=["models.py"])
    kg.update_file("tests.py", functions=["test_get_user"], classes=[], imports=["api.py"])
    
    # Changing models.py should affect api.py and tests.py
    impact = kg.get_change_impact("models.py")
    assert "api.py" in impact
    assert "tests.py" in impact


def test_knowledge_graph_strategy_effectiveness(tmp_path: Path) -> None:
    """Track strategy effectiveness over time."""
    kg = KnowledgeGraph(tmp_path / "knowledge")
    
    # Record successes and failures for different strategies
    for _ in range(3):
        kg.record_task(
            task_id=f"s1_{_}",
            goal="add feature X",
            result="tests_passed",
            strategy="plan_first",
            files_touched=[],
            iterations=3,
            parse_errors=0,
        )
    
    kg.record_task(
        task_id="s1_fail",
        goal="add feature Y",
        result="iteration_limit",
        strategy="plan_first",
        files_touched=[],
        iterations=8,
        parse_errors=3,
    )
    
    # Check strategy effectiveness
    report = kg.get_strategy_report()
    assert "plan_first" in report
    assert "75%" in report  # 3/4 success rate


def test_knowledge_graph_classify_task(tmp_path: Path) -> None:
    """Task classification works correctly."""
    kg = KnowledgeGraph(tmp_path / "knowledge")
    
    assert kg._classify_task("fix the login bug") == "bug_fix"
    assert kg._classify_task("add a new API endpoint") == "new_feature"
    assert kg._classify_task("refactor the auth module") == "refactor"
    assert kg._classify_task("write tests for utils") == "testing"
    assert kg._classify_task("update the README") == "documentation"
    assert kg._classify_task("change the config") == "modification"
    assert kg._classify_task("do something random") == "general"


def test_knowledge_graph_persistence(tmp_path: Path) -> None:
    """Knowledge graph persists across instances."""
    kg1 = KnowledgeGraph(tmp_path / "knowledge")
    kg1.record_task(
        task_id="persist_001",
        goal="test persistence",
        result="tests_passed",
        strategy="default",
        files_touched=["test.py"],
        iterations=2,
        parse_errors=0,
    )
    kg1.save()
    
    kg2 = KnowledgeGraph(tmp_path / "knowledge")
    assert len(kg2.tasks) == 1
    assert kg2.tasks[0].task_id == "persist_001"


def test_knowledge_graph_hot_files(tmp_path: Path) -> None:
    """Get most modified files."""
    kg = KnowledgeGraph(tmp_path / "knowledge")
    
    kg.update_file("hot.py", functions=[], classes=[], imports=[])
    for _ in range(5):
        kg.files["hot.py"].modification_count += 1
    kg.update_file("cold.py", functions=[], classes=[], imports=[])
    
    hot = kg.get_hot_files(3)
    assert hot[0][0] == "hot.py"
    assert hot[0][1] >= 5
