"""Measured local-model routing independent from model-generated judgments."""
from __future__ import annotations

import json
import re
import math
import hashlib
from pathlib import Path
from typing import Dict, Iterable, Optional

from ..utils.storage import atomic_json, exclusive_lock


def _nonnegative_number(value) -> bool:
    try:
        return type(value) in (int, float) and value >= 0 and math.isfinite(value)
    except OverflowError:
        return False


class ModelReputationStore:
    def __init__(self, project_root: Path) -> None:
        self.path = Path(project_root) / ".ghost" / "models.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> Dict[str, dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Model reputation must be an object")
            for key, item in data.items():
                if (not isinstance(item, dict) or not isinstance(item.get("model"), str)
                        or not isinstance(item.get("task_class"), str)
                        or key != f"{item['model']}|{item['task_class']}"):
                    raise ValueError("Invalid reputation identity")
                if any(not item[name].strip() or "|" in item[name] for name in ("model", "task_class")):
                    raise ValueError("Invalid reputation identity")
                for counter in ("runs", "successes", "verification_failures", "context_chars_total", "repair_attempts"):
                    value = item.get(counter, 0)
                    if type(value) is not int or value < 0:
                        raise ValueError("Invalid reputation counter")
                if any(item.get(counter, 0) > item.get("runs", 0)
                       for counter in ("successes", "verification_failures", "repair_attempts")):
                    raise ValueError("Reputation outcomes exceed run count")
                for metric in ("latency_seconds_total", "estimated_cost_total"):
                    if not _nonnegative_number(item.get(metric, 0)):
                        raise ValueError("Invalid reputation metric")
                for required in ("runs", "successes", "verification_failures", "latency_seconds_total"):
                    if required not in item:
                        raise ValueError("Missing reputation aggregate")
                item.setdefault("context_chars_total", 0)
                item.setdefault("repair_attempts", 0)
                item.setdefault("estimated_cost_total", 0.0)
                receipts = item.setdefault("evaluation_receipts", {})
                if (not isinstance(receipts, dict) or any(
                        not isinstance(identifier, str) or not isinstance(digest, str)
                        or not re.fullmatch(r"[0-9a-f]{64}", digest)
                        for identifier, digest in receipts.items())):
                    raise ValueError("Invalid evaluation receipts")
            return data
        except FileNotFoundError:
            return {}
        except (ValueError, OSError, TypeError) as exc:
            raise ValueError("Model reputation is unreadable or corrupt; refusing to overwrite it") from exc

    def record(self, model: str, task_class: str, verified_success: bool,
               latency_seconds: float = 0.0, verification_failure: bool = False,
               context_chars: int = 0, repair_attempted: bool = False,
               estimated_cost: float = 0.0, *, record_id: str | None = None) -> bool:
        if record_id is not None and (not isinstance(record_id, str) or not re.fullmatch(r"evaluation_[0-9a-f]{32}", record_id)):
            raise ValueError("Invalid evaluation record ID")
        if any(not isinstance(value, str) or not value.strip() or "|" in value for value in (model, task_class)):
            raise ValueError("Model and task class must be nonempty identities without '|'")
        if any(type(value) is not bool for value in (verified_success, verification_failure, repair_attempted)):
            raise ValueError("Reputation outcomes must be booleans")
        if (not _nonnegative_number(latency_seconds) or not _nonnegative_number(estimated_cost)
                or type(context_chars) is not int or context_chars < 0):
            raise ValueError("Reputation metrics must be finite and nonnegative")
        with exclusive_lock(self.path.with_suffix(".lock")):
            return self._record_locked(model, task_class, verified_success, latency_seconds,
                                verification_failure, context_chars, repair_attempted, estimated_cost, record_id)

    def _record_locked(self, model, task_class, verified_success, latency_seconds,
                       verification_failure, context_chars, repair_attempted, estimated_cost, record_id=None) -> bool:
        data = self._load()
        digest = hashlib.sha256(json.dumps([model, task_class, verified_success, latency_seconds,
            verification_failure, context_chars, repair_attempted, estimated_cost], separators=(",", ":")).encode()).hexdigest()
        if record_id is not None:
            for existing in data.values():
                previous = existing["evaluation_receipts"].get(record_id)
                if previous is not None:
                    if previous != digest:
                        raise ValueError("Evaluation record ID was already used with different metrics")
                    return False
        key = f"{model}|{task_class}"
        item = data.setdefault(key, {"model": model, "task_class": task_class,
                                     "runs": 0, "successes": 0, "verification_failures": 0,
                                     "latency_seconds_total": 0.0, "context_chars_total": 0,
                                     "repair_attempts": 0, "estimated_cost_total": 0.0})
        item["runs"] += 1
        item["successes"] += int(verified_success)
        item["verification_failures"] += int(verification_failure)
        item["latency_seconds_total"] += latency_seconds
        item["context_chars_total"] += context_chars
        item["repair_attempts"] += int(repair_attempted)
        item["estimated_cost_total"] += estimated_cost
        if record_id is not None:
            item.setdefault("evaluation_receipts", {})[record_id] = digest
        if not _nonnegative_number(item["latency_seconds_total"]) or not _nonnegative_number(item["estimated_cost_total"]):
            raise ValueError("Reputation aggregate overflow")
        atomic_json(self.path, data)
        return True

    def score(self, model: str, task_class: str) -> Optional[float]:
        item = self._load().get(f"{model}|{task_class}")
        if not item or not item.get("runs"):
            return None
        runs = item["runs"]
        success = item["successes"] / runs
        verification_penalty = item["verification_failures"] / runs
        latency = item["latency_seconds_total"] / runs
        return success - 0.5 * verification_penalty - min(latency / 3600, 0.1)


class ModelRouter:
    def __init__(self, reputation: ModelReputationStore) -> None:
        self.reputation = reputation

    def choose(self, task_class: str, candidates: Iterable[str], fallback: str) -> str:
        options = [candidate for candidate in candidates if candidate]
        scored = [(self.reputation.score(candidate, task_class), candidate) for candidate in options]
        known = [(score, candidate) for score, candidate in scored if score is not None]
        if not known:
            return fallback
        return max(known, key=lambda item: (item[0], item[1]))[1]


class TaskClassifier:
    """Conservative deterministic task classification before model routing."""
    CLASSES = {"research", "coding", "planning", "testing", "security", "summarization"}

    def classify(self, goal: str) -> tuple[str, str]:
        text = goal.lower()
        if any(word in text for word in ("research", "study", "evidence", "literature", "citation")):
            task_class = "research"
        elif any(word in text for word in ("test", "regression", "benchmark", "verify")):
            task_class = "testing"
        elif any(word in text for word in ("security", "threat", "secret", "permission")):
            task_class = "security"
        elif any(word in text for word in ("summarize", "summary")):
            task_class = "summarization"
        elif any(word in text for word in ("plan", "design", "architecture")):
            task_class = "planning"
        else:
            task_class = "coding"
        concepts = len(set(re.findall(r"[a-zA-Z][a-zA-Z0-9-]{2,}", text)))
        complexity = "high" if concepts >= 14 or len(text) >= 180 else "medium" if concepts >= 7 else "low"
        return task_class, complexity


class ModelOrchestrator:
    """Select models and a verification role from measurable local evidence."""
    def __init__(self, reputation: ModelReputationStore) -> None:
        self.reputation = reputation
        self.router = ModelRouter(reputation)
        self.classifier = TaskClassifier()

    def route(self, goal: str, candidates: Iterable[str], fallback: str) -> dict[str, str]:
        task_class, complexity = self.classifier.classify(goal)
        primary = self.router.choose(task_class, candidates, fallback)
        # Verification is an independent role even where the same local model
        # serves both roles. The execution layer must still rely on tools/tests,
        # not a model's self-assessment.
        verifier = self.router.choose("testing", candidates, primary)
        planner = self.router.choose("planning", candidates, primary)
        critic = self.router.choose("security" if task_class == "security" else "testing", candidates, verifier)
        synthesizer = self.router.choose("summarization", candidates, primary)
        return {"task_class": task_class, "complexity": complexity, "planner": planner,
                "primary": primary, "critic": critic, "synthesizer": synthesizer, "verifier": verifier}
