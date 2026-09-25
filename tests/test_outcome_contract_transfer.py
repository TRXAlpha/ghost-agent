import pytest

from ghost_agent.autonomy import OutcomeStore
from ghost_agent.orchestrator import Task, Orchestrator


def test_required_files_reach_enforced_task_constraints(tmp_path):
    store = OutcomeStore(tmp_path)
    job = store.create("Create app")
    job.acceptance_contract["required_files"] = ["app.js"]
    payload = store.make_task_payload(job, "evidence", 4)
    task = Task(payload["id"], payload["title"], payload["goal"], payload["constraints"], payload["context"])
    assert Orchestrator._missing_required_files(task, tmp_path) == ["app.js"]
    job.acceptance_contract["required_files"].append("later.js")
    assert task.required_files() == ["app.js"]
    assert payload["context"]["acceptance_contract"]["required_files"] == ["app.js"]


def test_invalid_persisted_requirement_is_not_silently_dropped(tmp_path):
    store = OutcomeStore(tmp_path)
    job = store.create("Create app")
    job.acceptance_contract["required_files"] = "app.py"
    payload = store.make_task_payload(job, "", 4)
    with pytest.raises(ValueError):
        Task(payload["id"], payload["title"], payload["goal"], payload["constraints"], payload["context"])
