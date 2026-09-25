"""OJ12: learner mastery, spaced repetition, oracles, progress, CTF reset/delete."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from ghost_agent.learner import (
    Concept, LearnerError, LearnerStore, RustCompileOracle,
)


class FakeOracle:
    """Deterministic oracle: correct iff submission == expected."""
    def __init__(self, expected: str = "correct"):
        self.expected = expected
        self.calls = 0
    def check(self, submission: str, context: str = "") -> tuple[bool, str]:
        self.calls += 1
        if submission == self.expected:
            return True, ""
        return False, "wrong solution"


def test_register_and_get(tmp_path: Path):
    store = LearnerStore(tmp_path)
    c = store.register_concept("rust_ownership", "Rust ownership")
    assert c.mastery == 0.0
    assert store.get_concept("rust_ownership").name == "Rust ownership"
    with pytest.raises(LearnerError, match="already registered"):
        store.register_concept("rust_ownership", "dup")


def test_register_validation(tmp_path: Path):
    store = LearnerStore(tmp_path)
    with pytest.raises(LearnerError):
        store.register_concept("  ", "name")
    with pytest.raises(LearnerError):
        store.register_concept("id", "  ")


def test_mastery_tracking_and_spaced_interval(tmp_path: Path):
    store = LearnerStore(tmp_path)
    store.register_concept("concept_a", "Concept A")
    store.register_concept("concept_b", "Concept B")
    t0 = 1_000_000.0
    store.record_attempt("concept_a", True, now=t0)
    c = store.record_attempt("concept_a", True, now=t0 + 1)
    assert c.attempts == 2 and c.mastery == 1.0
    assert c.streak == 2
    assert c.next_review_at == (t0 + 1) + 2 * 86400  # interval doubled to 2 days
    # a wrong answer resets streak and interval
    d = store.record_attempt("concept_a", False, now=t0 + 5)
    assert d.streak == 0 and d.interval_days == 1.0
    assert d.mastery == 2 / 3


def test_feedback_driven_next_activity(tmp_path: Path):
    store = LearnerStore(tmp_path)
    store.register_concept("weak", "Weak concept")
    store.register_concept("strong", "Strong concept")
    t0 = 100.0
    store.record_attempt("strong", True, now=t0)  # interval -> 1 day, not due soon
    # weak is fresh (due immediately); strong is due after 1 day
    next_c = store.next_activity(now=t0 + 1)
    assert next_c.concept_id == "weak"
    # nothing due before weak resets if weak now also scheduled away
    store.record_attempt("weak", True, now=t0 + 1)
    assert store.due_count(now=t0 + 1) == 0
    assert store.next_activity(now=t0 + 1) is None


def test_cited_lessons(tmp_path: Path):
    store = LearnerStore(tmp_path)
    store.register_concept("concept_a", "Concept A")
    t0 = 100.0
    store.record_attempt("concept_a", True, lesson_id="lesson-1", now=t0)
    store.record_attempt("concept_a", True, lesson_id="lesson-2", now=t0 + 1)
    assert store.lessons("concept_a") == ["lesson-1", "lesson-2"]


def test_progress_view(tmp_path: Path):
    store = LearnerStore(tmp_path)
    store.register_concept("a", "A")
    store.register_concept("b", "B")
    t0 = 100.0
    store.record_attempt("a", True, now=t0)
    store.record_attempt("b", False, now=t0)
    store.record_attempt("b", True, now=t0 + 1)
    prog = store.progress()
    assert prog["overall_mastery"] == round(2 / 3, 3)
    assert len(prog["concepts"]) == 2
    by_id = {c["concept_id"]: c for c in prog["concepts"]}
    assert by_id["a"]["mastery"] == 1.0
    assert by_id["b"]["mastery"] == 0.5


def test_assess_wrong_solution_rejected(tmp_path: Path):
    store = LearnerStore(tmp_path)
    store.register_concept("concept_a", "Concept A")
    oracle = FakeOracle(expected="correct")
    attempt = store.assess("concept_a", "wrong", oracle)
    assert attempt.correct is False
    assert "wrong solution" in attempt.feedback
    assert store.get_concept("concept_a").mastery == 0.0


def test_assess_correct_solution_accepted(tmp_path: Path):
    store = LearnerStore(tmp_path)
    store.register_concept("concept_a", "Concept A")
    attempt = store.assess("concept_a", "correct", FakeOracle(expected="correct"),
                           lesson_id="lesson-9")
    assert attempt.correct is True
    assert store.get_concept("concept_a").mastery == 1.0
    assert store.lessons("concept_a") == ["lesson-9"]


def test_reset_makes_ctf_solvable_again(tmp_path: Path):
    store = LearnerStore(tmp_path)
    store.register_concept("ctf_01", "CTF level 1")
    t0 = 100.0
    store.record_attempt("ctf_01", True, now=t0)
    assert store.get_concept("ctf_01").mastery == 1.0
    store.reset("ctf_01")
    c = store.get_concept("ctf_01")
    assert c.mastery == 0.0 and c.attempts == 0 and c.interval_days == 1.0
    assert c.next_review_at == 0.0  # due immediately again


def test_remove_deletes_concept_and_attempts(tmp_path: Path):
    store = LearnerStore(tmp_path)
    store.register_concept("gone", "Gone")
    store.record_attempt("gone", True)
    store.remove("gone")
    assert store.get_concept("gone") is None
    assert store.attempts("gone") == []
    assert store.lessons("gone") == []


def test_rust_oracle_missing_compiler(tmp_path: Path):
    oracle = RustCompileOracle(rustc="definitely-not-a-rustc-binary-xyz")
    # compiler absent -> cleanly reports failure, never crashes
    ok, feedback = oracle.check("fn main() {}")
    assert ok is False
    assert "not available" in feedback


def test_rust_oracle_empty_submission():
    oracle = RustCompileOracle()
    ok, feedback = oracle.check("   ")
    assert ok is False
    assert feedback == "empty submission"


def test_corrupt_state_fails_closed(tmp_path: Path):
    store = LearnerStore(tmp_path)
    store.register_concept("a", "A")
    store.concepts_path.write_text("{ broken", encoding="utf-8")
    with pytest.raises(LearnerError, match="corrupt"):
        store.concepts()
