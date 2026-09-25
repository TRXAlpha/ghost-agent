import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from ghost_agent.runtime import ScheduledJob, Scheduler
from ghost_agent.worker import OutcomeWorker, WorkerResult


def test_exact_claim_leaves_older_high_priority_job_queued(tmp_path):
    scheduler = Scheduler(tmp_path)
    scheduler.enqueue(ScheduledJob("old", "g", priority=100))
    scheduler.enqueue(ScheduledJob("requested", "g", priority=1))
    assert scheduler.claim_next(job_id="requested").job_id == "requested"
    assert scheduler.next().job_id == "old"


@pytest.mark.parametrize("content", ["{broken", "{}", "[{}]"])
def test_corrupt_scheduler_is_preserved(tmp_path, content):
    scheduler = Scheduler(tmp_path)
    scheduler.path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt"):
        scheduler.enqueue(ScheduledJob("new", "g"))
    assert scheduler.path.read_text() == content


def test_duplicate_and_terminal_state_rewrites_are_refused(tmp_path):
    scheduler = Scheduler(tmp_path)
    scheduler.enqueue(ScheduledJob("one", "g"))
    with pytest.raises(ValueError, match="already"):
        scheduler.enqueue(ScheduledJob("one", "g"))
    scheduler.claim_next()
    scheduler.mark("one", "completed")
    with pytest.raises(ValueError, match="transition"):
        scheduler.mark("one", "running")


def test_two_processes_cannot_claim_the_same_job(tmp_path):
    scheduler = Scheduler(tmp_path)
    scheduler.enqueue(ScheduledJob("one", "g"))
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
    assert sorted(outputs) == ["none", "one"]
    assert scheduler.list()[0].attempts == 1


def test_dead_worker_is_paused_without_replaying_handler(tmp_path):
    scheduler = Scheduler(tmp_path)
    scheduler.enqueue(ScheduledJob("one", "g"))
    code = '''
import os, sys
from pathlib import Path
from ghost_agent.worker import OutcomeWorker
OutcomeWorker(Path(sys.argv[1])).run_next(lambda job: os._exit(73))
'''
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)],
                            stdin=subprocess.DEVNULL, capture_output=True, timeout=15)
    assert result.returncode == 73, result.stderr
    assert scheduler.list()[0].status == "running"
    worker = OutcomeWorker(tmp_path)
    calls = []
    assert worker.run_next(lambda job: calls.append(job)) is None
    assert scheduler.list()[0].status == "paused"
    assert calls == []
    assert len(worker.attention.pending()) == 1
    assert worker.run_next(lambda job: calls.append(job)) is None
    assert len(worker.attention.pending()) == 1
    scheduler.resume("one")  # Explicit operator review/resume, not an automatic retry.
    assert worker.run_next(lambda job: WorkerResult(job.job_id, "completed")).status == "completed"
    assert scheduler.list()[0].attempts == 2


def test_handler_cannot_report_success_for_another_job(tmp_path):
    worker = OutcomeWorker(tmp_path)
    worker.scheduler.enqueue(ScheduledJob("one", "g"))
    result = worker.run_next(lambda job: WorkerResult("other", "completed"))
    assert result.status == "failed"


def test_unknown_resources_and_corrupt_goals_fail_closed(tmp_path):
    scheduler = Scheduler(tmp_path)
    scheduler.enqueue(ScheduledJob("gpu", "g", budget={"vram_bytes": 1024}))
    assert scheduler.next({}) is None
    assert scheduler.next({"vram_bytes": 2048}).job_id == "gpu"
    (tmp_path / ".ghost" / "goals.json").write_text("{broken", encoding="utf-8")
    assert scheduler.next({"vram_bytes": 2048}) is None


def test_job_cli_exposes_explicit_recovery_controls(tmp_path, monkeypatch, capsys):
    from ghost_agent.cli import _cmd_jobs
    monkeypatch.chdir(tmp_path)
    scheduler = Scheduler(tmp_path)
    scheduler.enqueue(ScheduledJob("one", "g"))
    _cmd_jobs(SimpleNamespace(action="pause", job_id="one"))
    assert scheduler.list()[0].status == "paused"
    _cmd_jobs(SimpleNamespace(action="resume", job_id="one"))
    assert scheduler.list()[0].status == "queued"
    scheduler.claim_next()
    _cmd_jobs(SimpleNamespace(action="cancel", job_id="one"))
    assert scheduler.list()[0].status == "running"
    assert "refused" in capsys.readouterr().out
