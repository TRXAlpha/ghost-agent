"""Tests for focus sessions (FocusStore, CLI, notification holding)."""
import json

import pytest

from ghost_agent.cli import main
from ghost_agent.focus import FocusStore
from ghost_agent.runtime import AttentionManager, Notification


def _notify(root, priority, note_id):
    return AttentionManager(root).notify(
        Notification(priority=priority, message=f"note {note_id}", notification_id=note_id))


def _run_cli(*argv):
    import sys
    old_argv = sys.argv
    sys.argv = ["ghost", *argv]
    try:
        main()
    except SystemExit:
        pass
    finally:
        sys.argv = old_argv


def test_start_status_stop_roundtrip(tmp_path):
    store = FocusStore(tmp_path)
    session = store.start("write report", 30, ["hold_notifications"])
    assert session.purpose == "write report"
    assert session.interventions == ["hold_notifications"]
    active = store.active()
    assert active is not None and active.purpose == "write report"
    stopped = store.stop()
    assert stopped is not None and stopped.stopped_at is not None
    assert store.active() is None


def test_start_validates_purpose_minutes_and_interventions(tmp_path):
    store = FocusStore(tmp_path)
    with pytest.raises(ValueError):
        store.start("", 30, [])
    with pytest.raises(ValueError):
        store.start("x" * 201, 30, [])
    with pytest.raises(ValueError):
        store.start("work", 0, [])
    with pytest.raises(ValueError):
        store.start("work", 601, [])
    with pytest.raises(ValueError):
        store.start("work", 30, ["block_apps"])


def test_double_start_is_refused(tmp_path):
    store = FocusStore(tmp_path)
    store.start("first", 30, [])
    with pytest.raises(ValueError):
        store.start("second", 30, [])


def test_session_expires_by_time(tmp_path, monkeypatch):
    store = FocusStore(tmp_path)
    store.start("work", 30, [])
    session = store.active()
    assert session is not None
    monkeypatch.setattr("ghost_agent.focus.time.time", lambda: session.ends_at + 1)
    assert store.active() is None


def test_stop_is_idempotent(tmp_path):
    store = FocusStore(tmp_path)
    assert store.stop() is None
    store.start("work", 30, [])
    assert store.stop() is not None
    assert store.stop() is None


def test_corrupt_file_active_returns_none_start_raises(tmp_path):
    store = FocusStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{not json")
    assert store.active() is None
    with pytest.raises(ValueError):
        store.start("work", 30, [])


def test_hold_notifications_holds_normal_silent_urgent_passes(tmp_path):
    attention = AttentionManager(tmp_path)
    _notify(tmp_path, "normal", "n1")
    _notify(tmp_path, "silent", "s1")
    _notify(tmp_path, "urgent", "u1")
    _notify(tmp_path, "important", "i1")
    held = attention.deliverable(focus_active=True)
    ids = {note.notification_id for note in held}
    assert "u1" in ids and "n1" not in ids and "s1" not in ids


def test_cli_focus_start_status_stop(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _run_cli("focus", "start", "--purpose", "deep work", "--minutes", "45", "--hold-notifications")
    started = json.loads(capsys.readouterr().out)
    assert started["purpose"] == "deep work"
    assert started["interventions"] == ["hold_notifications"]
    _run_cli("focus", "status")
    assert json.loads(capsys.readouterr().out)["purpose"] == "deep work"
    _run_cli("focus", "stop")
    assert json.loads(capsys.readouterr().out)["stopped_at"] is not None
    _run_cli("focus", "status")
    assert "No active focus session" in capsys.readouterr().out


def test_cli_focus_double_start_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _run_cli("focus", "start", "--purpose", "one", "--minutes", "10")
    capsys.readouterr()
    _run_cli("focus", "start", "--purpose", "two", "--minutes", "10")
    assert "already active" in capsys.readouterr().out


def test_cli_notifications_all_labels_focus_holds(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _notify(tmp_path, "normal", "n1")
    _notify(tmp_path, "urgent", "u1")
    _run_cli("focus", "start", "--purpose", "work", "--minutes", "30", "--hold-notifications")
    capsys.readouterr()
    _run_cli("notifications", "list", "--all")
    items = json.loads(capsys.readouterr().out)
    held = {note["notification_id"]: note.get("held") for note in items}
    assert held == {"n1": "focus", "u1": None}


def test_cli_notifications_all_labels_quiet_hours_without_focus(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _notify(tmp_path, "normal", "n1")
    (tmp_path / ".ghost").mkdir(exist_ok=True)
    (tmp_path / ".ghost" / "notification_prefs.json").write_text(
        json.dumps({"quiet_hours_start": 2, "quiet_hours_end": 4}))
    from ghost_agent import cli as cli_module
    monkeypatch.setattr(cli_module.time, "localtime",
                        lambda t=None: type("T", (), {"tm_hour": 3})())
    _run_cli("notifications", "list", "--all")
    items = json.loads(capsys.readouterr().out)
    held = {note["notification_id"]: note.get("held") for note in items}
    assert held == {"n1": "quiet hours"}
