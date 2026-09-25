"""OJ15: S08 self-improvement — benefit/linkage, scope, loop prevention, apply/rollback."""
from __future__ import annotations

from pathlib import Path

import pytest

from ghost_agent.self_repair import SelfRepairManager


def make_sandbox(tmp_path, rel="workspace/candidate"):
    sandbox = tmp_path / rel
    sandbox.mkdir(parents=True, exist_ok=True)
    (sandbox / "candidate.py").write_text("value = 1\n", encoding="utf-8")
    return sandbox


def verified(manager, proposal_id):
    return manager.record_verification(proposal_id, [{"returncode": 0}], [{"returncode": 0}])


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
    with pytest.raises(ValueError, match="transition"):
        manager.mark_applied(p.proposal_id, ["applied.patch"])
    verified(manager, p.proposal_id)
    applied = manager.mark_applied(p.proposal_id, ["applied.patch", "evidence.json"])
    assert applied.status == "applied"
    assert "evidence.json" in applied.benchmark_results


def test_applied_then_rollback(tmp_path: Path):
    sandbox = make_sandbox(tmp_path)
    manager = SelfRepairManager(tmp_path)
    p = manager.propose("Fix parser", sandbox)
    verified(manager, p.proposal_id)
    manager.mark_applied(p.proposal_id, ["applied.patch"])
    rolled = manager.mark_rollback(p.proposal_id, "regression observed after apply")
    assert rolled.status == "rolled_back"
    with pytest.raises(ValueError, match="transition"):
        manager.mark_applied(rolled.proposal_id, ["x"])


def test_rejected_cannot_be_applied(tmp_path: Path):
    sandbox = make_sandbox(tmp_path)
    manager = SelfRepairManager(tmp_path)
    p = manager.propose("Fix parser", sandbox)
    assert manager.record_verification(p.proposal_id, [], []).status == "rejected"
    with pytest.raises(ValueError, match="transition"):
        manager.mark_applied(p.proposal_id, ["applied.patch"])


def test_different_diagnosis_allowed(tmp_path: Path):
    sandbox = make_sandbox(tmp_path)
    manager = SelfRepairManager(tmp_path)
    p = manager.propose("Fix issue A", sandbox)
    manager.record_verification(p.proposal_id, [], [])  # rejected
    p2 = manager.propose("Fix issue B", make_sandbox(tmp_path, "other2"))
    assert p2.proposal_id != p.proposal_id
