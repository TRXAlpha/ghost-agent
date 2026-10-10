"""Tests for `ghost notifications` CLI and snapshot held-notification counts."""
import json

from ghost_agent.cli import main
from ghost_agent.control_plane import GhostSDK
from ghost_agent.runtime import AttentionManager, Notification


def _notify(root, priority, note_id):
    return AttentionManager(root).notify(
        Notification(priority=priority, message=f"note {note_id}", notification_id=note_id))


def _run_cli(capsys, *argv):
    from ghost_agent.cli import main
    import sys
    try:
        monkeypatch_argv = list(argv)
        old_argv = sys.argv
        sys.argv = ["ghost", *monkeypatch_argv]
        try:
            main()
        finally:
            sys.argv = old_argv
    except SystemExit:
        pass


def _prefs(tmp_path):
    prefs = {"quiet_hours_start": 2, "quiet_hours_end": 4}
    (tmp_path / ".ghost").mkdir(exist_ok=True)
    (tmp_path / ".ghost" / "notification_prefs.json").write_text(json.dumps(prefs))


def _patch_hour(monkeypatch, hour):
    from ghost_agent import cli as cli_module
    monkeypatch.setattr(cli_module.time, "localtime",
                        lambda t=None: type("T", (), {"tm_hour": hour})())


def test_list_during_quiet_hours_hides_normal_shows_urgent(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    attention = AttentionManager(tmp_path)
    attention.notify(Notification(priority="normal", message="n1", notification_id="n1"))
    attention.notify(Notification(priority="urgent", message="u1", notification_id="u1"))
    _prefs(tmp_path)
    _patch_hour(monkeypatch, 3)
    _run_cli(capsys, "notifications", "list")
    items = json.loads(capsys.readouterr().out)
    assert [note["notification_id"] for note in items] == ["u1"]


def test_list_all_marks_held_items(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    attention = AttentionManager(tmp_path)
    attention.notify(Notification(priority="normal", message="n1", notification_id="n1"))
    attention.notify(Notification(priority="urgent", message="u1", notification_id="u1"))
    _prefs(tmp_path)
    _patch_hour(monkeypatch, 3)
    _run_cli(capsys, "notifications", "list", "--all")
    items = json.loads(capsys.readouterr().out)
    held = {note["notification_id"]: note.get("held") for note in items}
    assert held == {"n1": "quiet hours", "u1": None}


def test_ack_acknowledges_notification(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    attention = AttentionManager(tmp_path)
    attention.notify(Notification(priority="normal", message="n1", notification_id="n1"))
    _run_cli(capsys, "notifications", "ack", "n1")
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "acknowledged"
    assert attention.pending() == []


def test_snapshot_counts_held_notifications(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    attention = AttentionManager(tmp_path)
    attention.notify(Notification(priority="normal", message="n1", notification_id="n1"))
    attention.notify(Notification(priority="urgent", message="u1", notification_id="u1"))
    sdk = GhostSDK(tmp_path)
    monkeypatch.setattr(sdk, "_quiet_hours", lambda: (3, 4))
    from ghost_agent import runtime as runtime_module
    monkeypatch.setattr(runtime_module.time, "localtime",
                        lambda t=None: type("T", (), {"tm_hour": 3})())
    snapshot = sdk.snapshot()
    ids = [note["notification_id"] for note in snapshot["notifications"]]
    assert ids == ["u1"]
    assert snapshot["held_notifications"] == 1
