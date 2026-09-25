import json

import pytest

from ghost_agent.utils import storage


def test_atomic_replace_failure_preserves_original_and_cleans_temporary(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    target.write_text('{"original": true}', encoding="utf-8")

    def fail(*args):
        raise OSError("injected replace failure")

    monkeypatch.setattr(storage.os, "replace", fail)
    with pytest.raises(OSError, match="injected"):
        storage.atomic_json(target, {"replacement": True})
    assert json.loads(target.read_text()) == {"original": True}
    assert sorted(path.name for path in tmp_path.iterdir()) == ["state.json"]


def test_atomic_json_round_trip(tmp_path):
    target = tmp_path / "new" / "state.json"
    storage.atomic_json(target, {"value": 1})
    storage.atomic_json(target, {"value": 2})
    assert json.loads(target.read_text()) == {"value": 2}
