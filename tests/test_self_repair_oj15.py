"""OJ15: S08 self-improvement — benefit/linkage, scope, loop prevention,
apply/rollback bound to REAL promotion transactions.

Positive verification uses ``verify_from_evaluator`` with a real subprocess
evaluator. ``mark_applied`` / ``mark_rollback`` are bound to real promotion
journal records created through the promotion service, never a bare string.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ghost_agent.self_repair import SelfRepairManager
from ghost_agent.promotion import PromotionService


def make_sandbox(tmp_path, rel="workspace/candidate"):
    sandbox = tmp_path / rel
    sandbox.mkdir(parents=True, exist_ok=True)
    (sandbox / "candidate.py").write_text("value = 1\n", encoding="utf-8")
    return sandbox


def _run(candidate: Path, code: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=str(candidate),
        capture_output=True, text=True, timeout=60,
        env={"PATH": os.environ.get("PATH", "")},
    )
    return {"returncode": proc.returncode,
            "output": ((proc.stdout or "") + (proc.stderr or ""))[-4000:]}


def evaluator_ok(candidate: Path) -> dict:
    return _run(candidate, "import sys; sys.exit(0)")


def verified(manager, proposal_id):
    return manager.verify_from_evaluator(proposal_id, evaluator_ok)


def seed_promotion(tmp_path, then_rollback=False):
    """Create a real promotion journal record under tmp_path/.ghost/promotions.

    Returns (promotion_id, service). The journal is in "verified" state unless
    then_rollback is set, in which case it ends "rolled_back".
    """
    sandbox = tmp_path / "seed_sandbox"
    sandbox.mkdir(exist_ok=True)
    (sandbox / "seed.py").write_text("seed = 1\n", encoding="utf-8")
    service = PromotionService(tmp_path)
    tx, approval, _ = service.request("goal_seed", sandbox)
    service.approvals.decide(approval, True)
    promotion_id = service.commit(tx, approval)
    if then_rollback:
        service.rollback(promotion_id)
    return promotion_id, service


def test_propose_records_benefit_and_linked_failure(tmp_path: Path):
    sandbox = make_sandbox(tmp_path)
    manager = SelfRepairManager(tmp_path)
    p = manager.propose("Fix parser crash", sandbox,
                        proposed_benefit="stops recurring timeout failures",
                        linked_failure="outcome_x failed with IndexError")
    assert p.proposed_benefit == "stops recurring timeout failures"
    assert p.linked_failure == "outcome_x failed with IndexError"
    assert manager.get(p.proposal_id).diagnosis == "Fix parser crash"


def test_candidate_scope_rejects_ghost_authority_dir(tmp_path: Path):
    manager = SelfRepairManager(tmp_path)
    sandbox = tmp_path / ".ghost" / "outcomes" / "candidate"
    sandbox.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError, match=".ghost"):
        manager.propose("Fix parser", sandbox)


def test_candidate_scope_rejects_source_tree(tmp_path: Path):
    manager = SelfRepairManager(tmp_path)
    sandbox = tmp_path / "ghost_agent" / "candidate"
    sandbox.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError, match="source tree|ghost_agent"):
        manager.propose("Fix parser", sandbox)


def test_endless_repair_loop_prevented_on_rejected(tmp_path: Path):
    sandbox = make_sandbox(tmp_path)
    manager = SelfRepairManager(tmp_path)
    p = manager.propose("Fix recurring flake", sandbox)
    assert manager.record_verification(p.proposal_id, [], []).status == "rejected"
    with pytest.raises(ValueError, match="repair loop"):
        manager.propose("Fix recurring flake", make_sandbox(tmp_path, "other/candidate"))


def test_mark_applied_requires_verified(tmp_path: Path):
    sandbox = make_sandbox(tmp_path)
    manager = SelfRepairManager(tmp_path)
    p = manager.propose("Fix parser", sandbox)
    promotion_id, _ = seed_promotion(tmp_path)
    # A non-verified proposal cannot be applied, even with a real promotion id.
    with pytest.raises(ValueError):
        manager.mark_applied(p.proposal_id, promotion_id)
    verified(manager, p.proposal_id)
    applied = manager.mark_applied(p.proposal_id, promotion_id)
    assert applied.status == "applied"
    assert applied.promotion_id == promotion_id


def test_mark_applied_rejects_invented_promotion(tmp_path: Path):
    sandbox = make_sandbox(tmp_path)
    manager = SelfRepairManager(tmp_path)
    p = manager.propose("Fix parser", sandbox)
    verified(manager, p.proposal_id)
    # A bare string that does not match a real promotion record is not evidence.
    with pytest.raises(ValueError, match="real promotion record"):
        manager.mark_applied(p.proposal_id, "promotion_nonexistent")


def test_applied_then_rollback(tmp_path: Path):
    sandbox = make_sandbox(tmp_path)
    manager = SelfRepairManager(tmp_path)
    p = manager.propose("Fix parser", sandbox)
    verified(manager, p.proposal_id)
    promotion_id, service = seed_promotion(tmp_path)
    manager.mark_applied(p.proposal_id, promotion_id)
    service.rollback(promotion_id)  # journal -> "rolled_back"
    rolled = manager.mark_rollback(p.proposal_id, promotion_id,
                                   "regression observed after apply")
    assert rolled.status == "rolled_back"
    assert rolled.rollback_promotion_id == promotion_id
    with pytest.raises(ValueError):
        manager.mark_applied(rolled.proposal_id, promotion_id)


def test_mark_rollback_requires_real_rolled_back_promotion(tmp_path: Path):
    sandbox = make_sandbox(tmp_path)
    manager = SelfRepairManager(tmp_path)
    p = manager.propose("Fix parser", sandbox)
    verified(manager, p.proposal_id)
    promotion_id, service = seed_promotion(tmp_path)  # journal still "verified"
    manager.mark_applied(p.proposal_id, promotion_id)
    # promotion journal is "verified", not "rolled_back": cannot mark rollback.
    with pytest.raises(ValueError, match="real promotion record"):
        manager.mark_rollback(p.proposal_id, promotion_id, "regression")
    # A bare non-existent promotion id is also rejected.
    with pytest.raises(ValueError, match="real promotion record"):
        manager.mark_rollback(p.proposal_id, "promotion_fake", "regression")


def test_rejected_cannot_be_applied(tmp_path: Path):
    sandbox = make_sandbox(tmp_path)
    manager = SelfRepairManager(tmp_path)
    p = manager.propose("Fix parser", sandbox)
    assert manager.record_verification(p.proposal_id, [], []).status == "rejected"
    with pytest.raises(ValueError):
        manager.mark_applied(p.proposal_id, "promotion_whatever")


def test_different_diagnosis_allowed(tmp_path: Path):
    sandbox = make_sandbox(tmp_path)
    manager = SelfRepairManager(tmp_path)
    p = manager.propose("Fix issue A", sandbox)
    manager.record_verification(p.proposal_id, [], [])  # rejected
    p2 = manager.propose("Fix issue B", make_sandbox(tmp_path, "other2"))
    assert p2.proposal_id != p.proposal_id
