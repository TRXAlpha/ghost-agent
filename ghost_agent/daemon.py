"""OJ-Daemon: always-on composition of the existing loops.

``GhostDaemon`` is a thin composition layer: each daemon cycle is exactly one
``OvernightRunner.run_cycle()`` plus two Ghost learning hooks (trace learning
and the capability improver) that run only when the owner is away and, for the
improver, outside quiet hours. It owns no scheduling of its own — no second
scheduler, queue or worker — in line with Ghost's single-control-plane
invariant.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .overnight import OvernightRunner
from .runtime import EmergencyStop
from .utils.storage import atomic_json


def _default_trace_fn(root: Path) -> Callable[[], dict]:
    def trace() -> dict:
        from .trace_learning import run_trace_learning
        summary = run_trace_learning(root, min_count=2)
        return {"scanned": summary["analysis"]["scanned"],
                "signatures": len(summary["analysis"]["top_signatures"]),
                "actions": len(summary["actions"])}
    return trace


def _default_improver_fn(root: Path, model: str) -> Callable[[], dict]:
    def improve() -> dict:
        from .autonomy_improver import CapabilityImprover
        result = CapabilityImprover(root, model).tick()
        return result or {}
    return improve


def _default_improve_sync_fn(root: Path) -> Callable[[], list]:
    def sync() -> list:
        from .self_improver import SelfImprover
        return [c.candidate_id for c in SelfImprover(root).sync()]
    return sync


def _default_heartbeat_fn(root: Path, model: str) -> Callable[[], dict]:
    """Run one heartbeat when due; returns its receipt or a not_due status."""
    def beat() -> dict:
        from .heartbeat import Heartbeat
        # Starts once Ghost has met the owner (`ghost tell` / `ghost owner show`).
        if not (Path(root) / ".ghost" / "owner" / "SOUL.md").exists():
            return {"status": "no_owner_files"}
        from .heartbeat import life_tick
        life = life_tick(root, model)
        heartbeat = Heartbeat(root, model)
        result = heartbeat.beat() if heartbeat.due() else {"status": "not_due"}
        return {**result, "life": life}
    return beat


class GhostDaemon:
    """Always-on composition of the existing loops; owns no scheduling of its own."""

    def __init__(self, root: Path, model: str, *, interval: float = 300.0,
                 idle_after: float = 900.0, trace_every: int = 10,
                 runner: Optional[OvernightRunner] = None,
                 trace_fn: Optional[Callable[[], dict]] = None,
                 improver_fn: Optional[Callable[[], dict]] = None,
                 improve_sync_fn: Optional[Callable[[], list]] = None,
                 sleep_fn: Callable[[float], None] = time.sleep,
                 heartbeat_fn: Optional[Callable[[], dict]] = None,
                 quiet_hours: Optional[tuple[int, int]] = None,
                 digest_window: Optional[tuple[int, int]] = None) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-blank string")
        if not isinstance(trace_every, int) or trace_every < 1:
            raise ValueError("trace_every must be an integer >= 1")
        if not isinstance(interval, (int, float)) or interval <= 0:
            raise ValueError("interval must be a positive number of seconds")
        for name, value in (("quiet_hours", quiet_hours), ("digest_window", digest_window)):
            if value is not None and (len(value) != 2 or not all(
                    type(h) is int and 0 <= h <= 23 for h in value)):
                raise ValueError(f"{name} must be a pair of ints in 0-23")
        self.root = Path(root).resolve()
        self.model = model
        self.interval = float(interval)
        self.trace_every = int(trace_every)
        runner_kwargs: dict = {}
        if quiet_hours is not None:
            runner_kwargs["quiet_hours"] = tuple(quiet_hours)
        if digest_window is not None:
            runner_kwargs["digest_window"] = tuple(digest_window)
        self.runner = runner if runner is not None else OvernightRunner(
            self.root, model, interval=interval, idle_after=idle_after, **runner_kwargs)
        self.trace_fn = trace_fn if trace_fn is not None else _default_trace_fn(self.root)
        self.improver_fn = improver_fn if improver_fn is not None else _default_improver_fn(
            self.root, model)
        self.improve_sync_fn = improve_sync_fn if improve_sync_fn is not None else _default_improve_sync_fn(self.root)
        self.sleep_fn = sleep_fn
        self.heartbeat_fn = heartbeat_fn if heartbeat_fn is not None else _default_heartbeat_fn(
            self.root, model)
        self.status_path = self.root / ".ghost" / "daemon_status.json"
        self._cycles = 0
        self._life_thread: Optional[threading.Thread] = None

    # -- one cycle ---------------------------------------------------------
    def run_cycle(self) -> dict:
        self._cycles += 1
        cycle = self._cycles
        entry = self.runner.run_cycle()
        record = {"cycle": cycle, "ts": time.time(),
                  "overnight": json.loads(entry.to_jsonl())}

        owner_away = not entry.user_active and not entry.emergency_stop

        if owner_away and cycle % self.trace_every == 0:
            try:
                record["trace_learning"] = self.trace_fn()
            except Exception as exc:
                record["trace_learning"] = {"status": "error", "error": str(exc)[:200]}
        elif not owner_away:
            record["trace_learning"] = "deferred"
        else:
            record["trace_learning"] = "not_due"

        if owner_away and not entry.quiet_hours:
            try:
                record["capability"] = self.improver_fn()
            except Exception as exc:
                record["capability"] = {"status": "error", "error": str(exc)[:200]}
        else:
            record["capability"] = "deferred"

        if owner_away:
            try:
                record["improve_sync"] = self.improve_sync_fn()
            except Exception as exc:
                record["improve_sync"] = {"status": "error", "error": str(exc)[:200]}
        else:
            record["improve_sync"] = "deferred"

        # LL4: the heartbeat is not idle-gated. Under run() it has its own thread
        # (a long sandbox build must not delay reminders or replies).
        if self._life_thread is None:
            record["heartbeat"] = self._life_once()
        else:
            record["heartbeat"] = "own_thread"

        atomic_json(self.status_path, record)
        return record

    def _life_once(self) -> dict:
        """One assistant-lane pass (life tick + heartbeat if due); errors are recorded, never raised."""
        try:
            return self.heartbeat_fn()
        except Exception as exc:
            return {"status": "error", "error": str(exc)[:200]}

    def _life_loop(self, stop_event: threading.Event, interval: float) -> None:
        """Assistant lane on its own cadence, independent of worker builds."""
        while not stop_event.is_set():
            result = self._life_once()
            atomic_json(self.root / ".ghost" / "life_status.json", {"ts": time.time(), "result": result})
            stop_event.wait(interval)

    # -- main loop ---------------------------------------------------------
    def run(self, *, max_cycles: Optional[int] = None, life_interval: float = 60.0) -> int:
        from .policy import PolicyError, PolicyStore
        PolicyStore(self.root).require("idle_work", "unattended background work")
        stop = EmergencyStop(self.root)
        life_stop = threading.Event()
        self._life_thread = threading.Thread(target=self._life_loop, args=(life_stop, life_interval),
                                             name="ghost-life-lane", daemon=True)
        self._life_thread.start()
        try:
            return self._run_cycles(stop, max_cycles)
        finally:
            life_stop.set()
            self._life_thread.join(timeout=5)
            self._life_thread = None

    def _run_cycles(self, stop, max_cycles: Optional[int]) -> int:
        while not stop.active():
            if max_cycles is not None and self._cycles >= max_cycles:
                break
            self.run_cycle()
            if max_cycles is not None and self._cycles >= max_cycles:
                break
            self.sleep_fn(self.interval)
        n = self._cycles
        atomic_json(self.status_path,
                    {"stopped": True, "total_cycles": n, "ts": time.time()})
        return n
