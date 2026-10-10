"""TA1: event envelope — event_id/ingested_at, legacy loading, sensitivity, correlation."""
import json
from pathlib import Path

from ghost_agent.runtime import Event, EventBus, Scheduler, ScheduledJob
from ghost_agent.worker import OutcomeWorker, WorkerResult


def test_new_event_gets_event_id_and_ingested_at(tmp_path: Path) -> None:
    bus = EventBus(tmp_path)

    event = bus.emit(Event("test.kind", {"x": 1}))

    assert len(event.event_id) == 32
    assert int(event.event_id, 16) >= 0
    assert event.ingested_at is not None
    loaded = bus.recent()[0]
    assert loaded.event_id == event.event_id
    assert loaded.ingested_at == event.ingested_at


def test_legacy_line_loads_with_schema_version_1(tmp_path: Path) -> None:
    path = tmp_path / ".ghost" / "events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"kind":"x","payload":{},"source":"s","created_at":1}\n', encoding="utf-8")
    bus = EventBus(tmp_path)

    loaded = bus.recent()[0]

    assert loaded.kind == "x"
    assert loaded.schema_version == 1


def test_line_with_unknown_extra_key_still_loads(tmp_path: Path) -> None:
    path = tmp_path / ".ghost" / "events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"kind":"x","payload":{},"source":"s","created_at":1,"future_key":42}\n',
                    encoding="utf-8")
    bus = EventBus(tmp_path)

    loaded = bus.recent()[0]

    assert loaded.kind == "x"
    assert loaded.schema_version == 1


def test_bad_sensitivity_raises(tmp_path: Path) -> None:
    bus = EventBus(tmp_path)

    try:
        bus.emit(Event("test.kind", {}, sensitivity="nuclear"))
    except ValueError as exc:
        assert "unknown event sensitivity" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown sensitivity")


def test_worker_emitted_event_has_correlation_id(tmp_path: Path) -> None:
    worker = OutcomeWorker(tmp_path)
    job = worker.scheduler.enqueue(ScheduledJob("job-1", "g1"))

    worker.run_next(lambda scheduled: WorkerResult(scheduled.job_id, "completed", "ok"))

    events = EventBus(tmp_path).recent()
    started = [e for e in events if e.kind == "worker.started"]
    finished = [e for e in events if e.kind == "worker.finished"]
    assert started and started[-1].correlation_id == job.job_id
    assert finished and finished[-1].correlation_id == job.job_id
