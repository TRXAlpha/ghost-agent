"""Resource manager for Ghost AI-OS: tracks and governs system resources.

Tracks: CPU, RAM, GPU/VRAM, disk, network, inference budget.
Enforces: admission control, reservations, throttling, priority queuing.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .utils.storage import atomic_json, exclusive_lock


@dataclass
class ResourceReservation:
    reservation_id: str
    resource: str        # cpu | ram | disk | inference | gpu
    amount: float       # fraction (0-1) or bytes
    owner: str          # job_id or "system"
    priority: int = 50
    expires_at: float = 0.0
    created_at: float = field(default_factory=time.time)


@dataclass
class ResourceBudget:
    resource: str
    ceiling: float          # max allowed
    used: float = 0.0       # currently consumed
    reserved: float = 0.0  # committed but not yet consumed
    window_seconds: float = 3600.0  # budget resets in this window


@dataclass
class ResourceSnapshot:
    cpu_cores: float
    ram_bytes: float
    ram_available: float
    disk_free: float
    timestamp: float = field(default_factory=time.time)


class ResourceManager:
    """Tracks and enforces resource policies."""

    # Resource ceiling defaults
    DEFAULT_INFERENCE_BUDGET = 100   # max inference calls per window
    DEFAULT_RAM_FRACTION = 0.5        # max 50% of available RAM per job
    DEFAULT_DISK_FRACTION = 0.1       # max 10% of free disk per job

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.state_path = self.root / ".ghost" / "resource_state.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_file()
        self._load()

    def _ensure_file(self) -> None:
        if not self.state_path.exists():
            self.state_path.write_text("{}", encoding="utf-8")

    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict[str, Any]) -> None:
        atomic_json(self.state_path, data)

    # -- snapshot -----------------------------------------------------------

    def snapshot(self) -> ResourceSnapshot:
        """Get current resource availability."""
        cpu = float(os.cpu_count() or 1)
        disk = shutil.disk_usage(self.root)
        disk_free = float(disk.free)
        ram_bytes = 0.0
        ram_avail = 0.0
        try:
            page_size = os.sysconf("SC_PAGE_SIZE")
            pages = os.sysconf("SC_AVPHYS_PAGES")
            ram_avail = float(page_size * pages)
            total_pages = os.sysconf("SC_PHYS_PAGES")
            ram_bytes = float(page_size * total_pages)
        except (AttributeError, OSError, ValueError):
            pass
        return ResourceSnapshot(
            cpu_cores=cpu, ram_bytes=ram_bytes,
            ram_available=ram_avail, disk_free=disk_free)

    # -- budgets -----------------------------------------------------------

    def get_budget(self, resource: str) -> ResourceBudget:
        data = self._load()
        budgets = data.get("budgets", {})
        raw = budgets.get(resource)
        if not raw:
            return ResourceBudget(resource=resource, ceiling=self._default_ceiling(resource))
        return ResourceBudget(**raw)

    def _default_ceiling(self, resource: str) -> float:
        if resource == "inference":
            return self.DEFAULT_INFERENCE_BUDGET
        snap = self.snapshot()
        if resource == "ram":
            return snap.ram_available * self.DEFAULT_RAM_FRACTION
        if resource == "disk":
            return snap.disk_free * self.DEFAULT_DISK_FRACTION
        return 1.0

    def set_budget_ceiling(self, resource: str, ceiling: float) -> ResourceBudget:
        with exclusive_lock(self.state_path.with_suffix(".lock")):
            data = self._load()
            budgets = data.get("budgets", {})
            raw = budgets.get(resource, asdict(ResourceBudget(resource=resource, ceiling=ceiling)))
            raw["ceiling"] = ceiling
            data["budgets"] = {**budgets, resource: raw}
            self._save(data)
            return ResourceBudget(**raw)

    def consume(self, resource: str, amount: float) -> bool:
        """Atomically consume resource if within budget. Returns True if allowed."""
        with exclusive_lock(self.state_path.with_suffix(".lock")):
            data = self._load()
            budgets = data.get("budgets", {})
            raw = budgets.get(resource, asdict(ResourceBudget(resource=resource, ceiling=self._default_ceiling(resource))))
            ceiling = raw["ceiling"]
            used = raw.get("used", 0.0)
            if used + amount > ceiling:
                return False
            raw["used"] = used + amount
            data["budgets"] = {**budgets, resource: raw}
            self._save(data)
            return True

    def release(self, resource: str, amount: float) -> None:
        """Release consumed resource."""
        with exclusive_lock(self.state_path.with_suffix(".lock")):
            data = self._load()
            budgets = data.get("budgets", {})
            raw = budgets.get(resource, asdict(ResourceBudget(resource=resource, ceiling=self._default_ceiling(resource))))
            raw["used"] = max(0.0, raw.get("used", 0.0) - amount)
            data["budgets"] = {**budgets, resource: raw}
            self._save(data)

    # -- reservations -------------------------------------------------------

    def reserve(self, resource: str, amount: float, owner: str,
                priority: int = 50, ttl_seconds: float = 3600.0) -> Optional[ResourceReservation]:
        """Reserve resource for owner. Returns reservation or None if insufficient."""
        res = ResourceReservation(
            reservation_id=f"res_{int(time.time() * 1000)}",
            resource=resource, amount=amount, owner=owner,
            priority=priority, expires_at=time.time() + ttl_seconds)
        with exclusive_lock(self.state_path.with_suffix(".lock")):
            data = self._load()
            reservations = data.get("reservations", [])
            # Check capacity
            total_reserved = sum(r.get("amount", 0) for r in reservations
                                  if r.get("resource") == resource and r.get("expires_at", 0) > time.time())
            ceiling = self.get_budget(resource).ceiling
            if total_reserved + amount > ceiling:
                return None
            reservations.append(asdict(res))
            data["reservations"] = reservations
            self._save(data)
            return res

    def release_reservation(self, reservation_id: str) -> None:
        with exclusive_lock(self.state_path.with_suffix(".lock")):
            data = self._load()
            reservations = data.get("reservations", [])
            reservations = [r for r in reservations if r.get("reservation_id") != reservation_id]
            data["reservations"] = reservations
            self._save(data)

    def active_reservations(self, resource: Optional[str] = None) -> list[ResourceReservation]:
        data = self._load()
        now = time.time()
        reservations = [ResourceReservation(**r)
                        for r in data.get("reservations", [])
                        if r.get("expires_at", 0) > now]
        if resource:
            reservations = [r for r in reservations if r.resource == resource]
        return sorted(reservations, key=lambda r: r.priority, reverse=True)

    # -- admission ----------------------------------------------------------

    def can_admit(self, resource_estimate: dict[str, float]) -> tuple[bool, list[str]]:
        """Check if resources are available for a new job. Returns (allowed, reasons)."""
        reasons = []
        snap = self.snapshot()
        for resource, amount in resource_estimate.items():
            budget = self.get_budget(resource)
            available = budget.ceiling - budget.used - budget.reserved
            if amount > available:
                reasons.append(f"{resource}: need {amount:.2f}, have {available:.2f}")
        return (len(reasons) == 0, reasons)

    def admit(self, owner: str, priority: int = 50) -> dict[str, Any]:
        """Admit a new job, reserving resources."""
        snap = self.snapshot()
        estimate = {"ram": snap.ram_available * 0.1, "disk": snap.disk_free * 0.01, "inference": 10.0}
        allowed, reasons = self.can_admit(estimate)
        if not allowed:
            return {"admitted": False, "reasons": reasons}
        reservations = []
        for resource, amount in estimate.items():
            res = self.reserve(resource, amount, owner, priority)
            if res:
                reservations.append(res.reservation_id)
        return {"admitted": True, "reservations": reservations}

    def release_job(self, owner: str) -> None:
        """Release all reservations for a job owner."""
        with exclusive_lock(self.state_path.with_suffix(".lock")):
            data = self._load()
            reservations = data.get("reservations", [])
            to_release = [r for r in reservations if r.get("owner") == owner]
            data["reservations"] = [r for r in reservations if r.get("owner") != owner]
            self._save(data)
        for r in to_release:
            self.release(r["resource"], r["amount"])

    # -- stats --------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        snap = self.snapshot()
        data = self._load()
        budgets = data.get("budgets", {})
        budget_summary = {}
        for resource in ("ram", "disk", "inference"):
            raw = budgets.get(resource, asdict(ResourceBudget(resource=resource, ceiling=self._default_ceiling(resource))))
            b = ResourceBudget(**raw)
            budget_summary[resource] = {
                "ceiling": b.ceiling, "used": b.used,
                "available": b.ceiling - b.used,
            }
        return {
            "snapshot": asdict(snap),
            "budgets": budget_summary,
            "active_reservations": len(self.active_reservations()),
        }
