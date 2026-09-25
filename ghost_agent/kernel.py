"""Small, deterministic AI-OS kernel primitives independent of any LLM."""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from .utils.storage import atomic_json, exclusive_lock


@dataclass
class Goal:
    goal_id: str
    title: str
    kind: str
    parent_id: Optional[str] = None
    priority: int = 50
    status: str = "active"
    deadline_at: Optional[float] = None
    constraints: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class GoalStore:
    def __init__(self, project_root: Path) -> None:
        self.path = Path(project_root) / ".ghost" / "goals.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []
        except (json.JSONDecodeError, OSError):
            return []

    def _save(self, goals: list[dict[str, Any]]) -> None:
        self.path.write_text(json.dumps(goals, indent=2), encoding="utf-8")

    def create(self, title: str, kind: str = "outcome", parent_id: Optional[str] = None,
               priority: int = 50, constraints: Optional[list[str]] = None,
               deadline_at: Optional[float] = None, dependencies: Optional[list[str]] = None) -> Goal:
        if parent_id and not self.get(parent_id):
            raise ValueError(f"Unknown parent goal: {parent_id}")
        dependency_ids = dependencies or []
        unknown = [goal_id for goal_id in dependency_ids if not self.get(goal_id)]
        if unknown:
            raise ValueError(f"Unknown goal dependency: {unknown[0]}")
        digest = hashlib.sha1(f"{title}|{time.time_ns()}".encode()).hexdigest()[:10]
        goal = Goal(f"goal_{digest}", title.strip(), kind, parent_id, priority,
                    deadline_at=deadline_at, constraints=constraints or [], dependencies=dependency_ids)
        data = self._load()
        data.append(asdict(goal))
        self._save(data)
        return goal

    def get(self, goal_id: str) -> Optional[Goal]:
        return next((Goal(**item) for item in self._load() if item.get("goal_id") == goal_id), None)

    def active(self) -> list[Goal]:
        return sorted((Goal(**item) for item in self._load() if item.get("status") == "active"),
                      key=lambda goal: (-goal.priority, goal.created_at))

    def ready(self) -> list[Goal]:
        """Active goals whose dependency outcomes have all completed."""
        by_id = {goal.goal_id: goal for goal in (Goal(**item) for item in self._load())}
        return [goal for goal in self.active()
                if all(by_id.get(dependency) and by_id[dependency].status == "completed"
                       for dependency in goal.dependencies)]

    def mark_stale(self, now: Optional[float] = None) -> list[Goal]:
        current = time.time() if now is None else now
        stale: list[Goal] = []
        for goal in self.active():
            if goal.deadline_at is not None and goal.deadline_at < current:
                stale.append(self.set_status(goal.goal_id, "stale"))
        return stale

    def set_status(self, goal_id: str, status: str) -> Goal:
        """Persist an explicit lifecycle outcome for a goal."""
        if status not in {"active", "blocked", "completed", "cancelled", "stale"}:
            raise ValueError(f"Unknown goal status: {status}")
        data = self._load()
        for item in data:
            if item.get("goal_id") == goal_id:
                item["status"] = status
                item["updated_at"] = time.time()
                self._save(data)
                return Goal(**item)
        raise ValueError("Goal not found")


@dataclass
class CapabilityManifest:
    capability: str
    inputs: list[str]
    outputs: list[str]
    filesystem_scopes: list[str] = field(default_factory=list)
    network_allowed: bool = False
    secrets_required: list[str] = field(default_factory=list)
    side_effects: list[str] = field(default_factory=list)
    reversible: bool = True
    risk: str = "low"
    audit_required: bool = True
    rollback: str = "restore recorded state"
    resource_limits: Dict[str, float] = field(default_factory=dict)
    health_check: str = ""


@dataclass
class ActionProposal:
    capability: str
    inputs: Dict[str, Any]
    target: str = ""
    reason: str = ""


@dataclass
class Authorization:
    decision: str
    reason: str
    approval_required: bool


class CapabilityKernel:
    """Policy gate: the model may propose; this class decides authorization."""
    def __init__(self) -> None:
        self.manifests: dict[str, CapabilityManifest] = {}

    def register(self, manifest: CapabilityManifest) -> None:
        self.manifests[manifest.capability] = manifest

    def authorize(self, proposal: ActionProposal, sandbox_root: Path) -> Authorization:
        manifest = self.manifests.get(proposal.capability)
        if not manifest:
            return Authorization("denied", "Capability is not registered.", True)
        missing = [
            name for name in manifest.inputs
            if proposal.inputs.get(name) in (None, "") and not (name == "path" and proposal.target)
        ]
        if missing:
            return Authorization("denied", f"Capability is missing required input(s): {', '.join(missing)}", True)
        if proposal.capability.startswith("network.") and not manifest.network_allowed:
            return Authorization("denied", "Network access is not allowed by this capability.", True)
        if proposal.capability == "filesystem.write":
            try:
                target = Path(proposal.target).resolve()
                root = Path(sandbox_root).resolve()
                if not target.is_relative_to(root):
                    return Authorization("denied", "Write target escapes sandbox.", True)
            except (OSError, ValueError):
                return Authorization("denied", "Invalid write target.", True)
        approval = manifest.risk != "low" or bool(manifest.side_effects) or not manifest.reversible
        return Authorization("approved" if not approval else "needs_approval",
                             "Capability policy accepted proposal.", approval)


@dataclass
class Transaction:
    transaction_id: str
    goal_id: str
    proposal: Dict[str, Any]
    state: str = "prepared"
    preview: Dict[str, Any] = field(default_factory=dict)
    history: list[Dict[str, Any]] = field(default_factory=list)


class TransactionJournal:
    _TRANSITIONS = {
        "prepared": {"previewed", "rolled_back"},
        "previewed": {"committed", "rolled_back"},
        "committed": {"verified", "rolled_back"},
        "verified": {"rolled_back"},
        "rolled_back": set(),
    }

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root) / ".ghost" / "transactions"
        self.root.mkdir(parents=True, exist_ok=True)

    def prepare(self, goal_id: str, proposal: ActionProposal) -> Transaction:
        digest = hashlib.sha1(f"{goal_id}|{time.time_ns()}".encode()).hexdigest()[:10]
        tx = Transaction(f"tx_{digest}", goal_id, asdict(proposal))
        self._save(tx)
        return tx

    def transition(self, tx: Transaction, state: str, detail: Optional[Dict[str, Any]] = None) -> Transaction:
        if not re.fullmatch(r"tx_[A-Za-z0-9_]+", tx.transaction_id):
            raise ValueError("Invalid transaction ID")
        with exclusive_lock(self.root / f"{tx.transaction_id}.lock"):
            current = self.get(tx.transaction_id)
            if not current or asdict(current) != asdict(tx):
                raise ValueError("Transaction changed or is invalid; reload before transitioning")
            return self._transition_locked(tx, state, detail)

    def _transition_locked(self, tx: Transaction, state: str, detail: Optional[Dict[str, Any]]) -> Transaction:
        if state not in self._TRANSITIONS.get(tx.state, set()):
            raise ValueError(f"Cannot transition {tx.state} -> {state}")
        tx.history.append({"from": tx.state, "to": state, "at": time.time(), "detail": detail or {}})
        tx.state = state
        if state == "previewed":
            tx.preview = detail or {}
        self._save(tx)
        return tx

    def _save(self, tx: Transaction) -> None:
        atomic_json(self.root / f"{tx.transaction_id}.json", asdict(tx))

    def get(self, transaction_id: str) -> Optional[Transaction]:
        if not re.fullmatch(r"tx_[A-Za-z0-9_]+", transaction_id):
            return None
        path = self.root / f"{transaction_id}.json"
        try:
            return Transaction(**json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError):
            return None
