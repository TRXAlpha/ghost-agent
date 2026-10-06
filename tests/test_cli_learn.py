"""Tests for ghost learn subcommands (OJ12)."""

from __future__ import annotations

import argparse
import time

from ghost_agent.cli import _cmd_learn
from ghost_agent.learner import LearnerStore


def test_cli_module_imports() -> None:
    import ghost_agent.cli
    assert hasattr(ghost_agent.cli, "main")


def test_learn_attempts_lists_every_attempt(tmp_path, monkeypatch, capsys) -> None:
    """All attempts up to limit are printed (no early return inside the loop)."""
    monkeypatch.chdir(tmp_path)
    learner = LearnerStore(tmp_path)
    learner.register_concept("rust-ownership", "Rust ownership rules")
    now = time.time()
    learner.record_attempt("rust-ownership", correct=True, now=now - 200)
    learner.record_attempt("rust-ownership", correct=False, now=now - 100)
    learner.record_attempt("rust-ownership", correct=True, now=now)

    _cmd_learn(argparse.Namespace(
        learn_action="attempts",
        concept_id="rust-ownership",
        limit=20))

    out = capsys.readouterr().out
    lines = [l for l in out.splitlines() if l.strip()]
    assert len(lines) == 3, f"Expected 3 attempt lines, got {len(lines)}: {lines}"
    assert any("correct" in l for l in lines)
    assert any("wrong" in l for l in lines)


def test_learn_attempts_respects_limit(tmp_path, monkeypatch, capsys) -> None:
    """Only limit=N attempts are shown."""
    monkeypatch.chdir(tmp_path)
    learner = LearnerStore(tmp_path)
    learner.register_concept("rust-ownership", "Rust ownership rules")
    now = time.time()
    for i in range(5):
        learner.record_attempt("rust-ownership", correct=i % 2 == 0, now=now - (5 - i) * 10)

    _cmd_learn(argparse.Namespace(
        learn_action="attempts",
        concept_id="rust-ownership",
        limit=2))

    out = capsys.readouterr().out
    lines = [l for l in out.splitlines() if l.strip()]
    assert len(lines) == 2, f"Expected 2 attempt lines (limit=2), got {len(lines)}: {lines}"


def test_learn_attempts_empty(tmp_path, monkeypatch, capsys) -> None:
    """No attempts prints the empty-state message."""
    monkeypatch.chdir(tmp_path)
    learner = LearnerStore(tmp_path)
    learner.register_concept("rust-ownership", "Rust ownership rules")

    _cmd_learn(argparse.Namespace(
        learn_action="attempts",
        concept_id="rust-ownership",
        limit=20))

    out = capsys.readouterr().out
    assert "No attempts recorded." in out


def test_learn_progress_reports_overall_mastery(tmp_path, monkeypatch, capsys) -> None:
    """progress command reports overall mastery percentage."""
    monkeypatch.chdir(tmp_path)
    learner = LearnerStore(tmp_path)
    learner.register_concept("rust-ownership", "Rust ownership rules")
    now = time.time()
    learner.record_attempt("rust-ownership", correct=True, now=now - 20)   # 1 correct
    learner.record_attempt("rust-ownership", correct=False, now=now)      # 1 wrong -> 50%

    _cmd_learn(argparse.Namespace(
        learn_action="progress",
        concept_id=None))

    out = capsys.readouterr().out
    assert "Overall mastery: 50.0%" in out, f"Output: {out}"


def test_learn_next_when_nothing_due(tmp_path, monkeypatch, capsys) -> None:
    """next returns the 'nothing due' message when no concept is due."""
    monkeypatch.chdir(tmp_path)
    learner = LearnerStore(tmp_path)
    # No concepts at all

    _cmd_learn(argparse.Namespace(
        learn_action="next",
        concept_id=None))

    out = capsys.readouterr().out
    assert "Nothing due for review right now." in out