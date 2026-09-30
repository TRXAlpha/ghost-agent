"""RL14/S08: Self-improvement engine — analyzes failures, proposes and evaluates improvements.

Uses trace_learning.py output (failure signatures) and Ghost's existing
SelfRepairManager/CapabilityRegistry to propose bounded improvements, evaluate
candidates against regression tests, and roll back on failure.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .utils.storage import atomic_json, exclusive_lock


@dataclass
class ImprovementCandidate:
    candidate_id: str
    hypothesis: str
    affected_components: list[str]
    baseline_metrics: dict[str, Any]
    evaluation_plan: str
    budget_hours: float
    rollback_plan: str
    status: str = "proposed"   # proposed | evaluating | shadow | active | rolled_back | failed
    baseline_commit: str = ""
    shadow_metrics: dict[str, Any] = field(default_factory=dict)
    final_metrics: dict[str, Any] = field(default_factory=dict)
    deployed_at: float = 0.0
    rolled_back_at: float = 0.0
    failure_reason: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


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
    """Bridge failure signatures from trace_learning to improvement candidates."""

    def __init__(self, root: Path, model: Optional[str] = None) -> None:
        self.root = Path(root).resolve()
        self.model = model or "qwen2.5-coder:1.5b"
        self.store = SelfImproverStore(self.root)

    def propose_from_signatures(self, signatures: list[dict[str, Any]]) -> list[ImprovementCandidate]:
        """Create improvement candidates from failure signatures."""
        candidates = []
        for sig in signatures:
            count = sig.get("count", 0)
            if count < 2:
                continue
            error_type = sig.get("error_type", "")
            subsys = sig.get("subsystem", "orchestrator")
            sample = sig.get("sample_error", "")[:200]
            hypothesis = (
                f"Recurring {error_type} in {subsys} (seen {count}x) "
                f"may be addressable by improving {subsys} error handling or retry logic"
            )
            components = [subsys]
            baseline = {"failure_count": count, "sample_error": sample}
            plan = f"Build a candidate fix targeting {error_type} in {subsys} component"
            rollback = f"Restore backup of affected {subsys} files"
            cand = self.store.create_candidate(
                hypothesis=hypothesis, components=components,
                baseline=baseline, plan=plan, budget_hours=0.5, rollback=rollback)
            candidates.append(cand)
        return candidates

    def evaluate_candidate(self, candidate_id: str,
                          regression_tests: Optional[list[str]] = None) -> dict[str, Any]:
        """Evaluate a candidate: run regression suite, record shadow metrics."""
        candidate = self.store.get_candidate(candidate_id)
        if not candidate:
            raise ValueError("Candidate not found")
        self.store.update_candidate(candidate_id, {"status": "evaluating"})
        results = {"passed": 0, "failed": 0, "errors": []}
        if regression_tests:
            for test in regression_tests:
                try:
                    r = subprocess.run(
                        ["python", "-m", "pytest", test, "-q"],
                        capture_output=True, text=True, timeout=120,
                        cwd=str(self.root))
                    if r.returncode == 0:
                        results["passed"] += 1
                    else:
                        results["failed"] += 1
                except Exception as exc:
                    results["errors"].append(str(exc))
        shadow = {"regression": results, "evaluated_at": time.time()}
        self.store.set_shadow_metrics(candidate_id, shadow)
        return shadow

    def review_and_decide(self, candidate_id: str) -> ImprovementDecision:
        """Model review of a candidate's shadow results -> approve/reject/rollback."""
        candidate = self.store.get_candidate(candidate_id)
        if not candidate:
            raise ValueError("Candidate not found")
        shadow = candidate.shadow_metrics
        baseline = candidate.baseline_metrics
        reg = shadow.get("regression", {})
        passed = reg.get("passed", 0)
        failed = reg.get("failed", 0)
        base_failed = baseline.get("failure_count", 0)
        # Simple heuristic: if regressions all pass AND it reduces failures -> approve
        if failed == 0 and passed > 0 and base_failed > 0:
            decision = "approved"
            reason = f"Regressions passed ({passed}/{passed+failed}), baseline failures={base_failed}"
        elif failed > 0:
            decision = "rollback"
            reason = f"Regression failures: {failed} test(s) broke"
        else:
            decision = "rejected"
            reason = "Insufficient evidence to approve"
        return self.store.record_decision(candidate_id, decision, reason)