"""TK2: practice challenges with protected solutions and reset."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ghost_agent.challenges import ChallengeError, ChallengeStore
from ghost_agent.learner import FormatStats, LearnerStore

STARTER = {"solution.py": "def add(a, b):\n    return a - b\n"}
SOLUTION = {"solution.py": "def add(a, b):\n    return a + b\n"}
TESTS = """from solution import add

def test_add():
    assert add(2, 3) == 5
"""
ORACLE = {"kind": "pytest", "tests": TESTS}


def _spec(tmp_path: Path) -> dict:
    return {
        "concept_ids": ["py-testing"],
        "prompt": "Fix add() so it adds.",
        "starter": STARTER,
        "oracle": ORACLE,
        "solution": SOLUTION,
    }


def test_create_refuses_unsolvable_spec(tmp_path: Path):
    store = ChallengeStore(tmp_path)
    bad = _spec(tmp_path)
    bad["solution"] = {"solution.py": "def add(a, b):\n    return a * b\n"}
    with pytest.raises(ChallengeError, match="oracle fails on the solution"):
        store.create(**{k: bad[k] for k in
                        ("concept_ids", "prompt", "starter", "oracle",
                         "solution")})


def test_create_refuses_starter_already_passes(tmp_path: Path):
    store = ChallengeStore(tmp_path)
    passing = _spec(tmp_path)
    passing["starter"] = SOLUTION
    with pytest.raises(ChallengeError, match="starter already passes"):
        store.create(**{k: passing[k] for k in
                        ("concept_ids", "prompt", "starter", "oracle",
                         "solution")})


def test_create_check_reset_lifecycle(tmp_path: Path):
    LearnerStore(tmp_path).register_concept("py-testing", "Python testing")
    store = ChallengeStore(tmp_path)
    cid = store.create(**{k: _spec(tmp_path)[k] for k in
                          ("concept_ids", "prompt", "starter", "oracle",
                           "solution")})
    # learner dir has prompt + starter, never oracle/solution
    learner = tmp_path / ".ghost" / "learner" / "challenges" / cid
    assert (learner / "README.md").exists()
    assert (learner / "starter" / "solution.py").exists()
    for path in learner.rglob("*"):
        assert "oracle" not in path.name and "solution_" not in path.name

    correct, _ = store.check(cid, "py-testing")
    assert not correct, "starter must fail the oracle (negative control)"

    # copy the solution in and check again
    sol = (tmp_path / ".ghost" / "learner" / "protected" / cid
           / "solution" / "solution.py").read_text(encoding="utf-8")
    (learner / "starter" / "solution.py").write_text(sol, encoding="utf-8")
    correct, feedback = store.check(cid, "py-testing")
    assert correct
    assert "passed" in feedback or "1 passed" in feedback

    # reset restores the original starter
    store.reset(cid)
    restored = (learner / "starter" / "solution.py").read_text(encoding="utf-8")
    assert restored == STARTER["solution.py"]
    correct, _ = store.check(cid, "py-testing")
    assert not correct


def test_list_and_unknown_challenge(tmp_path: Path):
    store = ChallengeStore(tmp_path)
    assert store.list() == []
    cid = store.create(**{k: _spec(tmp_path)[k] for k in
                          ("concept_ids", "prompt", "starter", "oracle",
                           "solution")})
    listed = store.list()
    assert [item["challenge_id"] for item in listed] == [cid]
    with pytest.raises(ChallengeError, match="Unknown challenge"):
        store.check("chl_missing", "")
    with pytest.raises(ChallengeError, match="Unknown challenge"):
        store.reset("chl_missing")


def test_challenge_format_counters_increment(tmp_path: Path):
    LearnerStore(tmp_path).register_concept("py-testing", "Python testing")
    store = ChallengeStore(tmp_path)
    cid = store.create(**{k: _spec(tmp_path)[k] for k in
                          ("concept_ids", "prompt", "starter", "oracle",
                           "solution")})
    stats = FormatStats(tmp_path).stats()
    assert stats["challenge"]["generated"] == 1
    store.check(cid, "py-testing")
    stats = FormatStats(tmp_path).stats()
    assert stats["challenge"]["attempted"] == 1
    assert stats["challenge"]["correct"] == 0


def test_invalid_spec_rejected(tmp_path: Path):
    store = ChallengeStore(tmp_path)
    with pytest.raises(ChallengeError):
        store.create(["c"], "", {"a.py": "x"},
                     {"kind": "bogus", "tests": "t"}, {"a.py": "y"})
    with pytest.raises(ChallengeError, match="starter files required"):
        store.create(["c"], "prompt", {}, ORACLE, SOLUTION)


def test_rustc_oracle_path_invokes_rustcompileoracle(tmp_path: Path):
    """The rustc kind routes through RustCompileOracle with the main.rs file."""
    import ghost_agent.challenges as ch

    calls = {}

    class FakeOracle:
        def __init__(self, plan_root=None):
            calls["plan_root"] = plan_root

        def check(self, submission, context=""):
            calls["submission"] = submission
            return True, "compiles"

    store = ChallengeStore(tmp_path)
    original = ch.RustCompileOracle
    ch.RustCompileOracle = FakeOracle
    try:
        ok, feedback = store._run_oracle(
            "rustc", "unused tests field",
            {"main.rs": "fn main() {}"})
    finally:
        ch.RustCompileOracle = original
    assert ok
    assert feedback == "compiles"
    assert calls["submission"] == "fn main() {}"


def test_rustc_oracle_requires_rs_source(tmp_path: Path):
    store = ChallengeStore(tmp_path)
    ok, feedback = store._run_oracle("rustc", "t", {"x.txt": "no rust here"})
    assert not ok
    assert "no Rust source" in feedback
