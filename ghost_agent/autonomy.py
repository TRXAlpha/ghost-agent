"""Persistent outcome jobs: the bridge between research and implementation."""
from __future__ import annotations

import hashlib
from copy import deepcopy
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from .utils.storage import atomic_json, atomic_write


@dataclass
class OutcomeJob:
    job_id: str
    goal: str
    status: str = "created"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    research_model: str = ""
    coding_model: str = ""
    research_report: str = ""
    sandbox_path: str = ""
    result: str = ""
    errors: list[str] = field(default_factory=list)
    acceptance_contract: Dict[str, Any] = field(default_factory=dict)
    goal_id: str = ""
    iterations: int = 12
    skip_research: bool = False

    def __post_init__(self):
        self.validate()

    def validate(self) -> None:
        if (not isinstance(self.job_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", self.job_id)
                or not isinstance(self.goal, str) or not self.goal.strip()
                or not isinstance(self.status, str) or not self.status.strip()
                or any(type(value) not in (int, float) or not 0 <= value < float("inf")
                       for value in (self.created_at, self.updated_at))
                or type(self.iterations) is not int or not 1 <= self.iterations <= 1000
                or type(self.skip_research) is not bool
                or not isinstance(self.errors, list) or any(not isinstance(error, str) for error in self.errors)
                or not isinstance(self.acceptance_contract, dict)
                or any(not isinstance(value, str) for value in (self.research_model, self.coding_model,
                    self.research_report, self.sandbox_path, self.result, self.goal_id))):
            raise ValueError("Invalid outcome record fields")
        json.dumps(self.acceptance_contract, allow_nan=False)


class OutcomeStore:
    """Store resumable outcome jobs beneath the project, never in user source."""

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root).absolute() / ".ghost" / "outcomes"
        if self.root.resolve() != self.root:
            raise ValueError("Outcome storage cannot follow links")
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, goal: str) -> OutcomeJob:
        clean = re.sub(r"\s+", " ", goal).strip()
        digest = hashlib.sha1(clean.encode("utf-8")).hexdigest()[:8]
        job_id = f"outcome_{time.strftime('%Y%m%d_%H%M%S')}_{digest}_{uuid4().hex[:8]}"
        job = OutcomeJob(job_id=job_id, goal=clean, acceptance_contract={
            "deliverable": clean,
            "sandbox_only": True,
            "required_checks": [],
            "promotion_requires_approval": True,
            "success_condition": "verification reports tests_passed",
        })
        self.save(job)
        return job

    def path_for(self, job: OutcomeJob) -> Path:
        return self._directory(job.job_id)

    def _directory(self, job_id: str) -> Path:
        if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", job_id):
            raise ValueError("Invalid outcome ID")
        path = self.root / job_id
        if path.resolve() != path:
            raise ValueError("Outcome path cannot follow links")
        return path

    def _file(self, job_id: str, name: str) -> Path:
        path = self._directory(job_id) / name
        if path.resolve() != path:
            raise ValueError("Outcome artifact cannot follow links")
        return path

    def save(self, job: OutcomeJob) -> Path:
        job.validate()
        job.updated_at = time.time()
        job_dir = self.path_for(job)
        job_dir.mkdir(parents=True, exist_ok=True)
        path = self._file(job.job_id, "outcome.json")
        if path.exists():
            self.load(job.job_id)  # Preserve malformed existing artifacts for review.
        atomic_json(path, asdict(job))
        return path

    def load(self, job_id: str) -> Optional[OutcomeJob]:
        path = self._file(job_id, "outcome.json")
        if not path.is_file():
            return None
        try:
            job = OutcomeJob(**json.loads(path.read_text(encoding="utf-8")))
            if job.job_id != job_id:
                raise ValueError("Outcome identity does not match its directory")
            return job
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid outcome record; preserve it for review") from exc

    def list(self) -> list[OutcomeJob]:
        jobs: list[OutcomeJob] = []
        for path in self.root.glob("*/outcome.json"):
            try:
                job = self.load(path.parent.name)
                if job is not None:
                    jobs.append(job)
            except (OSError, ValueError, TypeError):
                continue
        return sorted(jobs, key=lambda item: item.created_at)

    def write_research(self, job: OutcomeJob, report: str) -> Path:
        path = self._file(job.job_id, "research.md")
        atomic_write(path, report.encode("utf-8"))
        job.research_report = str(path)
        self.save(job)
        return path

    def make_task_payload(self, job: OutcomeJob, research: str, iterations: int) -> Dict[str, Any]:
        job.validate()
        constraints = {"iteration_limit": iterations}
        for key in ("required_files", "required_checks", "smoke_cmd",
                    "command_budget_seconds", "command_budget_calls",
                    "command_timeout_seconds", "workspace_mode", "yield_on_owner_return"):
            if key in job.acceptance_contract:
                constraints[key] = deepcopy(job.acceptance_contract[key])
        return {
            "id": job.job_id,
            "title": "Autonomous outcome job",
            "goal": (
                f"{job.goal}\n\n"
                "Work only in the sandbox. Implement the outcome, add focused tests "
                "for new or changed behavior using the project's ecosystem, run them, "
                "then repair failures. Supported default runners are pytest for Python "
                "and node --test for native JavaScript tests."
            ),
            "constraints": constraints,
            "context": {
                # Greenfield creation goals are harmed by RAG-injected snippets
                # of Ghost's own source (verified 2026-09-27: small models spent
                # turns reading ghost_agent/schema.py for a hello.py task).
                # Only existing-code tasks need repository context; orchestrator
                # still auto-enables it for fix/repair/refactor wording.
                "use_existing_context": False,
                "research_dossier": research[:12_000],
                "notes": "Research is evidence context, not permission to invent facts.",
                "acceptance_contract": deepcopy(job.acceptance_contract),
            },
        }


class CapabilityRegistry:
    """Remember missing capabilities and queue sandboxed workaround investigations."""

    def __init__(self, project_root: Path) -> None:
        self.path = Path(project_root) / ".ghost" / "capabilities.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"gaps": [], "queue": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"gaps": [], "queue": []}
        except (json.JSONDecodeError, OSError):
            return {"gaps": [], "queue": []}

    def _save(self, data: Dict[str, Any]) -> None:
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def record_gap(self, goal: str, result: str, details: str = "") -> Dict[str, str]:
        """Record one unmet outcome and queue a reviewable workaround investigation."""
        data = self._load()
        fingerprint = hashlib.sha1(f"{goal}|{result}".encode("utf-8")).hexdigest()[:10]
        existing = next((gap for gap in data["gaps"] if gap.get("id") == fingerprint), None)
        if existing:
            existing["count"] = int(existing.get("count", 1)) + 1
            existing["updated_at"] = time.time()
            self._save(data)
            return existing
        followup = (
            "Investigate a sandboxed workaround for this unmet outcome: "
            f"{goal}. Failure signal: {result}. {details[:500]} "
            "First inspect existing capabilities and logs. If a new integration is needed, "
            "prototype it as a local extension with tests; do not install or grant permissions "
            "outside the sandbox without user review."
        )
        gap = {
            "id": fingerprint,
            "goal": goal,
            "result": result,
            "details": details[:1000],
            "count": 1,
            "created_at": time.time(),
            "updated_at": time.time(),
            "followup_goal": followup,
            "status": "queued",
        }
        data["gaps"].append(gap)
        data["queue"].append({"gap_id": fingerprint, "goal": followup, "status": "queued"})
        self._save(data)
        return gap

    def get_queued_gaps(self, min_count: int = 2) -> list[dict]:
        """Return gaps with 2+ failures that are still queued — candidates for autonomous build."""
        data = self._load()
        return [g for g in data.get("gaps", []) if g.get("count", 1) >= min_count and g.get("status") == "queued"]

    def mark_in_progress(self, gap_id: str) -> None:
        data = self._load()
        for g in data.get("gaps", []):
            if g["id"] == gap_id:
                g["status"] = "in_progress"
        self._save(data)

    def mark_resolved(self, gap_id: str, resolution: str) -> None:
        data = self._load()
        for g in data.get("gaps", []):
            if g["id"] == gap_id:
                g["status"] = "resolved"
                g["resolution"] = resolution[:500]
        self._save(data)

    def reset_to_queued(self, gap_id: str) -> None:
        data = self._load()
        for g in data.get("gaps", []):
            if g["id"] == gap_id and g.get("status") == "in_progress":
                g["status"] = "queued"
        self._save(data)
