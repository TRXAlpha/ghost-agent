"""Tests for the AI-OS subsystems: self_improver (store)."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from ghost_agent.self_improver import SelfImprover, SelfImproverStore


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path


# ── SelfImproverStore ──────────────────────────────────────────────────────

class TestSelfImproverStore:
    def test_create_candidate(self, root):
        store = SelfImproverStore(root)
        cand = store.create_candidate(
            hypothesis="timeout fix", components=["worker"],
            baseline={"failure_count": 3}, plan="retry", budget_hours=0.5,
            rollback="restore backup")
        assert cand.candidate_id.startswith("imp_")
        assert cand.status == "proposed"
        assert store.get_candidate(cand.candidate_id) is not None

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
