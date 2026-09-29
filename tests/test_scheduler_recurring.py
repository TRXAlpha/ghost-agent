"""OJ07: recurring schedules — due-gating, requeue-on-settle, catch-up cap, CLI.

Complements tests/test_runtime.py (base scheduler) and
tests/test_scheduler_recovery.py (claim/settle recovery semantics).
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ghost_agent.runtime import Event, EventBus, ScheduledJob, Scheduler


def test_interval_job_requeues_after_settle_with_next_due(tmp_path: Path) -> None:
    scheduler = Scheduler(tmp_path)
    scheduler.enqueue(ScheduledJob("r1", "g", recurrence={"kind": "interval", "interval_seconds": 3600},
                                   due_at=time.time() - 1))
    claimed = scheduler.claim_next()
    assert claimed is not None and claimed.job_id == "r1"
    done = scheduler.mark("r1", "completed")
    # Settle re-queues the recurring job in the same locked save.
    assert done.status == "queued"
    assert done.attempts == 0
    assert done.due_at is not None
    first_due = done.due_at
    # Next instance must NOT be claimable before its due time.
    again = scheduler.claim_next()
    assert again is None
    # Simulate the passage of the interval by rewinding due_at, then claim.
    jobs = json.loads((tmp_path / ".ghost" / "scheduler.json").read_text())
    jobs[0]["due_at"] = first_due - 3601
    (tmp_path / ".ghost" / "scheduler.json").write_text(json.dumps(jobs))
    claimed2 = scheduler.claim_next()
    assert claimed2 is not None and claimed2.job_id == "r1"
    assert claimed2.attempts == 1  # reset on requeue, incremented on the new claim


def test_catchup_snaps_forward_and_emits_skip_event(tmp_path: Path) -> None:
    events = EventBus(tmp_path)
    scheduler = Scheduler(tmp_path, events)
    # due_at 100 periods in the past relative to settle time.
    due_at = time.time() - 100 * 60
    scheduler.enqueue(ScheduledJob("r2", "g", recurrence={"kind": "interval", "interval_seconds": 60},
                                   due_at=due_at))
    scheduler.claim_next()
    scheduler.mark("r2", "failed")
    kinds = [event.kind for event in events.recent(limit=100)]
    assert "job.recurrence.skipped" in kinds
    assert "job.recurrence.requeued" in kinds
    job = scheduler.list()[0]
    assert job.status == "queued"
    assert job.due_at > time.time()  # snapped to the next future period, backlog dropped


def test_paused_recurring_job_stays_paused(tmp_path: Path) -> None:
    scheduler = Scheduler(tmp_path)
    scheduler.enqueue(ScheduledJob("r3", "g", recurrence={"kind": "interval", "interval_seconds": 60},
                                   due_at=time.time() - 1))
    scheduler.claim_next()
    scheduler.mark("r3", "paused")
    # Paused stays paused — no requeue loop on a paused recurring job.
    assert scheduler.list()[0].status == "paused"
    # And a paused recurring job cannot be claimed.
    assert scheduler.claim_next() is None


def test_emergency_stopped_recurring_job_does_not_requeue_dispatch(tmp_path: Path) -> None:
    scheduler = Scheduler(tmp_path)
    scheduler.enqueue(ScheduledJob("r4", "g", recurrence={"kind": "interval", "interval_seconds": 60},
                                   due_at=time.time() - 1))
    from ghost_agent.runtime import EmergencyStop
    EmergencyStop(tmp_path).engage("test")
    # Emergency stop blocks the claim path; settle still re-queues the next instance
    # (due-gating keeps it inert until stop clears).
    scheduler.claim_next()  # returns None under stop
    assert scheduler.next() is None


def test_old_format_record_without_recurrence_loads_unchanged(tmp_path: Path) -> None:
    # Hand-write a legacy record exactly as pre-OJ07 code would have persisted it:
    # no recurrence/due_at keys at all.
    path = tmp_path / ".ghost" / "scheduler.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([{"job_id": "legacy", "goal_id": "g", "priority": 50, "status": "queued",
                                 "budget": {}, "created_at": 1.0, "owner": None, "attempts": 0,
                                 "claimed_at": None}]))
    scheduler = Scheduler(tmp_path)
    job = scheduler.list()[0]
    assert job.recurrence is None and job.due_at is None
    # Round-trip through mark still works for legacy jobs.
    assert scheduler.mark("legacy", "paused").status == "paused"


def test_corrupt_recurrence_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / ".ghost" / "scheduler.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([{"job_id": "bad", "goal_id": "g", "status": "queued",
                                 "recurrence": {"kind": "cron", "interval_seconds": "60"}}]))
    with pytest.raises(ValueError):
        Scheduler(tmp_path).list()


def test_two_processes_cannot_both_claim_requeued_instance(tmp_path: Path) -> None:
    scheduler = Scheduler(tmp_path)
    scheduler.enqueue(ScheduledJob("r5", "g", recurrence={"kind": "interval", "interval_seconds": 3600},
                                   due_at=time.time() - 1))
    scheduler.claim_next()
    scheduler.mark("r5", "completed")  # requeues in the same lock, due one interval later
    # Rewind due_at so the next instance is claimable now (as the daemon would find it after the interval).
    jobs = json.loads((tmp_path / ".ghost" / "scheduler.json").read_text())
    jobs[0]["due_at"] = time.time() - 1
    (tmp_path / ".ghost" / "scheduler.json").write_text(json.dumps(jobs))
    code = '''
import sys, time
from pathlib import Path
from ghost_agent.runtime import Scheduler
scheduler = Scheduler(Path(sys.argv[1]))
for _ in range(100):
    try:
        job = scheduler.claim_next()
        print(job.job_id if job else "none")
        break
    except BlockingIOError:
        time.sleep(0.01)
else:
    raise RuntimeError("lock never released")
'''
    processes = [subprocess.Popen([sys.executable, "-c", code, str(tmp_path)],
                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
    outputs = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, stderr
        outputs.append(stdout.decode().strip())
    # Exactly one process wins the claim of the requeued instance; the loser sees nothing.
    assert sorted(outputs) == ["none", "r5"]
    assert scheduler.list()[0].attempts == 1


def test_future_due_recurring_job_not_claimed(tmp_path: Path) -> None:
    scheduler = Scheduler(tmp_path)
    scheduler.enqueue(ScheduledJob("r6", "g", recurrence={"kind": "interval", "interval_seconds": 3600},
                                   due_at=time.time() + 3600))
    assert scheduler.claim_next() is None
    assert scheduler.next() is None


def test_cli_jobs_add_list_round_trip(tmp_path: Path, monkeypatch, capsys) -> None:
    from types import SimpleNamespace
    from ghost_agent.cli import _cmd_jobs
    monkeypatch.chdir(tmp_path)
    _cmd_jobs(SimpleNamespace(action="add", job_id="", goal_id="g1", recurrence="interval:60",
                              priority=50, iterations=3))
    out = capsys.readouterr().out
    assert '"recurrence"' in out and '"interval_seconds": 60' in out
    scheduler = Scheduler(tmp_path)
    jobs = scheduler.list()
    assert len(jobs) == 1 and jobs[0].recurrence == {"kind": "interval", "interval_seconds": 60}
    assert jobs[0].due_at is not None
    # list shows next_due for the recurring queued job
    _cmd_jobs(SimpleNamespace(action="list", job_id="", goal_id="", recurrence="", priority=50, iterations=3))
    out = capsys.readouterr().out
    assert '"next_due"' in out
    # add refuses a malformed recurrence
    _cmd_jobs(SimpleNamespace(action="add", job_id="", goal_id="g2", recurrence="cron:0 9 * * *",
                              priority=50, iterations=3))
    out = capsys.readouterr().out
    assert "refused" in out and "interval:SECONDS" in out


def test_cli_remove_refused_for_queued_oneshot(tmp_path: Path, monkeypatch, capsys) -> None:
    from types import SimpleNamespace
    from ghost_agent.cli import _cmd_jobs
    monkeypatch.chdir(tmp_path)
    scheduler = Scheduler(tmp_path)
    scheduler.enqueue(ScheduledJob("q1", "g"))
    _cmd_jobs(SimpleNamespace(action="remove", job_id="q1", goal_id="", recurrence="", priority=50, iterations=3))
    out = capsys.readouterr().out
    assert "refused" in out
    assert len(scheduler.list()) == 1
    # A recurring job can be removed while queued.
    scheduler.enqueue(ScheduledJob("r9", "g", recurrence={"kind": "interval", "interval_seconds": 60},
                                   due_at=time.time()))
    _cmd_jobs(SimpleNamespace(action="remove", job_id="r9", goal_id="", recurrence="", priority=50, iterations=3))
    assert "refused" not in capsys.readouterr().out
    assert len(scheduler.list()) == 1
