import json

import pytest

from ghost_agent.knowledge import KnowledgeGraph


@pytest.mark.parametrize("result", ["no_test_cmd", "plan_ok", "implement_ok", "emergency_stop",
                                   "command_budget_exhausted", "command_budget_invalid", "unknown"])
def test_unassessed_outcomes_remain_history_not_strategy_scores(tmp_path, result):
    graph = KnowledgeGraph(tmp_path)
    graph.record_task("t", "add feature X", result, "plan_first", [], 3, 0)
    assert len(graph.tasks) == 1
    assert graph.tasks[0].success is False
    assert graph.strategies == {}
    assert "success_rate" not in graph.get_task_suggestions("add feature X")
    reloaded = KnowledgeGraph(tmp_path)
    assert len(reloaded.tasks) == 1
    assert reloaded.strategies == {}


def test_legacy_false_success_is_not_recredited_on_load(tmp_path):
    graph = KnowledgeGraph(tmp_path)
    graph.record_task("t", "add feature X", "no_test_cmd", "plan_first", [], 3, 0)
    # Locate the store produced by the graph, not a guessed storage filename.
    path = next(tmp_path.glob("*.json"))
    data = json.loads(path.read_text())
    data["tasks"][0]["success"] = True
    data["strategies"] = {"plan_first:feature": {"strategy": "plan_first", "task_type": "feature",
        "success_count": 99, "failure_count": 0, "avg_iterations": 3, "last_used": 1}}
    raw = json.dumps(data)
    path.write_text(raw)
    reloaded = KnowledgeGraph(tmp_path)
    assert reloaded.tasks[0].success is False
    assert reloaded.strategies == {}
    assert path.read_text() == raw  # Read-only load preserves the legacy file.


def test_identical_completion_is_scored_once(tmp_path):
    graph = KnowledgeGraph(tmp_path)
    for _ in range(3):
        graph.record_task("t", "add feature X", "tests_passed", "plan_first", [], 3, 0)
    assert len(graph.tasks) == 1
    assert sum(item.success_count for item in graph.strategies.values()) == 1
    # Preserve duplicate historical records, but exclude their duplicate credit.
    data = json.loads(graph.graph_path.read_text())
    duplicate = dict(data["tasks"][0], timestamp=data["tasks"][0]["timestamp"] + 1)
    data["tasks"].append(duplicate)
    graph.graph_path.write_text(json.dumps(data))
    reloaded = KnowledgeGraph(tmp_path)
    assert len(reloaded.tasks) == 2
    assert sum(item.success_count for item in reloaded.strategies.values()) == 1
    assert reloaded.get_task_suggestions("add feature X")["similar_tasks"] == 1


@pytest.mark.parametrize("field,value", [("iterations", True), ("parse_errors", -1),
    ("goal", None), ("tags", "invalid"), ("files_touched", [3])])
def test_invalid_new_task_is_not_saved(tmp_path, field, value):
    graph = KnowledgeGraph(tmp_path)
    args = dict(task_id="t", goal="goal", result="tests_passed", strategy="default",
                files_touched=[], iterations=1, parse_errors=0, tags=[])
    args[field] = value
    with pytest.raises(ValueError, match="task record"):
        graph.record_task(**args)
    assert graph.tasks == []
    assert not graph.graph_path.exists()
