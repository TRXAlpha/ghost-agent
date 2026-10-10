"""Environment identity recorded in verification evidence (R07, R13)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import platform as platform_module

from ghost_agent.self_repair import SelfRepairManager, environment_identity

from tests.test_self_repair import evaluator_ok


def test_environment_identity_contains_expected_keys(tmp_path: Path) -> None:
    identity = environment_identity(tmp_path, "pytest -q")
    assert set(identity) == {
        "python", "executable", "platform", "evaluator_command",
        "supervisor_rev", "dependency_digests"}
    assert identity["python"] == sys.version.split()[0]
    assert identity["executable"] == sys.executable
    assert identity["platform"] == platform_module.platform()
    assert identity["evaluator_command"] == "pytest -q"
    assert identity["supervisor_rev"]
    assert identity["dependency_digests"] == {}


def test_dependency_digests_change_when_pyproject_changes(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("name = \"a\"\n", encoding="utf-8")
    before = environment_identity(tmp_path, "cmd")["dependency_digests"]
    (tmp_path / "pyproject.toml").write_text("name = \"b\"\n", encoding="utf-8")
    after = environment_identity(tmp_path, "cmd")["dependency_digests"]
    assert set(after) == {"pyproject.toml"}
    assert after["pyproject.toml"] != before["pyproject.toml"]


def test_verify_evidence_stores_environment(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "candidate.py").write_text("value = 1\n", encoding="utf-8")
    manager = SelfRepairManager(tmp_path)
    proposal = manager.propose("Fix parser", sandbox)
    result = manager.verify_from_evaluator(
        proposal.proposal_id, lambda candidate: {"returncode": 0, "output": "ok"})
    assert result.status == "verified"
    stored = json.loads((tmp_path / ".ghost" / "self-repair.json").read_text())
    evidence = stored[0]["regression_results"]
    environment = evidence[0]["environment"]
    assert set(environment) >= {
        "python", "executable", "platform", "evaluator_command",
        "supervisor_rev", "dependency_digests"}
