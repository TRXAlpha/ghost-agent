from pathlib import Path

from ghost_agent.runtime import (
    AttentionManager,
    EmergencyStop,
    Event,
    EventBus,
    Observation,
    Scheduler,
    ScheduledJob,
    WorldModel,
    Notification,
    ResourceMonitor,
    Doctor,
)
from ghost_agent.worker import OutcomeWorker, WorkerResult
from ghost_agent.kernel import GoalStore


def test_event_bus_persists_and_filters_events(tmp_path: Path) -> None:
    bus = EventBus(tmp_path)
    bus.emit(Event("job.queued", {"id": "one"}))
    bus.emit(Event("world.updated", {"id": "two"}))

    assert len(bus.recent()) == 2
    assert bus.recent(kind="job.queued")[0].payload["id"] == "one"


def test_world_model_rejects_stale_observation(tmp_path: Path) -> None:
    world = WorldModel(tmp_path)
    world.observe(Observation("service.api", "service", {"status": "up"}, "healthcheck"))

    assert world.get("service.api").state["status"] == "up"
    assert world.get("service.api", max_age_seconds=-1) is None


def test_scheduler_honors_priority_budget_and_emergency_stop(tmp_path: Path) -> None:
    scheduler = Scheduler(tmp_path)
    scheduler.enqueue(ScheduledJob("slow", "g1", priority=90, budget={"ram_mb": 1000}))
    scheduler.enqueue(ScheduledJob("fast", "g2", priority=10, budget={"ram_mb": 100}))

    assert scheduler.next({"ram_mb": 200}).job_id == "fast"
    stop = EmergencyStop(tmp_path)
    stop.engage("user requested stop")
    assert scheduler.next({"ram_mb": 200}) is None
    stop.clear()
    assert scheduler.next({"ram_mb": 200}).job_id == "fast"
    assert scheduler.mark("fast", "running").status == "running"


def test_scheduler_claims_a_job_once(tmp_path: Path) -> None:
    scheduler = Scheduler(tmp_path)
    scheduler.enqueue(ScheduledJob("one", "g1"))

    assert scheduler.claim_next().status == "running"
    assert scheduler.claim_next() is None


def test_scheduler_pause_resume_retry_and_resource_snapshot(tmp_path: Path) -> None:
    scheduler = Scheduler(tmp_path)
    scheduler.enqueue(ScheduledJob("one", "g1"))
    assert scheduler.pause("one").status == "paused"
    assert scheduler.resume("one").status == "queued"
    assert scheduler.mark("one", "failed").status == "failed"
    assert scheduler.retry("one").status == "queued"
    assert ResourceMonitor(tmp_path).available()["disk_bytes"] > 0


def test_scheduler_waits_for_goal_dependency(tmp_path: Path) -> None:
    goals = GoalStore(tmp_path)
    prerequisite = goals.create("Research")
    outcome = goals.create("Build", dependencies=[prerequisite.goal_id])
    scheduler = Scheduler(tmp_path)
    scheduler.enqueue(ScheduledJob("build", outcome.goal_id))

    assert scheduler.next() is None
    goals.set_status(prerequisite.goal_id, "completed")
    assert scheduler.next().job_id == "build"


def test_worker_persists_terminal_state_and_notification(tmp_path: Path) -> None:
    worker = OutcomeWorker(tmp_path)
    worker.scheduler.enqueue(ScheduledJob("one", "g1"))

    result = worker.run_next(lambda job: WorkerResult(job.job_id, "completed", "verified"))

    assert result and result.status == "completed"
    assert worker.scheduler.next() is None
    assert worker.attention.pending()[0].message.endswith("verified")


def test_attention_manager_persists_prioritized_notifications(tmp_path: Path) -> None:
    attention = AttentionManager(tmp_path)
    note = attention.notify(Notification("important", "Contradictory evidence found"))

    assert attention.pending()[0].message == "Contradictory evidence found"
    assert attention.deliverable((22, 7), now_hour=23) == []
    urgent = attention.notify(Notification("urgent", "Deployment failure"))
    assert attention.deliverable((22, 7), now_hour=23)[0].notification_id == urgent.notification_id
    assert attention.acknowledge(note.notification_id).status == "acknowledged"


def test_doctor_reports_malformed_extension_manifest_as_degraded(tmp_path: Path) -> None:
    manifest = tmp_path / ".ghost" / "extensions" / "bad" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("not-json", encoding="utf-8")

    assert Doctor(tmp_path).check()["extension_manifests"] == "degraded"
