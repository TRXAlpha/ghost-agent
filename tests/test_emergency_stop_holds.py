"""TB5: emergency stop holds, observation latency, and status report (R04)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ghost_agent.cli import _cmd_emergency_stop
from ghost_agent.runtime import EmergencyStop, ScheduledJob, Scheduler
from ghost_agent.security import SecurityMonitor
from ghost_agent.overnight import OvernightRunner
from ghost_agent.worker import OutcomeWorker


def _queue_jobs(tmp_path: Path) -> None:
    scheduler = Scheduler(tmp_path)
    scheduler.enqueue(ScheduledJob(job_id="queued-1", goal_id="g"))
    scheduler.enqueue(ScheduledJob(job_id="queued-2", goal_id="g"))
    paused = Scheduler(tmp_path).mark("queued-2", "paused", expected={"queued"})
    # Make a manually paused job and a running one for contrast
    scheduler.enqueue(ScheduledJob(job_id="running-1", goal_id="g"))
    scheduler.mark("running-1", "running", expected={"queued"})
    scheduler.enqueue(ScheduledJob(job_id="manual-paused", goal_id="g"))
    scheduler.mark("manual-paused", "paused", expected={"queued"})


def test_engage_pauses_queued_jobs_disengage_restores_only_those(tmp_path: Path) -> None:
    _queue_jobs(tmp_path)
    scheduler = Scheduler(tmp_path)
    stop = EmergencyStop(tmp_path)
    stop.engage("test")
    held = scheduler.hold_all("test")
    statuses = {job.job_id: job.status for job in scheduler.list()}
    assert statuses["queued-1"] == "paused"
    assert statuses["manual-paused"] == "paused"  # manual pause untouched
    released = scheduler.release_emergency_stop_holds()
    statuses = {job.job_id: job.status for job in scheduler.list()}
    assert statuses["queued-1"] == "queued"
    assert statuses["manual-paused"] == "paused"  # stays paused
    assert released == ["queued-1"]


def test_running_jobs_untouched_by_hold_all(tmp_path: Path) -> None:
    _queue_jobs(tmp_path)
    scheduler = Scheduler(tmp_path)
    stop = EmergencyStop(tmp_path)
    stop.engage("test")
    scheduler.hold_all("test")
    statuses = {job.job_id: job.status for job in scheduler.list()}
    assert statuses["running-1"] == "running"


def test_observation_recorded_once_per_component(tmp_path: Path) -> None:
    stop = EmergencyStop(tmp_path)
    stop.engage("test")
    monitor = SecurityMonitor(tmp_path)
    runner = OvernightRunner(tmp_path, "m", proactive_tick=lambda *a, **k: {})
    worker = OutcomeWorker(tmp_path)
    for component_recorder, component in ((runner._record_stop_observation, "overnight_runner"),
                                          (worker._record_stop_observation, "worker")):
        component_recorder(component)
        component_recorder(component)  # second observation is a no-op
    events = [e for e in monitor.recent_events() if e["kind"] == "emergency_stop.observed"]
    assert len(events) == 2
    components = sorted(e["detail"]["component"] for e in events)
    assert components == ["overnight_runner", "worker"]
    assert all("latency_s" in e["detail"] for e in events)
    assert stop.observed() == ["overnight_runner", "worker"]


def test_worker_claim_path_records_observation(tmp_path: Path) -> None:
    stop = EmergencyStop(tmp_path)
    stop.engage("test")
    worker = OutcomeWorker(tmp_path)
    result = worker.run_next(lambda job: (_ for _ in ()).throw(AssertionError("no job")))
    assert result is None
    assert "worker" in stop.observed()


def test_status_output(tmp_path: Path, monkeypatch, capsys) -> None:
    from ghost_agent.cli import _cmd_emergency_stop
    monkeypatch.chdir(tmp_path)
    _queue_jobs(tmp_path)
    _cmd_emergency_stop(argparse.Namespace(action="engage", reason="drill"))
    capsys.readouterr()
    _cmd_emergency_stop(argparse.Namespace(action="status", reason=""))
    payload = json.loads(capsys.readouterr().out)
    assert payload["engaged"] is True
    assert isinstance(payload["engaged_at"], float)
    assert payload["observed"] == []
    _cmd_emergency_stop(argparse.Namespace(action="clear", reason=""))
    assert "requeued" in capsys.readouterr().out


def test_cli_clear_requeues_only_emergency_paused(tmp_path: Path, monkeypatch, capsys) -> None:
    from ghost_agent.cli import _cmd_emergency_stop
    monkeypatch.chdir(tmp_path)
    _queue_jobs(tmp_path)
    _cmd_emergency_stop(argparse.Namespace(action="engage", reason="drill"))
    _cmd_emergency_stop(argparse.Namespace(action="clear", reason=""))
    statuses = {job.job_id: job.status for job in Scheduler(tmp_path).list()}
    assert statuses["queued-1"] == "queued"
    assert statuses["manual-paused"] == "paused"
