import pytest

from ghost_agent.knowledge import KnowledgeGraph


@pytest.mark.parametrize("raw", ["{", "[]", '{"tasks": {}}', '{"unexpected": 1}'])
def test_corrupt_store_does_not_load_as_empty(tmp_path, raw):
    path = tmp_path / "knowledge.json"
    path.write_text(raw)
    with pytest.raises(ValueError, match="corrupt"):
        KnowledgeGraph(tmp_path)
    assert path.read_text() == raw


def test_stale_instance_cannot_erase_new_history(tmp_path):
    first = KnowledgeGraph(tmp_path)
    stale = KnowledgeGraph(tmp_path)
    first.record_task("first", "goal", "tests_passed", "default", [], 1, 0)
    before = first.graph_path.read_bytes()
    with pytest.raises(ValueError, match="changed since load"):
        stale.record_task("stale", "goal", "tests_passed", "default", [], 1, 0)
    assert first.graph_path.read_bytes() == before
    assert [task.task_id for task in KnowledgeGraph(tmp_path).tasks] == ["first"]


def test_failed_atomic_replace_preserves_old_store(tmp_path, monkeypatch):
    import ghost_agent.knowledge as module
    graph = KnowledgeGraph(tmp_path)
    graph.save()
    before = graph.graph_path.read_bytes()

    def fail(*args):
        raise OSError("injected write failure")

    monkeypatch.setattr(module, "atomic_write", fail)
    with pytest.raises(OSError):
        graph.record_task("t", "goal", "tests_passed", "default", [], 1, 0)
    assert graph.graph_path.read_bytes() == before


def test_external_delete_is_not_silently_recreated(tmp_path):
    graph = KnowledgeGraph(tmp_path)
    graph.save()
    graph.graph_path.unlink()
    with pytest.raises(ValueError, match="changed since load"):
        graph.save()
    assert not graph.graph_path.exists()
