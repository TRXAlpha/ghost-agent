"""TG4: deadline-aware review scheduling."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ghost_agent.learner import LearnerError, LearnerStore


def _make_due(store: LearnerStore, concept_id: str, mastery_attempts: tuple[int, int] = (0, 0),
              now: float = 1000.0) -> None:
    """Register a concept due at `now` with the given (correct, attempts)."""
    store.register_concept(concept_id, concept_id)
    correct, attempts = mastery_attempts
    # Simulate the interval growth to know where the last attempt must sit so
    # next_review_at (last_ts + interval days) lands at or before `now`.
    streak, interval = 0, 1.0
    for _ in range(attempts):
        if _ < correct:
            streak += 1
            interval = min(interval * 2.0 if streak >= 2 else interval,
                           30.0) if streak >= 2 else interval
        else:
            streak, interval = 0, 1.0
    last_ts = now - interval * 86400.0 - 1.0
    ts = last_ts
    for i in range(attempts):
        store.record_attempt(concept_id, i < correct, now=ts)
        ts += 0.001


def test_set_deadline_persists(tmp_path: Path):
    store = LearnerStore(tmp_path)
    record = store.set_deadline(["a", "b"], due_at=999999.0, label="midterm")
    assert record["concept_ids"] == ["a", "b"]
    data = json.loads((tmp_path / ".ghost" / "learner" / "deadlines.json").read_text())
    assert data[0]["label"] == "midterm"


def test_set_deadline_replaces_same_label(tmp_path: Path):
    store = LearnerStore(tmp_path)
    store.set_deadline(["a"], due_at=1000.0, label="midterm")
    store.set_deadline(["b"], due_at=2000.0, label="midterm")
    data = json.loads((tmp_path / ".ghost" / "learner" / "deadlines.json").read_text())
    assert len(data) == 1 and data[0]["concept_ids"] == ["b"]


def test_set_deadline_validation(tmp_path: Path):
    store = LearnerStore(tmp_path)
    with pytest.raises(LearnerError):
        store.set_deadline(["a"], due_at=1000.0, label="  ")
    with pytest.raises(LearnerError):
        store.set_deadline(["a"], due_at=-1.0, label="x")
    with pytest.raises(LearnerError):
        store.set_deadline([], due_at=1000.0, label="x")


def test_nearer_deadline_chosen_first(tmp_path: Path):
    store = LearnerStore(tmp_path)
    now = 1000.0
    # both due; a has lower mastery so would win without deadlines
    _make_due(store, "far", (0, 2), now=now)
    _make_due(store, "near", (1, 2), now=now)
    store.set_deadline(["near"], due_at=now + 86400, label="soon")
    store.set_deadline(["far"], due_at=now + 5 * 86400, label="later")
    assert store.next_activity(now=now).concept_id == "near"


def test_past_deadlines_ignored(tmp_path: Path):
    store = LearnerStore(tmp_path)
    now = 1_000_000_000.0
    _make_due(store, "a", (0, 2), now=now)
    _make_due(store, "b", (1, 2), now=now)
    store.set_deadline(["b"], due_at=now - 86400, label="expired")
    assert store.next_activity(now=now).concept_id == "a"


def test_far_future_deadline_outside_window_ignored(tmp_path: Path):
    store = LearnerStore(tmp_path)
    now = 1000.0
    _make_due(store, "a", (0, 2), now=now)
    _make_due(store, "b", (1, 2), now=now)
    store.set_deadline(["b"], due_at=now + 20 * 86400, label="finals")
    assert store.next_activity(now=now).concept_id == "a"


def test_no_deadlines_ordering_unchanged(tmp_path: Path):
    store = LearnerStore(tmp_path)
    now = 1000.0
    _make_due(store, "b", (1, 2), now=now)
    _make_due(store, "a", (1, 2), now=now)
    # equal mastery: lowest concept_id first, as before TG4
    assert store.next_activity(now=now).concept_id == "a"
