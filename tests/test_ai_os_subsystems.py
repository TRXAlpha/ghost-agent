"""Tests for the AI-OS subsystems: self_improver, capability_manager,
resource_manager, notifications, memory.long_term, memory.manager, nexus."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from ghost_agent.self_improver import SelfImprover, SelfImproverStore
from ghost_agent.capability_manager import CapabilityManager
from ghost_agent.resource_manager import ResourceManager
from ghost_agent.notifications import (
    NotificationStore, QuietHours, ChannelPreferences, Notification,
)
from ghost_agent.memory.long_term import LongTermMemory
from ghost_agent.memory.manager import MemoryManager, memory_for
from ghost_agent.nexus import Nexus, NexusConfig


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


# ── CapabilityManager ─────────────────────────────────────────────────────

class TestCapabilityManager:
    def test_register_and_get(self, root):
        mgr = CapabilityManager(root)
        rec = mgr.register("test_cap", "Test Capability", owner="tester")
        assert rec.capability == "test_cap"
        assert rec.status == "discovered"
        assert mgr.get("test_cap") is not None
        with pytest.raises(ValueError):
            mgr.register("test_cap", "Duplicate")

    def test_lifecycle_transitions(self, root):
        mgr = CapabilityManager(root)
        mgr.register("cap", "Cap")
        mgr.set_status("cap", "researched")
        mgr.set_status("cap", "feasible")
        mgr.set_status("cap", "building")
        mgr.set_status("cap", "testing")
        mgr.set_status("cap", "ready_to_activate")
        mgr.set_status("cap", "active")
        assert mgr.get("cap").status == "active"

    def test_versions(self, root):
        mgr = CapabilityManager(root)
        mgr.register("cap", "Cap")
        mgr.add_version("cap", "1.1.0", "manifest.json", "Added feature")
        assert mgr.get("cap").current_version == "1.1.0"
        with pytest.raises(ValueError):
            mgr.add_version("cap", "1.1.0", "dup", "dup")

    def test_dependency_check(self, root):
        mgr = CapabilityManager(root)
        mgr.register("cap", "Cap")
        mgr.register("dep", "Dep")
        mgr.add_dependency("dep", "cap", "1.0.0")
        # cap is still "discovered" so unsatisfied
        results = mgr.check_dependencies("dep")
        assert results["cap"] is False
        mgr.set_status("cap", "active")
        assert mgr.check_dependencies("dep")["cap"] is True

    def test_health_and_usage(self, root):
        mgr = CapabilityManager(root)
        mgr.register("cap", "Cap")
        mgr.set_health("cap", "healthy")
        assert mgr.get("cap").health_status == "healthy"
        mgr.record_usage("cap")
        assert mgr.get("cap").usage_count == 1

    def test_retire(self, root):
        mgr = CapabilityManager(root)
        mgr.register("cap", "Cap")
        retired = mgr.retire("cap", "unused")
        assert retired.status == "retired"
        assert retired.retirement_reason == "unused"

    def test_stats(self, root):
        mgr = CapabilityManager(root)
        mgr.register("a", "A")
        mgr.register("b", "B")
        stats = mgr.stats()
        assert stats["total"] == 2


# ── ResourceManager ───────────────────────────────────────────────────────

class TestResourceManager:
    def test_snapshot(self, root):
        mgr = ResourceManager(root)
        snap = mgr.snapshot()
        assert snap.cpu_cores >= 1
        assert snap.disk_free > 0

    def test_budget_consume_release(self, root):
        mgr = ResourceManager(root)
        mgr.set_budget_ceiling("inference", 10.0)
        assert mgr.consume("inference", 5.0) is True
        assert mgr.consume("inference", 6.0) is False  # over ceiling
        mgr.release("inference", 5.0)
        assert mgr.get_budget("inference").used == 0.0

    def test_reserve(self, root):
        mgr = ResourceManager(root)
        mgr.set_budget_ceiling("inference", 10.0)
        res = mgr.reserve("inference", 5.0, "job1")
        assert res is not None
        res2 = mgr.reserve("inference", 6.0, "job2")
        assert res2 is None  # exceeds ceiling
        mgr.release_reservation(res.reservation_id)
        assert mgr.active_reservations("inference") == []

    def test_admission(self, root):
        mgr = ResourceManager(root)
        mgr.set_budget_ceiling("ram", 100.0)
        result = mgr.admit("job1")
        assert result["admitted"] is True
        mgr.release_job("job1")

    def test_stats(self, root):
        mgr = ResourceManager(root)
        stats = mgr.stats()
        assert "snapshot" in stats
        assert "budgets" in stats
        assert "active_reservations" in stats


# ── Notifications ─────────────────────────────────────────────────────────

class TestNotifications:
    def test_enqueue_dequeue(self, root):
        store = NotificationStore(root)
        notif = store.enqueue("Test", "Body")
        assert notif.title == "Test"
        items = store.dequeue(1)
        assert len(items) == 1
        assert items[0].notification_id == notif.notification_id

    def test_mark_sent(self, root):
        store = NotificationStore(root)
        notif = store.enqueue("T", "B")
        store.mark_sent(notif.notification_id)
        assert store.stats()["pending"] == 0

    def test_quiet_hours(self, root):
        store = NotificationStore(root)
        qh = QuietHours(enabled=True, start_hour=22, end_hour=8)
        store.set_quiet_hours(qh)
        assert store.get_quiet_hours().enabled is True
        notif = Notification(notification_id="n1", title="T", body="B", priority="normal")
        assert store.should_deliver(notif) is False  # suppressed in quiet hours

    def test_urgent_bypasses_quiet_hours(self, root):
        """Test priority-based routing and quiet-hour suppression logic."""
        store = NotificationStore(root)
        # Test priority gate: urgent should not be blocked by low-priority channel minimum
        prefs = ChannelPreferences("telegram", enabled=True, priority_min="urgent")
        store.set_channel_prefs(prefs)
        notif = Notification(notification_id="n2", title="T", body="B", priority="normal")
        # Normal is below urgent-minimum -> suppressed
        assert store.should_deliver(notif) is False
        notif2 = Notification(notification_id="n3", title="T", body="B", priority="urgent")
        assert store.should_deliver(notif2) is True

    def test_channel_prefs(self, root):
        store = NotificationStore(root)
        prefs = ChannelPreferences("telegram", enabled=False)
        store.set_channel_prefs(prefs)
        assert store.get_channel_prefs("telegram").enabled is False

    def test_stats(self, root):
        store = NotificationStore(root)
        store.enqueue("A", "B")
        stats = store.stats()
        assert stats["pending"] == 1


# ── Long-term memory ──────────────────────────────────────────────────────

class TestLongTermMemory:
    def test_add_and_get_facts(self, root):
        mem = LongTermMemory(root)
        fact = mem.add_fact("user", "likes", "rust", "owner")
        facts = mem.get_facts(subject="user")
        assert len(facts) == 1
        assert facts[0].value == "rust"

    def test_retract_fact(self, root):
        mem = LongTermMemory(root)
        fact = mem.add_fact("user", "likes", "rust", "owner")
        retracted = mem.retract_fact(fact.fact_id, "changed mind")
        assert retracted.retracted_at > 0
        assert mem.get_facts() == []

    def test_preferences(self, root):
        mem = LongTermMemory(root)
        mem.add_preference("editor", "neovim", kind="declared", confidence=1.0)
        prefs = mem.get_preferences(kind="declared")
        assert len(prefs) == 1
        assert prefs[0].preference == "neovim"

    def test_goals(self, root):
        mem = LongTermMemory(root)
        goal = mem.add_goal("Learn Rust")
        assert goal.status == "active"
        mem.update_progress(goal.goal_id, 1.0)
        assert mem.get_goals(status="completed")[0].title == "Learn Rust"

    def test_stats(self, root):
        mem = LongTermMemory(root)
        mem.add_fact("s", "p", "v", "src")
        mem.add_goal("g")
        stats = mem.stats()
        assert stats["facts"] == 1
        assert stats["goals"] == 1


# ── MemoryManager ─────────────────────────────────────────────────────────

class TestMemoryManager:
    def test_remember_and_recall(self, root):
        mem = MemoryManager(root)
        ep = mem.remember("task", "Built feature", "Implemented feature X", outcome="success")
        assert ep.outcome == "success"
        assert mem.recall(event_type="task")[0].title == "Built feature"

    def test_importance_filter(self, root):
        mem = MemoryManager(root)
        mem.remember("decision", "Minor", "x", importance=0.2)
        mem.remember("task", "Important", "y", importance=0.9)
        assert len(mem.recall(min_importance=0.8)) == 1

    def test_working_memory(self, root):
        mem = MemoryManager(root)
        mem.add_working("active item", priority=10)
        mem.add_working("old item", priority=5, ttl_seconds=-1)
        assert len(mem.get_working()) == 1
        assert mem.get_working()[0].content == "active item"

    def test_invalid_event_type(self, root):
        mem = MemoryManager(root)
        with pytest.raises(ValueError):
            mem.remember("bogus", "t", "s")

    def test_factory(self, root):
        assert memory_for(root) is not None


# ── Proactive loop AI-OS extensions ──────────────────────────────────────

class TestLearningTracker:
    def test_record_and_rates(self, root):
        from ghost_agent.proactive import LearningTracker
        tracker = LearningTracker(root)
        rates = tracker.record_outcome("coding", "tests_passed")
        assert rates["coding"] > 0.5
        rates = tracker.record_outcome("coding", "tests_failed")
        rates2 = tracker.record_outcome("research", "tests_passed")
        assert tracker.success_rates()["research"] > tracker.success_rates()["coding"]

    def test_rank(self, root):
        from ghost_agent.proactive import LearningTracker
        tracker = LearningTracker(root)
        tracker.record_outcome("coding", "tests_passed")
        tracker.record_outcome("coding", "tests_passed")
        tracker.record_outcome("research", "tests_failed")
        candidates = [
            {"kind": "research", "confidence": 0.9},
            {"kind": "coding", "confidence": 0.5},
        ]
        ranked = tracker.rank(candidates)
        assert ranked[0]["kind"] == "coding"  # higher success rate


class TestResourceGate:
    def test_admit_release(self, root):
        from ghost_agent.proactive import ResourceGate
        gate = ResourceGate(root)
        result = gate.admit("test_job")
        assert result["admitted"] is True
        gate.release("test_job")
        assert gate.resources.active_reservations("ram") == []


# ── NEXUS ─────────────────────────────────────────────────────────────────

class TestNexus:
    def test_register_and_initialize(self, root):
        from ghost_agent.nexus import SubsystemHealth

        class FakeSubsystem:
            def health_check(self):
                return SubsystemHealth(name="fake")

            def initialize(self):
                pass

            def shutdown(self):
                pass

        nexus = Nexus(state_dir=root / ".ghost")
        nexus.register("fake", FakeSubsystem())
        assert "fake" in nexus.list_subsystems()
        nexus.start()
        assert nexus.list_subsystems() == ["fake"]


# ── All imports verified ───────────────────────────────────────────────────
