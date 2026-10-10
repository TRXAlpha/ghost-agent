"""TA3: job leases and generation guards."""
import json

from ghost_agent.runtime import ScheduledJob, Scheduler
from ghost_agent.worker import OutcomeWorker, WorkerResult


def test_claim_increments_generation_and_sets_lease(tmp_path):
    scheduler = Scheduler(tmp_path)
    scheduler.enqueue(ScheduledJob("one", "g", budget={"lease_seconds": 60}))
    job = scheduler.claim_next({"lease_seconds": 60}, owner="worker_x")
    assert job.generation == 1
    assert job.lease_expires_at is not None
    assert job.lease_expires_at > job.claimed_at


def test_mark_with_stale_generation_raises_and_changes_nothing(tmp_path):
    scheduler = Scheduler(tmp_path)
    scheduler.enqueue(ScheduledJob("one", "g"))
    scheduler.claim_next()
    before = scheduler.path.read_text(encoding="utf-8")
    with __import__("pytest").raises(ValueError, match="stale generation"):
        scheduler.mark("one", "completed", expected={"running"}, generation=0)
    assert scheduler.path.read_text(encoding="utf-8") == before
    # Correct generation still works.
    scheduler.mark("one", "completed", expected={"running"}, generation=1)


def test_mark_without_generation_still_works(tmp_path):
    scheduler = Scheduler(tmp_path)
    scheduler.enqueue(ScheduledJob("one", "g"))
    scheduler.claim_next()
    job = scheduler.mark("one", "paused", expected={"running"})
    assert job.status == "paused"
    assert job.lease_expires_at is None


def test_expired_lease_paused_at_startup_with_event(tmp_path):
    scheduler = Scheduler(tmp_path)
    scheduler.enqueue(ScheduledJob("one", "g"))
    scheduler.claim_next(owner="worker_dead")
    jobs = scheduler._load()
    jobs[0]["lease_expires_at"] = 1.0  # far in the past
    scheduler._save(jobs)

    worker = OutcomeWorker(tmp_path)
    worker.run_next(lambda job: WorkerResult(job.job_id, "completed"))
    kinds = [event.kind for event in worker.events.recent(100)]
    assert "job.lease_expired" in kinds
    jobs_now = {job.job_id: job for job in scheduler.list()}
    assert jobs_now["one"].status == "paused"


def test_invalid_lease_seconds_falls_back_to_six_hours(tmp_path):
    scheduler = Scheduler(tmp_path)
    scheduler.enqueue(ScheduledJob("one", "g", budget={"lease_seconds": 30}))
    job = scheduler.claim_next({"lease_seconds": 30})
    assert job.generation == 1
    assert job.lease_expires_at is not None
    # Default 21600s = 6h: lease expiry within [claimed_at + 21599, claimed_at + 21601].
    assert abs((job.lease_expires_at - job.claimed_at) - 21600) < 1


def test_legacy_scheduler_json_without_new_keys_loads(tmp_path):
    scheduler = Scheduler(tmp_path)
    legacy = [{"job_id": "old", "goal_id": "g", "priority": 50, "status": "queued",
               "budget": {}, "created_at": 1.0, "owner": None, "attempts": 0,
               "claimed_at": None}]
    scheduler.path.write_text(json.dumps(legacy), encoding="utf-8")
    jobs = scheduler.list()
    assert len(jobs) == 1
    assert jobs[0].generation == 0
    assert jobs[0].lease_expires_at is None
    claimed = scheduler.claim_next()
    assert claimed.generation == 1
