"""TJ1: feasibility record on CapabilityAcquisition records."""
import json
import time

import pytest

from ghost_agent.capability_acquisition import (
    AcquisitionError,
    CapabilityAcquisition,
)


def _store(tmp_path):
    return CapabilityAcquisition(tmp_path)


def _sample(**overrides):
    data = {
        "need": "A feasibility record for R06",
        "approaches": ["reuse openjarvis_memory", "build new"],
        "sources": [{"url": "https://example.com/docs", "checked_at": 1700000000.0,
                     "kind": "official_docs"}],
        "cost": "",
        "maintenance": "low",
        "next_step": "prototype",
    }
    data.update(overrides)
    return data


def test_record_feasibility_moves_discovered_to_researched(tmp_path):
    store = _store(tmp_path)
    record = store.discover("web_scraping", "missing capability")
    assert record.status == "discovered"
    updated = store.record_feasibility(record.record_id, _sample())
    assert updated.status == "researched"
    # via the transition table, so the status really came from STATES
    assert "researched" in CapabilityAcquisition.STATES["discovered"]


def test_record_feasibility_preserves_non_discovered_status(tmp_path):
    store = _store(tmp_path)
    record = store.discover("web_scraping", "missing")
    store.research(record.record_id, "build", "need new code")
    updated = store.record_feasibility(record.record_id, _sample())
    assert updated.status == "researched"


def test_record_feasibility_merges_into_existing(tmp_path):
    store = _store(tmp_path)
    record = store.discover("web_scraping", "missing")
    store.record_feasibility(record.record_id, {"need": "first"})
    updated = store.record_feasibility(record.record_id, {"cost": "moderate"})
    assert updated.feasibility["need"] == "first"
    assert updated.feasibility["cost"] == "moderate"
    assert updated.updated_at >= record.updated_at


def test_feasibility_rejects_unknown_key(tmp_path):
    with pytest.raises(AcquisitionError):
        CapabilityAcquisition.validate_feasibility({"bogus_key": "x"})


def test_feasibility_rejects_oversize_string(tmp_path):
    with pytest.raises(AcquisitionError):
        CapabilityAcquisition.validate_feasibility({"need": "x" * 501})


def test_feasibility_rejects_oversize_list(tmp_path):
    with pytest.raises(AcquisitionError):
        CapabilityAcquisition.validate_feasibility({"approaches": ["a"] * 11})


def test_feasibility_rejects_bad_source_kind(tmp_path):
    with pytest.raises(AcquisitionError):
        CapabilityAcquisition.validate_feasibility(
            {"sources": [{"url": "https://x", "checked_at": 1.0, "kind": "blog"}]})


def test_feasibility_rejects_source_missing_url_or_path(tmp_path):
    with pytest.raises(AcquisitionError):
        CapabilityAcquisition.validate_feasibility(
            {"sources": [{"checked_at": 1.0, "kind": "repo"}]})


def test_feasibility_empty_cost_is_unknown_not_free(tmp_path):
    store = _store(tmp_path)
    record = store.discover("web_scraping", "missing")
    updated = store.record_feasibility(record.record_id, _sample())
    assert updated.feasibility["cost"] == ""


def test_old_records_load_without_feasibility(tmp_path):
    store = _store(tmp_path)
    record = store.discover("web_scraping", "missing")
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    raw[record.record_id].pop("feasibility")
    store.path.write_text(json.dumps(raw), encoding="utf-8")
    loaded = store.get(record.record_id)
    assert loaded is not None
    assert loaded.feasibility == {}
    # and recording still works on the loaded (old) record
    updated = store.record_feasibility(record.record_id, {"need": "n"})
    assert updated.feasibility["need"] == "n"


def _cli_ns(**kw):
    from types import SimpleNamespace
    defaults = dict(capability_action="", record_id="", candidate="", status=None,
                    health=None, reason="", capability="", symptom="", gap_id="",
                    decision="", rationale="", reuse_target="", set_json="")
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def test_cli_feasibility_set_json_and_report(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from ghost_agent import cli
    store = _store(tmp_path)
    record = store.discover("web_scraping", "missing")
    payload_path = tmp_path / "feas.json"
    payload_path.write_text(json.dumps(_sample()), encoding="utf-8")
    cli._cmd_capability(_cli_ns(capability_action="feasibility", record_id=record.record_id,
                                set_json=str(payload_path)))
    updated = store.get(record.record_id)
    assert updated.status == "researched"
    assert json.loads(capsys.readouterr().out)["feasibility"]["need"] == _sample()["need"]

    cli._cmd_capability(_cli_ns(capability_action="feasibility", record_id=record.record_id))
    report = capsys.readouterr().out
    assert "cost: unknown" in report
    assert "maintenance: low" in report
    assert "[official_docs] https://example.com/docs" in report
