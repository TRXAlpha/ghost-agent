"""Container-isolated self-repair verification (R07/TC1).

Drives the REAL CLI path (``_cmd_self_repair`` with a fake argparse Namespace)
against a real SelfRepairManager, with only ``run_in_isolation`` /
``docker_available`` monkeypatched — Docker is never invoked.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

import ghost_agent.cli as cli
import ghost_agent.openjarvis_bridge.isolated_execution as isolated_execution
from ghost_agent.self_repair import SelfRepairManager


def _make_proposal(tmp_path: Path) -> tuple[SelfRepairManager, str]:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "candidate.py").write_text("value = 1\n", encoding="utf-8")
    manager = SelfRepairManager(tmp_path)
    proposal = manager.propose("Fix parser", sandbox)
    return manager, proposal.proposal_id


def _run_cli_verify(root: Path, proposal_id: str, isolation: str) -> str:
    """Invoke the real verify branch of _cmd_self_repair; return its stdout."""
    args = argparse.Namespace(
        action="verify", value=proposal_id, command=f'"{sys.executable}" -c ""',
        isolation=isolation, sandbox="", goal_id="",
    )
    captured = io.StringIO()
    cwd = Path.cwd()
    try:
        os.chdir(root)
        with redirect_stdout(captured):
            cli._cmd_self_repair(args)
    finally:
        os.chdir(cwd)
    return captured.getvalue()


def _patch_unavailable(monkeypatch) -> None:
    def _run(sandbox_path, command, env=None, timeout=30.0, input_data=None,
             artifacts_dir=None):
        return {"available": False, "error": "Docker daemon unavailable.",
                "error_type": "docker_unavailable", "isolated": False}

    monkeypatch.setattr(isolated_execution, "run_in_isolation", _run)


def _patch_available(monkeypatch) -> None:
    calls: list[dict] = []

    def _run(sandbox_path, command, env=None, timeout=30.0, input_data=None,
             artifacts_dir=None):
        calls.append({"command": list(command), "timeout": timeout})
        return {"available": True, "returncode": 0, "output": "ok",
                "error": False, "error_type": None, "isolated": True}

    monkeypatch.setattr(isolated_execution, "run_in_isolation", _run)
    return calls


def _stored_evidence(root: Path, proposal_id: str) -> dict:
    records = json.loads(
        (root / ".ghost" / "self-repair.json").read_text(encoding="utf-8"))
    return next(r for r in records if r["proposal_id"] == proposal_id)


def test_auto_with_unavailable_isolation_falls_back_to_host(tmp_path, monkeypatch):
    _patch_unavailable(monkeypatch)
    manager, proposal_id = _make_proposal(tmp_path)
    out = _run_cli_verify(tmp_path, proposal_id, "auto")
    assert '"status": "verified"' in out
    evidence = _stored_evidence(tmp_path, proposal_id)["regression_results"][0]
    assert evidence["runner"] == "host"
    assert evidence["isolation_unavailable"] == "docker_unavailable"


def test_container_with_unavailable_isolation_is_refused(tmp_path, monkeypatch):
    _patch_unavailable(monkeypatch)
    manager, proposal_id = _make_proposal(tmp_path)
    out = _run_cli_verify(tmp_path, proposal_id, "container")
    assert "Self-repair refused: container isolation unavailable" in out
    records = json.loads(
        (tmp_path / ".ghost" / "self-repair.json").read_text(encoding="utf-8"))
    item = next(r for r in records if r["proposal_id"] == proposal_id)
    assert item["regression_results"] == [] and item["benchmark_results"] == []


def test_container_available_runs_in_container_and_records_runner(tmp_path, monkeypatch):
    calls = _patch_available(monkeypatch)
    manager, proposal_id = _make_proposal(tmp_path)
    out = _run_cli_verify(tmp_path, proposal_id, "container")
    assert '"status": "verified"' in out
    evidence = _stored_evidence(tmp_path, proposal_id)["regression_results"][0]
    assert evidence["runner"] == "container"
    assert "isolation_unavailable" not in evidence
    # The container got a plain argv list and the evaluator deadline.
    assert calls[0]["command"][0] == sys.executable
    assert calls[0]["timeout"] == 60.0


def test_host_isolation_records_host_runner(tmp_path, monkeypatch):
    manager, proposal_id = _make_proposal(tmp_path)

    def _forbidden(*a, **k):  # host mode must never ask Docker anything
        raise AssertionError("run_in_isolation called under --isolation host")

    monkeypatch.setattr(isolated_execution, "run_in_isolation", _forbidden)
    out = _run_cli_verify(tmp_path, proposal_id, "host")
    assert '"status": "verified"' in out
    evidence = _stored_evidence(tmp_path, proposal_id)["regression_results"][0]
    assert evidence["runner"] == "host"
    assert "isolation_unavailable" not in evidence
