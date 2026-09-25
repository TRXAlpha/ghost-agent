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
    proposed_benefit: str = ""          # S08: real benefit linked to the change
    linked_failure: str = ""            # S08: the real failure that motivated it
    regression_results: list[dict[str, Any]] = field(default_factory=list)
    benchmark_results: list[dict[str, Any]] = field(default_factory=list)
    status: str = "diagnosed"
    created_at: float = field(default_factory=time.time)
    verified_snapshot: dict[str, dict[str, str | None]] = field(default_factory=dict)

    # A candidate must never be able to modify these (authority / evidence).
    PROTECTED_DIRS = {".ghost"}


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
                        or not isinstance(proposal.proposed_benefit, str)
                        or not isinstance(proposal.linked_failure, str)
                        or proposal.status not in {"diagnosed", "patched", "verified",
                                                   "rejected", "applied", "rolled_back"}):
                    raise ValueError("Invalid or duplicate self-repair record")
                identifiers.add(proposal.proposal_id)
            return items
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError("Self-repair records are unreadable or malformed; preserve them for review") from exc

    def _save(self, items: list[dict[str, Any]]) -> None:
        atomic_json(self.path, items)

    def propose(self, diagnosis: str, sandbox: Path, proposed_benefit: str = "",
                linked_failure: str = "") -> SelfRepairProposal:
        with exclusive_lock(self.path.with_suffix(".lock")):
            return self._propose_locked(diagnosis, sandbox, proposed_benefit, linked_failure)

    def _propose_locked(self, diagnosis: str, sandbox: Path, proposed_benefit: str = "",
                        linked_failure: str = "") -> SelfRepairProposal:
        candidate = Path(sandbox).resolve()
        if not candidate.is_dir():
            raise ValueError("Self-repair candidate must be an existing sandbox directory")
        self._assert_candidate_scope(candidate)
        diagnosis = diagnosis.strip()
        if not diagnosis:
            raise ValueError("Diagnosis must be nonempty")
        # S08 loop prevention: do not endlessly re-propose an identical rejected
        # diagnosis. A rejected/unchanged candidate is a terminal outcome.
        if any(item.get("diagnosis") == diagnosis
               and item.get("status") in {"rejected", "rolled_back"}
               for item in self._load()):
            raise ValueError(
                "Identical diagnosis was already rejected; refusing an endless repair loop")
        digest = hashlib.sha1(f"{diagnosis}|{time.time_ns()}".encode()).hexdigest()[:10]
        proposal = SelfRepairProposal(
            f"repair_{digest}", diagnosis, str(candidate),
            proposed_benefit=proposed_benefit.strip(),
            linked_failure=linked_failure.strip(), status="patched")
        data = self._load()
        data.append(asdict(proposal))
        self._save(data)
        return proposal

    @classmethod
    def _assert_candidate_scope(cls, candidate: Path) -> None:
        """A candidate must not sit inside Ghost's own authority/evidence dirs.

        Grants, approvals, evidence and jobs live under the project's `.ghost`
        tree; a self-repair candidate that could write there would be able to
        alter the evaluator, permission decisions or its own evidence (S08).
        """
        if candidate == candidate.anchor or not candidate.is_absolute():
            raise ValueError("Candidate must be an absolute path outside .ghost")
        parts = candidate.parts
        if ".ghost" in parts:
            raise ValueError(
                "Self-repair candidate cannot be inside Ghost's authority/.ghost area")
        if not any(part in {"ghost_agent"} for part in parts):
            return  # fine: a normal workspace/sandbox path
        raise ValueError(
            "Self-repair candidate cannot target the Ghost source tree itself")

    def mark_applied(self, proposal_id: str, artifacts: list[str]) -> SelfRepairProposal:
        """Record that a verified candidate was applied, preserving evidence."""
        if not isinstance(artifacts, list) or any(not isinstance(a, str) for a in artifacts):
            raise ValueError("Artifacts must be a list of strings")
        with exclusive_lock(self.path.with_suffix(".lock")):
            return self._mark_state_locked(proposal_id, "applied", {
                "benchmark_results": list(artifacts)})

    def mark_rollback(self, proposal_id: str, reason: str) -> SelfRepairProposal:
        """Record a rollback of an applied candidate (returns to known-good)."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Rollback needs a reason")
        with exclusive_lock(self.path.with_suffix(".lock")):
            return self._mark_state_locked(proposal_id, "rolled_back", {
                "proposed_benefit": reason.strip()})

    def _mark_state_locked(self, proposal_id: str, target: str,
                           updates: dict[str, Any]) -> SelfRepairProposal:
        data = self._load()
        for item in data:
            if item.get("proposal_id") == proposal_id:
                current = item["status"]
                allowed = {"verified": {"applied"}, "applied": {"rolled_back"}}
                if target not in allowed.get(current, set()):
                    raise ValueError(
                        f"Invalid self-repair state transition: {current} -> {target}")
                for key, value in updates.items():
                    item[key] = value
                item["status"] = target
                self._save(data)
                return SelfRepairProposal(**item)
        raise ValueError("Self-repair proposal not found")

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
