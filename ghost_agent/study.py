"""TG3: exercise generation linked to concepts and cited owner sources.

Exercises are generated from retrieved knowledge-base chunks for a concept.
The learner-facing exercise record never contains the answer: expected answers
live separately under ``.ghost/learner/answers/`` (R10/S04/S05 protected
answers). Model output is parsed defensively; nothing is ever executed.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .knowledge_base import OwnerKnowledgeBase
from .learner import LearnerStore, RustCompileOracle
from .models.chat import chat

from .utils.storage import atomic_json

MAX_PROMPT_CHARS = 600
MAX_HINT_CHARS = 240
MAX_EXPECTED_CHARS = 2000
ANSWER_KINDS = ("exact", "rustc", "keywords")


class StudyError(ValueError):
    """Invalid exercise generation, check, or hint request."""


@dataclass
class Exercise:
    exercise_id: str
    concept_id: str
    prompt: str
    hints: list[str]          # 1-3 progressive hints
    answer_kind: str          # exact | rustc | keywords
    source_refs: list[str]    # kbase chunk ids the exercise is grounded in
    created_at: float


class ExerciseGenerator:
    """Generates concept exercises via the model, grounded in owner sources."""

    def __init__(self, root: Path, model: str,
                 chat_fn: Callable[..., str] = chat) -> None:
        self.root = Path(root).resolve()
        if not isinstance(model, str) or not model.strip():
            raise StudyError("Exercise generation requires an explicit model")
        self.model = model.strip()
        self.chat = chat_fn
        self.kb = OwnerKnowledgeBase(self.root)
        self.exercises_dir = self.root / ".ghost" / "learner" / "exercises"
        self.answers_dir = self.root / ".ghost" / "learner" / "answers"
        self.exercises_dir.mkdir(parents=True, exist_ok=True)
        self.answers_dir.mkdir(parents=True, exist_ok=True)

    # -- persistence ------------------------------------------------------
    def _save_exercise(self, exercise: Exercise) -> None:
        atomic_json(self.exercises_dir / f"{exercise.exercise_id}.json",
                    asdict(exercise))

    def get_exercise(self, exercise_id: str) -> Optional[Exercise]:
        path = self.exercises_dir / f"{exercise_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return Exercise(**{k: data[k] for k in Exercise.__dataclass_fields__})

    def _save_answer(self, exercise_id: str, expected: str) -> None:
        atomic_json(self.answers_dir / f"{exercise_id}.json",
                    {"expected": expected})

    def _load_answer(self, exercise_id: str) -> str:
        path = self.answers_dir / f"{exercise_id}.json"
        if not path.exists():
            raise StudyError(f"No stored answer for exercise {exercise_id}")
        return str(json.loads(path.read_text(encoding="utf-8")).get("expected", ""))

    # -- generation -------------------------------------------------------
    def generate(self, concept_id: str, count: int = 3) -> list[Exercise]:
        """Generate up to ``count`` validated exercises for a concept."""
        if type(count) is not int or count < 1 or count > 10:
            raise StudyError("count must be an integer between 1 and 10")
        chunks = self.kb.retrieve(concept_id, limit=4)
        source_refs = [chunk.chunk_id for chunk in chunks]
        grounding = "\n\n".join(
            f"[{c.chunk_id}] {c.source}\n{c.content[:1500]}" for c in chunks)
        if not chunks:
            grounding = "no owner sources available"
        prompt = (
            "Create practice exercises for the concept "
            f"'{concept_id}'. Use ONLY the owner reference material below.\n\n"
            f"{grounding}\n\n"
            "Reply with strict JSON only, no prose:\n"
            '{"exercises": [{"prompt": str, "hints": [str], '
            '"answer_kind": "exact"|"rustc"|"keywords", "expected": str}]}'
        )
        raw = self.chat(self.model, [{"role": "user", "content": prompt}])
        items = self._parse_exercises(raw)
        exercises: list[Exercise] = []
        for item in items:
            exercise = self._validate(item, concept_id, source_refs)
            if exercise is None:
                continue
            self._save_exercise(exercise)
            self._save_answer(exercise.exercise_id,
                              str(item["expected"]).strip())
            exercises.append(exercise)
            if len(exercises) >= count:
                break
        return exercises

    @staticmethod
    def _parse_exercises(raw: Any) -> list[dict[str, Any]]:
        """Defensively extract the exercise list from model output."""
        if not isinstance(raw, str):
            return []
        text = raw.strip()
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return []
        try:
            data = json.loads(text[start:end + 1])
        except (ValueError, TypeError):
            return []
        items = data.get("exercises") if isinstance(data, dict) else None
        return [item for item in items if isinstance(item, dict)] \
            if isinstance(items, list) else []

    def _validate(self, item: dict[str, Any], concept_id: str,
                  source_refs: list[str]) -> Optional[Exercise]:
        """Validate one model-produced exercise; return None if invalid."""
        prompt = item.get("prompt")
        hints = item.get("hints")
        kind = item.get("answer_kind")
        expected = item.get("expected")
        if not isinstance(prompt, str) or not prompt.strip() \
                or len(prompt) > MAX_PROMPT_CHARS:
            return None
        if not isinstance(hints, list) or not 1 <= len(hints) <= 3 \
                or not all(isinstance(h, str) and h.strip()
                           and len(h) <= MAX_HINT_CHARS for h in hints):
            return None
        if kind not in ANSWER_KINDS or not isinstance(expected, str) \
                or not expected.strip() or len(expected) > MAX_EXPECTED_CHARS:
            return None
        exercise_id = "exe_" + hashlib.sha1(
            f"{concept_id}|{prompt.strip()}|{time.time()}".encode()
        ).hexdigest()[:12]
        return Exercise(
            exercise_id=exercise_id, concept_id=concept_id,
            prompt=prompt.strip(), hints=[h.strip() for h in hints],
            answer_kind=kind, source_refs=list(source_refs),
            created_at=time.time(),
        )

    # -- checking ---------------------------------------------------------
    def check(self, exercise_id: str, submission: str) -> tuple[bool, str]:
        """Check a submission and record the attempt on the concept."""
        exercise = self.get_exercise(exercise_id)
        if exercise is None:
            raise StudyError(f"Unknown exercise {exercise_id}")
        if not isinstance(submission, str):
            raise StudyError("Submission must be text")
        expected = self._load_answer(exercise_id)
        if exercise.answer_kind == "exact":
            correct = submission.strip().lower() == expected.strip().lower()
            feedback = "correct" if correct else "expected answer differs"
        elif exercise.answer_kind == "keywords":
            missing = [kw for kw in expected.split(",")
                       if kw.strip().lower() not in submission.lower()]
            correct = not missing
            feedback = "correct" if correct else \
                f"missing keywords: {', '.join(missing)}"
        else:  # rustc
            correct, feedback = RustCompileOracle(plan_root=self.root).check(
                submission, context=exercise.concept_id)
        lesson_id = exercise.source_refs[0] if exercise.source_refs else ""
        LearnerStore(self.root).record_attempt(
            exercise.concept_id, correct, lesson_id=lesson_id,
            feedback=feedback, submission=submission)
        return correct, feedback

    # -- hints ------------------------------------------------------------
    def next_hint(self, exercise_id: str, wrong_attempts: int) -> str:
        """Reveal hints progressively: one more hint per wrong attempt."""
        exercise = self.get_exercise(exercise_id)
        if exercise is None:
            raise StudyError(f"Unknown exercise {exercise_id}")
        index = max(0, wrong_attempts)
        if index >= len(exercise.hints):
            return exercise.hints[-1]
        return exercise.hints[index]
