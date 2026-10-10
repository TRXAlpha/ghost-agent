"""OJ11/S06: unattended overnight runtime composing existing Ghost subsystems.

The OvernightRunner is a thin unattended loop that composes three existing,
accepted subsystems — the proactive engine (one proposal tick), the outcome
worker (one job claim+run), and the morning digest — under one idle-aware,
quiet-hours-aware, emergency-stop-responsive driver. It owns no scheduling,
authority, or memory logic itself: every action it takes goes through the
existing public APIs, in line with Ghost's single-control-plane invariant.

Cycle shape (every ``interval`` seconds until stopped):
1. emergency stop engaged -> exit immediately (checked every cycle);
2. user active (OS idle below threshold) -> idle wait, no proactive work;
3. quiet hours (default 01:00-07:00 local) -> skip proactive ticks, but the
   worker still runs bounded jobs;
4. otherwise one proactive tick + at most one worker job;
5. one JSONL status line appended to ``.ghost/overnight/log.jsonl``.

Between 07:00-09:00 local, if last night produced log entries, the runner
writes a morning digest markdown summary to ``.ghost/digest/YYYY-MM-DD.md``.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from .digest import MorningDigest
from .runtime import EmergencyStop

DEFAULT_QUIET_HOURS = (1, 7)
DEFAULT_DIGEST_WINDOW = (7, 9)
DEFAULT_INTERVAL_SECONDS = 300


def in_quiet_hours(now: Optional[datetime] = None,
                   quiet_hours: tuple[int, int] = DEFAULT_QUIET_HOURS) -> bool:
    """True inside [start, end) quiet window; supports wrap-around windows."""
    now = now or datetime.now()
    start, end = quiet_hours
    hour = now.hour
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def in_digest_window(now: Optional[datetime] = None,
                     window: tuple[int, int] = DEFAULT_DIGEST_WINDOW) -> bool:
    return in_quiet_hours(now, window)


@dataclass
class OvernightCycleResult:
    """One cycle's outcome; also the JSONL log line schema."""
    cycle: int
    ts: str
    user_active: bool
    quiet_hours: bool
    emergency_stop: bool
    proactive: str = "skipped"          # ok | deferred | skipped | stopped | error
    proactive_reason: str = ""
    worker: str = ""                     # job id when a job ran
    worker_status: str = ""              # job status when a job ran
    errors: list[str] = field(default_factory=list)

    def to_jsonl(self) -> str:
        return json.dumps({
            "ts": self.ts_iso(),
            "cycle": self.cycle,
            "user_active": self.user_active,
            "quiet_hours": self.quiet_hours,
            "emergency_stop": self.emergency_stop,
            "proactive": self.proactive,
            "proactive_reason": self.proactive_reason,
            "worker": self.worker,
            "worker_status": self.worker_status,
            "errors": self.errors,
        }, ensure_ascii=False)

    def ts_iso(self) -> str:
        return datetime.now().isoformat(timespec="seconds")


class OvernightRunner:
    """Unattended proactive+worker loop over existing public subsystem APIs."""

    def __init__(self, root: Path, model: str, *, interval: float = 300.0,
                 idle_after: float = 900.0,
                 quiet_hours: tuple[int, int] = DEFAULT_QUIET_HOURS,
                 digest_window: tuple[int, int] = DEFAULT_DIGEST_WINDOW,
                 proactive_tick: Optional[Callable[..., dict]] = None,
                 worker_fn: Optional[Callable[[], object]] = None,
                 inference_budget: Optional[object] = None) -> None:
        self.root = Path(root).resolve()
        self.model = model
        if not isinstance(interval, (int, float)) or interval <= 0:
            raise ValueError("interval must be a positive number of seconds")
        self.interval = float(interval)
        self.idle_after = float(idle_after)
        self.quiet_hours = tuple(quiet_hours)
        self.digest_window = tuple(digest_window)
        self.log_path = self.root / ".ghost" / "overnight" / "log.jsonl"
        self.digest_dir = self.root / ".ghost" / "digest"
        self._proactive_tick = proactive_tick      # injectable for tests
        self._worker_injected = worker_fn          # injectable for tests
        self.inference_budget = inference_budget   # TD5 (S06): daily inference cap
        self._cycles = 0

    # -- collaborators (lazy: import cost and test isolation) --------------
    def _proactive(self) -> Callable[..., dict]:
        if self._proactive_tick is not None:
            return self._proactive_tick
        from .proactive import ProactiveEngine
        engine = ProactiveEngine(self.root, self.model,
                                 inference_budget=self.inference_budget)
        return engine.tick

    def _worker_next(self) -> Callable[[], Optional[object]]:
        if self._worker_injected is not None:
            return self._worker_injected
        from .worker import OutcomeWorker
        from .cli import _execute_queued_outcome
        worker = OutcomeWorker(self.root)
        return lambda: worker.run_next(
            lambda scheduled: _execute_queued_outcome(self.root, scheduled))

    # -- idle detection -----------------------------------------------------
    def _user_active(self, idle_after: Optional[float] = None) -> bool:
        """True when the owner was active more recently than idle_after."""
        idle = self._os_idle_seconds()
        if idle is None:
            # Cannot prove idle -> treat as active (never speculate while
            # unsure whether the owner is at the keyboard).
            return True
        threshold = self.idle_after if idle_after is None else idle_after
        return idle < threshold

    def _os_idle_seconds(self) -> Optional[float]:
        try:
            from .proactive import idle_seconds
            return idle_seconds()
        except Exception:
            return None

    # -- logging ------------------------------------------------------------
    def _log(self, entry: OvernightCycleResult) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(entry.to_jsonl() + "\n")

    # -- digest ---------------------------------------------------------
    def write_digest_if_due(self, now: Optional[datetime] = None) -> Optional[str]:
        """Write the morning digest once per day inside the digest window."""
        now = now or datetime.now()
        if not in_digest_window(now, self.digest_window):
            return None
        self.digest_dir.mkdir(parents=True, exist_ok=True)
        marker = self.digest_dir / f".last-digest-{now:%Y-%m-%d}"
        if marker.exists():
            return None
        entries = self._entries_since(now - timedelta(hours=24))
        if not entries:
            return None
        path = self.digest_dir / f"{now:%Y-%m-%d}.md"
        path.write_text(self._render_digest(now, entries), encoding="utf-8")
        marker.write_text("1", encoding="utf-8")
        return str(path)

    def _entries_since(self, since: datetime) -> list[dict]:
        out: list[dict] = []
        if not self.log_path.exists():
            return out
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            try:
                if datetime.fromisoformat(entry["ts"]) >= since:
                    out.append(entry)
            except (KeyError, ValueError, TypeError):
                continue
        return out

    def _render_digest(self, now: datetime, entries: list[dict]) -> str:
        jobs = [e for e in entries if e.get("worker")]
        failed = [e for e in jobs if e.get("worker_status") not in ("succeeded", "skipped", "")]
        proactive_ok = sum(1 for e in entries if e.get("proactive") == "ok")
        lines = [
            f"# Overnight digest — {now:%Y-%m-%d}",
            "",
            f"- Cycles: {len(entries)}",
            f"- Jobs run: {len(jobs)} ({len(failed)} with non-success status)",
            f"- Proactive ticks: {proactive_ok} ok, "
            f"{sum(1 for e in entries if e.get('proactive') != 'ok')} other",
            "",
        ]
        for e in failed[:10]:
            lines.append(f"- ⚠ job {e.get('worker')} status={e.get('worker_status')}")
        if not failed:
            lines.append("- No failed jobs.")
        lines.extend(self._render_outcomes(now))
        lines.extend(self._render_coverage(now, entries))
        return "\n".join(lines) + "\n"

    def _render_outcomes(self, now: datetime) -> list[str]:
        """TD6 (S06): what actually ran, from the outcome store."""
        since = (now - timedelta(hours=24)).timestamp()
        items = MorningDigest(self.root).collect(since)
        if not items:
            return ["", "## Outcomes", "- No completed jobs."]
        rendered = MorningDigest.render(items)
        return ["", "## Outcomes", *rendered.splitlines()]

    def _render_coverage(self, now: datetime, entries: list[dict]) -> list[str]:
        """TD6 (S06): when Ghost actually ran, and when it did not."""
        lines = ["", "## Coverage"]
        if not entries:
            lines.append("Ghost did not run during this window.")
            return lines
        times = []
        for e in entries:
            try:
                times.append(datetime.fromisoformat(e["ts"]))
            except (KeyError, ValueError, TypeError):
                continue
        if not times:
            lines.append("Ghost did not run during this window.")
            return lines
        times.sort()
        lines.append(f"- First cycle: {times[0]:%H:%M}")
        lines.append(f"- Last cycle: {times[-1]:%H:%M}")
        gap_threshold = timedelta(seconds=3 * self.interval)
        for prev, nxt in zip(times, times[1:]):
            if nxt - prev > gap_threshold:
                lines.append(f"- no cycles from {prev:%H:%M} to {nxt:%H:%M} "
                             "(PC asleep/offline or Ghost not running)")
        return lines

    # -- main loop ----------------------------------------------------------
    def run(self, *, max_cycles: Optional[int] = None) -> int:
        """Run cycles until max_cycles or emergency stop; returns cycles run."""
        stop = EmergencyStop(self.root)
        while not stop.active():
            if max_cycles is not None and self._cycles >= max_cycles:
                break
            self.run_cycle()
            if max_cycles is not None and self._cycles >= max_cycles:
                break
            self._sleep_between()
        return self._cycles

    def _sleep_between(self) -> None:
        # Honored for tests: they patch this to avoid real waiting.
        time.sleep(self.interval)

    def run_cycle(self, *, now: Optional[datetime] = None) -> OvernightCycleResult:
        """One unattended cycle: emergency-stop -> idle gate -> proactive + worker."""
        now = now or datetime.now()
        self._cycles += 1
        entry = OvernightCycleResult(cycle=self._cycles, ts=now.isoformat(timespec="seconds"),
                                     user_active=False,
                                     quiet_hours=in_quiet_hours(now, self.quiet_hours),
                                     emergency_stop=False)
        if EmergencyStop(self.root).active():
            entry.emergency_stop = True
            entry.proactive = "stopped"
            self._log(entry)
            return entry

        entry.user_active = self._user_active()
        if not entry.quiet_hours and not entry.user_active:
            try:
                result = self._proactive()()
                entry.proactive = str(result.get("status", "error"))
                entry.proactive_reason = str(result.get("reason", ""))
            except Exception as exc:  # never crash the unattended loop
                entry.proactive = "error"
                entry.errors.append(f"proactive: {exc!r}")

        if not entry.user_active:
            try:
                run_one = self._worker_next()
                result = run_one() if callable(run_one) else None
                if result is not None:
                    entry.worker = getattr(result, "job_id", "") or ""
                    entry.worker_status = getattr(result, "status", "")
            except Exception as exc:
                entry.errors.append(f"worker: {exc!r}")
                entry.worker_status = "error"

        self._log(entry)
        self.write_digest_if_due(now)
        return entry

    def run_until_idle(self) -> OvernightCycleResult:
        """Single pass executed when the owner goes idle."""
        return self.run_cycle()
