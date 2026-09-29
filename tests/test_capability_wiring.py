"""OJ10: capability acquisition production wiring.
Covers CLI lifecycle, gap-pipeline advancement, and lifecycle validation."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ghost_agent.capability_acquisition import AcquisitionError, CapabilityAcquisition


def _ns(**kw):
    defaults = dict(capability_action="", record_id="", candidate="", status=None,
                    health=None, reason="", capability="", symptom="", gap_id="",
                    decision="", rationale="", reuse_target="")
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _import_cli():
    from ghost_agent import cli as m
    return m


# ---------------------------------------------------------------------------
# Store-level lifecycle tests
# ---------------------------------------------------------------------------

def test_invalid_transition_refused(tmp_path: Path) -> None:
    store = CapabilityAcquisition(tmp_path)
    record = store.discover("cap-x", "symptom")
    with pytest.raises(AcquisitionError):
        store.advance(record.record_id, "active")   # discovered -> active: invalid


def test_activate_blocked_by_unresolved_prereq(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    cli = _import_cli()
    store = CapabilityAcquisition(tmp_path)
    record = store.discover("cap-blocked", "symptom")
    store.research(record.record_id, "build", "needed")
    store.advance(record.record_id, "feasible")
    store.advance(record.record_id, "building")
    store.advance(record.record_id, "testing")
    store.advance(record.record_id, "ready_to_activate")
    store.set_prerequisite(record.record_id, "gap:abc", False)
    cli._cmd_capability(_ns(capability_action="activate",
                             record_id=record.record_id, candidate="sandbox/out"))
    assert "refused" in capsys.readouterr().out
    assert store.get(record.record_id).status == "ready_to_activate"


def test_happy_path_propose_to_activate(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    cli = _import_cli()
    store = CapabilityAcquisition(tmp_path)
    r = store.discover("cap-happy", "symptom")
    store.research(r.record_id, "build", "build it")
    store.advance(r.record_id, "feasible")
    store.advance(r.record_id, "building")
    store.advance(r.record_id, "testing")
    store.advance(r.record_id, "ready_to_activate")
    cli._cmd_capability(_ns(capability_action="activate",
                             record_id=r.record_id, candidate="sandbox/out"))
    out = capsys.readouterr().out
    assert '"status": "active"' in out
    assert store.get(r.record_id).status == "active"


def test_propose_creates_discovered_with_gap_prereq(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    cli = _import_cli()
    cli._cmd_capability(_ns(capability_action="propose", capability="web_research",
                             symptom="missing", gap_id="dd0f449d87"))
    out = capsys.readouterr().out
    assert '"status": "discovered"' in out
    store = CapabilityAcquisition(tmp_path)
    records = store.list()
    assert len(records) == 1
    assert records[0].capability == "web_research"
    assert records[0].prerequisites.get("gap:dd0f449d87") is False


def test_decide_validates_decision(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    cli = _import_cli()
    store = CapabilityAcquisition(tmp_path)
    r = store.discover("cap-d", "symptom")
    # valid decision
    cli._cmd_capability(_ns(capability_action="decide", record_id=r.record_id,
                             decision="build", rationale="needed"))
    assert store.get(r.record_id).status == "researched"
    assert store.get(r.record_id).decision == "build"
    # invalid decision refused
    cli._cmd_capability(_ns(capability_action="decide", record_id=r.record_id,
                             decision="yolo", rationale=""))
    assert "refused" in capsys.readouterr().out


def test_retire_from_active(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    cli = _import_cli()
    store = CapabilityAcquisition(tmp_path)
    r = store.discover("cap-ret", "s")
    store.research(r.record_id, "build", "r")
    store.advance(r.record_id, "feasible")
    store.advance(r.record_id, "building")
    store.advance(r.record_id, "testing")
    store.advance(r.record_id, "ready_to_activate")
    store.advance(r.record_id, "active")
    cli._cmd_capability(_ns(capability_action="retire", record_id=r.record_id,
                             reason="obsolete"))
    assert store.get(r.record_id).status == "retired"


# ---------------------------------------------------------------------------
# Gap pipeline hook: gap resolved -> acquisition lifecycle advance
# ---------------------------------------------------------------------------

def test_close_gap_feedback_advances_building_to_testing(tmp_path: Path, monkeypatch) -> None:
    from ghost_agent.autonomy import CapabilityRegistry
    from ghost_agent.autonomy_improver import close_gap_feedback

    monkeypatch.chdir(tmp_path)
    registry = CapabilityRegistry(tmp_path)
    gap = registry.record_gap("build a thing", "iteration_limit", "details")
    gap_id = gap["id"]

    store = CapabilityAcquisition(tmp_path)
    rec = store.discover("cap-pipe", "gap symptom")
    store.research(rec.record_id, "build", "r")
    store.advance(rec.record_id, "feasible")
    store.advance(rec.record_id, "building")

    job = SimpleNamespace(job_id="j1", status="completed", result="tests_passed",
                          goal="anything", acceptance_contract={"gap_id": gap_id})
    result = close_gap_feedback(tmp_path, job)
    assert result == gap_id
    assert store.get(rec.record_id).status == "testing"  # building -> testing


def test_close_gap_feedback_advances_testing_to_ready(tmp_path: Path, monkeypatch) -> None:
    from ghost_agent.autonomy import CapabilityRegistry
    from ghost_agent.autonomy_improver import close_gap_feedback

    monkeypatch.chdir(tmp_path)
    registry = CapabilityRegistry(tmp_path)
    gap = registry.record_gap("build it", "iteration_limit", "d")
    gap_id = gap["id"]

    store = CapabilityAcquisition(tmp_path)
    rec = store.discover("cap-pipe2", "gap symptom")
    store.research(rec.record_id, "build", "r")
    store.advance(rec.record_id, "feasible")
    store.advance(rec.record_id, "building")
    store.advance(rec.record_id, "testing")

    job = SimpleNamespace(job_id="j2", status="completed", result="tests_passed",
                          goal="thing", acceptance_contract={"gap_id": gap_id})
    result = close_gap_feedback(tmp_path, job)
    assert result == gap_id
    assert store.get(rec.record_id).status == "ready_to_activate"  # testing -> ready