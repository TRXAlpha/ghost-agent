"""Local API/SDK facade for the Ghost control plane; transport remains opt-in.

This module provides the unified control plane with:
- Goal management with hierarchy and dependencies
- Opportunity pipeline visualization
- Job state and resource usage monitoring
- Integration health dashboard
- Notification preferences and routing
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field, asdict

from .approvals import ApprovalStore
from .command_budget import CommandBudget
from .checkins import LocalCheckIns
from .autonomy import OutcomeStore
from .autonomy import CapabilityRegistry
from .context_manager import ValidatedMemoryStore
from .devices import DeviceRegistry
from .provenance import ProvenanceLedger
from .promotion import PromotionManager
from .self_repair import SelfRepairManager
from .security import SecurityMonitor
from .intent import IntentStore
from .kernel import GoalStore
from .runtime import AttentionManager, Doctor, Event, EventBus, WorldModel, Scheduler
from .proactive import OpportunityStore


# ── Goal Hierarchy and Dependencies ─────────────────────────────────────────────

@dataclass
class GoalNode:
    """A goal with full hierarchy information for visualization."""
    goal_id: str
    title: str
    kind: str
    parent_id: Optional[str] = None
    priority: int = 50
    status: str = "active"
    deadline_at: Optional[float] = None
    dependencies: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    children: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)
    progress: float = 0.0  # 0.0 to 1.0
    tags: list[str] = field(default_factory=list)


@dataclass
class GoalTree:
    """Hierarchical goal structure for visualization."""
    nodes: list[GoalNode]
    roots: list[str]  # Goal IDs with no parent
    leaf_count: int
    active_count: int
    blocked_count: int
    completed_count: int


# ── Opportunity Pipeline ────────────────────────────────────────────────────────

@dataclass
class OpportunityPipeline:
    """Opportunity pipeline stages and counts."""
    proposed: int = 0
    queued: int = 0
    running: int = 0
    completed: int = 0
    rejected: int = 0
    dismissed: int = 0
    total: int = 0
    high_confidence: list[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ResourceUsage:
    """Job state and resource usage summary."""
    jobs: list[Dict[str, Any]]
    active_count: int = 0
    queued_count: int = 0
    completed_today: int = 0
    failed_today: int = 0
    total_cpu_time: float = 0.0
    total_memory_mb: float = 0.0
    disk_usage_mb: float = 0.0
    network_requests: int = 0


@dataclass
class IntegrationHealth:
    """Integration status for the health dashboard."""
    name: str
    status: str  # healthy, degraded, down, not_configured
    last_check: float
    latency_ms: Optional[float] = None
    error_count: int = 0
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NotificationPreferences:
    """Notification routing and delivery preferences."""
    channels: Dict[str, bool] = field(default_factory=dict)  # telegram, desktop, email, etc.
    quiet_hours_start: int = 22  # 0-23
    quiet_hours_end: int = 8     # 0-23
    priority_threshold: str = "normal"  # silent, normal, important, urgent
    batch_interval_seconds: int = 300  # 5 minutes
    enabled: bool = True
    digest_mode: bool = True  # Summarize instead of individual notifications


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

    def snapshot_json(self) -> str:
        return json.dumps(self.snapshot(), indent=2, ensure_ascii=False)

    def snapshot(self) -> Dict[str, Any]:
        outcome_root = self.outcomes.root
        outcome_count = len([path for path in outcome_root.iterdir() if path.is_dir()]) if outcome_root.exists() else 0
        deliverable = self.attention.deliverable(quiet_hours=self._quiet_hours())
        return {
            "goals": [goal.__dict__ for goal in self.goals.active()],
            "pending_approvals": [approval.__dict__ for approval in self.approvals.pending()],
            "notifications": [note.__dict__ for note in deliverable],
            "held_notifications": len(self.attention.pending()) - len(deliverable),
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

    def explain(self, item_id: str) -> Optional[Dict[str, Any]]:
        """Explain why a job or opportunity exists (functional 'why')."""
        if not isinstance(item_id, str) or not item_id.strip():
            raise ValueError("Explain requires an item id")
        item_id = item_id.strip()
        try:
            job = self.outcomes.load(item_id)
        except (OSError, ValueError):
            job = None
        opportunity = None
        # An opportunity id may be given directly, or linked from the job.
        linked_id = ""
        if job is not None:
            linked_id = job.acceptance_contract.get("opportunity_id", "")
        for candidate in OpportunityStore(self.root).list_all():
            if candidate.opportunity_id == item_id or (linked_id and not opportunity
                                                       and candidate.opportunity_id == linked_id):
                opportunity = candidate
                if opportunity.opportunity_id == item_id:
                    break
        if job is None and opportunity is not None and opportunity.job_id:
            try:
                job = self.outcomes.load(opportunity.job_id)
            except (OSError, ValueError):
                job = None
        if job is None and opportunity is None:
            return None
        goal = self.goals.get(job.goal_id) if job is not None and job.goal_id else None
        intent = next((record for record in self.intents._load()
                       if goal is not None and record.get("goal_id") == goal.goal_id), None)
        # Last 20 events whose payload mentions the id.
        needle = opportunity.opportunity_id if (job is None and opportunity is not None) else (job.job_id if job is not None else item_id)
        events = [event.__dict__ for event in self.events.recent(500)
                  if needle in json.dumps(event.payload)]
        return {
            "item_id": item_id,
            "job": job.__dict__ if job is not None else None,
            "opportunity": opportunity.__dict__ if opportunity is not None else None,
            "goal": goal.__dict__ if goal is not None else None,
            "intent": intent,
            "events": events[-20:],
        }

# ── Goal Management ─────────────────────────────────────────────────────────────

    def goal_tree(self) -> Dict[str, Any]:
        """Build hierarchical goal tree for visualization."""
        all_goals = self.goals._load()
        by_id = {g["goal_id"]: g for g in all_goals}
        
        # Build children/dependents mapping
        children_map: Dict[str, list] = {}
        dependents_map: Dict[str, list] = {}
        roots = []
        
        for goal in all_goals:
            pid = goal.get("parent_id")
            gid = goal["goal_id"]
            if pid and pid in by_id:
                children_map.setdefault(pid, []).append(gid)
            else:
                roots.append(gid)
            
            for dep in goal.get("dependencies", []):
                dependents_map.setdefault(dep, []).append(gid)
        
        nodes = []
        for g in all_goals:
            node = GoalNode(
                goal_id=g["goal_id"],
                title=g.get("title", ""),
                kind=g.get("kind", "outcome"),
                parent_id=g.get("parent_id"),
                priority=g.get("priority", 50),
                status=g.get("status", "active"),
                deadline_at=g.get("deadline_at"),
                dependencies=g.get("dependencies", []),
                created_at=g.get("created_at", time.time()),
                updated_at=g.get("updated_at", time.time()),
                children=children_map.get(g["goal_id"], []),
                dependents=dependents_map.get(g["goal_id"], []),
                progress=self._compute_goal_progress(g),
                tags=g.get("tags", []),
            )
            nodes.append(asdict(node))
        
        return {
            "nodes": nodes,
            "roots": roots,
            "leaf_count": len([n for n in nodes if not n["children"]]),
            "active_count": len([n for n in nodes if n["status"] == "active"]),
            "blocked_count": len([n for n in nodes if n["status"] == "blocked"]),
            "completed_count": len([n for n in nodes if n["status"] == "completed"]),
        }

    def _compute_goal_progress(self, goal: Dict) -> float:
        """Compute goal progress based on children completion."""
        all_goals = self.goals._load()
        children = [g for g in all_goals if g.get("parent_id") == goal.get("goal_id")]
        if not children:
            return 1.0 if goal.get("status") == "completed" else 0.0
        completed = sum(1 for c in children if c.get("status") == "completed")
        return completed / len(children) if children else 0.0

    def goal_create_with_dependencies(
        self,
        title: str,
        kind: str = "outcome",
        parent_id: Optional[str] = None,
        priority: int = 50,
        dependencies: Optional[List[str]] = None,
        deadline_at: Optional[float] = None,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create a goal with full dependency tracking."""
        goal = self.goals.create(
            title=title,
            kind=kind,
            parent_id=parent_id,
            priority=priority,
            dependencies=dependencies or [],
            deadline_at=deadline_at,
        )
        result = asdict(goal)
        if tags:
            # Update goal with tags
            goals_data = self.goals._load()
            for g in goals_data:
                if g["goal_id"] == goal.goal_id:
                    g["tags"] = tags
                    self.goals._save(goals_data)
                    result["tags"] = tags
                    break
        return result

    def goal_update(self, goal_id: str, **updates) -> Optional[Dict[str, Any]]:
        """Update goal fields."""
        goals_data = self.goals._load()
        for g in goals_data:
            if g["goal_id"] == goal_id:
                valid_fields = {"title", "priority", "status", "deadline_at", "dependencies", "tags"}
                for key, value in updates.items():
                    if key in valid_fields:
                        g[key] = value
                g["updated_at"] = time.time()
                self.goals._save(goals_data)
                return g
        return None

    def goal_ready(self) -> List[Dict[str, Any]]:
        """Return goals ready to execute (dependencies met)."""
        ready_goals = self.goals.ready()
        return [asdict(g) for g in ready_goals]

    def goal_blockers(self) -> List[Dict[str, Any]]:
        """Return goals blocked by dependencies."""
        all_goals = self.goals._load()
        blocked = []
        for g in all_goals:
            if g.get("status") == "active" and g.get("dependencies"):
                deps = g["dependencies"]
                dep_goals = {d: next((x.get("status") for x in all_goals if x.get("goal_id") == d), None) for d in deps}
                incomplete = [d for d, s in dep_goals.items() if s != "completed"]
                if incomplete:
                    blocked.append({
                        "goal_id": g["goal_id"],
                        "title": g.get("title"),
                        "blocking_deps": incomplete,
                    })
        return blocked

# ── Opportunity Pipeline ────────────────────────────────────────────────────────

    def opportunity_pipeline(self) -> Dict[str, Any]:
        """Get opportunity pipeline visualization data."""
        store = OpportunityStore(self.root)
        all_opps = store.list_all()
        
        pipeline = {
            "proposed": 0,
            "queued": 0,
            "running": 0,
            "completed": 0,
            "rejected": 0,
            "dismissed": 0,
        }
        high_confidence = []
        
        for opp in all_opps:
            status = opp.status if hasattr(opp, 'status') else opp.get("status", "proposed")
            if status in pipeline:
                pipeline[status] += 1
            
            conf = opp.confidence if hasattr(opp, 'confidence') else opp.get("confidence", 0)
            if conf >= 0.8:
                high_confidence.append({
                    "id": opp.opportunity_id if hasattr(opp, 'opportunity_id') else opp.get("opportunity_id", ""),
                    "title": opp.title if hasattr(opp, 'title') else opp.get("title", ""),
                    "confidence": conf,
                    "kind": opp.kind if hasattr(opp, 'kind') else opp.get("kind", ""),
                    "cost_effort": opp.cost_effort if hasattr(opp, 'cost_effort') else opp.get("cost_effort", "small"),
                })
        
        return {
            "pipeline": pipeline,
            "total": len(all_opps),
            "high_confidence": sorted(high_confidence, key=lambda x: -x["confidence"])[:5],
            "recent": [asdict(o) if hasattr(o, '__dict__') else o for o in all_opps[-10:]],
        }

# ── Job State and Resource Usage ────────────────────────────────────────────────

    def resource_usage(self) -> Dict[str, Any]:
        """Get job state and resource usage summary."""
        import shutil
        
        jobs = self.scheduler.list()
        now = time.time()
        today_start = now - (now % 86400)
        
        active = [j for j in jobs if j.status == "running"]
        queued = [j for j in jobs if j.status == "queued"]
        completed_today = [
            j for j in jobs 
            if j.status == "completed" and j.created_at >= today_start
        ]
        failed_today = [
            j for j in jobs 
            if j.status == "failed" and j.created_at >= today_start
        ]
        
        # Estimate disk usage
        disk_usage_mb = 0.0
        if self.outcomes.root.exists():
            try:
                total_bytes = sum(
                    f.stat().st_size for f in self.outcomes.root.rglob("*") if f.is_file()
                )
                disk_usage_mb = total_bytes / (1024 * 1024)
            except OSError:
                pass
        
        return {
            "jobs": [asdict(j) for j in jobs],
            "active_count": len(active),
            "queued_count": len(queued),
            "completed_today": len(completed_today),
            "failed_today": len(failed_today),
            "total_disk_mb": round(disk_usage_mb, 2),
            "available_resources": ResourceMonitor(self.root).available(),
        }

# ── Integration Health ───────────────────────────────────────────────────────────

    def integration_health(self) -> Dict[str, Any]:
        """Get integration health dashboard data."""
        now = time.time()
        health_status = Doctor(self.root).check()
        
        integrations = []
        key_integrations = ["telegram", "huawei", "desksentinel", "ollama", "docker"]
        
        for name in key_integrations:
            status = health_status.get(name, "unknown")
            status_map = {
                "ok": "healthy",
                "healthy": "healthy",
                "not_configured": "not_configured",
                "not_ready": "degraded",
                "degraded": "degraded",
                "not_running": "down",
                "error": "down",
                "unknown": "unknown",
            }
            integrations.append(IntegrationHealth(
                name=name,
                status=status_map.get(status, "unknown"),
                last_check=now,
                details={"raw_status": status},
            ))
        
        return {
            "integrations": [asdict(i) for i in integrations],
            "overall_status": "healthy" if all(
                i.status == "healthy" for i in integrations
            ) else "degraded",
            "last_full_check": now,
            "checks": health_status,
        }

# ── Notification Preferences ────────────────────────────────────────────────────

    def notification_preferences(self) -> Dict[str, Any]:
        """Get notification preferences."""
        prefs_path = self.root / ".ghost" / "notification_prefs.json"
        try:
            if prefs_path.exists():
                return json.loads(prefs_path.read_text())
        except (OSError, json.JSONDecodeError):
            pass
        
        # Default preferences
        return asdict(NotificationPreferences())

    def _quiet_hours(self) -> tuple[int, int] | None:
        """Quiet-hours tuple from notification preferences; deliver all if missing."""
        prefs = self.notification_preferences()
        start, end = prefs.get("quiet_hours_start"), prefs.get("quiet_hours_end")
        if type(start) is int and type(end) is int and 0 <= start <= 23 and 0 <= end <= 23:
            return (start, end)
        return None

    def notification_preferences_save(self, prefs: Dict[str, Any]) -> Dict[str, Any]:
        """Save notification preferences."""
        prefs_path = self.root / ".ghost" / "notification_prefs.json"
        prefs_path.parent.mkdir(parents=True, exist_ok=True)
        prefs_path.write_text(json.dumps(prefs, indent=2))
        return prefs

    def notification_test(self, channel: str = "telegram") -> Dict[str, Any]:
        """Test notification delivery on a channel."""
        # This would integrate with the notification system
        return {
            "channel": channel,
            "status": "sent" if self.notification_preferences().get("channels", {}).get(channel, False) else "disabled",
            "timestamp": time.time(),
        }

# ── Versioned export (R14) ─────────────────────────────────────────────────────

    def export(self, include: Optional[set] = None) -> Dict[str, Any]:
        """Versioned, secret-free export of personal state for backup/portability.

        Sections are read through each store's existing list/load method.
        Secrets and credential references are redacted recursively.
        """
        from datetime import datetime, timezone

        from .kernel import GoalStore
        from .context_manager import ValidatedMemoryStore
        from .devices import DeviceRegistry
        from .proactive import OpportunityStore
        from .policy import PolicyStore
        from .security import redact_secrets

        def _jobs() -> list:
            return [job.__dict__ if hasattr(job, "__dict__") else job
                    for job in self.scheduler.list()]

        all_sections: Dict[str, Any] = {
            "goals": [goal.__dict__ if hasattr(goal, "__dict__") else goal
                      for goal in GoalStore(self.root)._load()],
            "approvals": self.approvals._load(),
            "notifications": [note.__dict__ if hasattr(note, "__dict__") else note
                              for note in self.attention.pending(include_acknowledged=True)],
            "validated_memories": [record.__dict__ if hasattr(record, "__dict__") else record
                                   for record in self.memories.active()],
            "devices": [device.__dict__ if hasattr(device, "__dict__") else device
                        for device in self.devices.list()],
            "opportunities": [opp.__dict__ if hasattr(opp, "__dict__") else opp
                              for opp in OpportunityStore(self.root).list_all()],
            "scheduler_jobs": [job.__dict__ if hasattr(job, "__dict__") else job
                               for job in self.scheduler.list()],
            "capability_gaps": CapabilityRegistry(self.root)._load().get("gaps", []),
            "policy": PolicyStore(self.root).load(),
        }
        wanted = sorted(all_sections) if include is None else [
            name for name in all_sections if name in set(include)]
        unknown = set(include or ()) - set(all_sections)
        if unknown:
            raise ValueError(f"unknown export section(s): {sorted(unknown)}")
        return {
            "format": "ghost-export",
            "version": 1,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "sections": {name: redact_secrets(all_sections[name]) for name in wanted},
        }


class ResourceMonitor:
    """Portable, read-only resource snapshot used for scheduler admission."""

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root)

    def available(self) -> Dict[str, float]:
        import os
        disk = shutil.disk_usage(self.root)
        snapshot: Dict[str, float] = {
            "cpu_cores": float(os.cpu_count() or 1),
            "disk_bytes": float(disk.free),
        }
        try:
            page_size = os.sysconf("SC_PAGE_SIZE")
            pages = os.sysconf("SC_AVPHYS_PAGES")
            snapshot["ram_bytes"] = float(page_size * pages)
        except (AttributeError, OSError, ValueError):
            pass
        return snapshot


if __name__ == "__main__":
    # Quick test
    sdk = GhostSDK(Path.cwd())
    print("Goal tree:", len(sdk.goal_tree()["nodes"]), "nodes")
    print("Pipeline:", sdk.opportunity_pipeline()["pipeline"])
    print("Resources:", sdk.resource_usage()["active_count"], "active jobs")
    print("Integrations:", sdk.integration_health()["overall_status"])
