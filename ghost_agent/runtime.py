"""Deterministic runtime services: events, world state, scheduling, health, stop."""
from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import time
import math
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .utils.storage import atomic_json, exclusive_lock


EVENT_SENSITIVITIES = ("normal", "personal", "health", "secret")


@dataclass
class Event:
    kind: str
    payload: Dict[str, Any]
    source: str = "ghost"
    created_at: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: uuid4().hex)
    ingested_at: Optional[float] = None
    correlation_id: str = ""
    causation_id: str = ""
    sensitivity: str = "normal"
    schema_version: int = 2


class EventBus:
    def __init__(self, project_root: Path) -> None:
        self.path = Path(project_root) / ".ghost" / "events.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: Event) -> Event:
        if event.sensitivity not in EVENT_SENSITIVITIES:
            raise ValueError("unknown event sensitivity")
        if event.ingested_at is None:
            event.ingested_at = time.time()
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(asdict(event)) + "\n")
        return event

    def recent(self, limit: int = 50, kind: Optional[str] = None) -> list[Event]:
        if not self.path.exists():
            return []
        known = {f.name for f in fields(Event)}
        events: list[Event] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                raw = json.loads(line)
                data = {key: value for key, value in raw.items() if key in known}
                if "schema_version" not in data:
                    data["schema_version"] = 1
                event = Event(**data)
                if not kind or event.kind == kind:
                    events.append(event)
            except (json.JSONDecodeError, TypeError):
                continue
        return events[-limit:]


@dataclass
class Observation:
    entity_id: str
    kind: str
    state: Dict[str, Any]
    source: str
    confidence: float = 1.0
    observed_at: float = field(default_factory=time.time)
    last_verified: float = field(default_factory=time.time)
    owner: str = "user"
    dependencies: list[str] = field(default_factory=list)


class WorldModel:
    def __init__(self, project_root: Path) -> None:
        self.path = Path(project_root) / ".ghost" / "world.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> Dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def observe(self, observation: Observation) -> Observation:
        data = self._load()
        data[observation.entity_id] = asdict(observation)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return observation

    def get(self, entity_id: str, max_age_seconds: Optional[float] = None) -> Optional[Observation]:
        raw = self._load().get(entity_id)
        if not isinstance(raw, dict):
            return None
        observation = Observation(**raw)
        if max_age_seconds is not None and time.time() - observation.last_verified > max_age_seconds:
            return None
        return observation

    def recent(self, limit: int = 24, max_age_seconds: Optional[float] = None) -> list[Observation]:
        observations: list[Observation] = []
        for raw in self._load().values():
            try:
                observation = Observation(**raw)
            except TypeError:
                continue
            if max_age_seconds is None or time.time() - observation.last_verified <= max_age_seconds:
                observations.append(observation)
        return sorted(observations, key=lambda item: item.last_verified, reverse=True)[:limit]


@dataclass
class ScheduledJob:
    job_id: str
    goal_id: str
    priority: int = 50
    status: str = "queued"
    budget: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    owner: Optional[str] = None
    attempts: int = 0
    claimed_at: Optional[float] = None
    # Recurrence (OJ07): absent = one-shot job, byte-for-byte legacy behavior.
    recurrence: Optional[Dict[str, Any]] = None  # {"kind": "interval", "interval_seconds": N}
    due_at: Optional[float] = None               # epoch seconds; recurring jobs claim only when due
    lease_expires_at: Optional[float] = None     # epoch seconds; running claim is stale once past this
    generation: int = 0                          # incremented on each claim; guards stale worker marks


class ResourceMonitor:
    """Portable, read-only resource snapshot used for scheduler admission."""

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root)

    def available(self) -> Dict[str, float]:
        disk = shutil.disk_usage(self.root)
        snapshot: Dict[str, float] = {
            "cpu_cores": float(os.cpu_count() or 1),
            "disk_bytes": float(disk.free),
        }
        try:
            page_size = os.sysconf("SC_PAGE_SIZE")
            pages = os.sysconf("SC_AVPHYS_PAGES")
            snapshot["ram_bytes"] = float(page_size * pages)
        except (AttributeError, OSError, ValueError):
            # RAM availability is intentionally absent rather than guessed.
            pass
        return snapshot


class Scheduler:
    TRANSITIONS = {
        "queued": {"running", "paused", "failed", "cancelled"},
        "running": {"paused", "completed", "failed", "cancelled"},
        "paused": {"queued", "cancelled"},
        "failed": {"queued", "cancelled"},
        "completed": set(), "cancelled": set(),
    }
    def __init__(self, project_root: Path, event_bus: Optional[EventBus] = None) -> None:
        self.path = Path(project_root) / ".ghost" / "scheduler.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.events = event_bus or EventBus(project_root)

    def _load(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                raise ValueError("Expected job records")
            ids = set()
            for raw in data:
                job = ScheduledJob(**raw)
                if not isinstance(job.job_id, str) or not job.job_id or job.job_id in ids or job.status not in self.TRANSITIONS:
                    raise ValueError("Invalid or duplicate job")
                if ((job.owner is not None and not isinstance(job.owner, str))
                        or not isinstance(job.attempts, int) or job.attempts < 0 or not isinstance(job.budget, dict)):
                    raise ValueError("Invalid ownership or budget state")
                if not isinstance(job.generation, int) or isinstance(job.generation, bool) or job.generation < 0:
                    raise ValueError("Invalid generation record")
                if job.lease_expires_at is not None and (not isinstance(job.lease_expires_at, (int, float))
                                                        or isinstance(job.lease_expires_at, bool)):
                    raise ValueError("Invalid lease_expires_at record")
                if job.recurrence is not None:
                    if (not isinstance(job.recurrence, dict)
                            or job.recurrence.get("kind") != "interval"
                            or not isinstance(job.recurrence.get("interval_seconds"), int)
                            or isinstance(job.recurrence.get("interval_seconds"), bool)
                            or job.recurrence["interval_seconds"] <= 0):
                        raise ValueError("Invalid recurrence record")
                    if job.due_at is not None and (not isinstance(job.due_at, (int, float)) or isinstance(job.due_at, bool)):
                        raise ValueError("Invalid due_at record")
                ids.add(job.job_id)
            return data
        except FileNotFoundError:
            return []
        except (ValueError, OSError, TypeError) as exc:
            raise ValueError("Scheduler state is corrupt or unavailable; preserved for review") from exc

    def _save(self, jobs: list[dict[str, Any]]) -> None:
        atomic_json(self.path, jobs)

    def list(self) -> list[ScheduledJob]:
        return [ScheduledJob(**raw) for raw in self._load()]

    def enqueue(self, job: ScheduledJob) -> ScheduledJob:
        if not job.job_id or job.status != "queued":
            raise ValueError("New jobs must have an ID and queued status")
        with exclusive_lock(self.path.with_suffix(".lock")):
            jobs = self._load()
            if any(raw["job_id"] == job.job_id for raw in jobs):
                raise ValueError("Scheduled job already exists")
            jobs.append(asdict(job))
            self._save(jobs)
        self.events.emit(Event("job.queued", {"job_id": job.job_id, "goal_id": job.goal_id}, correlation_id=job.job_id))
        return job

    def next(self, available: Optional[Dict[str, float]] = None) -> Optional[ScheduledJob]:
        if EmergencyStop(self.path.parents[1]).active():
            return None
        available = available or {}
        now = time.time()
        candidates = [ScheduledJob(**raw) for raw in self._load() if raw.get("status") == "queued"]
        # Recurring jobs are only eligible once due (OJ07); one-shot jobs as before.
        candidates = [job for job in candidates if job.due_at is None or job.due_at <= now]
        candidates = [job for job in candidates if self._goal_eligible(job)]
        candidates = [job for job in candidates if self._fits_budget(job, available)]
        return sorted(candidates, key=lambda job: (-job.priority, job.created_at))[0] if candidates else None

    def _goal_eligible(self, job: ScheduledJob) -> bool:
        """Honor persisted goal dependencies when this scheduler owns that goal."""
        try:
            from .kernel import GoalStore
            goals = GoalStore(self.path.parents[1])
            if goals.path.exists():
                records = json.loads(goals.path.read_text(encoding="utf-8"))
                if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
                    return False
            goal = goals.get(job.goal_id)
            if goal is None:
                return not goals.path.exists()
            if goal.deadline_at is not None and goal.deadline_at < time.time():
                return False
            return any(candidate.goal_id == job.goal_id for candidate in goals.ready())
        except Exception:
            return False

    def claim_next(self, available: Optional[Dict[str, float]] = None,
                   job_id: Optional[str] = None, owner: Optional[str] = None) -> Optional[ScheduledJob]:
        """Claim the next eligible job before a worker begins executing it."""
        with exclusive_lock(self.path.with_suffix(".lock")):
            if job_id is None:
                job = self.next(available)
            else:
                job = next((ScheduledJob(**raw) for raw in self._load()
                            if raw["job_id"] == job_id and raw["status"] == "queued"), None)
                if (EmergencyStop(self.path.parents[1]).active() or
                        (job and (not self._goal_eligible(job) or not self._fits_budget(job, available or {})))):
                    job = None
            return self._mark_locked(job.job_id, "running", owner=owner) if job else None

    def mark(self, job_id: str, status: str, *, expected: Optional[set[str]] = None,
             generation: Optional[int] = None) -> ScheduledJob:
        with exclusive_lock(self.path.with_suffix(".lock")):
            return self._mark_locked(job_id, status, expected, generation=generation)

    def _mark_locked(self, job_id: str, status: str, expected: Optional[set[str]] = None,
                     owner: Optional[str] = None, generation: Optional[int] = None) -> ScheduledJob:
        if status not in {"queued", "running", "paused", "completed", "failed", "cancelled"}:
            raise ValueError(f"Unknown job status: {status}")
        jobs = self._load()
        for raw in jobs:
            if raw.get("job_id") == job_id:
                previous = raw["status"]
                if expected is not None and previous not in expected:
                    raise ValueError("Job state changed or does not permit this operation")
                if generation is not None and raw.get("generation", 0) != generation:
                    raise ValueError("stale generation")
                if status != previous and status not in self.TRANSITIONS[previous]:
                    raise ValueError(f"Invalid job transition: {previous} -> {status}")
                if status == "running" and previous == "queued":
                    raw["owner"] = owner
                    raw["attempts"] = raw.get("attempts", 0) + 1
                    raw["claimed_at"] = time.time()
                    raw["generation"] = raw.get("generation", 0) + 1
                    lease_seconds = raw.get("budget", {}).get("lease_seconds", 21600)
                    if (not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool)
                            or not 60 <= lease_seconds <= 604800):
                        lease_seconds = 21600
                    raw["lease_expires_at"] = time.time() + lease_seconds
                elif status == "queued":
                    raw["owner"] = None
                    raw["claimed_at"] = None
                    raw["lease_expires_at"] = None
                raw["status"] = status
                if status != "running":
                    raw["lease_expires_at"] = None
                recurrence_event: Optional[Event] = None
                if status in {"completed", "failed"} and raw.get("recurrence"):
                    # OJ07: a recurring job settles terminal AND re-queues in the
                    # SAME locked save — no window for a duplicate claim.
                    next_due, missed = self._next_due(raw["recurrence"], raw.get("due_at"), time.time())
                    if missed > 0:
                        recurrence_event = Event("job.recurrence.skipped", {"job_id": job_id, "missed": missed}, correlation_id=job_id)
                    raw["due_at"] = next_due
                    raw["status"] = "queued"
                    raw["owner"] = None
                    raw["claimed_at"] = None
                    raw["attempts"] = 0
                self._save(jobs)
                job = ScheduledJob(**raw)
                self.events.emit(Event(f"job.{status}", {"job_id": job_id, "goal_id": job.goal_id}, correlation_id=job_id))
                if recurrence_event is not None:
                    self.events.emit(recurrence_event)
                if raw.get("recurrence") and status in {"completed", "failed"}:
                    self.events.emit(Event("job.recurrence.requeued",
                                           {"job_id": job_id, "goal_id": job.goal_id, "due_at": raw["due_at"]},
                                           correlation_id=job_id))
                return job
        raise ValueError("Scheduled job not found")

    @staticmethod
    def _next_due(recurrence: Any, due_at: Any, now: float) -> tuple[float, int]:
        """Next due epoch for a recurring job, with catch-up capped at 64 periods."""
        if not isinstance(recurrence, dict) or recurrence.get("kind") != "interval":
            raise ValueError("Recurring job requires recurrence kind 'interval'")
        interval = recurrence.get("interval_seconds")
        if not isinstance(interval, int) or isinstance(interval, bool) or interval <= 0:
            raise ValueError("Recurring job requires interval_seconds > 0")
        base = due_at if isinstance(due_at, (int, float)) and not isinstance(due_at, bool) else now
        nxt = float(base) + interval
        missed = 0
        while nxt <= now:
            missed += 1
            if missed > 64:
                # Sustained downtime: snap to the next future period, never replay the backlog.
                return now + interval, missed
            nxt += interval
        return nxt, missed

    def remove(self, job_id: str) -> ScheduledJob:
        """Remove a recurring or terminal job; refused otherwise (OJ07)."""
        with exclusive_lock(self.path.with_suffix(".lock")):
            jobs = self._load()
            for index, raw in enumerate(jobs):
                if raw.get("job_id") == job_id:
                    status = raw.get("status")
                    if status == "running":
                        raise ValueError("Refusing to remove a running job")
                    if not raw.get("recurrence") and status not in {"completed", "failed", "cancelled"}:
                        raise ValueError("Only recurring or terminal jobs can be removed")
                    removed = ScheduledJob(**raw)
                    del jobs[index]
                    self._save(jobs)
                    self.events.emit(Event("job.removed", {"job_id": job_id, "goal_id": removed.goal_id}, correlation_id=job_id))
                    return removed
            raise ValueError("Scheduled job not found")

    def pause(self, job_id: str) -> ScheduledJob:
        return self.mark(job_id, "paused", expected={"queued"})

    def resume(self, job_id: str) -> ScheduledJob:
        return self.mark(job_id, "queued", expected={"paused"})

    def retry(self, job_id: str) -> ScheduledJob:
        return self.mark(job_id, "queued", expected={"failed"})

    @staticmethod
    def _fits_budget(job: ScheduledJob, available: Dict[str, float]) -> bool:
        for key, required in job.budget.items():
            if isinstance(required, bool) or not isinstance(required, (int, float)) or not math.isfinite(required) or required < 0:
                return False
            if key == "iterations":
                if not isinstance(required, int) or required < 1:
                    return False
                continue  # Enforced by the coding loop, not a machine resource.
            if key not in available or not math.isfinite(available[key]) or required > available[key]:
                return False
        return True


class EmergencyStop:
    def __init__(self, project_root: Path) -> None:
        self.path = Path(project_root) / ".ghost" / "emergency-stop.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def engage(self, reason: str) -> None:
        self.path.write_text(json.dumps({"active": True, "reason": reason, "at": time.time()}), encoding="utf-8")

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

    def active(self) -> bool:
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
            return not isinstance(state, dict) or state.get("active") is not False
        except FileNotFoundError:
            return False
        except (ValueError, OSError):
            # A damaged safety marker is not permission to continue.
            return True


@dataclass
class Notification:
    priority: str
    message: str
    notification_id: str = ""
    source: str = "ghost"
    status: str = "pending"
    created_at: float = field(default_factory=time.time)


class AttentionManager:
    """Persist notifications with acknowledgement and quiet-hour delivery policy."""
    PRIORITIES = {"silent", "normal", "important", "urgent"}
    BATCH_THRESHOLD = 3

    def __init__(self, project_root: Path) -> None:
        self.path = Path(project_root) / ".ghost" / "notifications.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def notify(self, notification: Notification) -> Notification:
        self._validate(notification, require_id=False)
        if notification.status != "pending":
            raise ValueError("New notifications must be pending")
        if not notification.notification_id:
            notification.notification_id = "note_" + hashlib.sha1(
                f"{notification.priority}|{notification.message}|{time.time_ns()}".encode()
            ).hexdigest()[:10]
        with exclusive_lock(self.path.with_suffix(".lock")):
            data = self.pending(include_acknowledged=True)
            for item in data:
                if item.notification_id == notification.notification_id:
                    if (item.priority, item.message, item.source) != (notification.priority, notification.message, notification.source):
                        raise ValueError("Notification ID already belongs to different content")
                    return item  # Retry does not duplicate or resurrect acknowledged work.
            data.append(notification)
            atomic_json(self.path, [asdict(item) for item in data])
        return notification

    @classmethod
    def _validate(cls, item: Notification, require_id: bool = True) -> None:
        if not isinstance(item.priority, str) or item.priority not in cls.PRIORITIES:
            raise ValueError("Unknown notification priority")
        if (not isinstance(item.message, str) or not item.message.strip()
                or not isinstance(item.source, str) or not item.source.strip()
                or not isinstance(item.notification_id, str) or (require_id and not item.notification_id)
                or item.status not in {"pending", "acknowledged"}
                or type(item.created_at) not in (int, float) or not 0 <= item.created_at < float("inf")):
            raise ValueError("Invalid notification record")

    def pending(self, include_acknowledged: bool = False) -> list[Notification]:
        try:
            raw_items = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw_items, list):
                raise ValueError("Expected notification records")
            items = [Notification(**raw) for raw in raw_items]
            seen = set()
            for item in items:
                self._validate(item)
                if item.notification_id in seen:
                    raise ValueError("Duplicate notification ID")
                seen.add(item.notification_id)
        except FileNotFoundError:
            items = []
        except (ValueError, OSError, TypeError) as exc:
            raise ValueError("Notification storage is corrupt; preserve it for review") from exc
        return items if include_acknowledged else [item for item in items if item.status == "pending"]

    def acknowledge(self, notification_id: str) -> Notification:
        with exclusive_lock(self.path.with_suffix(".lock")):
            return self._acknowledge(notification_id)

    def _acknowledge(self, notification_id: str) -> Notification:
        data = self.pending(include_acknowledged=True)
        for item in data:
            if item.notification_id == notification_id:
                if item.status != "pending":
                    return item
                item.status = "acknowledged"
                atomic_json(self.path, [asdict(note) for note in data])
                return item
        raise ValueError("Notification not found")

    def deliverable(self, quiet_hours: tuple[int, int] | None = None, now_hour: int | None = None) -> list[Notification]:
        """Return notifications fit for delivery; urgent items bypass quiet hours."""
        pending = self.pending()
        if quiet_hours is None:
            items = [item for item in pending if item.priority != "silent"]
        else:
            hour = time.localtime().tm_hour if now_hour is None else now_hour
            if (not isinstance(quiet_hours, (tuple, list)) or len(quiet_hours) != 2
                    or any(type(value) is not int or not 0 <= value <= 23 for value in (*quiet_hours, hour))):
                raise ValueError("Quiet hours and current hour must be integers from 0 to 23")
            start, end = quiet_hours
            quiet = start <= hour < end if start <= end else hour >= start or hour < end
            items = [item for item in pending if item.priority != "silent" and (not quiet or item.priority == "urgent")]
        return self._batch(items)

    @classmethod
    def _batch(cls, items: list[Notification]) -> list[Notification]:
        """Collapse bursts of normal notifications from one source into one summary."""
        result: list[Notification] = []
        for source in {item.source for item in items if item.priority == "normal"}:
            burst = [item for item in items if item.priority == "normal" and item.source == source]
            if len(burst) > cls.BATCH_THRESHOLD:
                newest = max(burst, key=lambda note: note.created_at)
                # Synthetic copy only: the originals stay pending and individually ackable.
                result.append(Notification(
                    "normal",
                    f"{len(burst)} updates from {source}; latest: {newest.message}",
                    notification_id=newest.notification_id,
                    source=source, status="pending", created_at=newest.created_at))
            else:
                result.extend(burst)
        result.extend(item for item in items if item.priority != "normal")
        return result


class Doctor:
    """Local health checks without attempting repairs or external side effects."""

    OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root)

    def check(self) -> Dict[str, str]:
        ghost = self.root / ".ghost"
        status = {
            "workspace": "ok" if self.root.is_dir() else "missing",
            "ghost_storage": "ok" if ghost.is_dir() else "not_initialized",
            "emergency_stop": "engaged" if ghost.is_dir() and EmergencyStop(self.root).active() else "clear",
            "outcomes": "ok" if (ghost / "outcomes").is_dir() else "not_initialized",
            "research": "ok" if (ghost / "research").is_dir() else "not_initialized",
            "scheduler": "ok" if (ghost / "scheduler.json").is_file() else "not_initialized",
            "event_bus": "ok" if (ghost / "events.jsonl").is_file() else "not_initialized",
            "extensions": "ok" if (ghost / "extensions").is_dir() else "not_initialized",
        }
        extension_root = ghost / "extensions"
        if extension_root.is_dir():
            manifests = list(extension_root.glob("*/manifest.json"))
            status["extension_manifests"] = "ok" if all(self._valid_extension(path) for path in manifests) else "degraded"
        if (ghost / "scheduler.json").is_file():
            # Storage already exists; this read does not claim or resume jobs.
            try:
                Scheduler(self.root).list()
            except (ValueError, OSError):
                status["scheduler"] = "degraded"
        if (ghost / "events.jsonl").is_file():
            status["event_bus"] = "ok" if self._valid_events(ghost / "events.jsonl") else "degraded"
        secret_file = ghost / "secrets.json"
        status["secret_references"] = "ok" if not secret_file.exists() or self._valid_json(secret_file) else "degraded"
        status["api"] = "loopback_only"  # Transport is intentionally not auto-started.
        status["execution_isolation"] = "not_implemented_host_user_access"
        status["ollama"] = self._probe_ollama()
        status["docker"] = self._probe_docker()
        # Integration adapters (read-only probes, no credentials required for status check)
        from .integrations import telegram_health_check, huawei_health_check, sentinel_health_check
        telegram_status = telegram_health_check(self.root)["status"]
        status["telegram"] = telegram_status
        if telegram_status != "not_ready":
            status["telegram_credentials"] = "present"
        else:
            status["telegram_credentials"] = "absent"
        status["huawei"] = huawei_health_check()["status"]
        status["desksentinel"] = sentinel_health_check()["status"]
        return status

    def available_models(self, timeout: float = 3.0) -> List[str]:
        """Return the names of installed Ollama models ([] if unreachable)."""
        import requests
        try:
            response = requests.get(self.OLLAMA_TAGS_URL, timeout=timeout)
            response.raise_for_status()
            data = response.json()
        except (requests.exceptions.RequestException, OSError, ValueError):
            return []
        return [m.get("name") for m in data.get("models", []) if m.get("name")]

    @staticmethod
    def _probe_ollama(timeout: float = 3.0) -> str:
        """Probe the local Ollama daemon. 'ok' | 'not_running' | 'error'."""
        try:
            import requests
            response = requests.get(Doctor.OLLAMA_TAGS_URL, timeout=timeout)
            response.raise_for_status()
            return "ok"
        except requests.exceptions.ConnectionError:
            return "not_running"
        except (requests.exceptions.RequestException, OSError, ValueError, ImportError):
            return "error"

    @staticmethod
    def _probe_docker(timeout: float = 3.0) -> str:
        """Probe the Docker daemon via `docker info`. 'ok' | 'not_running' | 'error'."""
        try:
            proc = subprocess.run(
                ["docker", "info"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return "error"
        except OSError:
            return "error"
        if proc.returncode == 0:
            return "ok"
        err = (proc.stderr or "").lower()
        if "cannot connect to the docker daemon" in err or "is the docker daemon running" in err:
            return "not_running"
        return "error"

    @staticmethod
    def _valid_extension(path: Path) -> bool:
        from .extensions import ExtensionManifest, ExtensionManager
        try:
            manifest = ExtensionManifest(**json.loads(path.read_text(encoding="utf-8")))
            return (manifest.name == path.parent.name and manifest.extension_id == f"ext_{manifest.name}"
                    and manifest.status in ExtensionManager.STATES and isinstance(manifest.permissions, dict))
        except (ValueError, OSError, TypeError):
            return False

    @staticmethod
    def _valid_events(path: Path) -> bool:
        try:
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    event = Event(**json.loads(line))
                    if not isinstance(event.kind, str) or not event.kind or not isinstance(event.payload, dict):
                        return False
            return True
        except (ValueError, OSError, TypeError):
            return False

    @staticmethod
    def _valid_json(path: Path) -> bool:
        try:
            json.loads(path.read_text(encoding="utf-8"))
            return True
        except (OSError, json.JSONDecodeError):
            return False
