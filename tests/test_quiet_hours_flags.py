"""Tests for quiet-hours and digest-window flags (TA5)."""

import argparse

import pytest

import ghost_agent.cli as cli
import ghost_agent.daemon as daemon_module
from ghost_agent.overnight import DEFAULT_DIGEST_WINDOW, DEFAULT_QUIET_HOURS


def _parse(argv):
    parser = argparse.ArgumentParser()
    # Minimal replica of main() parser wiring for the two run subparsers.
    sub = parser.add_subparsers(dest="command")
    overnight_parser = sub.add_parser("overnight")
    overnight_sub = overnight_parser.add_subparsers(dest="overnight_action")
    overnight_run = overnight_sub.add_parser("run")
    overnight_run.add_argument("--interval", type=float, default=300)
    overnight_run.add_argument("--idle-after-seconds", type=float, default=900)
    overnight_run.add_argument("--max-cycles", type=int, default=None)
    overnight_run.add_argument("--quiet-hours", nargs=2, type=int, default=None)
    overnight_run.add_argument("--digest-window", nargs=2, type=int, default=None)
    daemon_parser = sub.add_parser("daemon")
    daemon_sub = daemon_parser.add_subparsers(dest="daemon_action")
    daemon_run = daemon_sub.add_parser("run")
    daemon_run.add_argument("--interval", type=float, default=300)
    daemon_run.add_argument("--idle-after-seconds", type=float, default=900)
    daemon_run.add_argument("--max-cycles", type=int, default=None)
    daemon_run.add_argument("--trace-every", type=int, default=10)
    daemon_run.add_argument("--model", default=None)
    daemon_run.add_argument("--quiet-hours", nargs=2, type=int, default=None)
    daemon_run.add_argument("--digest-window", nargs=2, type=int, default=None)
    return parser.parse_args(argv)


def test_overnight_run_passes_flags_to_runner(tmp_path, monkeypatch, capsys):
    captured: dict = {}

    class FakeRunner:
        log_path = tmp_path / "log.jsonl"

        def __init__(self, root, model, **kwargs):
            captured.update(kwargs)

        def run(self, max_cycles=None):
            return 0

    import ghost_agent.overnight as overnight_module
    monkeypatch.setattr(overnight_module, "OvernightRunner", FakeRunner)
    args = _parse(["overnight", "run", "--quiet-hours", "22", "7",
                   "--digest-window", "6", "8"])
    monkeypatch.chdir(tmp_path)
    cli._cmd_overnight(args)
    assert captured["quiet_hours"] == (22, 7)
    assert captured["digest_window"] == (6, 8)


def test_overnight_run_defaults_without_flags(tmp_path, monkeypatch):
    captured: dict = {}

    class FakeRunner:
        log_path = tmp_path / "log.jsonl"

        def __init__(self, root, model, **kwargs):
            captured.update(kwargs)

        def run(self, max_cycles=None):
            return 0

    import ghost_agent.overnight as overnight_module
    monkeypatch.setattr(overnight_module, "OvernightRunner", FakeRunner)
    args = _parse(["overnight", "run"])
    monkeypatch.chdir(tmp_path)
    cli._cmd_overnight(args)
    assert captured["quiet_hours"] == DEFAULT_QUIET_HOURS
    assert captured["digest_window"] == DEFAULT_DIGEST_WINDOW


def test_overnight_run_rejects_out_of_range_hours(tmp_path, monkeypatch, capsys):
    args = _parse(["overnight", "run", "--quiet-hours", "24", "7"])
    monkeypatch.chdir(tmp_path)
    cli._cmd_overnight(args)
    assert "hours must be 0-23" in capsys.readouterr().out


def test_overnight_run_rejects_out_of_range_digest_window(tmp_path, monkeypatch, capsys):
    args = _parse(["overnight", "run", "--digest-window", "6", "-1"])
    monkeypatch.chdir(tmp_path)
    cli._cmd_overnight(args)
    assert "hours must be 0-23" in capsys.readouterr().out


def test_daemon_run_quiet_hours_reach_runner(tmp_path, monkeypatch, capsys):
    captured: dict = {}

    def fake_init(self, root, model, **kwargs):
        captured.update(kwargs)
        captured["_root"] = root

    monkeypatch.setattr(daemon_module.GhostDaemon, "__init__", fake_init)
    monkeypatch.setattr(daemon_module.GhostDaemon, "run",
                        lambda self, max_cycles=None: 0)
    args = _parse(["daemon", "run", "--quiet-hours", "22", "7",
                   "--digest-window", "5", "9"])
    monkeypatch.chdir(tmp_path)
    cli._cmd_daemon(args)
    assert captured["quiet_hours"] == (22, 7)
    assert captured["digest_window"] == (5, 9)


def test_daemon_run_rejects_out_of_range_hours(tmp_path, monkeypatch, capsys):
    args = _parse(["daemon", "run", "--quiet-hours", "22", "30"])
    monkeypatch.chdir(tmp_path)
    cli._cmd_daemon(args)
    assert "hours must be 0-23" in capsys.readouterr().out


def test_ghost_daemon_forwards_to_default_runner(tmp_path):
    from ghost_agent.daemon import GhostDaemon
    daemon = GhostDaemon(tmp_path, "scripted", quiet_hours=(22, 7),
                         digest_window=(5, 9))
    assert daemon.runner.quiet_hours == (22, 7)
    assert daemon.runner.digest_window == (5, 9)


def test_ghost_daemon_rejects_out_of_range_hours(tmp_path):
    from ghost_agent.daemon import GhostDaemon
    with pytest.raises(ValueError):
        GhostDaemon(tmp_path, "scripted", quiet_hours=(22, 24))
