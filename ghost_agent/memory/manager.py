"""Memory subsystem for Ghost: consolidated interfaces to all memory types."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..utils.storage import atomic_json, exclusive_lock


@dataclass
class EpisodicMemory:
    episode_id: str
    event_type: str
    title: str
    summary: str
    outcome: str = ""
    importance: float = 0.5
    entities: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkingItem:
    item_id: str
    content: str
    priority: int = 50
    expires_at: float = 0.0
    created_at: float = field(default_factory=time.time)


class MemoryManager:
    """Unified interface for long-term, episodic, and working memory."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.base_dir = self.root / ".ghost" / "memory"
        self.episodic_path = self.base_dir / "episodic.json"
        self.working_path = self.base_dir / "working.json"
        self.base_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _load_list(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, list) else []
        except (OSError, ValueError):
            return []

    @staticmethod
    def _save_list(path: Path, items: list[dict[str, Any]]) -> None:
        atomic_json(path, items)

    def remember(self, event_type: str, title: str, summary: str,
                 outcome: str = "", importance: float = 0.5,
                 entities: Optional[list[str]] = None,
                 context: Optional[dict[str, Any]] = None) -> EpisodicMemory:
        if event_type not in {"task", "conversation", "outcome", "decision"}:
            raise ValueError("event_type must be task, conversation, outcome, or decision")
        ep = EpisodicMemory(
            episode_id=f"ep_{hashlib.sha1(f'{title}|{summary}|{time.time_ns()}'.encode()).hexdigest()[:12]}",
            event_type=event_type, title=title, summary=summary,
            outcome=outcome, importance=importance,
            entities=entities or [], context=context or {})
        items = self._load_list(self.episodic_path)
        items.append(asdict(ep))
        self._save_list(self.episodic_path, items[-1000:])
        return ep

    def recall(self, event_type: Optional[str] = None,
               limit: int = 20, min_importance: float = 0.0) -> list[EpisodicMemory]:
        episodes = [EpisodicMemory(**i) for i in self._load_list(self.episodic_path)]
        if event_type:
            episodes = [e for e in episodes if e.event_type == event_type]
        if min_importance > 0:
            episodes = [e for e in episodes if e.importance >= min_importance]
        episodes.sort(key=lambda e: (e.importance, e.timestamp), reverse=True)
        return episodes[:limit]

    def add_working(self, content: str, priority: int = 50,
                    ttl_seconds: float = 3600) -> WorkingItem:
        item = WorkingItem(
            item_id=f"w_{hashlib.sha1(f'{content}|{time.time_ns()}'.encode()).hexdigest()[:12]}",
            content=content, priority=priority, expires_at=time.time() + ttl_seconds)
        items = self._load_list(self.working_path)
        items.append(asdict(item))
        self._save_list(self.working_path, items)
        return item

    def get_working(self, active_only: bool = True) -> list[WorkingItem]:
        now = time.time()
        items = [WorkingItem(**i) for i in self._load_list(self.working_path)]
        if active_only:
            items = [i for i in items if i.expires_at > now]
        return sorted(items, key=lambda i: i.priority, reverse=True)

    def stats(self) -> dict[str, Any]:
        return {
            "episodic": len(self._load_list(self.episodic_path)),
            "working": len(self._load_list(self.working_path)),
        }


def memory_for(root: Path) -> MemoryManager:
    return MemoryManager(root)
