import json

import pytest

from ghost_agent.knowledge import KnowledgeGraph


def test_new_fix_does_not_inherit_success_credit(tmp_path):
    graph = KnowledgeGraph(tmp_path)
    message = "TypeError invalid argument"
    for _ in range(3):
        graph.record_error("type_error", message, "Convert argument to integer", True)
    graph.record_error("type_error", message, "Delete validation entirely", False)
    assert len(graph.error_patterns) == 2
    assert graph.error_patterns[0].success_rate == 1
    assert graph.error_patterns[1].success_rate == 0
    assert graph.get_suggested_fix("type_error", message) == "Convert argument to integer"


def test_error_prefix_does_not_merge_different_messages(tmp_path):
    graph = KnowledgeGraph(tmp_path)
    prefix = "x" * 50
    graph.record_error("error", prefix + " first", "fix", True)
    graph.record_error("error", prefix + " second", "fix", False)
    assert len(graph.error_patterns) == 2


@pytest.mark.parametrize("worked", ["false", 1, 0, None, {}, []])
def test_ambiguous_verdicts_are_rejected(tmp_path, worked):
    graph = KnowledgeGraph(tmp_path)
    with pytest.raises(ValueError, match="boolean"):
        graph.record_error("error", "message", "fix", worked)
    assert graph.error_patterns == []
    assert not graph.graph_path.exists()


def test_invalid_loaded_repair_score_is_refused(tmp_path):
    graph = KnowledgeGraph(tmp_path)
    graph.record_error("error", "message", "fix", True)
    data = json.loads(graph.graph_path.read_text())
    data["error_patterns"][0]["success_rate"] = 99
    raw = json.dumps(data)
    graph.graph_path.write_text(raw)
    with pytest.raises(ValueError, match="repair-pattern"):
        KnowledgeGraph(tmp_path)
    assert graph.graph_path.read_text() == raw
