"""Tests for the AI-OS subsystems: self_improver."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from ghost_agent.self_improver import SelfImprover, SelfImproverStore


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path


# ── SelfImprover ───────────────────────────────────────────────────────────

class TestSelfImprover:
    def test_create_candidate(self, root):
        store = SelfImproverStore(root)
        cand = store.create_candidate(
            hypothesis="timeout fix", components=["worker"],
            baseline={"failure_count": 3}, plan="retry", budget_hours=0.5,
            rollback="restore backup")
        assert cand.candidate_id.startswith("imp_")
        assert cand.status == "proposed"
        assert store.get_candidate(cand.candidate_id) is not None

    def test_propose_from_signatures(self, root):
        improver = SelfImprover(root)
        candidates = improver.propose_from_signatures([
            {"error_type": "timeout", "subsystem": "worker", "count": 5, "sample_error": "x"},
            {"error_type": "rare", "subsystem": "worker", "count": 1, "sample_error": "y"},
        ])
        assert len(candidates) == 1
        assert "timeout" in candidates[0].hypothesis

    def test_evaluate_and_decision(self, root, tmp_path):
        improver = SelfImprover(root)
        cand = improver.store.create_candidate(
            hypothesis="h", components=["c"], baseline={"failure_count": 2},
            plan="p", budget_hours=1, rollback="r")
        shadow = improver.evaluate_candidate(cand.candidate_id, regression_tests=[])
        assert "regression" in shadow
        decision = improver.review_and_decide(cand.candidate_id)
        assert decision.decision in {"approved", "rejected", "rollback"}

    def test_rollback_and_fail(self, root):
        store = SelfImproverStore(root)
        cand = store.create_candidate("h", ["c"], {}, "p", 1, "r")
        rolled = store.rollback(cand.candidate_id, "broke tests")
        assert rolled.status == "rolled_back"
        assert "broke" in rolled.failure_reason
        failed = store.fail(cand.candidate_id, "nope")
        assert failed.status == "failed"

    def test_history(self, root):
        store = SelfImproverStore(root)
        store.create_candidate("h", ["c"], {}, "p", 1, "r")
        history = store.history()
        assert history["total"] == 1


# ── All imports verified ───────────────────────────────────────────────────
