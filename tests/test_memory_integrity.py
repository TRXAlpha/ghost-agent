import json

import pytest

from ghost_agent.context_manager import MemoryRecord, ValidatedMemoryStore
from ghost_agent.utils.storage import exclusive_lock


def record(**changes):
    return MemoryRecord(**{"memory_id": "m1", "kind": "semantic", "content": "Evidence",
                           "source": "test", "confidence": 0.9, "validation": "validated", **changes})


def test_duplicate_id_cannot_survive_invalidation(tmp_path):
    store = ValidatedMemoryStore(tmp_path)
    store.remember(record())
    with pytest.raises(ValueError, match="already exists"):
        store.remember(record(content="Conflicting evidence"))
    first = store.invalidate("m1", "Source withdrawn")
    second = store.invalidate("m1", "Another reason")
    assert first == second
    assert first.validation == "retracted"
    assert first.invalidation_reason == "Source withdrawn"
    assert store.active() == []
    with pytest.raises(ValueError, match="already exists"):
        store.remember(record())


def test_retracted_label_without_timestamp_is_never_active(tmp_path):
    store = ValidatedMemoryStore(tmp_path)
    store.remember(record(validation="retracted"))
    assert store.active() == []
    assert store.active("retracted") == []


@pytest.mark.parametrize("changes", [{"confidence": True}, {"confidence": float("nan")},
    {"expires_at": float("inf")}, {"created_at": None}, {"invalidated_at": -1},
    {"source": ""}, {"memory_id": ""}, {"supporting_artifacts": "not a list"}])
def test_invalid_memory_is_not_persisted(tmp_path, changes):
    store = ValidatedMemoryStore(tmp_path)
    with pytest.raises(ValueError):
        store.remember(record(**changes))
    assert not store.path.exists()


@pytest.mark.parametrize("content", ["{broken", "{}", "[null]"])
def test_corrupt_memory_is_preserved(tmp_path, content):
    store = ValidatedMemoryStore(tmp_path)
    store.path.write_text(content)
    with pytest.raises(ValueError, match="corrupt"):
        store.active()
    with pytest.raises(ValueError, match="corrupt"):
        store.remember(record())
    assert store.path.read_text() == content


def test_legacy_duplicate_ids_fail_closed(tmp_path):
    store = ValidatedMemoryStore(tmp_path)
    store.remember(record())
    data = json.loads(store.path.read_text())
    store.path.write_text(json.dumps(data + data))
    with pytest.raises(ValueError, match="corrupt"):
        store.active("validated")


def test_memory_mutations_respect_process_lock(tmp_path):
    store = ValidatedMemoryStore(tmp_path)
    store.remember(record())
    before = store.path.read_bytes()
    with exclusive_lock(store.path.with_suffix(".lock")):
        with pytest.raises(BlockingIOError):
            store.invalidate("m1", "retracted")
    assert store.path.read_bytes() == before
    store.invalidate("m1", "retracted")
    assert not store.active()
