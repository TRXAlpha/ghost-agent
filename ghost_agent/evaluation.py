"""Deterministic evaluation records for model and agent changes."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .models.routing import ModelReputationStore
from .security import redact_secrets
from .utils.storage import atomic_write, exclusive_lock


@dataclass
class EvaluationCase:
    case_id: str
    task_class: str
    goal: str
    acceptance: dict[str, Any]


@dataclass
class EvaluationResult:
    case_id: str
    model: str
    verified_success: bool
    latency_seconds: float
    verification_failure: bool = False
    repair_attempted: bool = False
    detail: str = ""
    created_at: float = field(default_factory=time.time)
    task_class: str = ""
    goal: str = ""
    acceptance: dict[str, Any] = field(default_factory=dict)
    evaluation_id: str = ""


class EvaluationSuite:
    """Run supplied deterministic evaluators and feed only verified results to routing."""

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root)
        self.path = self.root / ".ghost" / "evaluations.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.reputation = ModelReputationStore(self.root)

    def run(self, case: EvaluationCase, model: str,
            evaluator: Callable[[EvaluationCase, str], dict[str, Any]]) -> EvaluationResult:
        self.reconcile()
        # Preserve the original contract even if the evaluator mutates its
        # argument. It must not relabel a protocol check as a coding result.
        contract = asdict(case)
        started = time.monotonic()
        try:
            verdict = evaluator(case, model)
            if not isinstance(verdict, dict) or type(verdict.get("verified_success")) is not bool:
                raise ValueError("Evaluator must return an explicit boolean verified_success")
            for key in ("verification_failure", "repair_attempted"):
                if key in verdict and type(verdict[key]) is not bool:
                    raise ValueError(f"Evaluator {key} must be boolean")
            if verdict["verified_success"] and verdict.get("verification_failure", False):
                raise ValueError("Evaluator returned contradictory success and verification failure")
        except Exception as exc:
            verdict = {"verified_success": False, "verification_failure": True, "detail": str(exc)}
        result = EvaluationResult(
            contract["case_id"], model, verdict["verified_success"], time.monotonic() - started,
            verdict.get("verification_failure", not verdict["verified_success"]),
            verdict.get("repair_attempted", False), redact_secrets(str(verdict.get("detail", ""))),
            task_class=contract["task_class"], goal=redact_secrets(contract["goal"]),
            acceptance=redact_secrets(contract["acceptance"]),
            evaluation_id="evaluation_" + uuid.uuid4().hex,
        )
        with exclusive_lock(self.path.with_suffix(".lock")):
            records = self._records()
            records.append(asdict(result))
            atomic_write(self.path, ("\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n").encode())
            self._apply(asdict(result))
        return result

    def _records(self) -> list[dict[str, Any]]:
        try:
            if not self.path.exists():
                return []
            records = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
            if any(not isinstance(item, dict) for item in records):
                raise ValueError("Expected evaluation objects")
            seen = {}
            for item in records:
                identifier = item.get("evaluation_id")
                if identifier:
                    if identifier in seen and seen[identifier] != item:
                        raise ValueError("Conflicting evaluation IDs")
                    seen[identifier] = item
            return records
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError("Evaluation log is unreadable or corrupt; preserve it for review") from exc

    def _apply(self, record: dict[str, Any]) -> bool:
        return self.reputation.record(record["model"], record["task_class"], record["verified_success"],
            record["latency_seconds"], record["verification_failure"], repair_attempted=record["repair_attempted"],
            record_id=record["evaluation_id"])

    def reconcile(self) -> int:
        """Replay identified durable results without rerunning their evaluators.

        Legacy records lack receipts and cannot safely be replayed automatically.
        """
        with exclusive_lock(self.path.with_suffix(".lock")):
            return sum(self._apply(item) for item in self._records() if item.get("evaluation_id"))
