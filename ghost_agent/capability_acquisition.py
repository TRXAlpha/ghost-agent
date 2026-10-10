"""OJ10: general capability acquisition lifecycle, decisions, and prerequisites.

Ghost discovers a missing capability and drives it through the reviewed
R08 lifecycle (discovered -> researched -> feasible -> building -> testing ->
ready_to_activate -> active), recording an explicit acquire decision (reuse /
configure / adapt / build / defer) so it reuses an existing integration before
writing new code. Prerequisites and missing external inputs are tracked, and a
record suspended on an account/service prerequisite is waiting_external rather
than silently dropped.

This module only RECORDS the acquisition pipeline. It never executes a tool,
runs a build, enables an adapter, or starts a second execution owner. Code
production and verification stay with Ghost's existing worker/verification.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from .utils.storage import atomic_json, exclusive_lock


class AcquisitionError(ValueError):
    """Invalid acquisition decision, transition, or record."""


@dataclass
class AcquisitionRecord:
    record_id: str
    capability: str
    symptom: str
    decision: str = ""              # "" until researched
    status: str = "discovered"      # R08 lifecycle state
    rationale: str = ""
    reuse_target: str = ""          # existing integration reused when decision==reuse
    prerequisites: Dict[str, bool] = field(default_factory=dict)  # name -> resolved
    missing_external: str = ""      # exact account/service/hardware prerequisite
    candidate: str = ""             # sandbox/manifest path bound at activation
    health: str = ""                # health-check result for active/degraded
    retired_reason: str = ""
    operationally_verified: bool = False  # R08: live evidence, not just activation
    verification_evidence: str = "" # recorded by mark_operationally_verified
    feasibility: dict = field(default_factory=dict)  # TJ1: feasibility record
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class CapabilityAcquisition:
    """Durable, reviewed acquisition pipeline. Recording layer only."""

    # R08 lifecycle transitions.
    STATES: dict[str, set[str]] = {
        "discovered": {"researched", "retired", "deferred"},
        "researched": {"feasible", "deferred", "waiting_external", "retired"},
        "feasible": {"building", "deferred", "waiting_external", "retired"},
        "building": {"testing", "failed", "waiting_external"},
        "testing": {"ready_to_activate", "failed", "waiting_external"},
        "ready_to_activate": {"active", "disabled", "retired"},
        "active": {"degraded", "disabled"},
        "degraded": {"active", "disabled"},
        "disabled": {"ready_to_activate", "retired"},
        "waiting_external": {"feasible", "building", "testing", "retired", "deferred"},
        "deferred": {"discovered", "retired"},
        "failed": {"building", "retired"},
        "retired": set(),
    }
    DECISIONS = {"reuse", "configure", "adapt", "build", "defer"}

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.path = self.root / ".ghost" / "acquisitions.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # -- persistence ------------------------------------------------------
    def _load(self) -> Dict[str, dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
            if not isinstance(raw, dict):
                raise ValueError("Acquisition register must be an object")
            for key, item in raw.items():
                record = AcquisitionRecord(**item)
                if record.record_id != key or record.status not in self.STATES:
                    raise ValueError("Invalid acquisition record")
                if record.decision and record.decision not in self.DECISIONS:
                    raise ValueError("Invalid acquisition decision")
                if not isinstance(record.prerequisites, dict):
                    raise ValueError("Prerequisites must be a mapping")
            return raw
        except (OSError, ValueError, TypeError) as exc:
            raise AcquisitionError(
                "Acquisition register is unreadable or corrupt; preserve it for review") from exc

    def _save(self, data: Dict[str, dict[str, Any]]) -> None:
        atomic_json(self.path, data)

    # -- acquisition pipeline ---------------------------------------------
    def discover(self, capability: str, symptom: str) -> AcquisitionRecord:
        if not isinstance(capability, str) or not capability.strip() or len(capability) > 120:
            raise AcquisitionError("Capability must be a nonempty short name")
        if not isinstance(symptom, str) or len(symptom) > 2000:
            raise AcquisitionError("Symptom must be text up to 2000 chars")
        digest = hashlib.sha1(f"{capability}|{time.time_ns()}".encode()).hexdigest()[:12]
        record = AcquisitionRecord(f"acq_{digest}", capability.strip(), symptom.strip())
        with exclusive_lock(self.path.with_suffix(".lock")):
            data = self._load()
            data[record.record_id] = asdict(record)
            self._save(data)
        return record

    def research(self, record_id: str, decision: str, rationale: str,
                 reuse_target: str = "") -> AcquisitionRecord:
        if decision not in self.DECISIONS:
            raise AcquisitionError(f"decision must be one of {sorted(self.DECISIONS)}")
        if decision == "reuse" and not reuse_target.strip():
            raise AcquisitionError("A reuse decision requires a reuse_target (existing integration)")
        return self._transition(record_id, "deferred" if decision == "defer" else "researched",
                                {"decision": decision,
                                 "rationale": rationale[:2000],
                                 "reuse_target": reuse_target.strip()})

    def set_prerequisite(self, record_id: str, name: str, resolved: bool) -> AcquisitionRecord:
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise AcquisitionError("Prerequisite must be a nonempty name")
        existing = self.get(record_id)
        if not existing:
            raise AcquisitionError("Record not found")
        prerequisites = dict(existing.prerequisites)
        prerequisites[name.strip()] = bool(resolved)
        return self._transition(record_id, None, {"prerequisites": prerequisites})

    def mark_waiting(self, record_id: str, missing_external: str) -> AcquisitionRecord:
        if not isinstance(missing_external, str) or not missing_external.strip():
            raise AcquisitionError("A waiting record needs the exact missing external input")
        return self._transition(record_id, "waiting_external", {"missing_external": missing_external.strip()})

    def resume(self, record_id: str, target: str = "feasible") -> AcquisitionRecord:
        return self._transition(record_id, target, {"missing_external": ""})

    def advance(self, record_id: str, target: str) -> AcquisitionRecord:
        return self._transition(record_id, target, {})

    def record_failure(self, record_id: str, reason: str) -> AcquisitionRecord:
        return self._transition(record_id, "failed", {"rationale": reason[:2000]})

    def retire(self, record_id: str, reason: str) -> AcquisitionRecord:
        return self._transition(record_id, "retired", {"retired_reason": reason[:2000]})

    def mark_health(self, record_id: str, health: str) -> AcquisitionRecord:
        if not isinstance(health, str) or not health.strip():
            raise AcquisitionError("Health result must be nonempty text")
        return self._transition(record_id, None, {"health": health.strip()})

    def mark_operationally_verified(self, record_id: str, evidence: str) -> AcquisitionRecord:
        """Record live operational evidence; only an active capability verifies."""
        if not isinstance(evidence, str) or not evidence.strip():
            raise AcquisitionError("Operational verification requires nonempty evidence")
        record = self.get(record_id)
        if record is None:
            raise AcquisitionError("Acquisition record not found")
        if record.status != "active":
            raise AcquisitionError(
                f"Only active capabilities can be operationally verified (status: {record.status})")
        return self._transition(record_id, None, {
            "operationally_verified": True,
            "verification_evidence": evidence.strip(),
        })

    def bind_candidate(self, record_id: str, candidate: str) -> AcquisitionRecord:
        candidate = candidate.strip()
        if not candidate:
            raise AcquisitionError("Candidate path must be provided")
        return self._transition(record_id, None, {"candidate": candidate})

    # -- TJ1 feasibility record --------------------------------------------
    FEASIBILITY_KEYS = {
        "need", "approaches", "sources", "constraints", "prerequisites",
        "scopes", "expected_data", "cost", "maintenance", "unknowns", "next_step",
    }
    FEASIBILITY_SOURCE_KINDS = {"official_docs", "repo", "article", "local"}
    _FEASIBILITY_STR_KEYS = {
        "need", "expected_data", "cost", "maintenance", "next_step",
    }
    _FEASIBILITY_LIST_KEYS = {
        "approaches", "constraints", "prerequisites", "scopes", "unknowns",
    }
    _FEASIBILITY_MAX_STR = 500
    _FEASIBILITY_MAX_LIST = 10

    @classmethod
    def validate_feasibility(cls, data: Any) -> dict[str, Any]:
        """Validate a feasibility payload; returns a normalized copy."""
        if not isinstance(data, dict):
            raise AcquisitionError("Feasibility data must be an object")
        unknown = set(data) - cls.FEASIBILITY_KEYS
        if unknown:
            raise AcquisitionError(f"Unknown feasibility keys: {sorted(unknown)}")
        clean: dict[str, Any] = {}
        for key in cls._FEASIBILITY_STR_KEYS:
            if key in data:
                value = data[key]
                if not isinstance(value, str) or len(value) > cls._FEASIBILITY_MAX_STR:
                    raise AcquisitionError(
                        f"Feasibility {key} must be a string up to {cls._FEASIBILITY_MAX_STR} chars")
                clean[key] = value
        for key in cls._FEASIBILITY_LIST_KEYS:
            if key in data:
                value = data[key]
                if (not isinstance(value, list) or len(value) > cls._FEASIBILITY_MAX_LIST
                        or not all(isinstance(v, str) and len(v) <= cls._FEASIBILITY_MAX_STR
                                   for v in value)):
                    raise AcquisitionError(
                        f"Feasibility {key} must be a list of up to {cls._FEASIBILITY_MAX_LIST} "
                        f"strings (each up to {cls._FEASIBILITY_MAX_STR} chars)")
                clean[key] = value
        if "sources" in data:
            value = data["sources"]
            if not isinstance(value, list) or len(value) > cls._FEASIBILITY_MAX_LIST:
                raise AcquisitionError(
                    f"Feasibility sources must be a list of up to {cls._FEASIBILITY_MAX_LIST} sources")
            clean_sources = []
            for source in value:
                if not isinstance(source, dict):
                    raise AcquisitionError("Feasibility sources must be objects")
                location = [k for k in ("url", "path") if k in source]
                if len(location) != 1 or not isinstance(source[location[0]], str):
                    raise AcquisitionError("Each feasibility source needs exactly one of url/path (string)")
                if "checked_at" not in source or not isinstance(source["checked_at"], (int, float)) \
                        or isinstance(source["checked_at"], bool):
                    raise AcquisitionError("Each feasibility source needs a numeric checked_at")
                if source.get("kind") not in cls.FEASIBILITY_SOURCE_KINDS:
                    raise AcquisitionError(
                        f"Feasibility source kind must be one of {sorted(cls.FEASIBILITY_SOURCE_KINDS)}")
                extra = set(source) - {"url", "path", "checked_at", "kind"}
                if extra:
                    raise AcquisitionError(f"Unknown feasibility source keys: {sorted(extra)}")
                clean_sources.append(dict(source))
            clean["sources"] = clean_sources
        return clean

    def record_feasibility(self, record_id: str, data: dict[str, Any]) -> AcquisitionRecord:
        """Merge a validated feasibility payload into a record (TJ1)."""
        clean = self.validate_feasibility(data)
        record = self.get(record_id)
        if record is None:
            raise AcquisitionError("Acquisition record not found")
        merged = dict(record.feasibility)
        merged.update(clean)
        target = "researched" if record.status == "discovered" else None
        return self._transition(record_id, target, {"feasibility": merged})

    # -- transitions ------------------------------------------------------
    def _transition(self, record_id: str, target: Optional[str],
                    updates: Dict[str, Any]) -> AcquisitionRecord:
        with exclusive_lock(self.path.with_suffix(".lock")):
            data = self._load()
            if record_id not in data:
                raise AcquisitionError("Acquisition record not found")
            item = data[record_id]
            for key, value in updates.items():
                item[key] = value
            if target is not None:
                current = item["status"]
                if target not in self.STATES.get(current, set()):
                    raise AcquisitionError(f"Invalid acquisition transition: {current} -> {target}")
                item["status"] = target
            item["updated_at"] = time.time()
            self._save(data)
            return AcquisitionRecord(**item)

    def get(self, record_id: str) -> Optional[AcquisitionRecord]:
        data = self._load()
        return AcquisitionRecord(**data[record_id]) if record_id in data else None

    def list(self, status: Optional[str] = None, limit: int = 50) -> list[AcquisitionRecord]:
        records = [AcquisitionRecord(**item) for item in self._load().values()]
        records.sort(key=lambda r: r.updated_at, reverse=True)
        if status:
            records = [r for r in records if r.status == status]
        return records[:limit]

    def pending_external(self) -> list[AcquisitionRecord]:
        return self.list(status="waiting_external")

    def active_or_pending(self) -> list[AcquisitionRecord]:
        return [r for r in self.list() if r.status != "retired"]

    # -- reuse-before-build -----------------------------------------------
    @staticmethod
    def reuse_inventory(capability: str) -> Optional[str]:
        """Return a known existing integration for `capability` if one exists.

        This encodes the OJ10 acceptance "reuse an existing integration before
        coding": a recognized, already-present integration is the default reuse
        target. A fresh capability returns None, meaning a build/adapt decision
        is warranted after research.
        """
        known = {
            "lesson_search": "openjarvis_memory",
            "telegram_transport": "openjarvis_telegram",
            "document_retrieval": "openjarvis_memory",
            "model_inference": "openjarvis_engine",
            "web_research": "ghost_web_tools",
        }
        return known.get(capability.strip())
