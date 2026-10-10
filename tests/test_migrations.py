import json

import pytest

from ghost_agent import migrations
from ghost_agent.runtime import Doctor


def _write_store(root, filename, content='{"x": 1}'):
    path = root / ".ghost" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _read_schema(root):
    return json.loads((root / ".ghost" / "schema.json").read_text(encoding="utf-8"))


def test_check_reports_newer_version(tmp_path):
    schema = tmp_path / ".ghost" / "schema.json"
    schema.parent.mkdir(parents=True)
    schema.write_text(json.dumps({"stores": {"events": 3}, "updated_at": 0.0}))
    problems = migrations.check(tmp_path)
    assert problems == ["events data version 3 is newer than this Ghost (2); refusing"]


def test_doctor_reports_newer_schema_version(tmp_path):
    schema = tmp_path / ".ghost" / "schema.json"
    schema.parent.mkdir(parents=True)
    schema.write_text(json.dumps({"stores": {"events": 99}, "updated_at": 0.0}))
    status = Doctor(tmp_path).check()
    assert "newer than this Ghost" in status["schema"]


def test_apply_with_registered_step_backs_up_then_bumps(tmp_path, monkeypatch):
    _write_store(tmp_path, "events.jsonl", '{"old": true}')
    monkeypatch.setitem(
        migrations.MIGRATIONS, ("events", 1), lambda root: _write_store(root, "events.jsonl", '{"new": true}')
    )
    schema = tmp_path / ".ghost" / "schema.json"
    schema.write_text(json.dumps({"stores": {"events": 1}, "updated_at": 0.0}))

    reports = migrations.apply(tmp_path)

    assert reports == ["events: 1 -> 2"]
    assert (tmp_path / ".ghost" / "events.jsonl").read_text() == '{"new": true}'
    backups = list((tmp_path / ".ghost" / "backups").glob("*"))
    assert len(backups) == 1
    assert (backups[0] / "events.jsonl").read_text() == '{"old": true}'
    assert _read_schema(tmp_path)["stores"]["events"] == 2


def test_apply_dry_run_writes_nothing(tmp_path, monkeypatch):
    _write_store(tmp_path, "events.jsonl")
    calls = []
    monkeypatch.setitem(
        migrations.MIGRATIONS, ("events", 1), lambda root: calls.append(root)
    )
    schema = tmp_path / ".ghost" / "schema.json"
    schema.write_text(json.dumps({"stores": {"events": 1}, "updated_at": 0.0}))

    reports = migrations.apply(tmp_path, dry_run=True)

    assert calls == []
    assert reports == ["events: 1 -> 2 (dry run)"]
    assert not (tmp_path / ".ghost" / "backups").exists()
    assert _read_schema(tmp_path)["stores"]["events"] == 1


def test_apply_missing_step_raises(tmp_path):
    _write_store(tmp_path, "events.jsonl")
    with pytest.raises(ValueError, match="no migration step registered for events"):
        migrations.apply(tmp_path)


def test_stamp_records_current_versions(tmp_path):
    _write_store(tmp_path, "scheduler.json")
    _write_store(tmp_path, "events.jsonl")

    stamped = migrations.stamp(tmp_path)

    versions = _read_schema(tmp_path)["stores"]
    assert versions["scheduler"] == migrations.CURRENT_VERSIONS["scheduler"]
    assert versions["events"] == migrations.CURRENT_VERSIONS["events"]
    assert "scheduler" in stamped and "events" in stamped
    # goals.json does not exist, so it is not stamped.
    assert "goals" not in versions
