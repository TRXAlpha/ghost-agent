from pathlib import Path
import json

import pytest

from ghost_agent.self_repair import SelfRepairManager


def test_self_repair_requires_verified_regression_before_promotion_request(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "candidate.py").write_text("value = 1\n", encoding="utf-8")
    manager = SelfRepairManager(tmp_path)
    proposal = manager.propose("Fix parser", sandbox)

    with pytest.raises(PermissionError):
        manager.request_promotion(proposal.proposal_id, "goal_1")
    manager.record_verification(proposal.proposal_id, [{"returncode": 0}], [{"returncode": 0}])
    transaction, approval, files = manager.request_promotion(proposal.proposal_id, "goal_1")
    assert transaction.startswith("tx_") and approval.startswith("approval_") and files == ["candidate.py"]


@pytest.mark.parametrize("regression,benchmark", [
    ([], []), ([], [{"returncode": 0}]), ([{"returncode": 0}], []),
    ([{"returncode": False}], [{"returncode": 0}]),
    ([{"returncode": 0.0}], [{"returncode": 0}]),
    ([{"returncode": "0"}], [{"returncode": 0}]),
    ([{}], [{"returncode": 0}]), ([None], [{"returncode": 0}]),
    ([{"returncode": 0}], [{"returncode": 1}]),
])
def test_missing_or_invalid_verification_cannot_request_promotion(tmp_path, regression, benchmark):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    manager = SelfRepairManager(tmp_path)
    proposal = manager.propose("Fix parser", sandbox)
    result = manager.record_verification(proposal.proposal_id, regression, benchmark)
    assert result.status == "rejected"
    assert manager.get(proposal.proposal_id).status == "rejected"
    with pytest.raises(PermissionError):
        manager.request_promotion(proposal.proposal_id, "goal_1")


@pytest.mark.parametrize("mutation", ["edit", "add", "delete", "live"])
def test_verification_cannot_be_reused_for_changed_patch(tmp_path, mutation):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    candidate = sandbox / "candidate.py"
    candidate.write_text("value = 1\n", encoding="utf-8")
    manager = SelfRepairManager(tmp_path)
    proposal = manager.propose("Fix parser", sandbox)
    verified = manager.record_verification(proposal.proposal_id, [{"returncode": 0}], [{"returncode": 0}])
    assert verified.verified_snapshot
    if mutation == "edit":
        candidate.write_text("value = 2\n", encoding="utf-8")
    elif mutation == "add":
        (sandbox / "extra.py").write_text("value = 3\n", encoding="utf-8")
    elif mutation == "delete":
        candidate.unlink()
    else:
        (tmp_path / "candidate.py").write_text("user_edit = 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed since verification"):
        manager.request_promotion(proposal.proposal_id, "goal_1")
    assert not (tmp_path / ".ghost" / "approvals.json").exists()


def test_legacy_verified_record_without_content_binding_requires_reverification(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    manager = SelfRepairManager(tmp_path)
    proposal = manager.propose("Fix parser", sandbox)
    records = json.loads(manager.path.read_text(encoding="utf-8"))
    records[0].pop("verified_snapshot")
    records[0]["status"] = "verified"
    manager.path.write_text(json.dumps(records), encoding="utf-8")
    with pytest.raises(PermissionError):
        manager.request_promotion(proposal.proposal_id, "goal_1")


@pytest.mark.parametrize("content", ["{broken", "{}", "[null]", '[{"proposal_id": "bad"}]'])
def test_corrupt_repair_records_are_preserved_not_overwritten(tmp_path, content):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    manager = SelfRepairManager(tmp_path)
    manager.path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="preserve"):
        manager.propose("Fix parser", sandbox)
    assert manager.path.read_text(encoding="utf-8") == content


def test_successful_commands_without_a_patch_do_not_verify(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    manager = SelfRepairManager(tmp_path)
    proposal = manager.propose("Fix parser", sandbox)
    result = manager.record_verification(proposal.proposal_id, [{"returncode": 0}], [{"returncode": 0}])
    assert result.status == "rejected"
