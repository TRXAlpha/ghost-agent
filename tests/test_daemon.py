"""Tests for the GhostDaemon (T12): thin composition over OvernightRunner."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from ghost_agent.daemon import GhostDaemon
from ghost_agent.overnight import OvernightCycleResult
from ghost_agent.runtime import EmergencyStop


class FakeRunner:
    """Fake OvernightRunner: scripted results, no cycle tracking of its own."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def run_cycle(self):
        self.calls += 1
        if self.calls > 10:  # hard guard: fail instead of hang
            raise AssertionError("runner called more than 10 times")
        if self.results:
            return self.results.pop(0)
        return OvernightCycleResult(cycle=self.calls, ts="t", user_active=True,
                                    quiet_hours=False, emergency_stop=False)


def make_results(n, *, user_active, quiet_hours=False):
    return [OvernightCycleResult(cycle=i + 1, ts="t", user_active=user_active,
                                 quiet_hours=quiet_hours, emergency_stop=False)
            for i in range(n)]


def make_daemon(tmp_path, results, *, trace_every=1, **kw):
    calls = {"trace": 0, "improve": 0, "sync": 0}

    def trace_fn():
        calls["trace"] += 1
        return {"scanned": 1, "signatures": 0, "actions": 0}

    def improver_fn():
        calls["improve"] += 1
        return {"status": "ok"}

    def improve_sync_fn():
        calls["sync"] += 1
        return []

    daemon = GhostDaemon(tmp_path, "m", trace_every=trace_every,
                         runner=FakeRunner(results),
                         trace_fn=trace_fn, improver_fn=improver_fn,
                         improve_sync_fn=improve_sync_fn,
                         sleep_fn=lambda s: None, **kw)
    return daemon, calls


def read_status(tmp_path):
    return json.loads((tmp_path / ".ghost" / "daemon_status.json").read_text(encoding="utf-8"))


def test_cycle_writes_status_file(tmp_path):
    daemon, _ = make_daemon(tmp_path, make_results(1, user_active=False))
    record = daemon.run_cycle()
    data = json.loads((tmp_path / ".ghost" / "daemon_status.json").read_text())
    assert data["cycle"] == 1
    assert isinstance(data["overnight"], dict)
    assert record == data


def test_owner_active_defers_learning_hooks(tmp_path):
    daemon, calls = make_daemon(tmp_path, make_results(1, user_active=True))
    record = daemon.run_cycle()
    assert calls["trace"] == 0
    assert calls["improve"] == 0
    assert record["trace_learning"] == "deferred"
    assert record["capability"] == "deferred"


def test_owner_away_runs_hooks(tmp_path):
    daemon, calls = make_daemon(tmp_path, make_results(1, user_active=False))
    record = daemon.run_cycle()
    assert calls["trace"] == 1
    assert calls["improve"] == 1
    assert record["trace_learning"]["scanned"] == 1


def test_quiet_hours_defers_improver_only(tmp_path):
    daemon, calls = make_daemon(tmp_path, make_results(1, user_active=False, quiet_hours=True))
    record = daemon.run_cycle()
    assert calls["trace"] == 1
    assert calls["improve"] == 0
    assert record["capability"] == "deferred"


def test_trace_every_cadence(tmp_path):
    daemon, calls = make_daemon(tmp_path, make_results(6, user_active=False), trace_every=3)
    for _ in range(6):
        daemon.run_cycle()
    assert calls["trace"] == 2
    assert calls["improve"] == 6


def test_improve_sync_runs_when_owner_away_even_in_quiet_hours(tmp_path):
    daemon, calls = make_daemon(tmp_path, make_results(1, user_active=False, quiet_hours=True))
    record = daemon.run_cycle()
    assert calls["sync"] == 1
    assert record["improve_sync"] == []


def test_improve_sync_deferred_when_owner_active(tmp_path):
    daemon, calls = make_daemon(tmp_path, make_results(1, user_active=True))
    record = daemon.run_cycle()
    assert calls["sync"] == 0
    assert record["improve_sync"] == "deferred"


def test_improve_sync_error_recorded_not_raised(tmp_path):
    def bad_sync():
        raise RuntimeError("sync boom")

    daemon = GhostDaemon(tmp_path, "m", trace_every=1,
                         runner=FakeRunner(make_results(1, user_active=False)),
                         trace_fn=lambda: {}, improver_fn=lambda: {},
                         improve_sync_fn=bad_sync,
                         sleep_fn=lambda s: None)
    record = daemon.run_cycle()
    assert record["improve_sync"]["status"] == "error"
    assert "sync boom" in record["improve_sync"]["error"]


def test_hook_exception_recorded_not_raised(tmp_path):
    def bad_trace():
        raise RuntimeError("boom")

    daemon = GhostDaemon(tmp_path, "m", trace_every=1,
                         runner=FakeRunner(make_results(2, user_active=False)),
                         trace_fn=bad_trace,
                         improver_fn=lambda: {"status": "ok"},
                         sleep_fn=lambda s: None)
    record = daemon.run_cycle()
    assert record["trace_learning"]["status"] == "error"
    assert "boom" in record["trace_learning"]["error"]
    next_record = daemon.run_cycle()  # next cycle still runs
    assert next_record["cycle"] == 2


def test_run_stops_at_max_cycles_with_counting_runner(tmp_path, permissive_policy):
    runner = FakeRunner(make_results(3, user_active=False))
    daemon = GhostDaemon(tmp_path, "m", runner=runner,
                         trace_fn=lambda: {}, improver_fn=lambda: {},
                         sleep_fn=lambda s: None)
    n = daemon.run(max_cycles=3)
    assert n == 3
    assert runner.calls == 3


def test_run_stops_on_emergency_stop(tmp_path, permissive_policy):
    EmergencyStop(tmp_path).engage("test")
    daemon, calls = make_daemon(tmp_path, make_results(5, user_active=False))
    n = daemon.run()
    assert n == 0
    assert calls["trace"] == 0
    data = json.loads((tmp_path / ".ghost" / "daemon_status.json").read_text())
    assert data["stopped"] is True
    assert data["total_cycles"] == 0


def test_unknown_idle_is_treated_as_active(tmp_path, monkeypatch):
    import ghost_agent.proactive as proactive_mod
    monkeypatch.setattr(proactive_mod, "idle_seconds", lambda: None)

    calls = {"proactive": 0, "worker": 0, "improve": 0}

    def fake_proactive(*a, **kw):
        calls["proactive"] += 1
        return {"status": "ok"}

    def fake_worker():
        calls["worker"] += 1
        return None

    runner = OvernightRunnerWithFakes(tmp_path, fake_proactive, fake_worker)
    daemon = GhostDaemon(tmp_path, "m", runner=runner,
                         trace_fn=lambda: {}, improver_fn=lambda: calls.__setitem__("improve", calls["improve"] + 1) or {},
                         sleep_fn=lambda s: None)
    daemon.run_cycle()
    assert calls["worker"] == 0
    assert calls["improve"] == 0


class OvernightRunnerWithFakes:
    """Real OvernightRunner but with injected fakes that record calls."""

    def __init__(self, root, proactive, worker):
        self._runner = __import__("ghost_agent.overnight", fromlist=["OvernightRunner"]).OvernightRunner(
            root, "m", proactive_tick=proactive, worker_fn=worker)

    def run_cycle(self):
        return self._runner.run_cycle()


def test_invalid_arguments(tmp_path):
    with pytest.raises(ValueError):
        GhostDaemon(tmp_path, "  ")
    with pytest.raises(ValueError):
        GhostDaemon(tmp_path, "m", trace_every=0)
    with pytest.raises(ValueError):
        GhostDaemon(tmp_path, "m", interval=0)


def test_cli_daemon_requires_model(tmp_path, monkeypatch, capsys):
    import ghost_agent.cli as cli_mod
    monkeypatch.setattr(cli_mod, "_resolve_model", lambda *a, **kw: "")
    monkeypatch.chdir(tmp_path)
    cli_mod._cmd_daemon(argparse.Namespace(daemon_action="run", model=None,
                                           interval=1, idle_after_seconds=1,
                                           trace_every=1, max_cycles=1))
    assert "A model is required" in capsys.readouterr().out
