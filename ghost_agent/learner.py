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
        self.deadlines_path = self.dir_path / "deadlines.json"
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

    # -- deadlines (TG4) ----------------------------------------------------
    def set_deadline(self, concept_ids: list[str], due_at: float, label: str) -> dict[str, Any]:
        """Record a deadline (e.g. an exam) that links concepts to a due date."""
        label = label.strip()
        if not label or len(label) > 200:
            raise LearnerError("Deadline label must be nonempty within length limits")
        if not isinstance(due_at, (int, float)) or due_at <= 0:
            raise LearnerError("due_at must be a positive timestamp")
        concept_ids = [cid.strip() for cid in concept_ids]
        if not concept_ids or any(not cid for cid in concept_ids):
            raise LearnerError("Deadline must link at least one concept id")
        with exclusive_lock(self.deadlines_path.with_suffix(".lock")):
            items = [d for d in self._load_list(self.deadlines_path)
                     if d.get("label") != label]
            items.append({"label": label, "due_at": float(due_at),
                          "concept_ids": concept_ids})
            self._save_list(self.deadlines_path, items)
        return {"label": label, "due_at": float(due_at), "concept_ids": concept_ids}

    def _deadline_concepts(self, now_ts: float) -> dict[str, float]:
        """Map concept_id -> nearest future deadline within 14 days (past ignored)."""
        nearest: dict[str, float] = {}
        for d in self._load_list(self.deadlines_path):
            due_at = float(d.get("due_at", 0.0))
            if due_at < now_ts or due_at > now_ts + 14 * 86400:
                continue
            for cid in d.get("concept_ids", []):
                if cid not in nearest or due_at < nearest[cid]:
                    nearest[cid] = due_at
        return nearest

    # -- study planning / progress -----------------------------------------
    def next_activity(self, now: Optional[float] = None) -> Optional[Concept]:
        """Due concept with the lowest mastery; deadline-linked concepts first.

        Among due concepts, those with a deadline within 14 days come first,
        ordered by nearest deadline, then lowest mastery.  With no deadlines
        the original (mastery, concept_id) ordering applies.
        """
        now_ts = time.time() if now is None else now
        due = [c for c in self.concepts() if now_ts >= c.next_review_at]
        if not due:
            return None
        deadlines = self._deadline_concepts(now_ts)
        return min(due, key=lambda c: (c.concept_id not in deadlines,
                                       deadlines.get(c.concept_id, float("inf")),
                                       c.mastery, c.concept_id))

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

    DEPENDENCY_PLAN_SOURCE = "https://rustup.rs"

    def __init__(self, rustc: str = "rustc", timeout: int = 60,
                 plan_root: Optional[Path] = None) -> None:
        self.rustc = rustc
        self.timeout = timeout
        self.plan_root = Path(plan_root) if plan_root is not None else None
        self._toolchain: Optional[str] = None
        self._toolchain_checked = False

    def toolchain(self) -> Optional[str]:
        """Return stripped ``rustc --version`` output, or None. Cached."""
        if not self._toolchain_checked:
            self._toolchain_checked = True
            try:
                probe = subprocess.run(
                    [self.rustc, "--version"], capture_output=True, text=True,
                    timeout=5)
            except (OSError, subprocess.SubprocessError):
                self._toolchain = None
            else:
                if probe.returncode == 0:
                    self._toolchain = (probe.stdout or "").strip() or None
        return self._toolchain

    def available(self) -> bool:
        return shutil.which(self.rustc) is not None

    def _write_dependency_plan(self, context: str = "") -> None:
        if self.plan_root is None:
            return
        plan_dir = self.plan_root / ".ghost" / "learner"
        plan_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Dependency plan: rustc",
            "",
            "## What is missing",
            "",
            "The `rustc` toolchain was not found on this system.",
        ]
        if context:
            lines += ["", f"Why: needed for the exercise/concept '{context}'."]
        lines += [
            "",
            "## How to install",
            "",
            f"Official install source: {self.DEPENDENCY_PLAN_SOURCE}",
            "",
            "The owner should run `rustup-init` themselves, downloaded from "
            "rustup.rs.",
            "",
            "Ghost will not install toolchains itself.",
            "",
        ]
        (plan_dir / "dependency_plan.md").write_text(
            "\n".join(lines), encoding="utf-8")

    def check(self, submission: str, context: str = "") -> tuple[bool, str]:
        if not submission.strip():
            return False, "empty submission"
        if not self.available():
            self._write_dependency_plan(context)
            return False, ("rustc not available; dependency plan written to "
                           ".ghost/learner/dependency_plan.md")
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
            feedback = "compiles"
        else:
            detail = (probe.stderr or probe.stdout
                      or "unknown compiler error").strip()
            feedback = detail[-500:]
        version = self.toolchain()
        if version:
            feedback = f"[rustc {version}] {feedback}"
        return probe.returncode == 0, feedback

class FormatStats:
    """Track which teaching format works (TK3/R10).

    Persistent counters under ``.ghost/learner/formats.json``: per format
    (exercise, challenge, explanation) how much material was generated and
    how often attempts happened and succeeded, so Ghost can recommend a
    format and stop producing unused material.
    """

    FORMATS = ("exercise", "challenge", "explanation")
    MIN_ATTEMPTS = 3
    UNUSED_GENERATED = 5

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.path = self.root / ".ghost" / "learner" / "formats.json"

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        atomic_json(self.path, data)

    def stats(self) -> dict[str, dict[str, Any]]:
        """Return per-format counters, defaults filled in."""
        raw = self._load()
        out: dict[str, dict[str, Any]] = {}
        for fmt in self.FORMATS:
            entry = raw.get(fmt) if isinstance(raw.get(fmt), dict) else {}
            out[fmt] = {
                "generated": int(entry.get("generated", 0) or 0),
                "attempted": int(entry.get("attempted", 0) or 0),
                "correct": int(entry.get("correct", 0) or 0),
                "last_used_at": float(entry.get("last_used_at", 0.0) or 0.0),
            }
        return out

    def record(self, fmt: str, *, generated: int = 0,
               attempted: int = 0, correct: int = 0) -> None:
        """Increment counters for a format through the real learning path."""
        if fmt not in self.FORMATS:
            raise LearnerError(f"Unknown teaching format: {fmt}")
        with exclusive_lock(self.path.with_suffix(".lock")):
            data = self._load()
            entry = data.get(fmt) if isinstance(data.get(fmt), dict) else {}
            entry["generated"] = int(entry.get("generated", 0) or 0) + generated
            entry["attempted"] = int(entry.get("attempted", 0) or 0) + attempted
            entry["correct"] = int(entry.get("correct", 0) or 0) + correct
            entry["last_used_at"] = time.time()
            data[fmt] = entry
            self._save(data)

    def recommend_format(self) -> dict[str, Any]:
        """Recommend the best-evidenced format; flag unused formats."""
        stats = self.stats()
        unused = [fmt for fmt, s in stats.items()
                  if s["generated"] >= self.UNUSED_GENERATED
                  and s["attempted"] == 0]
        best: Optional[str] = None
        best_rate = -1.0
        for fmt, s in stats.items():
            if s["attempted"] >= self.MIN_ATTEMPTS:
                rate = s["correct"] / s["attempted"]
                if rate > best_rate:
                    best, best_rate = fmt, rate
        if best is not None:
            reason = (f"highest success rate {best_rate:.0%} over "
                      f"{stats[best]['attempted']} attempts")
        else:
            best = "exercise"
            reason = "no evidence yet"
        result: dict[str, Any] = {"format": best, "reason": reason}
        if unused:
            result["unused"] = unused
            result["unused_reason"] = \
                "generated but never attempted: stop generating"
        return result
