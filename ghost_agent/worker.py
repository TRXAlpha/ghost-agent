"""Local persisted-job worker for Ghost's scheduler."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from uuid import uuid4

from .utils.storage import exclusive_lock

from .runtime import (AttentionManager, EmergencyStop, Event, EventBus, Notification, ResourceMonitor,
                      ScheduledJob, Scheduler)


@dataclass
class WorkerResult:
    job_id: str
    status: str
    detail: str = ""


class OutcomeWorker:
    """Claim and execute one job with durable lifecycle events.

    It owns scheduling state, stop handling, event provenance, and terminal
    notifications. A caller supplies the domain-specific handler, so this
    layer never grants a model or an extension any implicit capability.
    """

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root)
        self.events = EventBus(self.root)
        self.scheduler = Scheduler(self.root, self.events)
        self.attention = AttentionManager(self.root)
        self.owner = "worker_" + uuid4().hex

    def _record_stop_observation(self, component: str) -> None:
        """Record this worker's first observation of an engaged emergency stop."""
        from .security import SecurityMonitor
        latency = EmergencyStop(self.root).observe(component)
        if latency is None:
            return  # already observed, or no longer active
        try:
            SecurityMonitor(self.root).record_event(
                "emergency_stop.observed",
                {"component": component, "latency_s": str(latency)})
        except OSError:
            pass

    def run_next(
        self,
        handler: Callable[[ScheduledJob], WorkerResult],
        available: Optional[dict[str, float]] = None,
        job_id: Optional[str] = None,
    ) -> Optional[WorkerResult]:
        # One worker per project is the initial lightweight concurrency limit.
        # Unlike a PID file, this lock proves exclusive live ownership and is
        # released by the OS if the process exits unexpectedly.
        try:
            with exclusive_lock(self.root / ".ghost" / "worker.lock"):
                return self._run_locked(handler, available, job_id)
        except BlockingIOError:
            return None

    def _run_locked(self, handler: Callable[[ScheduledJob], WorkerResult],
                    available: Optional[dict[str, float]], job_id: Optional[str]) -> Optional[WorkerResult]:
        for previous in self.scheduler.list():
            if previous.owner and previous.owner.startswith("worker_") and previous.status == "running":
                self.scheduler.mark(previous.job_id, "paused", expected={"running"})
                self.attention.notify(Notification("important",
                    f"Worker exited during {previous.job_id}. Job paused; review artifacts and any child processes before resuming.",
                    source="worker"))
                self.events.emit(Event("worker.interrupted", {"job_id": previous.job_id}, source="worker"))
        if any(job.owner and job.owner.startswith("worker_") and job.status == "paused"
               for job in self.scheduler.list()):
            return None
        if EmergencyStop(self.root).active():
            self._record_stop_observation("worker")
            self.events.emit(Event("worker.skipped", {"reason": "emergency_stop"}, source="worker"))
            return None
        job = self.scheduler.claim_next(ResourceMonitor(self.root).available() if available is None else available,
                                        owner=self.owner, job_id=job_id)
        if not job:
            return None
        self.events.emit(Event("worker.started", {"job_id": job.job_id}, source="worker"))
        try:
            result = handler(job)
            if result.job_id != job.job_id:
                raise ValueError("Worker handler returned a result for a different job")
            terminal = result.status if result.status in {"completed", "failed", "cancelled"} else "failed"
        except Exception as exc:
            result = WorkerResult(job.job_id, "failed", str(exc))
            terminal = "failed"
        current = next(item for item in self.scheduler.list() if item.job_id == job.job_id)
        if current.status in {"paused", "cancelled"}:
            return WorkerResult(job.job_id, current.status, "Execution returned after a control-state change")
        self.scheduler.mark(job.job_id, terminal, expected={"running"})
        priority = "normal" if terminal == "completed" else "important"
        self.attention.notify(Notification(priority, f"Job {job.job_id} {terminal}: {result.detail}", source="worker"))
        self.events.emit(Event("worker.finished", {"job_id": job.job_id, "status": terminal,
                                                     "detail": result.detail}, source="worker"))
        return WorkerResult(job.job_id, terminal, result.detail)
