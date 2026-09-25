import json

import pytest

from ghost_agent.autonomy import OutcomeJob, OutcomeStore


@pytest.mark.parametrize("identifier", ["..", "../escape", "C:\\escape", "/escape", "a/b", "a:stream"])
def test_outcome_paths_reject_escape(tmp_path, identifier):
    store = OutcomeStore(tmp_path)
    with pytest.raises(ValueError):
        store.load(identifier)
    with pytest.raises(ValueError):
        store.save(OutcomeJob(identifier, "goal"))


def test_repeated_goal_creates_distinct_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr("ghost_agent.autonomy.time.strftime", lambda _: "20260101_000000")
    store = OutcomeStore(tmp_path)
    first = store.create("same goal")
    second = store.create("same goal")
    assert first.job_id != second.job_id
    assert len(store.list()) == 2


@pytest.mark.parametrize("content", ["{", json.dumps({"job_id": "different", "goal": "goal"})])
def test_invalid_record_is_not_overwritten(tmp_path, content):
    store = OutcomeStore(tmp_path)
    job = store.create("goal")
    path = store.path_for(job) / "outcome.json"
    path.write_text(content)
    with pytest.raises(ValueError):
        store.save(job)
    assert path.read_text() == content
