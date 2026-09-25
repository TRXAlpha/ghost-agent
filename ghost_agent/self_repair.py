"""Bounded Ghost self-repair proposals; no unrestricted self-modification."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .promotion import PromotionManager, PromotionService
from .utils.storage import atomic_json, exclusive_lock


@dataclass
class SelfRepairProposal:
    proposal_id: str
    diagnosis: str
    sandbox: str
    regression_results: list[dict[str, Any]] = field(default_factory=list)
    benchmark_results: list[dict[str, Any]] = field(default_factory=list)
    status: str = "diagnosed"
    created_at: float = field(default_factory=time.time)
    verified_snapshot: dict[str, dict[str, str | None]] = field(default_factory=dict)


class SelfRepairManager:
    """Persist diagnose -> sandbox -> verify -> promotion-request progression."""

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root)
        self.path = self.root / ".ghost" / "self-repair.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[dict[str, Any]]:
        try:
            items = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []
            if not isinstance(items, list):
                raise ValueError("Expected a list of self-repair proposals")
            identifiers = set()
            for item in items:
                proposal = SelfRepairProposal(**item)
                if (not isinstance(proposal.proposal_id, str) or not proposal.proposal_id
                        or proposal.proposal_id in identifiers
                        or not isinstance(proposal.sandbox, str)
                        or not isinstance(proposal.diagnosis, str)
                        or not isinstance(proposal.regression_results, list)
                        or not isinstance(proposal.benchmark_results, list)
                        or not isinstance(proposal.verified_snapshot, dict)
                        or proposal.status not in {"diagnosed", "patched", "verified", "rejected"}):
                    raise ValueError("Invalid or duplicate self-repair record")
                identifiers.add(proposal.proposal_id)
            return items
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError("Self-repair records are unreadable or malformed; preserve them for review") from exc

    def _save(self, items: list[dict[str, Any]]) -> None:
        atomic_json(self.path, items)

    def propose(self, diagnosis: str, sandbox: Path) -> SelfRepairProposal:
        with exclusive_lock(self.path.with_suffix(".lock")):
            return self._propose_locked(diagnosis, sandbox)

    def _propose_locked(self, diagnosis: str, sandbox: Path) -> SelfRepairProposal:
        candidate = Path(sandbox).resolve()
        if not candidate.is_dir():
            raise ValueError("Self-repair candidate must be an existing sandbox directory")
        digest = hashlib.sha1(f"{diagnosis}|{time.time_ns()}".encode()).hexdigest()[:10]
        proposal = SelfRepairProposal(f"repair_{digest}", diagnosis.strip(), str(candidate), status="patched")
        data = self._load()
        data.append(asdict(proposal))
        self._save(data)
        return proposal

    def get(self, proposal_id: str) -> SelfRepairProposal | None:
        return next((SelfRepairProposal(**item) for item in self._load() if item.get("proposal_id") == proposal_id), None)

    def list(self) -> list[SelfRepairProposal]:
        return [SelfRepairProposal(**item) for item in self._load()]

    def record_verification(self, proposal_id: str, regression_results: list[dict[str, Any]],
                            benchmark_results: list[dict[str, Any]]) -> SelfRepairProposal:
        with exclusive_lock(self.path.with_suffix(".lock")):
            return self._record_verification_locked(proposal_id, regression_results, benchmark_results)

    def _record_verification_locked(self, proposal_id: str, regression_results: list[dict[str, Any]],
                                    benchmark_results: list[dict[str, Any]]) -> SelfRepairProposal:
        if not isinstance(regression_results, list) or not isinstance(benchmark_results, list):
            raise ValueError("Regression and benchmark results must be lists")
        data = self._load()
        for item in data:
            if item.get("proposal_id") == proposal_id:
                item["regression_results"] = regression_results
                item["benchmark_results"] = benchmark_results
                # Both gates need explicit successful results. In particular,
                # all([]) and False == 0 must not manufacture verification.
                passed = bool(regression_results) and bool(benchmark_results) and all(
                    isinstance(result, dict)
                    and type(result.get("returncode")) is int
                    and result["returncode"] == 0
                    for result in regression_results + benchmark_results
                )
                snapshot = PromotionManager(self.root).snapshot(Path(item["sandbox"])) if passed else {}
                item["verified_snapshot"] = snapshot
                passed = passed and bool(snapshot)
                item["status"] = "verified" if passed else "rejected"
                self._save(data)
                return SelfRepairProposal(**item)
        raise ValueError("Self-repair proposal not found")

    def request_promotion(self, proposal_id: str, goal_id: str) -> tuple[str, str, list[str]]:
        with exclusive_lock(self.path.with_suffix(".lock")):
            return self._request_promotion_locked(proposal_id, goal_id)

    def _request_promotion_locked(self, proposal_id: str, goal_id: str) -> tuple[str, str, list[str]]:
        proposal = self.get(proposal_id)
        if not proposal or proposal.status != "verified" or not proposal.verified_snapshot:
            raise PermissionError("Only verified self-repair proposals can request promotion")
        return PromotionService(self.root).request(
            goal_id, Path(proposal.sandbox), expected_snapshot=proposal.verified_snapshot)
