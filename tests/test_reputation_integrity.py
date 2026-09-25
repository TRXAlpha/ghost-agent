import json

import pytest

from ghost_agent.models.routing import ModelReputationStore
from ghost_agent.utils.storage import exclusive_lock


@pytest.mark.parametrize("metrics", [
    {"verified_success": "false"}, {"verified_success": 1},
    {"verification_failure": "no"}, {"repair_attempted": 1},
    {"latency_seconds": float("nan")}, {"latency_seconds": float("inf")},
    {"estimated_cost": -1}, {"context_chars": True}, {"context_chars": 2.5},
    {"model": "m|testing"}, {"task_class": ""},
])
def test_invalid_metrics_cannot_change_existing_reputation(tmp_path, metrics):
    store = ModelReputationStore(tmp_path)
    store.record("model", "coding", True)
    before = store.path.read_bytes()
    args = {"model": "model", "task_class": "coding", "verified_success": True, **metrics}
    with pytest.raises(ValueError):
        store.record(**args)
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("content", ["{broken", "[]", '{"x":null}', '{"x":{}}'])
def test_corrupt_reputation_is_not_discarded(tmp_path, content):
    store = ModelReputationStore(tmp_path)
    store.path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt"):
        store.record("model", "coding", True)
    with pytest.raises(ValueError, match="corrupt"):
        store.score("model", "coding")
    assert store.path.read_text(encoding="utf-8") == content


@pytest.mark.parametrize("field,value", [("successes", 2), ("runs", True),
    ("latency_seconds_total", float("nan")), ("repair_attempts", -1)])
def test_invalid_aggregates_cannot_influence_routing(tmp_path, field, value):
    store = ModelReputationStore(tmp_path)
    store.record("model", "coding", True)
    data = json.loads(store.path.read_text(encoding="utf-8"))
    data["model|coding"][field] = value
    store.path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt"):
        store.score("model", "coding")


def test_reputation_updates_are_serialized(tmp_path):
    store = ModelReputationStore(tmp_path)
    store.record("model", "coding", True)
    before = store.path.read_bytes()
    with exclusive_lock(store.path.with_suffix(".lock")):
        with pytest.raises(BlockingIOError):
            store.record("model", "coding", False)
    assert store.path.read_bytes() == before
    store.record("model", "coding", False)
    assert json.loads(store.path.read_text(encoding="utf-8"))["model|coding"]["runs"] == 2


def test_legacy_optional_metrics_are_initialized_without_losing_history(tmp_path):
    store = ModelReputationStore(tmp_path)
    store.path.write_text(json.dumps({"model|coding": {"model": "model", "task_class": "coding",
        "runs": 1, "successes": 1, "verification_failures": 0, "latency_seconds_total": 2}}), encoding="utf-8")
    store.record("model", "coding", False, context_chars=100)
    item = json.loads(store.path.read_text(encoding="utf-8"))["model|coding"]
    assert item["runs"] == 2 and item["successes"] == 1 and item["context_chars_total"] == 100
