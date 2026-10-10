"""TK3: which teaching format works — FormatStats and recommendation."""
from __future__ import annotations

import json
from unittest import mock

from ghost_agent.learner import FormatStats, LearnerStore
from ghost_agent.study import ExerciseGenerator


def test_recommendation_by_success_rate(tmp_path):
    store = FormatStats(tmp_path)
    store.record("exercise", attempted=4, correct=1)   # 25%
    store.record("challenge", attempted=3, correct=3)  # 100%
    rec = store.recommend_format()
    assert rec["format"] == "challenge"
    assert "success rate" in rec["reason"]


def test_unused_format_detection(tmp_path):
    store = FormatStats(tmp_path)
    store.record("explanation", generated=5)
    rec = store.recommend_format()
    assert rec["format"] == "exercise"
    assert "no evidence yet" in rec["reason"]
    assert rec["unused"] == ["explanation"]
    assert rec["unused_reason"] == \
        "generated but never attempted: stop generating"


def test_no_data_default(tmp_path):
    rec = FormatStats(tmp_path).recommend_format()
    assert rec == {"format": "exercise", "reason": "no evidence yet"}


def test_counters_increment_through_real_study_path(tmp_path):
    model_json = json.dumps({"exercises": [
        {"prompt": "What does moving a value do?",
         "hints": ["ownership"], "answer_kind": "exact",
         "expected": "It moves ownership"}]})

    def scripted_chat(_model, _messages, **_kwargs):
        return model_json

    from ghost_agent.knowledge_base import OwnerKnowledgeBase
    doc = tmp_path / "refs"
    doc.mkdir()
    (doc / "ownership.md").write_text(
        "# Ownership\nMoving a value transfers ownership in Rust.\n")
    OwnerKnowledgeBase(tmp_path).ingest(doc)
    LearnerStore(tmp_path).register_concept("rust-ownership", "Rust ownership")

    gen = ExerciseGenerator(tmp_path, "scripted", chat_fn=scripted_chat)
    gen.generate("rust-ownership", count=1)
    exercises = gen.generate("rust-ownership", count=1)
    stats = FormatStats(tmp_path).stats()
    assert stats["exercise"]["generated"] == 2

    gen.check(exercises[0].exercise_id, "it moves ownership")
    stats = FormatStats(tmp_path).stats()
    assert stats["exercise"]["attempted"] == 1
    assert stats["exercise"]["correct"] == 1


def test_unknown_format_rejected(tmp_path):
    from ghost_agent.learner import LearnerError
    import pytest
    with pytest.raises(LearnerError):
        FormatStats(tmp_path).record("nonsense", generated=1)


def test_stats_defaults_and_survives_corrupt_file(tmp_path):
    store = FormatStats(tmp_path)
    assert store.stats()["exercise"] == {
        "generated": 0, "attempted": 0, "correct": 0, "last_used_at": 0.0}
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("not json", encoding="utf-8")
    assert store.stats()["challenge"]["attempted"] == 0
