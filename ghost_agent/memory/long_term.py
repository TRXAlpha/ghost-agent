"""Long-term memory: durable personal facts, preferences, and goals.

Stores:
- facts: explicit owner statements and verified observations
- preferences: inferred or declared preferences with confidence
- goals: personal objectives with progress tracking
- constraints: known limits and policies

Read-only by default; updates go through validated correction flow.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from ..utils.storage import atomic_json, exclusive_lock


@dataclass
class Fact:
    fact_id: str
    subject: str
    predicate: str
    value: str
    source: str
    confidence: float = 1.0
    verified_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = None
    sensitivity: str = "personal"  # personal | health | financial | public
    retracted_at: Optional[float] = None
    retraction_reason: str = ""
    created_at: float = field(default_factory=time.time)


@dataclass
class Preference:
    preference_id: str
    kind: str  # declared | inferred | corrected
    subject: str
    preference: str
    confidence: float = 0.5
    source: str = ""
    retracted: bool = False
    retraction_reason: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass
class PersonalGoal:
    goal_id: str
    title: str
    owner: str = "user"
    priority: int = 50
    deadline_at: Optional[float] = None
    progress: float = 0.0  # 0.0 to 1.0
    status: str = "active"  # active | completed | cancelled | stale
    tags: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class LongTermMemory:
    """Durable long-term personal memory store."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.dir_path = self.root / ".ghost" / "memory" / "long_term"
        self.facts_path = self.dir_path / "facts.json"
        self.prefs_path = self.dir_path / "preferences.json"
        self.goals_path = self.dir_path / "goals.json"
        self.dir_path.mkdir(parents=True, exist_ok=True)

    # -- persistence helpers ------------------------------------------------

    @staticmethod
    def _load_list(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("Expected list")
            return raw
        except (OSError, ValueError, TypeError):
            return []

    @staticmethod
    def _save_list(path: Path, items: list[dict[str, Any]]) -> None:
        atomic_json(path, items)

    @staticmethod
    def _make_id(prefix: str, seed: str) -> str:
        return f"{prefix}_{hashlib.sha1(seed.encode()).hexdigest()[:12]}"

    # -- facts --------------------------------------------------------------

    def add_fact(self, subject: str, predicate: str, value: str,
                 source: str, confidence: float = 1.0,
                 sensitivity: str = "personal",
                 expires_at: Optional[float] = None) -> Fact:
        if not all(isinstance(x, str) and x.strip() for x in (subject, predicate, value, source)):
            raise ValueError("subject, predicate, value, source must be nonempty strings")
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        fact = Fact(
            fact_id=self._make_id("fact", f"{subject}|{predicate}|{value}|{time.time_ns()}"),
            subject=subject.strip(), predicate=predicate.strip(),
            value=value.strip(), source=source.strip(),
            confidence=confidence, sensitivity=sensitivity, expires_at=expires_at,
        )
        with exclusive_lock(self.facts_path.with_suffix(".lock")):
            items = self._load_list(self.facts_path)
            items.append(asdict(fact))
            self._save_list(self.facts_path, items)
        return fact

    def get_facts(self, subject: Optional[str] = None,
                  active_only: bool = True) -> list[Fact]:
        now = time.time()
        facts = [Fact(**item) for item in self._load_list(self.facts_path)]
        if active_only:
            facts = [f for f in facts
                      if f.retracted_at is None
                      and (f.expires_at is None or f.expires_at > now)]
        if subject:
            facts = [f for f in facts if f.subject == subject]
        return facts

    def retract_fact(self, fact_id: str, reason: str) -> Fact:
        with exclusive_lock(self.facts_path.with_suffix(".lock")):
            items = self._load_list(self.facts_path)
            for item in items:
                if item.get("fact_id") == fact_id:
                    item["retracted_at"] = time.time()
                    item["retraction_reason"] = reason[:400]
                    self._save_list(self.facts_path, items)
                    return Fact(**item)
        raise ValueError("Fact not found")

    # -- preferences --------------------------------------------------------

    def add_preference(self, subject: str, preference: str,
                      kind: str = "inferred",
                      confidence: float = 0.5,
                      source: str = "") -> Preference:
        if kind not in {"declared", "inferred", "corrected"}:
            raise ValueError("kind must be declared, inferred, or corrected")
        pref = Preference(
            preference_id=self._make_id("pref", f"{subject}|{preference}|{time.time_ns()}"),
            kind=kind, subject=subject.strip(), preference=preference.strip(),
            confidence=confidence, source=source.strip(),
        )
        with exclusive_lock(self.prefs_path.with_suffix(".lock")):
            items = self._load_list(self.prefs_path)
            items.append(asdict(pref))
            self._save_list(self.prefs_path, items)
        return pref

    def get_preferences(self, kind: Optional[str] = None) -> list[Preference]:
        prefs = [Preference(**item) for item in self._load_list(self.prefs_path)]
        prefs = [p for p in prefs if not p.retracted]
        if kind:
            prefs = [p for p in prefs if p.kind == kind]
        return prefs

    def retract_preference(self, preference_id: str, reason: str) -> Preference:
        with exclusive_lock(self.prefs_path.with_suffix(".lock")):
            items = self._load_list(self.prefs_path)
            for item in items:
                if item.get("preference_id") == preference_id:
                    item["retracted"] = True
                    item["retraction_reason"] = reason[:400]
                    item["updated_at"] = time.time()
                    self._save_list(self.prefs_path, items)
                    return Preference(**item)
        raise ValueError("Preference not found")

    # -- personal goals ------------------------------------------------------

    def add_goal(self, title: str, priority: int = 50,
                 deadline_at: Optional[float] = None,
                 tags: Optional[list[str]] = None,
                 constraints: Optional[list[str]] = None) -> PersonalGoal:
        goal = PersonalGoal(
            goal_id=self._make_id("pgoal", f"{title}|{time.time_ns()}"),
            title=title.strip(), priority=priority,
            deadline_at=deadline_at, tags=tags or [], constraints=constraints or [],
        )
        with exclusive_lock(self.goals_path.with_suffix(".lock")):
            items = self._load_list(self.goals_path)
            items.append(asdict(goal))
            self._save_list(self.goals_path, items)
        return goal

    def get_goals(self, status: Optional[str] = None) -> list[PersonalGoal]:
        goals = [PersonalGoal(**item) for item in self._load_list(self.goals_path)]
        if status:
            goals = [g for g in goals if g.status == status]
        return goals

    def update_progress(self, goal_id: str, progress: float) -> PersonalGoal:
        if not 0 <= progress <= 1:
            raise ValueError("progress must be between 0 and 1")
        with exclusive_lock(self.goals_path.with_suffix(".lock")):
            items = self._load_list(self.goals_path)
            for item in items:
                if item.get("goal_id") == goal_id:
                    item["progress"] = progress
                    item["updated_at"] = time.time()
                    if progress >= 1.0:
                        item["status"] = "completed"
                    self._save_list(self.goals_path, items)
                    return PersonalGoal(**item)
        raise ValueError("Goal not found")

    def set_status(self, goal_id: str, status: str) -> PersonalGoal:
        if status not in {"active", "completed", "cancelled", "stale"}:
            raise ValueError("invalid status")
        with exclusive_lock(self.goals_path.with_suffix(".lock")):
            items = self._load_list(self.goals_path)
            for item in items:
                if item.get("goal_id") == goal_id:
                    item["status"] = status
                    item["updated_at"] = time.time()
                    self._save_list(self.goals_path, items)
                    return PersonalGoal(**item)
        raise ValueError("Goal not found")

    # -- stats --------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        return {
            "facts": len(self._load_list(self.facts_path)),
            "preferences": len([p for p in self._load_list(self.prefs_path) if not p.get("retracted")]),
            "goals": len(self._load_list(self.goals_path)),
        }