"""User-controlled focus sessions with optional notification holding (R09)."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from ghost_agent.utils.storage import atomic_json

ALLOWED_INTERVENTIONS = {"hold_notifications"}
MIN_MINUTES = 1
MAX_MINUTES = 600


@dataclass
class FocusSession:
    purpose: str
    started_at: float
    ends_at: float
    interventions: list[str]
    stopped_at: Optional[float] = None


class FocusStore:
    """Persistent focus state in .ghost/focus.json; at most one active session."""

    def __init__(self, root: Path) -> None:
        self.path = Path(root) / ".ghost" / "focus.json"

    def _load(self) -> Optional[FocusSession]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (ValueError, OSError) as exc:
            raise ValueError("Focus storage is corrupt; preserve it for review") from exc
        if not isinstance(raw, dict):
            raise ValueError("Focus storage is corrupt; preserve it for review")
        try:
            session = FocusSession(**raw)
        except TypeError as exc:
            raise ValueError("Focus storage is corrupt; preserve it for review") from exc
        if (not isinstance(session.purpose, str) or not session.purpose.strip()
                or type(session.started_at) not in (int, float)
                or type(session.ends_at) not in (int, float)
                or not isinstance(session.interventions, list)
                or any(item not in ALLOWED_INTERVENTIONS for item in session.interventions)
                or (session.stopped_at is not None and type(session.stopped_at) not in (int, float))):
            raise ValueError("Focus storage is corrupt; preserve it for review")
        return session

    def start(self, purpose: str, minutes: int, interventions: list[str]) -> FocusSession:
        if not isinstance(purpose, str) or not 1 <= len(purpose.strip()) <= 200:
            raise ValueError("Focus purpose must be 1 to 200 characters")
        if type(minutes) is not int or not MIN_MINUTES <= minutes <= MAX_MINUTES:
            raise ValueError("Focus minutes must be an integer from 1 to 600")
        if (not isinstance(interventions, list)
                or any(item not in ALLOWED_INTERVENTIONS for item in interventions)):
            raise ValueError("Unknown focus intervention")
        self._load()  # Corrupt storage must block start rather than be silently overwritten.
        if self.active() is not None:
            raise ValueError("A focus session is already active")
        now = time.time()
        session = FocusSession(purpose=purpose, started_at=now,
                               ends_at=now + minutes * 60, interventions=list(interventions))
        atomic_json(self.path, asdict(session))
        return session

    def active(self, now: float | None = None) -> Optional[FocusSession]:
        try:
            session = self._load()
        except ValueError:
            return None  # Corrupt file: report no active session; start() still refuses.
        if session is None or session.stopped_at is not None:
            return None
        if now is None:
            now = time.time()
        if now >= session.ends_at:
            return None
        return session

    def stop(self) -> Optional[FocusSession]:
        session = self._load()
        if session is None or session.stopped_at is not None:
            return None
        now = time.time()
        if now >= session.ends_at:
            return None
        session.stopped_at = now
        atomic_json(self.path, asdict(session))
        return session
