"""Tests for the SelfImprover pipeline (T18): propose -> worker -> sync -> self-repair."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ghost_agent.autonomy import OutcomeStore
from ghost_agent.self_improver import SelfImprover, SelfImproverStore
from ghost_agent.self_repair import SelfRepairManager


def _sig(error_type: str = "timeout", subsystem: str = "worker",
         count: int = 3, sample: str = "cmd timed out",
         normalized: str = "cmd timed out") -> dict:
    return {
        "error_type": error_type,
        "subsystem": subsystem,
        "count": count,
        "sample_error": sample,
        "normalized_error": normalized,
        "sample_job_id": "job_x",
    }


# ── propose ────────────────────────────────────────────────────────────────

def test_propose_queues_project_mode_job(tmp_path: Path, permissive_policy):
    improver = SelfImprover(tmp_path)
    cand = improver.propose([_sig(count=3)], "test-model")
    assert cand is not None
    assert cand.status == "queued"
    assert cand.job_id
    job = OutcomeStore(tmp_path).load(cand.job_id)
    assert job is not None
    assert job.acceptance_contract["workspace_mode"] == "project"
    assert job.acceptance_contract["source"] == "self_improver"
    # The scheduler has the job.
    from ghost_agent.runtime import Scheduler
    scheduled = Scheduler(tmp_path).list()
    assert any(s.job_id == cand.job_id for s in scheduled)


def test_propose_skips_low_count_and_duplicates(tmp_path: Path, permissive_policy):
    improver = SelfImprover(tmp_path)
    assert improver.propose([_sig(count=1)], "m") is None
    first = improver.propose([_sig(count=2)], "m")
    assert first is not None and first.status == "queued"
    # Same signature again: open candidate exists -> None.
    assert improver.propose([_sig(count=2)], "m") is None


def test_propose_requires_model(tmp_path: Path):
    improver = SelfImprover(tmp_path)
    with pytest.raises(ValueError):
        improver.propose([_sig(count=2)], "  ")


def test_propose_blocked_marker_fails_candidate(tmp_path: Path):
    improver = SelfImprover(tmp_path)
    cand = improver.propose(
        [_sig(count=2, sample="deploy to production now")], "m")
    assert cand.status == "failed"
    assert "queue refused" in cand.failure_reason


def test_goal_text_within_limit(tmp_path: Path, permissive_policy):
    improver = SelfImprover(tmp_path)
    cand = improver.propose(
        [_sig(count=2, sample="x" * 5000)], "m")
    assert cand is not None and cand.job_id
    job = OutcomeStore(tmp_path).load(cand.job_id)
    assert len(job.goal) <= 600


# ── sync ───────────────────────────────────────────────────────────────────

def _simulate_worker_pass(tmp_path: Path, job_id: str) -> None:
    store = OutcomeStore(tmp_path)
    job = store.load(job_id)
    sandbox = tmp_path / ".ghost" / "outcomes" / job.job_id / "run" / "sandbox"
    sandbox.mkdir(parents=True, exist_ok=True)
    (sandbox / "fix.py").write_text("# candidate fix\n", encoding="utf-8")
    job.status = "completed"
    job.result = "tests_passed"
    job.sandbox_path = str(sandbox)
    store.save(job)


def test_sync_hands_passing_job_to_self_repair(tmp_path: Path, permissive_policy):
    improver = SelfImprover(tmp_path)
    cand = improver.propose([_sig(count=2)], "m")
    assert cand is not None and cand.job_id
    _simulate_worker_pass(tmp_path, cand.job_id)

    changed = improver.sync()
    assert len(changed) == 1
    assert changed[0].status == "handed_to_self_repair"
    copy = tmp_path / "workspaces" / "self-improve" / cand.candidate_id
    assert copy.is_dir() and (copy / "fix.py").exists()
    proposal = SelfRepairManager(tmp_path).get(changed[0].repair_proposal_id)
    assert proposal is not None
    assert Path(proposal.sandbox) == copy
    assert ".ghost" not in Path(proposal.sandbox).parts


def test_sync_failed_job_marks_failed(tmp_path: Path, permissive_policy):
    improver = SelfImprover(tmp_path)
    cand = improver.propose([_sig(count=2)], "m")
    store = OutcomeStore(tmp_path)
    job = store.load(cand.job_id)
    job.status = "completed"
    job.result = "iteration_limit"
    store.save(job)

    changed = improver.sync()
    assert changed[0].status == "failed"
    assert "iteration_limit" in changed[0].failure_reason
    assert SelfRepairManager(tmp_path).list() == []


def test_sync_leaves_running_job(tmp_path: Path, permissive_policy):
    improver = SelfImprover(tmp_path)
    cand = improver.propose([_sig(count=2)], "m")
    assert cand is not None
    changed = improver.sync()
    assert changed == []
    assert improver.store.get_candidate(cand.candidate_id).status == "queued"


def test_sync_refuses_existing_handoff_dir(tmp_path: Path, permissive_policy):
    improver = SelfImprover(tmp_path)
    cand = improver.propose([_sig(count=2)], "m")
    _simulate_worker_pass(tmp_path, cand.job_id)
    # Pre-create the handoff dir.
    (tmp_path / "workspaces" / "self-improve" / cand.candidate_id).mkdir(parents=True)
    changed = improver.sync()
    assert changed[0].status == "failed"
    assert "already exists" in changed[0].failure_reason


def test_old_candidate_records_still_load(tmp_path: Path):
    store = SelfImproverStore(tmp_path)
    old_record = {
        "candidate_id": "imp_legacy1",
        "hypothesis": "old record",
        "affected_components": ["worker"],
        "baseline_metrics": {"failure_count": 2},
        "evaluation_plan": "old plan",
        "budget_hours": 0.5,
        "rollback_plan": "old rollback",
        "status": "failed",
        "created_at": 0.0,
        "updated_at": 0.0,
    }
    import json
    store.candidates_path.write_text(json.dumps([old_record]), encoding="utf-8")
    listed = store.list_candidates()
    assert len(listed) == 1
    assert listed[0].candidate_id == "imp_legacy1"


def test_no_live_tree_modification(tmp_path: Path, permissive_policy):
    def snapshot() -> set[str]:
        return sorted(
            str(p.relative_to(tmp_path))
            for p in tmp_path.rglob("*")
            if ".ghost" not in p.parts and "workspaces" not in p.parts)

    before = snapshot()
    improver = SelfImprover(tmp_path)
    cand = improver.propose([_sig(count=2)], "m")
    _simulate_worker_pass(tmp_path, cand.job_id)
    improver.sync()
    assert snapshot() == before
