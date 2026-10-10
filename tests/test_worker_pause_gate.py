"""An explained pause (yield to owner) must not stall the queue; a crash pause must.

Live benchmark 2026-10-10: one job yielded to the owner and every later job
stayed 'created', because the worker refused all work while any worker-owned
job was paused.
"""
from ghost_agent.runtime import ScheduledJob, Scheduler
from ghost_agent.worker import OutcomeWorker, WorkerResult


def _two_jobs(root):
    sched = Scheduler(root)
    sched.enqueue(ScheduledJob("job_a", "g1", priority=60))
    sched.enqueue(ScheduledJob("job_b", "g1", priority=50))
    return sched


def test_yield_pause_does_not_block_next_job(tmp_path):
    sched = _two_jobs(tmp_path)
    worker = OutcomeWorker(tmp_path)

    def yield_handler(job):
        sched.mark(job.job_id, "paused", expected={"running"}, paused_by="yield_to_owner")
        return WorkerResult(job.job_id, "paused", "yielded_to_owner")

    first = worker.run_next(yield_handler)
    assert first.job_id == "job_a" and first.status == "paused"
    second = worker.run_next(lambda job: WorkerResult(job.job_id, "completed", "ok"))
    assert second is not None and second.job_id == "job_b"
    paused = next(j for j in sched.list() if j.job_id == "job_a")
    assert paused.paused_by == "yield_to_owner"


def test_unexplained_worker_pause_still_blocks(tmp_path):
    sched = _two_jobs(tmp_path)
    worker = OutcomeWorker(tmp_path)

    def crash_like(job):
        sched.mark(job.job_id, "paused", expected={"running"})
        return WorkerResult(job.job_id, "paused", "interrupted")

    worker.run_next(crash_like)
    assert worker.run_next(lambda job: WorkerResult(job.job_id, "completed", "ok")) is None


def test_resume_clears_paused_by(tmp_path):
    sched = _two_jobs(tmp_path)
    sched.mark("job_a", "running", expected={"queued"})
    sched.mark("job_a", "paused", expected={"running"}, paused_by="yield_to_owner")
    sched.mark("job_a", "queued", expected={"paused"})
    assert next(j for j in sched.list() if j.job_id == "job_a").paused_by is None


def test_benchmark_jobs_do_not_yield(tmp_path):
    from ghost_agent.policy import PolicyStore
    from ghost_agent.proactive import queue_sandbox_outcome
    for mode in ("build", "improve"):
        PolicyStore(tmp_path).set(mode, True)
    bench = queue_sandbox_outcome(tmp_path, "build a tiny calculator for bench", "m", source="benchmark")
    pro = queue_sandbox_outcome(tmp_path, "build a tiny calculator proactively", "m", source="proactive")
    assert bench.acceptance_contract["yield_on_owner_return"] is False
    assert pro.acceptance_contract["yield_on_owner_return"] is True
