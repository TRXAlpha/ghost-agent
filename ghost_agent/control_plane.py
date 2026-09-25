"""Local API/SDK facade for the Ghost control plane; transport remains opt-in."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from .approvals import ApprovalStore
from .command_budget import CommandBudget
from .checkins import LocalCheckIns
from .autonomy import OutcomeStore
from .context_manager import ValidatedMemoryStore
from .devices import DeviceRegistry
from .provenance import ProvenanceLedger
from .promotion import PromotionManager
from .self_repair import SelfRepairManager
from .security import SecurityMonitor
from .intent import IntentStore
from .kernel import GoalStore
from .runtime import AttentionManager, Doctor, Event, EventBus, WorldModel, Scheduler


class GhostSDK:
    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root)
        self.goals = GoalStore(self.root)
        self.events = EventBus(self.root)
        self.world = WorldModel(self.root)
        self.approvals = ApprovalStore(self.root)
        self.outcomes = OutcomeStore(self.root)
        self.memories = ValidatedMemoryStore(self.root)
        self.attention = AttentionManager(self.root)
        self.devices = DeviceRegistry(self.root)
        self.provenance = ProvenanceLedger(self.root)
        self.promotions = PromotionManager(self.root)
        self.self_repair = SelfRepairManager(self.root)
        self.security = SecurityMonitor(self.root)
        self.intents = IntentStore(self.root)
        self.scheduler = Scheduler(self.root)

    def create_goal(self, title: str, **kwargs: Any) -> Dict[str, Any]:
        return self.goals.create(title, **kwargs).__dict__

    def emit(self, kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.events.emit(Event(kind, payload)).__dict__

    def observe(self, entity_id: str, kind: str, state: Dict[str, Any], source: str,
                confidence: float = 1.0) -> Dict[str, Any]:
        from .runtime import Observation
        return self.world.observe(Observation(entity_id, kind, state, source, confidence)).__dict__

    def register_device(self, device_id: str, name: str, kind: str, capabilities: list[str],
                        constraints: Dict[str, Any] | None = None) -> Dict[str, Any]:
        return self.devices.register(device_id, name, kind, capabilities, constraints).__dict__

    def snapshot(self) -> Dict[str, Any]:
        outcome_root = self.outcomes.root
        outcome_count = len([path for path in outcome_root.iterdir() if path.is_dir()]) if outcome_root.exists() else 0
        return {
            "goals": [goal.__dict__ for goal in self.goals.active()],
            "pending_approvals": [approval.__dict__ for approval in self.approvals.pending()],
            "notifications": [note.__dict__ for note in self.attention.pending()],
            "validated_memories": [memory.__dict__ for memory in self.memories.active("validated")],
            "recent_events": [event.__dict__ for event in self.events.recent(20)],
            "outcome_count": outcome_count,
            "command_budgets": self.command_budgets(),
            "check_in": self.check_in_status(),
            "jobs": [job.__dict__ for job in self.scheduler.list()],
            "devices": [device.__dict__ for device in self.devices.list()],
            "recent_provenance": [record.__dict__ for record in self.provenance.all()[-20:]],
            "promotions": self.promotions.list(),
            "self_repair": [proposal.__dict__ for proposal in self.self_repair.list()[-20:]],
            "security_events": self.security.recent_events(20),
            "intents": self.intents._load()[-20:],
            "health": Doctor(self.root).check(),
        }

    def command_budgets(self) -> list[Dict[str, Any]]:
        summaries = []
        for job in self.outcomes.list()[-20:]:
            try:
                path = self.outcomes.path_for(job) / "run" / "command_budget.json"
                if path.resolve() != path:
                    raise ValueError("Redirected budget path")
                budget = CommandBudget.inspect(path)
                summaries.append({"job_id": job.job_id, "status": "available" if budget else "not_started",
                                  "budget": budget})
            except (OSError, ValueError):
                summaries.append({"job_id": job.job_id, "status": "invalid", "budget": None})
        return summaries

    def check_in_status(self) -> Dict[str, Any]:
        try:
            schedule = LocalCheckIns(self.root).status()
            return {"status": "enabled" if schedule and schedule.enabled else
                    "disabled" if schedule else "not_configured",
                    "schedule": schedule.__dict__ if schedule else None}
        except (OSError, ValueError):
            return {"status": "invalid", "schedule": None}

    def snapshot_json(self) -> str:
        return json.dumps(self.snapshot(), indent=2, ensure_ascii=False)
