"""TC5: owner update policy and observation window (S08, R13)."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from ghost_agent.promotion import PromotionManager, PromotionService, load_update_policy
from ghost_agent.self_repair import SelfRepairManager, SelfRepairError


def make_sandbox(tmp_path: Path, name: str = "candidate") -> Path:
    sandbox = tmp_path / "workspace" / name
    sandbox.mkdir(parents=True, exist_ok=True)
    (sandbox / "candidate.py").write_text("value = 1\n", encoding="utf-8")
    return sandbox


def verified_manager(tmp_path: Path, sandbox: Path) -> tuple[SelfRepairManager, str]:
    manager = SelfRepairManager(tmp_path)
    proposal = manager.propose("Fix a crash", sandbox)
    verified = manager.verify_from_evaluator(
        proposal.proposal_id, lambda candidate: {"returncode": 0, "output": ""})
    assert verified.status == "verified"
    return manager, proposal.proposal_id


def test_load_update_policy_defaults_when_missing(tmp_path: Path) -> None:
    policy = load_update_policy(tmp_path)
    assert policy["version"] == 1
    assert policy["auto_activate"] is False
    assert policy["observation_window_seconds"] == 86400
    assert "ghost_agent/promotion.py" in policy["protected_paths"]


def test_load_update_policy_reads_owner_overrides(tmp_path: Path) -> None:
    policy_path = tmp_path / ".ghost" / "update_policy.json"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text(json.dumps({"observation_window_seconds": 5}), encoding="utf-8")
    policy = load_update_policy(tmp_path)
    assert policy["observation_window_seconds"] == 5
    assert policy["version"] == 1


def test_load_update_policy_malformed_json_fails_loud(tmp_path: Path) -> None:
    policy_path = tmp_path / ".ghost" / "update_policy.json"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ValueError, match="update_policy.json is unreadable or malformed"):
        load_update_policy(tmp_path)


def test_load_update_policy_non_object_fails_loud(tmp_path: Path) -> None:
    policy_path = tmp_path / ".ghost" / "update_policy.json"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    with pytest.raises(ValueError, match="update_policy.json is unreadable or malformed"):
        load_update_policy(tmp_path)


def test_self_repair_protected_path_refused(tmp_path: Path) -> None:
    sandbox = tmp_path / "workspace" / "candidate"
    sandbox.mkdir(parents=True)
    (sandbox / "ghost_agent" / "policy.py").parent.mkdir(parents=True)
    (sandbox / "ghost_agent" / "policy.py").write_text("value = 1\n", encoding="utf-8")
    manager = SelfRepairManager(tmp_path)
    proposal = manager.propose("Fix policy", sandbox)
    manager.verify_from_evaluator(proposal.proposal_id,
                                  lambda candidate: {"returncode": 0, "output": ""})
    with pytest.raises(ValueError, match="protected path requires the owner-controlled update process"):
        manager.request_promotion(proposal.proposal_id, "goal_1")


def test_normal_path_promotion_request_allowed(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    manager, proposal_id = verified_manager(tmp_path, sandbox)
    _, _, files = manager.request_promotion(proposal_id, "goal_1")
    assert files == ["candidate.py"]


def test_promotion_status_moves_observing_to_operationally_verified(
        tmp_path: Path, monkeypatch) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "new.py").write_text("new", encoding="utf-8")
    from ghost_agent.approvals import ApprovalStore
    service = PromotionService(tmp_path)
    tx, approval, _ = service.request("goal_1", sandbox)
    service.approvals.decide(approval, True)
    fake_now = 1_000_000.0
    monkeypatch.setattr("ghost_agent.promotion.time.time", lambda: fake_now)
    promotion_id = service.commit(tx, approval)
    # Window has not passed: observing.
    record = next(r for r in service.promotions.list()
                  if r["promotion_id"] == promotion_id)
    assert record["status"] == "verified"
    assert record["observation"] == "observing"
    # Patch time past the window: operationally_verified, computed on read.
    monkeypatch.setattr("ghost_agent.promotion.time.time", lambda: fake_now + 86401.0)
    record = next(r for r in service.promotions.list()
                  if r["promotion_id"] == promotion_id)
    assert record["observation"] == "operationally_verified"


def test_rollback_journal_unchanged_by_observation(tmp_path: Path) -> None:
    """The observation window is a read-time view, not a journal status change."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "new.py").write_text("new", encoding="utf-8")
    service = PromotionService(tmp_path)
    tx, approval, _ = service.request("goal_1", sandbox)
    service.approvals.decide(approval, True)
    promotion_id = service.commit(tx, approval)
    journal = json.loads(
        (service.promotions.backups / promotion_id / "journal.json").read_text())
    assert journal["status"] == "verified"
    assert journal["observe_until"] > journal["applied_at"]
