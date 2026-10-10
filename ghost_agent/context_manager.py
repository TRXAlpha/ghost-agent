"""Validated, retractable memory and bounded runtime context assembly."""
from __future__ import annotations

import json
import time
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from .identity import IdentityStore
from .autonomy import CapabilityRegistry
from .kernel import GoalStore
from .runtime import EventBus, WorldModel
from .context_synthesizer import ContextItem, ContextSynthesizer
from .utils.storage import atomic_json, exclusive_lock


@dataclass
class MemoryRecord:
    memory_id: str
    kind: str
    content: str
    source: str
    confidence: float
    validation: str = "unvalidated"
    expires_at: Optional[float] = None
    supporting_artifacts: list[str] = field(default_factory=list)
    invalidated_at: Optional[float] = None
    invalidation_reason: str = ""
    created_at: float = field(default_factory=time.time)
    depends_on: list[str] = field(default_factory=list)
    subject: str = ""
    sensitivity: str = "normal"
    last_verified_at: Optional[float] = None
    revisions: list[dict] = field(default_factory=list)


class ValidatedMemoryStore:
    KINDS = {"working", "episodic", "semantic", "procedural", "validated_knowledge",
             "preference", "hypothesis", "feedback"}
    SENSITIVITIES = {"normal", "personal", "health", "secret"}
    VALIDATIONS = {"unvalidated", "validated", "disputed", "retracted"}
    def __init__(self, project_root: Path) -> None:
        self.path = Path(project_root) / ".ghost" / "validated-memory.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[dict[str, Any]]:
        try:
            records = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(records, list):
                raise ValueError("Memory storage must be a list")
            seen = set()
            for raw in records:
                record = MemoryRecord(**raw)
                self._validate(record)
                if record.memory_id in seen:
                    raise ValueError("Duplicate memory ID")
                if not set(record.depends_on) <= seen:
                    raise ValueError("Memory dependencies must reference earlier records")
                seen.add(record.memory_id)
            return records
        except FileNotFoundError:
            return []
        except (ValueError, OSError, TypeError, OverflowError) as exc:
            raise ValueError("Memory storage is corrupt; preserve it for review") from exc

    def _save(self, records: list[dict[str, Any]]) -> None:
        atomic_json(self.path, records)

    @classmethod
    def _validate(cls, record: MemoryRecord) -> None:
        if not isinstance(record.kind, str) or record.kind not in cls.KINDS:
            raise ValueError(f"Unknown memory kind: {record.kind}")
        if not isinstance(record.sensitivity, str) or record.sensitivity not in cls.SENSITIVITIES:
            raise ValueError(f"Unknown memory sensitivity: {record.sensitivity}")
        if not isinstance(record.validation, str) or record.validation not in cls.VALIDATIONS:
            raise ValueError(f"Unknown memory validation: {record.validation}")
        if type(record.confidence) not in (int, float) or not 0.0 <= record.confidence <= 1.0:
            raise ValueError("Memory confidence must be between 0 and 1")
        if any(not isinstance(value, str) or not value.strip() for value in (record.memory_id, record.content, record.source)):
            raise ValueError("Memory ID, content, and source must be nonempty strings")
        for stamp in (record.created_at, record.expires_at, record.invalidated_at, record.last_verified_at):
            if stamp is not None and (type(stamp) not in (int, float) or stamp < 0 or not math.isfinite(stamp)):
                raise ValueError("Memory timestamps must be finite and nonnegative")
        if record.created_at is None:
            raise ValueError("Memory creation time is required")
        if (not isinstance(record.supporting_artifacts, list)
                or any(not isinstance(path, str) for path in record.supporting_artifacts)
                or not isinstance(record.invalidation_reason, str)):
            raise ValueError("Invalid memory artifact references or invalidation reason")
        if (not isinstance(record.depends_on, list)
                or any(not isinstance(identifier, str) or not identifier for identifier in record.depends_on)
                or len(set(record.depends_on)) != len(record.depends_on)):
            raise ValueError("Memory dependencies must be unique memory IDs")

    def remember(self, record: MemoryRecord) -> MemoryRecord:
        self._validate(record)
        with exclusive_lock(self.path.with_suffix(".lock")):
            data = self._load()
            if any(item["memory_id"] == record.memory_id for item in data):
                raise ValueError("Memory ID already exists; use a new ID for new evidence")
            available = {item.memory_id for item in self._active_records(data)
                         if record.validation != "validated" or item.validation == "validated"}
            if not set(record.depends_on) <= available:
                raise ValueError("Memory dependencies must be active and, for validated memory, validated")
            data.append(asdict(record))
            self._save(data)
        return record

    def invalidate(self, memory_id: str, reason: str) -> MemoryRecord:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Memory invalidation requires a reason")
        with exclusive_lock(self.path.with_suffix(".lock")):
            return self._invalidate(memory_id, reason)

    def _invalidate(self, memory_id: str, reason: str) -> MemoryRecord:
        data = self._load()
        root = next((item for item in data if item["memory_id"] == memory_id), None)
        if root is None:
            raise ValueError("Memory not found")
        affected = {memory_id}
        now = time.time()
        # Append-only dependencies always precede their descendants.
        for raw in data:
            if raw["memory_id"] in affected or affected.intersection(raw.get("depends_on", [])):
                affected.add(raw["memory_id"])
                if raw.get("invalidated_at") is not None:
                    continue
                raw["invalidated_at"] = now
                raw["validation"] = "retracted"
                raw["invalidation_reason"] = reason if raw is root else f"Dependency {memory_id} retracted: {reason}"
        self._save(data)
        return MemoryRecord(**root)

    @staticmethod
    def _active_records(data: list[dict[str, Any]]) -> list[MemoryRecord]:
        now = time.time()
        active = {}
        for raw in data:
            record = MemoryRecord(**raw)
            if (record.invalidated_at is None and record.validation != "retracted"
                    and (record.expires_at is None or record.expires_at > now)
                    and all(identifier in active for identifier in record.depends_on)
                    and (record.validation != "validated" or all(
                        active[identifier].validation == "validated" for identifier in record.depends_on))):
                active[record.memory_id] = record
        return list(active.values())

    def retract(self, memory_id: str, reason: str) -> MemoryRecord:
        """Mark a memory as retracted (soft delete, no cascade).

        Subsequent retrievals exclude retracted records.  The record remains
        on disk with validation='retracted' so the action is auditable.
        """
        return self.invalidate(memory_id, reason)

    def purge(self, memory_id: str, reason: str) -> MemoryRecord:
        """Hard-delete a memory and all its transitive dependents.

        Any record that cites `memory_id` (directly or through a chain) in its
        ``depends_on`` field is also purged.  Purged records are removed from
        the store entirely; the root record is returned for confirmation.
        """
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Memory purge requires a reason")
        with exclusive_lock(self.path.with_suffix(".lock")):
            data = self._load()
            root = next((item for item in data if item["memory_id"] == memory_id), None)
            if root is None:
                raise ValueError("Memory not found")
            # Transitive cascade via depends_on (append-only order guarantees
            # descendants appear after their ancestors in the data list).
            to_purge: set[str] = {memory_id}
            for raw in data:
                if to_purge.intersection(raw.get("depends_on", [])):
                    to_purge.add(raw["memory_id"])
            data = [item for item in data if item["memory_id"] not in to_purge]
            self._save(data)
            return MemoryRecord(**root)

    def edit(self, memory_id: str, *, content: Optional[str] = None,
             subject: Optional[str] = None, sensitivity: Optional[str] = None,
             confirm_verified: bool = False) -> MemoryRecord:
        """Owner edit of an active memory; content edits keep revision history."""
        with exclusive_lock(self.path.with_suffix(".lock")):
            data = self._load()
            raw = next((item for item in data if item["memory_id"] == memory_id), None)
            if raw is None:
                raise ValueError("Memory not found")
            record = MemoryRecord(**raw)
            if record.invalidated_at is not None or record.validation == "retracted":
                raise ValueError("Retracted or purged memories cannot be edited")
            if content is not None:
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("Memory content must be a nonempty string")
                revisions = record.revisions
                revisions.append({"content": record.content, "edited_at": time.time()})
                record.revisions = revisions[-10:]  # bounded revision history
                record.content = content
            if subject is not None:
                if not isinstance(subject, str):
                    raise ValueError("Memory subject must be a string")
                record.subject = subject
            if sensitivity is not None:
                if not isinstance(sensitivity, str) or sensitivity not in self.SENSITIVITIES:
                    raise ValueError(f"Unknown memory sensitivity: {sensitivity}")
                record.sensitivity = sensitivity
            if confirm_verified:
                record.last_verified_at = time.time()
            updated = MemoryRecord(**{**raw, **asdict(record)})
            self._validate(updated)
            for item in data:
                if item["memory_id"] == memory_id:
                    item.update(asdict(updated))
            self._save(data)
        return updated

    def active(self, validation: Optional[str] = None) -> list[MemoryRecord]:
        if validation is not None and validation not in self.VALIDATIONS:
            raise ValueError("Unknown memory validation filter")
        retention = self._load_retention()
        now = time.time()
        kept = []
        for record in self._active_records(self._load()):
            if validation is not None and record.validation != validation:
                continue
            window = self._retention_days(record, retention)
            # Retention only hides expired records; apply_retention purges them.
            if window is not None and record.created_at + window <= now:
                continue
            kept.append(record)
        return kept

    def _load_retention(self) -> dict[str, Any]:
        path = self.path.parent / "memory_retention.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"default_days": None, "by_kind": {}}
        except (ValueError, OSError) as exc:
            raise ValueError("Memory retention config is corrupt; preserve it for review") from exc
        if not isinstance(data, dict) or not isinstance(data.get("by_kind", {}), dict):
            raise ValueError("Memory retention config is corrupt; preserve it for review")
        return {"default_days": data.get("default_days"), "by_kind": data.get("by_kind", {})}

    def set_retention(self, kind: str, days: Optional[int]) -> None:
        if kind not in self.KINDS:
            raise ValueError(f"Unknown memory kind: {kind}")
        if days is not None and (type(days) is not int or days <= 0):
            raise ValueError("Retention days must be a positive integer or null")
        with exclusive_lock(self.path.with_suffix(".lock")):
            data = self._load_retention()
            if days is None:
                data["by_kind"].pop(kind, None)
            else:
                data["by_kind"][kind] = days
            atomic_json(self.path.parent / "memory_retention.json", data)

    def _retention_days(self, record: MemoryRecord, retention: dict[str, Any]) -> Optional[float]:
        days = retention["by_kind"].get(record.kind, retention.get("default_days"))
        if days is None:
            return None
        return float(days) * 86_400.0

    def apply_retention(self) -> int:
        """Purge records past their retention through the existing purge path."""
        retention = self._load_retention()
        now = time.time()
        data = self._load()
        expired: list[str] = []
        for raw in data:
            record = MemoryRecord(**raw)
            window = self._retention_days(record, retention)
            if window is not None and record.created_at + window <= now and record.invalidated_at is None:
                expired.append(record.memory_id)
        count = 0
        for memory_id in expired:
            try:
                self.purge(memory_id, "retention window elapsed")
            except ValueError:
                continue  # already cascade-purged by an earlier purge
            count += 1
        return count


class ContextAssembler:
    """Assemble bounded, current context instead of replaying conversation history."""
    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root)
        self.identity = IdentityStore(self.root)
        self.goals = GoalStore(self.root)
        self.world = WorldModel(self.root)
        self.events = EventBus(self.root)
        self.memories = ValidatedMemoryStore(self.root)
        self.capabilities = CapabilityRegistry(self.root)

    def assemble(self, goal_id: Optional[str] = None, max_chars: int = 8_000) -> Dict[str, Any]:
        """Bound json.dumps(result, ensure_ascii=False), not model token count.

        Keep the entire user profile and selected goal. If those cannot fit,
        refuse rather than silently remove their constraints or preferences.
        """
        if type(max_chars) is not int or max_chars <= 0:
            raise ValueError("max_chars must be a positive integer")
        profile = self.identity.load()
        goals = self.goals.active()
        active_goal = self.goals.get(goal_id) if goal_id else None
        if goal_id and active_goal is None:
            raise ValueError("Requested context goal is missing or unreadable")
        payload = {
            "user": {"display_name": profile.display_name, "preferences": profile.preferences,
                     "policies": profile.policies, "prohibitions": profile.prohibitions},
            "goal": asdict(active_goal) if active_goal else None,
            "active_goals": [asdict(goal) for goal in goals if goal.goal_id != goal_id][:8],
            "validated_memories": [asdict(memory) for memory in self.memories.active("validated")[:12]],
            "world_state": [asdict(item) for item in self.world.recent(12, max_age_seconds=86_400)],
            "prior_failures": self.capabilities._load().get("gaps", [])[-6:],
            "recent_events": [asdict(event) for event in self.events.recent(12)],
        }
        # Surface contradictions between validated memories so the model is
        # told not to assume either record is true.
        memory_items = [
            ContextItem(item_id=m["memory_id"], content=m["content"], source="validated_memory")
            for m in payload["validated_memories"]
        ]
        conflicts = ContextSynthesizer(self.root).detect_conflicts(memory_items)[:5]
        if conflicts:
            payload["conflicts"] = [
                {"a": c.item_ids[0], "b": c.item_ids[1], "reason": c.summary} for c in conflicts
            ]
            payload["conflict_warnings"] = [
                f"Conflicting records: {c.item_ids[0]} vs {c.item_ids[1]} ({c.summary});"
                " do not assume either is true."
                for c in conflicts
            ]

        def fits() -> bool:
            return len(json.dumps(payload, ensure_ascii=False)) <= max_chars

        if fits():
            return payload
        # Remove whole optional records, never sever a quote, policy, or goal
        # contract mid-string. Surface omissions so context is not mistaken for
        # a complete view of the user's state.
        order = ("conflicts", "conflict_warnings", "recent_events", "active_goals",
                 "prior_failures", "world_state", "validated_memories")
        payload["omitted_for_budget"] = {key: 0 for key in order}
        for key in order:
            while payload.get(key) and not fits():
                # Event/gap stores return chronological lists; discard their
                # oldest entries first. Active goals are priority ordered.
                payload[key].pop(0 if key in {"recent_events", "prior_failures"} else -1)
                payload["omitted_for_budget"][key] += 1
            if fits():
                return payload
        raise ValueError("Context budget cannot fit the complete user profile and goal; increase max_chars")
