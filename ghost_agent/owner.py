"""Life layer LL0/LL1: owner files and owner-stated facts.

Ghost Core is the only writer. Owner statements ("I got a new Samsung watch")
become validated memory records (owner said it first-hand) plus an
``owner.fact`` event. The proactive loop consumes those events through a
durable cursor, so one statement triggers one proactive tick.

Parsing is deterministic and conservative: it labels a statement as a device,
goal, deadline, complaint or note. It never infers health conditions; health
wording only raises the sensitivity tag.
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from .context_manager import MemoryRecord, ValidatedMemoryStore
from .runtime import Event, EventBus
from .security import SECRET_PATTERNS
from .utils.storage import atomic_write, exclusive_lock

OWNER_FILES = ("SOUL.md", "USER.md")
FACT_KINDS = ("device", "goal", "deadline", "complaint", "note")
_MAX_STATEMENT = 1000
_MAX_FILE = 20_000

DEFAULT_SOUL = """# SOUL

I am Ghost, Christian's personal AI. I run on his PC and answer only to him.

Purpose toward Christian:
- Know his context: projects, devices, deadlines, routine.
- Pay attention, take useful initiative, and stay out of his way.
- Build things for him while he is away; report results with a "why".
- Reuse tools he already runs before building anything new.

Limits I keep:
- Facts and trends only about health. I never diagnose.
- Anything public or in his name is a draft. He presses send.
- I never act outside the grants he gave me.
"""

DEFAULT_USER = """# USER

Owner-written. Ghost reads this on every heartbeat and proactive tick.
Edit with: ghost owner edit USER.md --file <path>

## Routine
- (fill in: school/work hours, sleep window, quiet hours)

## Tools I already run (reuse before building)
- HealthHub: aggregates Android Health Connect on my phone. Samsung Health,
  Oura and other wearables sync into Health Connect, so new wearables need no
  new adapter.
- ghostpi (Raspberry Pi 4B): AdGuard, Ghostwall, ghost-hub bot, Jellyfin.
- ActivityWatch / WakaTime on the PC (when installed).

## Projects
- FTC robotics (mentor), Ghost, ignis, research.

## Preferences
- Terse messages. No filler.
"""

# Word prefixes; matched against word starts so "ill" never hits "will".
_HEALTH_WORDS = ("sleep", "slept", "heart", "hrv", "sick", "ill", "pain", "health", "temperature",
                 "fever", "doctor", "medic", "oura", "readiness", "steps", "weight", "tired")
_DEVICE_RE = re.compile(
    r"\b(?:i\s+(?:got|bought|have|own|received|ordered|just\s+got)|my\s+new|i'?m\s+using)\s+"
    r"(?:a\s+|an\s+|the\s+|new\s+|my\s+)*(?P<subject>[\w\- ]{2,60}?)(?:[.!,]|$|\s+(?:today|yesterday|for|from|and|arrived|came|is|works)\b)",
    re.IGNORECASE)
_DEVICE_NOUNS = ("watch", "phone", "ring", "band", "tracker", "laptop", "pc", "tablet",
                 "esp32", "pi", "printer", "camera", "router", "headphones", "earbuds",
                 "sensor", "board", "robot", "car", "drone", "radio", "monitor", "keyboard")
_GOAL_RE = re.compile(
    r"\b(?:i'?m|i\s+am)\s+(?:joining|entering|doing|starting|taking|signing\s+up\s+for)\s+(?P<subject>.{2,120}?)\s*(?:[.!]|$)"
    r"|\bi\s+(?:want|need|plan|have)\s+to\s+(?P<subject2>.{2,120}?)\s*(?:[.!]|$)",
    re.IGNORECASE)
_COMPLAINT_RE = re.compile(
    r"\b(?:annoying|annoys|hate|sucks|frustrat|painful|tedious|takes\s+forever|keeps\s+breaking|"
    r"always\s+(?:fails|breaks)|i\s+wish)\b", re.IGNORECASE)
_DATE_RE = re.compile(r"\b(?P<iso>\d{4}-\d{2}-\d{2})\b")


class OwnerError(ValueError):
    """An owner statement or owner file edit was refused."""


def owner_dir(root: Path) -> Path:
    """Directory holding SOUL.md, USER.md and their version history."""
    return Path(root).resolve() / ".ghost" / "owner"


def ensure_owner_files(root: Path) -> Path:
    """Create SOUL.md and USER.md from templates if missing; never overwrite."""
    base = owner_dir(root)
    base.mkdir(parents=True, exist_ok=True)
    for name, text in (("SOUL.md", DEFAULT_SOUL), ("USER.md", DEFAULT_USER)):
        path = base / name
        if not path.exists():
            atomic_write(path, text.encode("utf-8"))
    return base


def read_owner_files(root: Path) -> dict[str, str]:
    """Return {name: text} for the owner files, creating defaults first."""
    base = ensure_owner_files(root)
    return {name: (base / name).read_text(encoding="utf-8")[:_MAX_FILE] for name in OWNER_FILES}


def edit_owner_file(root: Path, name: str, text: str) -> dict[str, Any]:
    """Replace an owner file, keeping the previous version under history/."""
    if name not in OWNER_FILES:
        raise OwnerError(f"Owner file must be one of {', '.join(OWNER_FILES)}")
    if not isinstance(text, str) or not text.strip() or len(text) > _MAX_FILE:
        raise OwnerError("Owner file text must be nonempty and at most 20000 characters")
    if any(pattern.search(text) for pattern in SECRET_PATTERNS.values()):
        raise OwnerError("Owner file contains a recognized secret; remove it first")
    base = ensure_owner_files(root)
    path = base / name
    history = base / "history"
    history.mkdir(exist_ok=True)
    with exclusive_lock(base / f"{name}.lock"):
        previous = path.read_text(encoding="utf-8")
        version = f"{name}.{time.strftime('%Y%m%dT%H%M%S')}.{uuid4().hex[:6]}"
        atomic_write(history / version, previous.encode("utf-8"))
        atomic_write(path, text.encode("utf-8"))
    EventBus(root).emit(Event("owner.file_edited", {"name": name, "previous_version": version},
                              source="owner", sensitivity="personal"))
    return {"name": name, "previous_version": version, "chars": len(text)}


def owner_file_history(root: Path, name: str) -> list[str]:
    """Version filenames for one owner file, oldest first."""
    return sorted(p.name for p in (owner_dir(root) / "history").glob(f"{name}.*"))


def parse_statement(text: str) -> dict[str, Any]:
    """Deterministically label an owner statement. Never infers health conditions."""
    clean = " ".join(str(text).split())
    lower = clean.casefold()
    words = re.findall(r"[a-z0-9]+", lower)
    sensitivity = "health" if any(w == h or (len(h) > 3 and w.startswith(h))
                                  for w in words for h in _HEALTH_WORDS) else "personal"
    deadline_at: Optional[float] = None
    date = _DATE_RE.search(clean)
    if date:
        try:
            deadline_at = datetime.strptime(date.group("iso"), "%Y-%m-%d").timestamp()
        except ValueError:
            deadline_at = None
    device = _DEVICE_RE.search(clean)
    if device and any(noun in device.group("subject").casefold().split()
                      for noun in _DEVICE_NOUNS):
        return {"kind": "device", "subject": device.group("subject").strip(),
                "deadline_at": deadline_at, "sensitivity": sensitivity}
    goal = _GOAL_RE.search(clean)
    if goal:
        subject = (goal.group("subject") or goal.group("subject2") or "").strip()
        return {"kind": "deadline" if deadline_at else "goal", "subject": subject,
                "deadline_at": deadline_at, "sensitivity": sensitivity}
    if _COMPLAINT_RE.search(clean):
        return {"kind": "complaint", "subject": clean[:120], "deadline_at": deadline_at,
                "sensitivity": sensitivity}
    return {"kind": "deadline" if deadline_at else "note", "subject": clean[:120],
            "deadline_at": deadline_at, "sensitivity": sensitivity}


def tell(root: Path, statement: str, *, source: str = "cli") -> dict[str, Any]:
    """Store one owner statement as a validated fact and emit ``owner.fact``."""
    if source not in {"cli", "telegram", "api"}:
        raise OwnerError("Unknown owner statement source")
    if not isinstance(statement, str):
        raise OwnerError("Statement must be text")
    clean = " ".join(statement.split())
    if not clean or len(clean) > _MAX_STATEMENT:
        raise OwnerError("Statement is empty or longer than 1000 characters")
    if any(pattern.search(clean) for pattern in SECRET_PATTERNS.values()):
        raise OwnerError("Statement contains a recognized secret; it was not stored")
    parsed = parse_statement(clean)
    ensure_owner_files(root)
    record = MemoryRecord(
        memory_id=f"owner_{uuid4().hex[:12]}",
        kind="semantic",
        content=clean,
        source=f"owner:{source}",
        confidence=1.0,
        validation="validated",  # first-hand owner statement
        subject=f"owner.{parsed['kind']}",
        sensitivity=parsed["sensitivity"],
        last_verified_at=time.time(),
    )
    ValidatedMemoryStore(root).remember(record)
    goal_id = ""
    if parsed["kind"] in {"goal", "deadline"} and parsed["subject"]:
        from .kernel import GoalStore
        goal = GoalStore(root).create(parsed["subject"][:200], kind="owner", priority=60,
                                      deadline_at=parsed["deadline_at"])
        goal_id = goal.goal_id
        from .briefing import queue_briefing
        queue_briefing(root, goal_id, parsed["subject"], parsed["deadline_at"])
    event = EventBus(root).emit(Event(
        "owner.fact",
        {"memory_id": record.memory_id, "kind": parsed["kind"], "subject": parsed["subject"],
         "goal_id": goal_id, "deadline_at": parsed["deadline_at"]},
        source=f"owner:{source}", sensitivity=parsed["sensitivity"]))
    return {"memory_id": record.memory_id, "event_id": event.event_id, "goal_id": goal_id, **parsed}


def owner_facts(root: Path, *, limit: int = 20, include_health: bool = False) -> list[dict[str, Any]]:
    """Active owner-stated facts, newest last. Health-tagged facts are excluded by default."""
    store = ValidatedMemoryStore(root)
    records = [r for r in store._active_records(store._load())
               if r.subject.startswith("owner.") and r.source.startswith("owner:")]
    if not include_health:
        records = [r for r in records if r.sensitivity != "health"]
    return [{"memory_id": r.memory_id, "kind": r.subject.removeprefix("owner."),
             "content": r.content, "sensitivity": r.sensitivity, "created_at": r.created_at}
            for r in records[-limit:]]


def owner_context(root: Path, *, include_health: bool = False) -> dict[str, Any]:
    """Bounded owner context for model prompts: SOUL, USER, recent facts."""
    files = read_owner_files(root)
    return {"soul": files["SOUL.md"][:4000], "user": files["USER.md"][:6000],
            "facts": owner_facts(root, include_health=include_health)}


def fact_record(root: Path, memory_id: str) -> Optional[dict[str, Any]]:
    """Return one owner fact record by id (any status) for `ghost why`."""
    store = ValidatedMemoryStore(root)
    for raw in store._load():
        if raw.get("memory_id") == memory_id and str(raw.get("source", "")).startswith("owner:"):
            return dict(raw)
    return None


__all__ = ["OwnerError", "ensure_owner_files", "read_owner_files", "edit_owner_file",
           "owner_file_history", "parse_statement", "tell", "owner_facts", "owner_context",
           "fact_record"]
