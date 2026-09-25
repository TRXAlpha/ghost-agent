import json

import pytest

from ghost_agent.context_manager import MemoryRecord, ValidatedMemoryStore


def memory(identifier, dependencies=(), **kwargs):
    return MemoryRecord(identifier, "semantic", "Evidence", "test", 1.0,
                        validation="validated", depends_on=list(dependencies), **kwargs)


def test_retraction_cascades_transitively_but_preserves_unrelated_memory(tmp_path):
    store = ValidatedMemoryStore(tmp_path)
    store.remember(memory("source"))
    store.remember(memory("derived", ["source"]))
    store.remember(memory("leaf", ["derived"]))
    store.remember(memory("other"))
    store.invalidate("source", "Evidence withdrawn")
    assert [item.memory_id for item in store.active()] == ["other"]
    records = json.loads(store.path.read_text())
    assert all(item["validation"] == "retracted" for item in records[:3])
    assert "source" in records[2]["invalidation_reason"]


def test_parent_expiry_removes_descendants_from_active_view(tmp_path, monkeypatch):
    monkeypatch.setattr("ghost_agent.context_manager.time.time", lambda: 100)
    store = ValidatedMemoryStore(tmp_path)
    store.remember(memory("source", expires_at=110))
    store.remember(memory("derived", ["source"]))
    monkeypatch.setattr("ghost_agent.context_manager.time.time", lambda: 111)
    assert store.active("validated") == []


@pytest.mark.parametrize("dependencies", [["missing"], ["new"], ["source", "source"]])
def test_invalid_dependencies_cannot_be_persisted(tmp_path, dependencies):
    store = ValidatedMemoryStore(tmp_path)
    store.remember(memory("source"))
    with pytest.raises(ValueError):
        store.remember(memory("new", dependencies))
    assert len(store.active()) == 1


def test_retracted_source_cannot_support_new_memory(tmp_path):
    store = ValidatedMemoryStore(tmp_path)
    store.remember(memory("source"))
    store.invalidate("source", "withdrawn")
    with pytest.raises(ValueError, match="active"):
        store.remember(memory("new", ["source"]))


def test_validated_memory_cannot_depend_on_unvalidated_memory(tmp_path):
    store = ValidatedMemoryStore(tmp_path)
    store.remember(MemoryRecord("source", "semantic", "Unverified", "test", 0.5))
    with pytest.raises(ValueError, match="validated"):
        store.remember(memory("new", ["source"]))


def test_corrupt_cycle_is_refused_and_preserved(tmp_path):
    store = ValidatedMemoryStore(tmp_path)
    store.remember(memory("source"))
    records = json.loads(store.path.read_text())
    records[0]["depends_on"] = ["source"]
    store.path.write_text(json.dumps(records))
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match="corrupt"):
        store.active()
    assert store.path.read_bytes() == before
