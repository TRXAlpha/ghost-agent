"""OJ10 R08: Capability lifecycle — discover, acquire, activate, retire.

Maps the R08 state machine (discovered → researched → feasible → building →
testing → ready_to_activate → active → disabled/retired) onto the existing
CapabilityAcquisition records and adds version tracking, dependency analysis,
and retirement/cleanup.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .utils.storage import atomic_json, exclusive_lock


@dataclass
class CapabilityVersion:
    version: str
    manifest_path: str
    changelog: str
    tested_at: float
    retired_at: float = 0.0


@dataclass
class CapabilityDependency:
    capability: str
    min_version: str
    kind: str = "required"  # required | optional


@dataclass
class CapabilityRecord:
    capability: str
    display_name: str
    current_version: str
    versions: list[dict[str, Any]]
    dependencies: list[dict[str, Any]]
    status: str = "discovered"  # R08 lifecycle state
    last_health_check: float = 0.0
    health_status: str = "unknown"  # unknown | healthy | degraded | failed
    usage_count: int = 0
    last_used_at: float = 0.0
    owner: str = ""  # subsystem that owns this capability
    retirement_reason: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class CapabilityManager:
    """Full lifecycle manager for Ghost capabilities."""

    # R08 lifecycle - mirrors CapabilityAcquisition.STATES
    LIFECYCLE = [
        "discovered", "researched", "feasible", "building",
        "testing", "ready_to_activate", "active", "disabled", "retired",
    ]

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.capabilities_path = self.root / ".ghost" / "capabilities.json"
        self.capabilities_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_file()

    def _ensure_file(self) -> None:
        if not self.capabilities_path.exists():
            self.capabilities_path.write_text("{}", encoding="utf-8")

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            return json.loads(self.capabilities_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        atomic_json(self.capabilities_path, data)

    def register(self, capability: str, display_name: str,
                 owner: str = "", version: str = "1.0.0") -> CapabilityRecord:
        if not isinstance(capability, str) or not capability.strip():
            raise ValueError("capability must be a nonempty string")
        with exclusive_lock(self.capabilities_path.with_suffix(".lock")):
            data = self._load()
            if capability in data:
                raise ValueError(f"Capability '{capability}' already registered")
            record = CapabilityRecord(
                capability=capability, display_name=display_name,
                current_version=version, versions=[asdict(CapabilityVersion(
                    version=version, manifest_path="", changelog="Initial version",
                    tested_at=time.time()))],
                dependencies=[], owner=owner)
            data[capability] = asdict(record)
            self._save(data)
            return record

    def get(self, capability: str) -> Optional[CapabilityRecord]:
        data = self._load()
        if capability not in data:
            return None
        return CapabilityRecord(**data[capability])

    def list_all(self, status: Optional[str] = None) -> list[CapabilityRecord]:
        records = [CapabilityRecord(**item) for item in self._load().values()]
        if status:
            records = [r for r in records if r.status == status]
        return records

    def set_status(self, capability: str, status: str) -> CapabilityRecord:
        if status not in self.LIFECYCLE:
            raise ValueError(f"Unknown lifecycle state: {status}")
        with exclusive_lock(self.capabilities_path.with_suffix(".lock")):
            data = self._load()
            if capability not in data:
                raise ValueError("Capability not found")
            data[capability]["status"] = status
            data[capability]["updated_at"] = time.time()
            self._save(data)
            return CapabilityRecord(**data[capability])

    def add_version(self, capability: str, version: str, manifest_path: str,
                    changelog: str = "") -> CapabilityRecord:
        with exclusive_lock(self.capabilities_path.with_suffix(".lock")):
            data = self._load()
            if capability not in data:
                raise ValueError("Capability not found")
            rec = data[capability]
            existing = [v for v in rec.get("versions", []) if v["version"] == version]
            if existing:
                raise ValueError(f"Version {version} already exists")
            new_ver = asdict(CapabilityVersion(
                version=version, manifest_path=manifest_path,
                changelog=changelog, tested_at=time.time()))
            rec["versions"].append(new_ver)
            rec["current_version"] = version
            rec["updated_at"] = time.time()
            self._save(data)
            return CapabilityRecord(**rec)

    def retire(self, capability: str, reason: str) -> CapabilityRecord:
        with exclusive_lock(self.capabilities_path.with_suffix(".lock")):
            data = self._load()
            if capability not in data:
                raise ValueError("Capability not found")
            rec = data[capability]
            rec["status"] = "retired"
            rec["retirement_reason"] = reason[:400]
            rec["updated_at"] = time.time()
            # Mark current version retired
            for v in rec.get("versions", []):
                if v["version"] == rec["current_version"]:
                    v["retired_at"] = time.time()
            self._save(data)
            return CapabilityRecord(**rec)

    def set_health(self, capability: str, health: str) -> CapabilityRecord:
        if health not in {"unknown", "healthy", "degraded", "failed"}:
            raise ValueError("health must be one of: unknown, healthy, degraded, failed")
        with exclusive_lock(self.capabilities_path.with_suffix(".lock")):
            data = self._load()
            if capability not in data:
                raise ValueError("Capability not found")
            data[capability]["health_status"] = health
            data[capability]["last_health_check"] = time.time()
            data[capability]["updated_at"] = time.time()
            self._save(data)
            return CapabilityRecord(**data[capability])

    def record_usage(self, capability: str) -> None:
        with exclusive_lock(self.capabilities_path.with_suffix(".lock")):
            data = self._load()
            if capability not in data:
                return
            data[capability]["usage_count"] = data[capability].get("usage_count", 0) + 1
            data[capability]["last_used_at"] = time.time()
            self._save(data)

    def add_dependency(self, capability: str, dep_capability: str,
                      min_version: str, kind: str = "required") -> CapabilityRecord:
        if kind not in {"required", "optional"}:
            raise ValueError("kind must be required or optional")
        with exclusive_lock(self.capabilities_path.with_suffix(".lock")):
            data = self._load()
            if capability not in data:
                raise ValueError("Capability not found")
            rec = data[capability]
            deps = rec.get("dependencies", [])
            for d in deps:
                if d["capability"] == dep_capability:
                    d["min_version"] = min_version
                    d["kind"] = kind
                    self._save(data)
                    return CapabilityRecord(**rec)
            deps.append(asdict(CapabilityDependency(
                capability=dep_capability, min_version=min_version, kind=kind)))
            rec["dependencies"] = deps
            rec["updated_at"] = time.time()
            self._save(data)
            return CapabilityRecord(**rec)

    def check_dependencies(self, capability: str) -> dict[str, bool]:
        """Return capability -> satisfied for each dependency."""
        rec = self.get(capability)
        if not rec:
            return {}
        results: dict[str, bool] = {}
        for dep in rec.dependencies:
            dep_rec = self.get(dep["capability"])
            if not dep_rec:
                results[dep["capability"]] = False
            elif dep_rec.status not in {"active", "ready_to_activate"}:
                results[dep["capability"]] = False
            else:
                # Version check
                cur = dep_rec.current_version
                required = dep["min_version"]
                parts_cur = [int(x) for x in cur.split(".")]
                parts_req = [int(x) for x in required.split(".")]
                satisfied = parts_cur >= parts_req
                results[dep["capability"]] = satisfied
        return results

    def stats(self) -> dict[str, Any]:
        data = self._load()
        by_status: dict[str, int] = {}
        for item in data.values():
            s = item.get("status", "unknown")
            by_status[s] = by_status.get(s, 0) + 1
        return {
            "total": len(data),
            "by_status": by_status,
            "health_summary": {
                "healthy": sum(1 for v in data.values() if v.get("health_status") == "healthy"),
                "degraded": sum(1 for v in data.values() if v.get("health_status") == "degraded"),
                "failed": sum(1 for v in data.values() if v.get("health_status") == "failed"),
            },
        }