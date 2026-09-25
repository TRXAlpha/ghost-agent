"""OJ11: local morning digest of overnight outcome results (S06).

S06 acceptance: "quiet hours suppress ordinary notifications while a morning
digest summarizes meaningful results."  This module collects the terminal
outcome results from the previous window, renders a bounded text digest, and
emits ONE deduplicated local attention notification after quiet hours end.

Deterministic and local: it uses the existing OutcomeStore and AttentionManager
and requires no model, Telegram, or upstream scheduler.  A model-backed long-form
summary can be layered on later; the mechanism itself is model-independent.
If nothing meaningful happened, no digest is emitted (no speculative work).
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Optional

from .autonomy import OutcomeStore
from .runtime import AttentionManager, EmergencyStop, Notification
from .utils.storage import atomic_json, exclusive_lock

_MAX_ITEMS = 20


@dataclass
class DigestState:
    enabled: bool = True
    last_date: str = ""  # "YYYY-MM-DD" of the last emitted digest day


def _is_quiet(hour: int, quiet_hours: tuple[int, int]) -> bool:
    start, end = quiet_hours
    return start <= hour < end if start <= end else hour >= start or hour < end


class MorningDigest:
    """Collect overnight outcomes and emit one deduplicated morning digest."""

    def __init__(self, root: Path, *, quiet_hours: tuple[int, int] = (22, 8),
                 digest_hour: int = 8, hours_back: float = 24.0,
                 collect_fn: Optional[Callable[[float], list[dict]]] = None) -> None:
        self.root = Path(root).resolve()
        self.path = self.root / ".ghost" / "digest.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not isinstance(quiet_hours, tuple) or len(quiet_hours) != 2 \
                or any(type(h) is not int or not 0 <= h <= 23 for h in quiet_hours):
            raise ValueError("quiet_hours must be (start, end) integers 0-23")
        self.quiet_hours = tuple(quiet_hours)
        if type(digest_hour) is not int or not 0 <= digest_hour <= 23:
            raise ValueError("digest_hour must be an integer 0-23")
        self.digest_hour = digest_hour
        if type(hours_back) not in (int, float) or not 0 < hours_back <= 24 * 90:
            raise ValueError("hours_back must be in (0, 2160]")
        self.hours_back = float(hours_back)
        self.collect_fn = collect_fn or self._collect_default

    # -- persistence ------------------------------------------------------
    def _load(self) -> DigestState:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
            if not isinstance(raw, dict):
                raise ValueError("Digest state must be an object")
            state = DigestState(**raw)
            if type(state.enabled) is not bool or not isinstance(state.last_date, str):
                raise ValueError("Invalid digest state")
            return state
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError("Digest state is corrupt; preserve it for review") from exc

    def _save(self, state: DigestState) -> None:
        atomic_json(self.path, asdict(state))

    # -- collection -------------------------------------------------------
    def _collect_default(self, since: float) -> list[dict]:
        """Terminal outcome results from the store since `since`."""
        items: list[dict] = []
        for job in OutcomeStore(self.root).list():
            if job.updated_at < since:
                continue
            if job.status in {"completed", "verified"} and job.result:
                items.append({"goal": job.goal[:240], "result": job.result, "finished": job.updated_at,
                              "status": "completed"})
            elif job.status == "failed":
                items.append({"goal": job.goal[:240], "result": "failed", "finished": job.updated_at,
                              "status": str(job.errors[0])[:80] if job.errors else "failed"})
        items.sort(key=lambda item: item.get("finished", 0))
        return items

    def collect(self, since: float) -> list[dict]:
        return self.collect_fn(since)[:_MAX_ITEMS]

    # -- rendering --------------------------------------------------------
    @staticmethod
    def render(items: list[dict]) -> str:
        if not items:
            return ""
        lines = ["Morning digest of overnight Ghost work:"]
        for item in items[: _MAX_ITEMS]:
            goal = item.get("goal", "").strip()
            result = (item.get("result") or "").strip()
            lines.append(f"- {goal} -> {result}" if result else f"- {goal}")
        return "\n".join(lines)

    # -- gating & emit ----------------------------------------------------
    def _should_emit(self, state: DigestState, now: float) -> bool:
        if not state.enabled or EmergencyStop(self.root).active():
            return False
        local = time.localtime(now)
        today = f"{local.tm_year}-{local.tm_mon:02d}-{local.tm_mday}"
        if state.last_date == today:
            return False  # already emitted today
        if _is_quiet(local.tm_hour, self.quiet_hours):
            return False  # wait until quiet hours end
        if local.tm_hour < self.digest_hour:
            return False
        return True

    def emit(self, now: Optional[float] = None) -> Optional[Notification]:
        current = time.time() if now is None else now
        local = time.localtime(current)
        today = f"{local.tm_year}-{local.tm_mon:02d}-{local.tm_mday}"
        with exclusive_lock(self.path.with_suffix(".lock")):
            state = self._load()
            if not self._should_emit(state, current):
                return None
            since = current - self.hours_back * 3600
            items = self.collect(since)
            if not items:
                # No meaningful overnight results -> skip (no speculative work).
                return None
            message = self.render(items)
            attention = AttentionManager(self.root)
            existing = attention.notify(Notification(
                "normal", message, notification_id=f"digest_{today}",
                source="digest", created_at=current))
            state.last_date = today
            self._save(state)
            return existing

    def disable(self) -> DigestState:
        with exclusive_lock(self.path.with_suffix(".lock")):
            state = self._load()
            state.enabled = False
            self._save(state)
            return state
