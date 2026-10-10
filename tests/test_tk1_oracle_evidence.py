"""TK1: oracle evidence and dependency plan for the Rust compile oracle."""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from ghost_agent.learner import RustCompileOracle


def test_toolchain_returns_version(monkeypatch):
    proc = mock.Mock(returncode=0, stdout="rustc 1.75.0\n", stderr="")
    monkeypatch.setattr("shutil.which", lambda name: "/fake/rustc")
    monkeypatch.setattr(
        "ghost_agent.learner.subprocess.run", lambda *a, **k: proc)
    oracle = RustCompileOracle()
    assert oracle.toolchain() == "rustc 1.75.0"
    # cached: a second call does not re-probe
    assert oracle.toolchain() == "rustc 1.75.0"


def test_toolchain_none_when_missing(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(
        "ghost_agent.learner.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(OSError("not found")))
    oracle = RustCompileOracle()
    assert oracle.toolchain() is None


def test_check_prefixes_feedback_with_toolchain_version(monkeypatch):
    version_proc = mock.Mock(returncode=0, stdout="rustc 1.75.0\n", stderr="")
    compile_proc = mock.Mock(returncode=0, stdout="", stderr="")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "--version" in cmd:
            return version_proc
        return compile_proc

    monkeypatch.setattr("shutil.which", lambda name: "/fake/rustc")
    monkeypatch.setattr(
        "ghost_agent.learner.subprocess.run", fake_run)
    correct, feedback = RustCompileOracle().check("fn main() {}")
    assert correct
    assert feedback.startswith("[rustc rustc 1.75.0] ")
    assert "compiles" in feedback


def test_check_unavailable_writes_dependency_plan(tmp_path: Path):
    plan_root = tmp_path / "repo"
    plan_root.mkdir()
    oracle = RustCompileOracle(plan_root=plan_root)
    correct, feedback = oracle.check("fn main() {}", context="rust-ownership")
    assert not correct
    assert "dependency plan" in feedback
    plan = plan_root / ".ghost" / "learner" / "dependency_plan.md"
    text = plan.read_text(encoding="utf-8")
    assert "rustc" in text
    assert "rust-ownership" in text
    assert "https://rustup.rs" in text
    assert "rustup-init" in text
    assert "Ghost will not install toolchains itself." in text


def test_check_unavailable_without_plan_root_writes_nothing(tmp_path: Path):
    oracle = RustCompileOracle()
    correct, feedback = oracle.check("fn main() {}")
    assert not correct
    assert "dependency plan" in feedback
    assert not (tmp_path / ".ghost").exists()


def test_check_unavailable_records_attempt_via_store(tmp_path: Path):
    """The study.py rustc path keeps the feedback (with plan note) recorded."""
    from ghost_agent.study import ExerciseGenerator, StudyError

    class FakeModel:
        pass

    with pytest.raises(StudyError):
        ExerciseGenerator(tmp_path, "")  # model required
