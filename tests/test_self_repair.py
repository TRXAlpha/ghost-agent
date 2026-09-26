"""Bounded self-repair verification: ONLY real evaluator runs count as evidence.

Every positive verification in this file goes through ``verify_from_evaluator``
with a real subprocess evaluator, so a "passing" result is an actual processed
run against the candidate bytes, not a caller-assembled dict (S08).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ghost_agent.self_repair import SelfRepairManager, _dir_digest


def _run(candidate: Path, code: str) -> dict:
    """Run an inline python program inside the candidate, time-bounded."""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(candidate), capture_output=True, text=True, timeout=60,
        # Sanitized env so host secrets are not inherited into the run.
        env={"PATH": os.environ.get("PATH", "")},
    )
    return {"returncode": proc.returncode,
            "output": ((proc.stdout or "") + (proc.stderr or ""))[-4000:]}


def evaluator_ok(candidate: Path) -> dict:
    return _run(candidate, "import sys; sys.exit(0)")


def evaluator_fail(candidate: Path) -> dict:
    return _run(candidate, "import sys; sys.exit(1)")


def evaluator_mutates(candidate: Path) -> dict:
    # The evaluator writes into the candidate, changing its digest mid-run.
    (candidate / "mutated.txt").write_text("x", encoding="utf-8")
    return _run(candidate, "import sys; sys.exit(0)")


def verify(manager, proposal_id, evaluator=evaluator_ok):
    """Verify via a real evaluator run (evaluator-sourced evidence)."""
    return manager.verify_from_evaluator(proposal_id, evaluator)


def test_self_repair_requires_verified_regression_before_promotion_request(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "candidate.py").write_text("value = 1\n", encoding="utf-8")
    manager = SelfRepairManager(tmp_path)
    proposal = manager.propose("Fix parser", sandbox)

    with pytest.raises(PermissionError):
        manager.request_promotion(proposal.proposal_id, "goal_1")
    verify(manager, proposal.proposal_id)
    transaction, approval, files = manager.request_promotion(proposal.proposal_id, "goal_1")
    assert transaction.startswith("tx_") and approval.startswith("approval_") and files == ["candidate.py"]


def test_failing_evaluator_does_not_verify(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "candidate.py").write_text("value = 1\n", encoding="utf-8")
    manager = SelfRepairManager(tmp_path)
    proposal = manager.propose("Fix parser", sandbox)
    result = verify(manager, proposal.proposal_id, evaluator_fail)
    assert result.status == "rejected"
    with pytest.raises(PermissionError):
        manager.request_promotion(proposal.proposal_id, "goal_1")


@pytest.mark.parametrize("regression,benchmark", [
    ([], []), ([], [{"returncode": 0}]), ([{"returncode": 0}], []),
    ([{"returncode": False}], [{"returncode": 0}]),
    ([{"returncode": 0.0}], [{"returncode": 0}]),
    ([{"returncode": "0"}], [{"returncode": 0}]),
    ([{}], [{"returncode": 0}]), ([None], [{"returncode": 0}]),
    ([{"returncode": 0}], [{"returncode": 1}]),
])
def test_missing_or_invalid_verification_cannot_request_promotion(tmp_path, regression, benchmark):
    # Evidence that is not evaluator-sourced (no source/run_digest) is rejected,
    # so a hand-entered note cannot impersonate independent verification.
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    manager = SelfRepairManager(tmp_path)
    proposal = manager.propose("Fix parser", sandbox)
    result = manager.record_verification(proposal.proposal_id, regression, benchmark)
    assert result.status == "rejected"
    assert manager.get(proposal.proposal_id).status == "rejected"
    with pytest.raises(PermissionError):
        manager.request_promotion(proposal.proposal_id, "goal_1")


def test_hand_entered_evidence_cannot_verify(tmp_path):
    # A manually constructed dict with returncode 0 but no evaluator source /
    # digest binding must NOT verify (S08: manual note != independent evidence).
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "candidate.py").write_text("value = 1\n", encoding="utf-8")
    manager = SelfRepairManager(tmp_path)
    proposal = manager.propose("Fix parser", sandbox)
    fake = {"returncode": 0, "output": "someone said it passed", "source": "user"}
    result = manager.record_verification(proposal.proposal_id, [fake], [fake])
    assert result.status == "rejected"
    with pytest.raises(PermissionError):
        manager.request_promotion(proposal.proposal_id, "goal_1")


def test_forged_evaluator_label_with_correct_digest_cannot_verify(tmp_path):
    # The hard forgery case: a caller computes _dir_digest (a public function)
    # and labels evidence source="evaluator" with that digest and returncode 0.
    # Without the manager-minted run_nonce this must NOT verify (S08): a
    # manufactured dict cannot impersonate an actual witnessed evaluator run.
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "candidate.py").write_text("value = 1\n", encoding="utf-8")
    manager = SelfRepairManager(tmp_path)
    proposal = manager.propose("Fix parser", sandbox)
    digest = _dir_digest(sandbox)
    forged = {"source": "evaluator", "run_digest": digest,
              "returncode": 0, "output": "forged pass"}
    result = manager.record_verification(proposal.proposal_id, [forged], [forged])
    assert result.status == "rejected"
    with pytest.raises(PermissionError):
        manager.request_promotion(proposal.proposal_id, "goal_1")


def test_forged_empty_nonce_on_fresh_proposal_cannot_verify(tmp_path):
    # A fresh proposal's stored run_nonce is the empty string. An attacker can
    # compute the digest AND supply run_nonce="" to match that default. A
    # proposal that has never had a manager-minted (non-empty) run must NEVER
    # verify, so this empty-nonce forgery must be rejected and must not promote.
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "candidate.py").write_text("value = 1\n", encoding="utf-8")
    manager = SelfRepairManager(tmp_path)
    proposal = manager.propose("Fix parser", sandbox)
    assert manager.get(proposal.proposal_id).run_nonce == ""  # never run
    digest = _dir_digest(sandbox)
    forged = {"source": "evaluator", "run_digest": digest,
              "run_nonce": "", "returncode": 0, "output": "forged empty-nonce pass"}
    result = manager.record_verification(proposal.proposal_id, [forged], [forged])
    assert result.status == "rejected"
    with pytest.raises(PermissionError):
        manager.request_promotion(proposal.proposal_id, "goal_1")


def test_stale_nonce_replay_against_mutated_candidate_cannot_verify(tmp_path):
    # After a real witnessed run, the stored run_nonce is readable from the
    # persisted state. A replay attack recomputes the digest after mutating the
    # candidate and re-submits the STORED nonce against the NEW digest. This must
    # be rejected: the witnessed digest is immutable, so evidence must match BOTH
    # the stored witnessed digest AND the current candidate digest. Different
    # bytes were never witnessed -> no verification, no promotion (S08).
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    candidate = sandbox / "candidate.py"
    candidate.write_text("value = 1\n", encoding="utf-8")
    manager = SelfRepairManager(tmp_path)
    proposal = manager.propose("Fix parser", sandbox)
    verified = verify(manager, proposal.proposal_id)
    assert verified.status == "verified"
    stored_nonce = verified.run_nonce
    assert stored_nonce  # a real run minted a non-empty nonce
    # mutate the candidate AFTER the witnessed run
    candidate.write_text("value = 2\n", encoding="utf-8")
    new_digest = _dir_digest(sandbox)
    assert new_digest != verified.run_digest
    replay = {"source": "evaluator", "run_digest": new_digest,
              "run_nonce": stored_nonce, "returncode": 0, "output": "replayed pass"}
    result = manager.record_verification(proposal.proposal_id, [replay], [replay])
    assert result.status == "rejected"
    with pytest.raises(PermissionError):
        manager.request_promotion(proposal.proposal_id, "goal_1")


def test_candidate_mutation_during_evaluation_rejected(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "candidate.py").write_text("value = 1\n", encoding="utf-8")
    manager = SelfRepairManager(tmp_path)
    proposal = manager.propose("Fix parser", sandbox)
    # The evaluator writes into the candidate mid-run => digest changes => rejected.
    with pytest.raises(ValueError, match="changed during evaluation"):
        verify(manager, proposal.proposal_id, evaluator_mutates)
    assert manager.get(proposal.proposal_id).status != "verified"


def test_candidate_mutation_after_verification_invalidates_evidence(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    candidate = sandbox / "candidate.py"
    candidate.write_text("value = 1\n", encoding="utf-8")
    manager = SelfRepairManager(tmp_path)
    proposal = manager.propose("Fix parser", sandbox)
    verified = verify(manager, proposal.proposal_id)
    assert verified.status == "verified" and verified.verified_snapshot
    stale_evidence = verified.regression_results  # digest bound to pre-mutation bytes
    (sandbox / "candidate.py").write_text("value = 2\n", encoding="utf-8")
    # Re-submitting the stale evaluator evidence must be rejected because the
    # run_digest no longer matches the current candidate digest.
    result = manager.record_verification(proposal.proposal_id, stale_evidence, stale_evidence)
    assert result.status == "rejected"
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
    verified = verify(manager, proposal.proposal_id)
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
    # An empty candidate builds a digest but has no promotable files, so even
    # genuine evaluator success cannot yield a verified snapshot.
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    manager = SelfRepairManager(tmp_path)
    proposal = manager.propose("Fix parser", sandbox)
    result = verify(manager, proposal.proposal_id, evaluator_ok)
    assert result.status == "rejected"
    with pytest.raises(PermissionError):
        manager.request_promotion(proposal.proposal_id, "goal_1")


def test_dir_digest_is_content_sensitive():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "a.py").write_text("x", encoding="utf-8")
        d1 = _dir_digest(root)
        (root / "a.py").write_text("y", encoding="utf-8")
        d2 = _dir_digest(root)
    assert d1 != d2


def test_evaluator_cache_output_does_not_invalidate_verification(tmp_path):
    """A real pytest-like evaluator writes cache dirs in the candidate; those
    ephemeral files must not change the digest of the evaluated source bytes."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "candidate.py").write_text("value = 1\n", encoding="utf-8")

    def evaluator_writes_cache(candidate: Path) -> dict:
        (candidate / ".pytest_cache" / "v" / "cache").mkdir(parents=True, exist_ok=True)
        (candidate / ".pytest_cache" / "v" / "cache" / "data").write_text("cache", encoding="utf-8")
        (candidate / "__pycache__").mkdir(exist_ok=True)
        (candidate / "__pycache__" / "candidate.cpython-314.pyc").write_bytes(b"pyc")
        return evaluator_ok(candidate)

    manager = SelfRepairManager(tmp_path)
    proposal = manager.propose("Fix parser", sandbox)
    verified = verify(manager, proposal.proposal_id, evaluator_writes_cache)
    assert verified.status == "verified" and verified.verified_snapshot
    # The candidate now contains cache junk, yet its evidence digest is stable.
    assert _dir_digest(sandbox) == verified.run_digest
