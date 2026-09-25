import json

import pytest

from ghost_agent.autonomy import OutcomeJob, OutcomeStore


@pytest.mark.parametrize("field,value", [("created_at", "now"), ("updated_at", float("nan")),
    ("iterations", True), ("iterations", 0), ("skip_research", "false"),
    ("goal", ""), ("errors", "error"), ("acceptance_contract", []), ("result", {})])
def test_invalid_outcome_fields_refused(field, value):
    args = dict(job_id="outcome_test", goal="goal")
    args[field] = value
    with pytest.raises((ValueError, TypeError)):
        OutcomeJob(**args)


def test_mutated_outcome_is_not_saved(tmp_path):
    store = OutcomeStore(tmp_path)
    job = store.create("goal")
    path = store.path_for(job) / "outcome.json"
    before = path.read_bytes()
    job.skip_research = "false"
    with pytest.raises(ValueError):
        store.save(job)
    assert path.read_bytes() == before


def test_invalid_timestamp_cannot_break_listing(tmp_path):
    store = OutcomeStore(tmp_path)
    bad = store.create("bad")
    good = store.create("good")
    path = store.path_for(bad) / "outcome.json"
    raw = json.loads(path.read_text())
    raw["created_at"] = "invalid"
    path.write_text(json.dumps(raw))
    assert [job.job_id for job in store.list()] == [good.job_id]
    with pytest.raises(ValueError):
        store.load(bad.job_id)
