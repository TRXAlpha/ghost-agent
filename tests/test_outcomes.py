from pathlib import Path

from ghost_agent.autonomy import OutcomeStore


def test_outcome_contract_is_persistent_and_attached_to_task_context(tmp_path: Path) -> None:
    store = OutcomeStore(tmp_path)
    job = store.create("Build a calculator")
    payload = store.make_task_payload(job, "evidence", 4)

    assert store.load(job.job_id).acceptance_contract["sandbox_only"] is True
    assert payload["context"]["acceptance_contract"]["success_condition"] == "verification reports tests_passed"
