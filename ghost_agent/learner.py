"""OJ12: learner progress, mastery, assessment oracles, and study planning.

Implements learner/educational domain logic on reused research/retrieval/
coding/UI: per-concept mastery with spaced-repetition scheduling, assessment
attempts with cited lessons, feedback-driven next activity, and progress views.
Assessment is decoupled through an injectable ``Oracle`` so both a wrong-solution
rejection and a compiled-Rust check are uniformly expressible; a concrete
``RustCompileOracle`` shells out to ``rustc``.

This module owns educational domain logic only. It reuses the existing stores
and never alters grants, authority, the scheduler, or the worker.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol

from .utils.storage import atomic_json, exclusive_lock

MAX_INTERVAL_DAYS = 30.0


class LearnerError(ValueError):
    """Invalid concept, attempt, or learner state."""


class Oracle(Protocol):
    """Checks a submitted solution for correctness."""

    def check(self, submission: str, context: str = "") -> tuple[bool, str]:
        """Return (correct, feedback).  feedback is nonempty when incorrect."""
        ...


@dataclass
class Concept:
    concept_id: str
    name: str
    attempts: int = 0
    correct: int = 0
    streak: int = 0
    interval_days: float = 1.0
    next_review_at: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @property
    def mastery(self) -> float:
        """Fraction of attempts correct, bounded to [0, 1]."""
        return (self.correct / self.attempts) if self.attempts else 0.0


@dataclass
class Attempt:
    attempt_id: str
    concept_id: str
    correct: bool
    at: float
    lesson_id: str = ""      # cited lesson / source for S04 cited-lessons
    feedback: str = ""
    submission: str = ""


class LearnerStore:
    """Persistent learner state under .ghost/learner/."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.dir_path = self.root / ".ghost" / "learner"
        self.concepts_path = self.dir_path / "concepts.json"
        self.attempts_path = self.dir_path / "attempts.json"
        self.dir_path.mkdir(parents=True, exist_ok=True)

    # -- persistence ------------------------------------------------------
    @staticmethod
    def _load_list(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("Expected a list")
            return raw
        except (OSError, ValueError, TypeError) as exc:
            raise LearnerError(
                "Learner state is unreadable or corrupt; preserve it for review") from exc

    @staticmethod
    def _save_list(path: Path, items: list[dict[str, Any]]) -> None:
        atomic_json(path, items)

    def _load_concepts(self) -> list[dict[str, Any]]:
        items = self._load_list(self.concepts_path)
        seen: set[str] = set()
        for item in items:
            concept = Concept(**item)
            if concept.concept_id in seen or not concept.concept_id:
                raise LearnerError("Duplicate or empty concept id")
            if type(concept.attempts) is not int or type(concept.correct) is not int \
                    or concept.correct > concept.attempts or concept.attempts < 0 \
                    or concept.correct < 0 or type(concept.streak) is not int:
                raise LearnerError("Invalid concept counters")
            seen.add(concept.concept_id)
        return items

    def _load_attempts(self) -> list[dict[str, Any]]:
        items = self._load_list(self.attempts_path)
        seen: set[str] = set()
        for item in items:
            attempt = Attempt(**item)
            if attempt.attempt_id in seen or not attempt.attempt_id:
                raise LearnerError("Duplicate or empty attempt id")
            if type(attempt.correct) is not bool or not isinstance(attempt.lesson_id, str):
                raise LearnerError("Invalid attempt record")
            seen.add(attempt.attempt_id)
        return items

    # -- concepts ---------------------------------------------------------
    def register_concept(self, concept_id: str, name: str) -> Concept:
        concept_id = concept_id.strip()
        name = name.strip()
        if not concept_id or not name or len(concept_id) > 120 or len(name) > 200:
            raise LearnerError("Concept id and name must be nonempty within length limits")
        with exclusive_lock(self.concepts_path.with_suffix(".lock")):
            items = self._load_concepts()
            if any(item["concept_id"] == concept_id for item in items):
                raise LearnerError("Concept already registered")
            concept = Concept(concept_id=concept_id, name=name)
            items.append(asdict(concept))
            self._save_list(self.concepts_path, items)
            return concept

    def get_concept(self, concept_id: str) -> Optional[Concept]:
        for item in self._load_concepts():
            if item["concept_id"] == concept_id:
                return Concept(**item)
        return None

    def concepts(self) -> list[Concept]:
        return [Concept(**item) for item in self._load_concepts()]

    def reset(self, concept_id: str) -> Concept:
        """Reset a concept to unmastered (solvable/resettable CTF)."""
        with exclusive_lock(self.concepts_path.with_suffix(".lock")):
            items = self._load_concepts()
            for item in items:
                if item["concept_id"] == concept_id:
                    item["attempts"] = 0
                    item["correct"] = 0
                    item["streak"] = 0
                    item["interval_days"] = 1.0
                    item["next_review_at"] = 0.0
                    item["updated_at"] = time.time()
                    self._save_list(self.concepts_path, items)
                    return Concept(**item)
            raise LearnerError("Concept not found")

    def remove(self, concept_id: str) -> None:
        """Delete a concept and its attempts (source deletion)."""
        with exclusive_lock(self.concepts_path.with_suffix(".lock")):
            items = [item for item in self._load_concepts()
                     if item["concept_id"] != concept_id]
            self._save_list(self.concepts_path, items)
        with exclusive_lock(self.attempts_path.with_suffix(".lock")):
            attempts = [item for item in self._load_attempts()
                        if item["concept_id"] != concept_id]
            self._save_list(self.attempts_path, attempts)

    # -- attempts / feedback -----------------------------------------------
    def record_attempt(self, concept_id: str, correct: bool,
                       *, lesson_id: str = "", feedback: str = "",
                       submission: str = "", now: Optional[float] = None) -> Concept:
        """Record an assessment and update mastery with spaced repetition."""
        if type(correct) is not bool:
            raise LearnerError("correct must be a boolean")
        now_ts = time.time() if now is None else now
        attempt_id = "atp_" + hashlib.sha1(
            f"{concept_id}|{now_ts}|{lesson_id}".encode()).hexdigest()[:12]
        with exclusive_lock(self.concepts_path.with_suffix(".lock")):
            items = self._load_concepts()
            target = next((item for item in items if item["concept_id"] == concept_id), None)
            if target is None:
                raise LearnerError("Concept not found")
            target["attempts"] += 1
            if correct:
                target["correct"] += 1
                target["streak"] += 1
                target["interval_days"] = min(
                    target["interval_days"] * 2.0 if target["streak"] >= 2
                    else target["interval_days"], MAX_INTERVAL_DAYS)
            else:
                target["streak"] = 0
                target["interval_days"] = 1.0
            target["next_review_at"] = now_ts + target["interval_days"] * 86400
            target["updated_at"] = now_ts
            self._save_list(self.concepts_path, items)
            concept = Concept(**target)
        with exclusive_lock(self.attempts_path.with_suffix(".lock")):
            attempts = self._load_attempts()
            attempts.append(asdict(Attempt(
                attempt_id=attempt_id, concept_id=concept_id, correct=correct,
                at=now_ts, lesson_id=lesson_id, feedback=feedback[:500],
                submission=submission[:2000])))
            self._save_list(self.attempts_path, attempts)
        return concept

    # -- study planning / progress -----------------------------------------
    def next_activity(self, now: Optional[float] = None) -> Optional[Concept]:
        """Feedback-driven next activity: due concept with the lowest mastery."""
        now_ts = time.time() if now is None else now
        due = [c for c in self.concepts() if now_ts >= c.next_review_at]
        if not due:
            return None
        return min(due, key=lambda c: (c.mastery, c.concept_id))

    def due_count(self, now: Optional[float] = None) -> int:
        now_ts = time.time() if now is None else now
        return sum(1 for c in self.concepts() if now_ts >= c.next_review_at)

    def progress(self) -> dict[str, Any]:
        """Progress views: per-concept mastery plus overall stats."""
        concepts = self.concepts()
        overall = (sum(c.correct for c in concepts) /
                   sum(c.attempts for c in concepts)) if any(c.attempts for c in concepts) else 0.0
        return {
            "concepts": [{"concept_id": c.concept_id, "name": c.name,
                          "mastery": round(c.mastery, 3),
                          "attempts": c.attempts, "correct": c.correct,
                          "streak": c.streak} for c in concepts],
            "overall_mastery": round(overall, 3),
            "due": self.due_count(),
        }

    def lessons(self, concept_id: str) -> list[str]:
        """Cited lesson/source ids for a concept (S04 cited lessons)."""
        return sorted({item["lesson_id"] for item in self._load_attempts()
                       if item["concept_id"] == concept_id and item["lesson_id"]})

    def attempts(self, concept_id: str, limit: int = 50) -> list[Attempt]:
        items = [Attempt(**item) for item in self._load_attempts()
                 if item["concept_id"] == concept_id]
        items.sort(key=lambda a: a.at, reverse=True)
        return items[:limit]

    # -- assessment -------------------------------------------------------
    def assess(self, concept_id: str, submission: str, oracle: Oracle,
               *, lesson_id: str = "", now: Optional[float] = None) -> Attempt:
        """Run an oracle against a submission and record the attempt."""
        if not isinstance(submission, str):
            raise LearnerError("Submission must be text")
        correct_flag, feedback = oracle.check(submission, context=concept_id)
        self.record_attempt(concept_id, bool(correct_flag), lesson_id=lesson_id,
                            feedback=feedback, submission=submission, now=now)
        attempt = self.attempts(concept_id, limit=1)[0]
        return attempt


class RustCompileOracle:
    """Concrete oracle: a submission is correct if it compiles under rustc."""

    def __init__(self, rustc: str = "rustc", timeout: int = 60) -> None:
        self.rustc = rustc
        self.timeout = timeout

    def available(self) -> bool:
        return shutil.which(self.rustc) is not None

    def check(self, submission: str, context: str = "") -> tuple[bool, str]:
        if not submission.strip():
            return False, "empty submission"
        workdir = tempfile.mkdtemp(prefix="ghost-learner-")
        source = Path(workdir) / "solution.rs"
        source.write_text(submission, encoding="utf-8")
        try:
            probe = subprocess.run(
                [self.rustc, "--edition", "2021", str(source)],
                capture_output=True, text=True, timeout=self.timeout)
        except FileNotFoundError:
            return False, f"rustc not available (searched '{self.rustc}')"
        if probe.returncode == 0:
            return True, "compiles"
        detail = (probe.stderr or probe.stdout or "unknown compiler error").strip()
        return False, detail[-500:]