"""Opt-in local attention reminders; no model calls or external delivery."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import time
from uuid import uuid4

from .runtime import AttentionManager, EmergencyStop, Notification
from .utils.storage import atomic_json, exclusive_lock


@dataclass
class CheckInSchedule:
    schedule_id: str
    enabled: bool
    interval_seconds: int
    next_due: float
    message: str
    quiet_hours: list[int]


class LocalCheckIns:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / ".ghost" / "checkins.json"

    @staticmethod
    def _validate(item: CheckInSchedule) -> None:
        if (not isinstance(item.schedule_id, str) or len(item.schedule_id) != 32
                or any(c not in "0123456789abcdef" for c in item.schedule_id)
                or type(item.enabled) is not bool
                or type(item.interval_seconds) is not int
                or not 60 <= item.interval_seconds <= 31536000
                or type(item.next_due) not in (int, float)
                or not 0 <= item.next_due < 1e15
                or not isinstance(item.message, str) or not item.message.strip()
                or len(item.message) > 2000
                or not isinstance(item.quiet_hours, list) or len(item.quiet_hours) != 2
                or any(type(h) is not int or not 0 <= h <= 23 for h in item.quiet_hours)):
            raise ValueError("Invalid local check-in schedule")

    def status(self) -> CheckInSchedule | None:
        try:
            item = CheckInSchedule(**json.loads(self.path.read_text(encoding="utf-8")))
            self._validate(item)
            return item
        except FileNotFoundError:
            return None
        except (ValueError, TypeError, OSError) as exc:
            raise ValueError("Check-in storage is corrupt; preserve it for review") from exc

    def configure(self, interval_seconds: int, message: str,
                  quiet_hours: tuple[int, int] = (22, 8)) -> CheckInSchedule:
        item = CheckInSchedule(uuid4().hex, True, interval_seconds,
                               time.time() + interval_seconds, message, list(quiet_hours))
        self._validate(item)
        with exclusive_lock(self.path.with_suffix(".lock")):
            self.status()  # Never overwrite corrupt user state.
            atomic_json(self.path, asdict(item))
        return item

    def disable(self) -> CheckInSchedule | None:
        with exclusive_lock(self.path.with_suffix(".lock")):
            item = self.status()
            if item:
                item.enabled = False
                atomic_json(self.path, asdict(item))
            return item

    def tick(self) -> Notification | None:
        # Unconfigured polling must not initialize any state.
        if not self.path.exists():
            return None
        with exclusive_lock(self.path.with_suffix(".lock")):
            item = self.status()
            now = time.time()
            if not item or not item.enabled or now < item.next_due:
                return None
            if EmergencyStop(self.root).active():
                return None
            attention = AttentionManager(self.root)
            notes = attention.pending(include_acknowledged=True)
            note_id = f"checkin_{item.schedule_id}_{item.next_due}"
            existing = next((n for n in notes if n.notification_id == note_id), None)
            if existing is None:
                start, end = item.quiet_hours
                hour = time.localtime(now).tm_hour
                quiet = start <= hour < end if start <= end else hour >= start or hour < end
                if quiet or any(n.source == "checkin" and n.status == "pending" for n in notes):
                    return None
                existing = attention.notify(Notification("normal", item.message,
                    notification_id=note_id, source="checkin", created_at=now))
            # Notification is committed first. A crash before schedule commit is
            # recovered by its stable ID, even if it was already acknowledged.
            item.next_due = now + item.interval_seconds
            atomic_json(self.path, asdict(item))
            return existing
