"""TG3: exercise generation linked to concepts (scripted chat only)."""
import json

import pytest

from ghost_agent.study import ExerciseGenerator, StudyError
from ghost_agent.learner import LearnerStore
from ghost_agent.knowledge_base import OwnerKnowledgeBase

MODEL_JSON = json.dumps({"exercises": [
    {"prompt": "What does moving a value do?",
     "hints": ["Think of ownership", "The old binding is gone"],
     "answer_kind": "exact", "expected": "It moves ownership"},
    {"prompt": "Name two keywords for borrowing.",
     "hints": ["One starts with &"],
     "answer_kind": "keywords", "expected": "borrow, reference"},
]})


def _seed_kb(tmp_path):
    kb = OwnerKnowledgeBase(tmp_path)
    doc = tmp_path / "refs"
    doc.mkdir()
    (doc / "ownership.md").write_text(
        "# Ownership\nMoving a value transfers ownership in Rust.\n")
    kb.ingest(doc)


def _scripted_chat(_model, _messages, **_kwargs):
    return MODEL_JSON


def test_generation_links_source_refs_and_hides_answer(tmp_path):
    _seed_kb(tmp_path)
    LearnerStore(tmp_path).register_concept("rust-ownership", "Rust ownership")
    gen = ExerciseGenerator(tmp_path, "scripted", chat_fn=_scripted_chat)
    exercises = gen.generate("rust-ownership", count=2)
    assert len(exercises) == 2
    for exercise in exercises:
        assert exercise.source_refs, "exercises must cite kbase chunks"
        record = json.loads((tmp_path / ".ghost" / "learner" / "exercises" /
                             f"{exercise.exercise_id}.json").read_text())
        assert "expected" not in json.dumps(record)
        assert exercise.answer_kind in ("exact", "rustc", "keywords")
        assert 1 <= len(exercise.hints) <= 3
        assert len(exercise.prompt) <= 600
    # answers live only in the separate answers directory
    answers = list((tmp_path / ".ghost" / "learner" / "answers").glob("*.json"))
    assert len(answers) == 2


def test_no_sources_yields_empty_refs_and_prompt_note(tmp_path, monkeypatch):
    LearnerStore(tmp_path).register_concept("empty-concept", "Nothing")
    seen_prompts = []

    def chat(_model, messages, **_kwargs):
        seen_prompts.append(messages[-1]["content"])
        return json.dumps({"exercises": [
            {"prompt": "q", "hints": ["h"], "answer_kind": "exact",
             "expected": "a"}]})

    gen = ExerciseGenerator(tmp_path, "scripted", chat_fn=chat)
    exercises = gen.generate("empty-concept", count=1)
    assert len(exercises) == 1
    assert exercises[0].source_refs == []
    assert "no owner sources available" in seen_prompts[0]


def test_exact_and_keyword_checks_record_attempts(tmp_path):
    _seed_kb(tmp_path)
    LearnerStore(tmp_path).register_concept("rust-ownership", "Rust ownership")
    gen = ExerciseGenerator(tmp_path, "scripted", chat_fn=_scripted_chat)
    exercises = gen.generate("rust-ownership", count=2)
    exact, keyword = exercises[0], exercises[1]

    correct, _ = gen.check(exact.exercise_id, "  it MOVES ownership \n")
    assert correct is True
    wrong, _ = gen.check(exact.exercise_id, "something else")
    assert wrong is False

    ok, _ = gen.check(keyword.exercise_id, "you borrow a value via reference")
    assert ok is True
    bad, feedback = gen.check(keyword.exercise_id, "nothing relevant")
    assert bad is False and "missing keywords" in feedback

    store = LearnerStore(tmp_path)
    concept = store.get_concept("rust-ownership")
    assert concept.attempts == 4 and concept.correct == 2
    # attempt cites the first source_ref (S04)
    attempts = store.attempts("rust-ownership")
    assert all(a.lesson_id == exact.source_refs[0] for a in attempts)


def test_next_hint_reveals_progressively(tmp_path):
    _seed_kb(tmp_path)
    LearnerStore(tmp_path).register_concept("rust-ownership", "Rust ownership")
    gen = ExerciseGenerator(tmp_path, "scripted", chat_fn=_scripted_chat)
    exercise = gen.generate("rust-ownership", count=1)[0]
    hints = [gen.next_hint(exercise.exercise_id, i) for i in range(3)]
    assert hints[0] == "Think of ownership"
    assert hints[1] == "The old binding is gone"
    assert hints[2] == "The old binding is gone"  # clamped to last hint


def test_invalid_model_json_yields_zero_exercises(tmp_path):
    _seed_kb(tmp_path)
    LearnerStore(tmp_path).register_concept("rust-ownership", "Rust ownership")
    gen = ExerciseGenerator(
        tmp_path, "scripted", chat_fn=lambda *_a, **_k: "not json at all")
    assert gen.generate("rust-ownership") == []

    def bad_json(_model, _messages, **_kwargs):
        return json.dumps({"exercises": [
            {"prompt": "", "hints": ["h"], "answer_kind": "exact",
             "expected": "x"},
            {"prompt": "ok", "hints": [], "answer_kind": "exact",
             "expected": "a"},
            {"prompt": "fine", "hints": ["h"], "answer_kind": "bogus",
             "expected": "a"},
        ]})

    gen2 = ExerciseGenerator(tmp_path, "scripted", chat_fn=bad_json)
    assert gen2.generate("rust-ownership") == []


def test_unknown_exercise_check_raises(tmp_path):
    gen = ExerciseGenerator(tmp_path, "scripted", chat_fn=lambda *_a, **_k: "")
    with pytest.raises(StudyError):
        gen.check("missing", "x")
