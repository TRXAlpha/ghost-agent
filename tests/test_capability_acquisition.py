"""OJ10: capability acquisition lifecycle, decisions, prerequisites, reuse."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ghost_agent.capability_acquisition import AcquisitionError, CapabilityAcquisition


def test_discover_research_build_return_flow(tmp_path: Path):
    acq = CapabilityAcquisition(tmp_path)
    rec = acq.discover("pdf_parsing", "Outcome X failed: could not extract PDF text")
    assert rec.status == "discovered"
    rec = acq.research(rec.record_id, "build", "No pinned integration covers extraction")
    assert rec.decision == "build"
    acq.advance(rec.record_id, "feasible")
    acq.advance(rec.record_id, "building")
    acq.bind_candidate(rec.record_id, ".ghost/sandbox/pdf-extractor")
    rec = acq.advance(rec.record_id, "testing")
    rec = acq.advance(rec.record_id, "ready_to_activate")
    rec = acq.advance(rec.record_id, "active")
    assert rec.status == "active"
    assert rec.candidate == ".ghost/sandbox/pdf-extractor"


def test_reuse_decision_requires_target(tmp_path: Path):
    acq = CapabilityAcquisition(tmp_path)
    rec = acq.discover("telegram_transport", "Need a transport")
    with pytest.raises(AcquisitionError, match="reuse_target"):
        acq.research(rec.record_id, "reuse", "reuse existing")


def test_reuse_inventory_prefers_existing(tmp_path: Path):
    assert CapabilityAcquisition.reuse_inventory("telegram_transport") == "openjarvis_telegram"
    assert CapabilityAcquisition.reuse_inventory("novel_missing_thing") is None


def test_waiting_external_preserves_progress(tmp_path: Path):
    acq = CapabilityAcquisition(tmp_path)
    rec = acq.discover("huawei_health", "Need watch data")
    acq.research(rec.record_id, "build", "No provider token yet")
    acq.advance(rec.record_id, "feasible")
    rec = acq.mark_waiting(rec.record_id, "requires Huawei developer account approval")
    assert rec.status == "waiting_external"
    assert rec.missing_external == "requires Huawei developer account approval"
    assert acq.pending_external() == [rec]
    # reaching feasibility must happen BEFORE waiting; resume goes back to feasible
    rec = acq.resume(rec.record_id, "feasible")
    assert rec.status == "feasible"
    assert rec.missing_external == ""


def test_prerequisite_tracking(tmp_path: Path):
    acq = CapabilityAcquisition(tmp_path)
    rec = acq.discover("rust_game", "No toolchain for compile checks")
    acq.research(rec.record_id, "adapt", "adapt existing scaffold")
    acq.set_prerequisite(rec.record_id, "rust_toolchain", False)
    assert acq.get(rec.record_id).prerequisites == {"rust_toolchain": False}
    acq.set_prerequisite(rec.record_id, "rust_toolchain", True)
    assert acq.get(rec.record_id).prerequisites == {"rust_toolchain": True}


def test_invalid_transition_rejected(tmp_path: Path):
    acq = CapabilityAcquisition(tmp_path)
    rec = acq.discover("web_automation", "Need browser automation")
    # discovered -> testing is not allowed
    with pytest.raises(AcquisitionError, match="Invalid acquisition transition"):
        acq.advance(rec.record_id, "testing")


def test_invalid_decision_rejected(tmp_path: Path):
    acq = CapabilityAcquisition(tmp_path)
    rec = acq.discover("mqtt_integration", "Need MQTT")
    with pytest.raises(AcquisitionError, match="decision"):
        acq.research(rec.record_id, "teleport", "nope")


def test_retire_and_health(tmp_path: Path):
    acq = CapabilityAcquisition(tmp_path)
    rec = acq.discover("async_execution", "Need background tasks")
    acq.research(rec.record_id, "reuse", "internal runner", reuse_target="ghost_worker")
    acq.advance(rec.record_id, "feasible")
    acq.advance(rec.record_id, "building")
    acq.advance(rec.record_id, "testing")
    acq.advance(rec.record_id, "ready_to_activate")
    acq.advance(rec.record_id, "active")
    acq.mark_health(rec.record_id, "ok: 5/5 checks pass")
    assert acq.get(rec.record_id).health == "ok: 5/5 checks pass"
    # R08: active -> disabled -> retired (retirement goes through disabled)
    acq.advance(rec.record_id, "disabled")
    rec = acq.retire(rec.record_id, "superseded by upstream")
    assert rec.status == "retired"
    assert rec.retired_reason == "superseded by upstream"
    assert rec not in acq.active_or_pending()


def test_degraded_recover_and_verify_live(tmp_path: Path):
    acq = CapabilityAcquisition(tmp_path)
    rec = acq.discover("pdf_parsing", "extraction failing")
    acq.research(rec.record_id, "build", "no reuse target")
    for status in ("feasible", "building", "testing", "ready_to_activate", "active"):
        acq.advance(rec.record_id, status)
    # transition table: active -> degraded -> active and active -> degraded -> disabled
    rec = acq.advance(rec.record_id, "degraded")
    assert rec.status == "degraded"
    rec = acq.advance(rec.record_id, "active")
    assert rec.status == "active"
    acq.advance(rec.record_id, "degraded")
    rec = acq.advance(rec.record_id, "disabled")
    assert rec.status == "disabled"
    # invalid per table: disabled -> active must be refused
    with pytest.raises(AcquisitionError, match="Invalid acquisition transition"):
        acq.advance(rec.record_id, "active")


def test_verify_live_requires_active_and_evidence(tmp_path: Path):
    acq = CapabilityAcquisition(tmp_path)
    rec = acq.discover("pdf_parsing", "extraction failing")
    with pytest.raises(AcquisitionError, match="active"):
        acq.mark_operationally_verified(rec.record_id, "live: parsed 3 PDFs")
    acq.research(rec.record_id, "build", "no reuse target")
    for status in ("feasible", "building", "testing", "ready_to_activate", "active"):
        acq.advance(rec.record_id, status)
    with pytest.raises(AcquisitionError, match="evidence"):
        acq.mark_operationally_verified(rec.record_id, "   ")
    rec = acq.mark_operationally_verified(rec.record_id, "live: parsed 3 PDFs end to end")
    assert rec.operationally_verified is True
    assert rec.verification_evidence == "live: parsed 3 PDFs end to end"
    persisted = acq.get(rec.record_id)
    assert persisted.operationally_verified is True


def test_old_records_without_new_fields_load(tmp_path: Path):
    acq = CapabilityAcquisition(tmp_path)
    rec = acq.discover("legacy_cap", "written before verification fields")
    path = tmp_path / ".ghost" / "acquisitions.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for item in data.values():
        item.pop("operationally_verified", None)
        item.pop("verification_evidence", None)
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = acq.get(rec.record_id)
    assert loaded is not None
    assert loaded.operationally_verified is False
    assert loaded.verification_evidence == ""


def test_corrupt_state_fails_closed(tmp_path: Path):
    path = tmp_path / ".ghost" / "acquisitions.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ invalid json", encoding="utf-8")
    acq = CapabilityAcquisition(tmp_path)
    with pytest.raises(AcquisitionError, match="corrupt"):
        acq.list()
    with pytest.raises(AcquisitionError, match="corrupt"):
        acq.discover("x", "y")
