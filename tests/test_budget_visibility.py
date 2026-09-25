from ghost_agent.command_budget import CommandBudget
from ghost_agent.control_plane import GhostSDK


def test_snapshot_exposes_budget_without_changing_it(tmp_path):
    sdk = GhostSDK(tmp_path)
    job = sdk.outcomes.create("goal")
    path = sdk.outcomes.path_for(job) / "run" / "command_budget.json"
    with CommandBudget(path, calls=2).slot():
        pass
    before = path.read_bytes()
    summary = sdk.snapshot()["command_budgets"][0]
    assert summary["job_id"] == job.job_id
    assert summary["budget"]["remaining_calls"] == 1
    assert path.read_bytes() == before


def test_missing_and_corrupt_budgets_are_distinct(tmp_path):
    sdk = GhostSDK(tmp_path)
    job = sdk.outcomes.create("goal")
    assert sdk.command_budgets()[0]["status"] == "not_started"
    path = sdk.outcomes.path_for(job) / "run" / "command_budget.json"
    path.parent.mkdir()
    path.write_text("{bad")
    assert sdk.command_budgets()[0]["status"] == "invalid"
    assert path.read_text() == "{bad"
