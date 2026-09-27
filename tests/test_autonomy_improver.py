"""Tests for ghost_agent.autonomy_improver and CapabilityRegistry gap methods."""
from __future__ import annotations

from pathlib import Path

import pytest

from ghost_agent.autonomy import CapabilityRegistry
from ghost_agent.autonomy_improver import CapabilityImprover
from ghost_agent.proactive import queue_sandbox_outcome, ProactiveEngine


# ------------------------------------------------------------------
# CapabilityRegistry gap management tests
# ------------------------------------------------------------------

def test_record_gap_and_get_queued(tmp_path: Path) -> None:
    """record_gap creates two gaps; only the one with count>=2 appears in get_queued_gaps."""
    reg = CapabilityRegistry(tmp_path)

    # gap1: count=1 — should NOT appear
    g1 = reg.record_gap("Goal A", "Result A", "detail A")
    assert g1["count"] == 1

    # gap2: record the same gap again so count reaches 3
    g2 = reg.record_gap("Goal B", "Result B")
    assert g2["count"] == 1
    g2b = reg.record_gap("Goal B", "Result B")  # bump count to 2
    assert g2b["count"] == 2
    g2c = reg.record_gap("Goal B", "Result B")  # bump count to 3
    assert g2c["count"] == 3

    gaps = reg.get_queued_gaps(min_count=2)
    assert len(gaps) == 1
    assert gaps[0]["goal"] == "Goal B"
    assert gaps[0]["count"] == 3
    assert gaps[0]["status"] == "queued"


def test_mark_in_progress(tmp_path: Path) -> None:
    """mark_in_progress updates the gap status to 'in_progress'."""
    reg = CapabilityRegistry(tmp_path)
    gap = reg.record_gap("Fix parser bug", "Parser crashes on empty input")
    assert gap["status"] == "queued"

    reg.mark_in_progress(gap["id"])

    # reload
    data = reg._load()
    updated = next(g for g in data["gaps"] if g["id"] == gap["id"])
    assert updated["status"] == "in_progress"


def test_mark_resolved(tmp_path: Path) -> None:
    """mark_resolved updates status to 'resolved' and stores the resolution."""
    reg = CapabilityRegistry(tmp_path)
    gap = reg.record_gap("Goal", "Failure")

    reg.mark_resolved(gap["id"], "Implemented a local adapter that handles this case.")

    data = reg._load()
    updated = next(g for g in data["gaps"] if g["id"] == gap["id"])
    assert updated["status"] == "resolved"
    assert updated["resolution"] == "Implemented a local adapter that handles this case."
    # resolution is truncated to 500 chars
    assert len(updated["resolution"]) <= 500


# ------------------------------------------------------------------
# CapabilityImprover tests (mock the model)
# ------------------------------------------------------------------

def test_assess_addressable(tmp_path: Path, monkeypatch) -> None:
    """assess() returns the model's JSON when it is valid and addressable."""

    def fake_chat(model, messages, **kwargs):
        return '{"addressable": true, "risky": false, "scope": "Write a local JSON adapter", "reason": "Clear gap"}'

    monkeypatch.setattr(
        "ghost_agent.autonomy_improver.ollama_chat", fake_chat
    )

    improver = CapabilityImprover(tmp_path, model="fake-model")
    gap = {"goal": "Parse my custom config format", "result": "Unsupported format",
           "followup_goal": "Investigate a workaround for unsupported format"}
    result = improver.assess(gap)

    assert result["addressable"] is True
    assert result["risky"] is False
    assert result["scope"] == "Write a local JSON adapter"


def test_assess_rejects_risky(tmp_path: Path, monkeypatch) -> None:
    """assess() returns the model's response even for risky gaps; caller checks risky."""

    def fake_chat(model, messages, **kwargs):
        return '{"addressable": true, "risky": true, "scope": "Too broad", "reason": "Requires external API"}'

    monkeypatch.setattr(
        "ghost_agent.autonomy_improver.ollama_chat", fake_chat
    )

    improver = CapabilityImprover(tmp_path, model="fake-model")
    gap = {"goal": "Call paid API", "result": "No API key",
           "followup_goal": "Call paid API without key"}
    result = improver.assess(gap)

    assert result["addressable"] is True
    assert result["risky"] is True  # caller must honour this


def test_assess_non_json_falls_back(tmp_path: Path, monkeypatch) -> None:
    """assess() returns safe defaults when the model response is not valid JSON."""

    def fake_chat(model, messages, **kwargs):
        return "I think maybe you should try something else"

    monkeypatch.setattr(
        "ghost_agent.autonomy_improver.ollama_chat", fake_chat
    )

    improver = CapabilityImprover(tmp_path, model="fake-model")
    gap = {"goal": "X", "result": "Y", "followup_goal": "Z"}
    result = improver.assess(gap)

    assert result["addressable"] is False
    assert result["risky"] is True
    assert result["reason"] == "model response not JSON"


def test_assess_model_unreachable(tmp_path: Path, monkeypatch) -> None:
    """assess() returns safe defaults when the model call raises."""

    def fake_chat(model, messages, **kwargs):
        raise ConnectionError("unreachable")

    monkeypatch.setattr(
        "ghost_agent.autonomy_improver.ollama_chat", fake_chat
    )

    improver = CapabilityImprover(tmp_path, model="fake-model")
    gap = {"goal": "X", "result": "Y", "followup_goal": "Z"}
    result = improver.assess(gap)

    assert result["addressable"] is False
    assert result["risky"] is True
    assert result["reason"] == "model unreachable"


def test_try_build_skips_low_count(tmp_path: Path, monkeypatch) -> None:
    """try_build() returns None without calling the model when no gaps meet min_count."""
    call_count = 0

    def fake_chat(model, messages, **kwargs):
        nonlocal call_count
        call_count += 1
        return '{"addressable": false}'

    monkeypatch.setattr(
        "ghost_agent.autonomy_improver.ollama_chat", fake_chat
    )

    improver = CapabilityImprover(tmp_path, model="fake-model")
    # record only one gap with count=1 — below min_count threshold of 2
    improver.gaps.record_gap("One-off goal", "One-off failure")

    result = improver.try_build()
    assert result is None
    assert call_count == 0


def test_try_build_queues_on_addressable(tmp_path: Path, monkeypatch) -> None:
    """try_build() calls queue_sandbox_outcome and returns gap_id when addressable."""

    queued_jobs = []

    def fake_queue(root, goal, model, *, source, priority):
        queued_jobs.append({"root": str(root), "goal": goal, "source": source})
        class FakeJob:
            job_id = "fake-job-123"
        return FakeJob()

    monkeypatch.setattr(
        "ghost_agent.autonomy_improver.ollama_chat",
        lambda *a, **kw: '{"addressable": true, "risky": false, "scope": "test", "reason": "ok"}'
    )
    monkeypatch.setattr(
        "ghost_agent.proactive.queue_sandbox_outcome", fake_queue
    )

    improver = CapabilityImprover(tmp_path, model="fake-model")
    # bump the gap to count=2 so it meets min_count
    g1 = improver.gaps.record_gap("Build adapter", "Missing adapter")
    improver.gaps.record_gap("Build adapter", "Missing adapter")

    gap_id = improver.try_build()

    assert gap_id == g1["id"]
    assert len(queued_jobs) == 1
    assert queued_jobs[0]["source"] == "capability_improver"
    assert "Build adapter" in queued_jobs[0]["goal"]

    # verify gap was marked in_progress
    data = improver.gaps._load()
    updated = next(g for g in data["gaps"] if g["id"] == g1["id"])
    assert updated["status"] == "in_progress"


def test_try_build_skips_risky(tmp_path: Path, monkeypatch) -> None:
    """try_build() returns None when the model marks the gap as risky."""

    def fake_queue(root, goal, model, *, source, priority):
        pytest.fail("queue_sandbox_outcome should not be called for risky gaps")

    monkeypatch.setattr(
        "ghost_agent.autonomy_improver.ollama_chat",
        lambda *a, **kw: '{"addressable": true, "risky": true, "scope": "too broad", "reason": "external"}'
    )
    monkeypatch.setattr(
        "ghost_agent.proactive.queue_sandbox_outcome", fake_queue
    )

    improver = CapabilityImprover(tmp_path, model="fake-model")
    g = improver.gaps.record_gap("Call paid API", "No key")
    improver.gaps.record_gap("Call paid API", "No key")

    result = improver.try_build()
    assert result is None


# ------------------------------------------------------------------
# Integration: queue_sandbox_outcome accepts capability_improver source
# ------------------------------------------------------------------

def test_queue_sandbox_outcome_accepts_capability_improver_source(tmp_path: Path, monkeypatch) -> None:
    """queue_sandbox_outcome accepts source='capability_improver'."""
    monkeypatch.setattr("ghost_agent.models.chat.chat", lambda *a, **kw: "done")
    job = queue_sandbox_outcome(tmp_path, "Implement the gap workaround",
                               "fake-model", source="capability_improver", priority=10)
    assert job.job_id  # job was created