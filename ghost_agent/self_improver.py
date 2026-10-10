"""RL14/S08: Self-improvement engine — turns recurring failures into queued sandbox jobs.

Front end to Ghost's reviewed candidate pipeline: trace-learning failure
signatures become real sandbox jobs (via ``queue_sandbox_outcome``); passing
sandboxes are handed to ``SelfRepairManager`` for verification and owner-approved
promotion. No evidence, approval or promotion logic lives here.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .utils.storage import atomic_json, exclusive_lock

# Job statuses at which a queued candidate can be resolved (from
# feedback_outcome_for_job plus cancelled as a defensive extra for
# emergency-stop paths in _execute_queued_outcome).
_TERMINAL_JOB_STATUSES = {"completed", "failed", "needs_review", "cancelled"}

# Backoff: a signature whose candidates keep failing stops being re-proposed.
MAX_FAILURES_PER_SIGNATURE = 3
FAILURE_WINDOW_SECONDS = 7 * 86400


@dataclass
class ImprovementCandidate:
    candidate_id: str
    hypothesis: str
    affected_components: list[str]
    baseline_metrics: dict[str, Any]
    evaluation_plan: str
    budget_hours: float
    rollback_plan: str
    status: str = "proposed"   # queued | handed_to_self_repair | failed | (legacy: proposed | evaluating | shadow | active | rolled_back)
    baseline_commit: str = ""
    shadow_metrics: dict[str, Any] = field(default_factory=dict)
    final_metrics: dict[str, Any] = field(default_factory=dict)
    deployed_at: float = 0.0
    rolled_back_at: float = 0.0
    failure_reason: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    job_id: str = ""
    signature_key: str = ""
    repair_proposal_id: str = ""


@dataclass
class ImprovementDecision:
    decision_id: str
    candidate_id: str
    decision: str   # approved | rejected | rollback
    reason: str
    by: str = "model_review"   # model_review | automated | owner
    created_at: float = field(default_factory=time.time)


class SelfImproverStore:
    """Persistent self-improvement records under .ghost/self_improvement/."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.dir_path = self.root / ".ghost" / "self_improvement"
        self.candidates_path = self.dir_path / "candidates.json"
        self.decisions_path = self.dir_path / "decisions.json"
        self.dir_path.mkdir(parents=True, exist_ok=True)
        self._ensure_files()

    def _ensure_files(self) -> None:
        for path in (self.candidates_path, self.decisions_path):
            if not path.exists():
                path.write_text("[]", encoding="utf-8")

    @staticmethod
    def _load_list(path: Path) -> list[dict[str, Any]]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    @staticmethod
    def _save_list(path: Path, items: list[dict[str, Any]]) -> None:
        atomic_json(path, items)

    # -- candidates --------------------------------------------------------

    def create_candidate(self, hypothesis: str, components: list[str],
                        baseline: dict[str, Any], plan: str,
                        budget_hours: float, rollback: str) -> ImprovementCandidate:
        cand = ImprovementCandidate(
            candidate_id=f"imp_{hashlib.sha1(f'{hypothesis}|{time.time_ns()}'.encode()).hexdigest()[:12]}",
            hypothesis=hypothesis, affected_components=components,
            baseline_metrics=baseline, evaluation_plan=plan,
            budget_hours=budget_hours, rollback_plan=rollback,
        )
        with exclusive_lock(self.candidates_path.with_suffix(".lock")):
            items = self._load_list(self.candidates_path)
            items.append(asdict(cand))
            self._save_list(self.candidates_path, items)
        return cand

    def get_candidate(self, candidate_id: str) -> Optional[ImprovementCandidate]:
        for item in self._load_list(self.candidates_path):
            if item.get("candidate_id") == candidate_id:
                return ImprovementCandidate(**item)
        return None

    def list_candidates(self, status: Optional[str] = None) -> list[ImprovementCandidate]:
        candidates = [ImprovementCandidate(**item)
                      for item in self._load_list(self.candidates_path)]
        if status:
            candidates = [c for c in candidates if c.status == status]
        return sorted(candidates, key=lambda c: c.created_at, reverse=True)

    def update_candidate(self, candidate_id: str, updates: dict[str, Any]) -> ImprovementCandidate:
        with exclusive_lock(self.candidates_path.with_suffix(".lock")):
            items = self._load_list(self.candidates_path)
            for item in items:
                if item.get("candidate_id") == candidate_id:
                    item.update(updates)
                    item["updated_at"] = time.time()
                    self._save_list(self.candidates_path, items)
                    return ImprovementCandidate(**item)
        raise ValueError("Candidate not found")

    def set_shadow_metrics(self, candidate_id: str, metrics: dict[str, Any]) -> ImprovementCandidate:
        return self.update_candidate(candidate_id, {
            "shadow_metrics": metrics, "status": "shadow"})

    def activate(self, candidate_id: str) -> ImprovementCandidate:
        return self.update_candidate(candidate_id, {
            "status": "active", "deployed_at": time.time()})

    def rollback(self, candidate_id: str, reason: str) -> ImprovementCandidate:
        return self.update_candidate(candidate_id, {
            "status": "rolled_back",
            "rolled_back_at": time.time(),
            "failure_reason": reason[:800]})

    def fail(self, candidate_id: str, reason: str) -> ImprovementCandidate:
        return self.update_candidate(candidate_id, {
            "status": "failed", "failure_reason": reason[:800]})

    # -- decisions --------------------------------------------------------

    def record_decision(self, candidate_id: str, decision: str,
                        reason: str, by: str = "model_review") -> ImprovementDecision:
        dec = ImprovementDecision(
            decision_id=f"dec_{hashlib.sha1(f'{candidate_id}|{decision}|{time.time_ns()}'.encode()).hexdigest()[:12]}",
            candidate_id=candidate_id, decision=decision, reason=reason[:400], by=by)
        with exclusive_lock(self.decisions_path.with_suffix(".lock")):
            items = self._load_list(self.decisions_path)
            items.append(asdict(dec))
            self._save_list(self.decisions_path, items)
        return dec

    def list_decisions(self, candidate_id: Optional[str] = None) -> list[ImprovementDecision]:
        decisions = [ImprovementDecision(**item)
                     for item in self._load_list(self.decisions_path)]
        if candidate_id:
            decisions = [d for d in decisions if d.candidate_id == candidate_id]
        return sorted(decisions, key=lambda d: d.created_at, reverse=True)

    # -- improvement history -----------------------------------------------

    def history(self, limit: int = 20) -> dict[str, Any]:
        candidates = self.list_candidates()[:limit]
        return {
            "candidates": [asdict(c) for c in candidates],
            "total": len(self._load_list(self.candidates_path)),
            "active": len(self.list_candidates("active")),
            "rolled_back": len(self.list_candidates("rolled_back")),
            "failed": len(self.list_candidates("failed")),
        }


class SelfImprover:
    """Bridge failure signatures to queued sandbox jobs and SelfRepairManager."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.store = SelfImproverStore(self.root)

    def propose(self, signatures: list[dict[str, Any]], model: str) -> Optional[ImprovementCandidate]:
        """Create one queued sandbox job from the first usable recurring signature."""
        if not model or not model.strip():
            raise ValueError("A model is required to propose improvements")

        for sig in signatures:
            if (sig.get("count", 0) or 0) < 2:
                continue
            key = (f"{sig['error_type']}|{sig.get('subsystem', 'unknown')}|"
                   f"{sig.get('normalized_error', '')[:120]}")
            open_keys = {c.signature_key for c in self.store.list_candidates()
                         if c.status in ("queued", "handed_to_self_repair")}
            if key in open_keys:
                continue  # duplicate open work
            now = time.time()
            recent_failures = [
                c for c in self.store.list_candidates("failed")
                if c.signature_key == key
                and now - c.created_at <= FAILURE_WINDOW_SECONDS
            ]
            if len(recent_failures) >= MAX_FAILURES_PER_SIGNATURE:
                # repeated-failure backoff: defer this signature (R13)
                last_failed = max(recent_failures, key=lambda c: c.created_at)
                if not self.store.list_decisions(last_failed.candidate_id):
                    self.store.record_decision(
                        last_failed.candidate_id, "rejected",
                        "deferred: 3 failures in 7 days")
                continue
            break
        else:
            return None  # none usable

        error_type = sig.get("error_type", "unknown failure")
        subsystem = sig.get("subsystem", "orchestrator")
        count = sig.get("count", 1)
        sample = (sig.get("sample_error") or sig.get("normalized_error") or "")[:200]
        sample = sample.replace("\n", " ")
        hypothesis = f"Address recurring {error_type} in {subsystem}"
        goal = (
            f"Improve Ghost to reduce the recurring failure \"{error_type}\" in "
            f"{subsystem} (seen {count} times). Example: {sample}. "
            f"Make the smallest code change under ghost_agent/ that addresses it, "
            f"add a focused pytest regression test under tests/, and keep the "
            f"existing tests passing."
        )
        if len(goal) > 600:
            while len(goal) > 600 and len(sample) > 20:
                sample = sample[: len(sample) - 20]
                goal = (
                    f"Improve Ghost to reduce \"{error_type}\" in {subsystem} "
                    f"(seen {count} times). Example: {sample}. "
                    f"Make the smallest code change under ghost_agent/ that fixes it, "
                    f"add a pytest regression test, and keep existing tests passing."
                )

        from .proactive import ProactiveError, queue_sandbox_outcome
        cand = self.store.create_candidate(
            hypothesis=hypothesis, components=[subsystem],
            baseline={"failure_count": count}, plan="Sandbox job via worker; verification via self-repair evaluator",
            budget_hours=1.0,
            rollback="ghost promote rollback of the promotion, if ever applied")

        try:
            job = queue_sandbox_outcome(
                self.root, goal, model.strip(),
                source="self_improver", priority=30, workspace_mode="project")
            self.store.update_candidate(cand.candidate_id, {
                "status": "queued", "job_id": job.job_id, "signature_key": key})
            cand.status = "queued"
            cand.job_id = job.job_id
            cand.signature_key = key
        except ProactiveError as exc:
            self.store.fail(cand.candidate_id, f"queue refused: {exc}")
            cand.status = "failed"
            cand.failure_reason = f"queue refused: {exc}"
        return cand

    def sync(self) -> list[ImprovementCandidate]:
        """Check queued jobs, hand passing ones to SelfRepairManager, fail the rest."""
        from .autonomy import OutcomeStore
        from .self_repair import SelfRepairManager

        changed: list[ImprovementCandidate] = []
        store = OutcomeStore(self.root)
        repair_mgr = SelfRepairManager(self.root)
        candidates = self.store.list_candidates("queued")
        for cand in candidates:
            job = store.load(cand.job_id)
            if job is None:
                cand = self.store.fail(cand.candidate_id, "job record missing")
                changed.append(cand)
                continue
            if job.status not in _TERMINAL_JOB_STATUSES:
                continue  # still running
            if job.result == "tests_passed" and Path(job.sandbox_path).is_dir():
                dst = self.root / "workspaces" / "self-improve" / cand.candidate_id
                if dst.exists():
                    cand = self.store.fail(cand.candidate_id, "handoff directory already exists")
                    changed.append(cand)
                    continue
                shutil.copytree(
                    job.sandbox_path, dst,
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
                try:
                    proposal = repair_mgr.propose(
                        diagnosis=cand.hypothesis, sandbox=dst,
                        proposed_benefit=(f"Targets {cand.baseline_metrics.get('failure_count', '?')} recurring failures"),
                        linked_failure=cand.signature_key)
                    cand = self.store.update_candidate(cand.candidate_id, {
                        "status": "handed_to_self_repair",
                        "repair_proposal_id": proposal.proposal_id})
                    changed.append(cand)
                except ValueError as exc:
                    shutil.rmtree(dst, ignore_errors=True)
                    cand = self.store.fail(cand.candidate_id, f"self-repair refused: {exc}")
                    changed.append(cand)
            else:
                reason = job.result or job.status
                cand = self.store.fail(cand.candidate_id, f"job ended {reason}")
                changed.append(cand)
        return changed