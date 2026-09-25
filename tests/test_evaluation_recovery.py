import json
import subprocess
import sys

import pytest

from ghost_agent.evaluation import EvaluationCase, EvaluationSuite
from ghost_agent.models.routing import ModelReputationStore


def test_durable_result_recovers_after_reputation_write_failure(tmp_path, monkeypatch):
    suite = EvaluationSuite(tmp_path)
    calls = []
    def evaluate(*args):
        calls.append(1)
        return {"verified_success": True}
    def fail(*args, **kwargs):
        raise OSError("injected interruption before reputation commit")
    monkeypatch.setattr(suite.reputation, "record", fail)
    with pytest.raises(OSError):
        suite.run(EvaluationCase("case", "testing", "Run tests", {}), "local", evaluate)
    assert json.loads(suite.path.read_text())["evaluation_id"]
    restarted = EvaluationSuite(tmp_path)
    assert restarted.reconcile() == 1
    assert restarted.reconcile() == 0
    assert len(calls) == 1
    assert json.loads(restarted.reputation.path.read_text())["local|testing"]["runs"] == 1


def test_recovery_after_commit_does_not_double_count(tmp_path, monkeypatch):
    suite = EvaluationSuite(tmp_path)
    record = suite.reputation.record
    def commit_then_fail(*args, **kwargs):
        record(*args, **kwargs)
        raise OSError("injected interruption after reputation commit")
    monkeypatch.setattr(suite.reputation, "record", commit_then_fail)
    with pytest.raises(OSError):
        suite.run(EvaluationCase("case", "testing", "Run tests", {}), "local", lambda *args: {"verified_success": True})
    restarted = EvaluationSuite(tmp_path)
    assert restarted.reconcile() == 0
    assert json.loads(restarted.reputation.path.read_text())["local|testing"]["successes"] == 1


def test_receipt_cannot_be_reused_with_changed_metrics_or_model(tmp_path):
    store = ModelReputationStore(tmp_path)
    identifier = "evaluation_" + "a" * 32
    assert store.record("local", "testing", True, record_id=identifier)
    assert not store.record("local", "testing", True, record_id=identifier)
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match="different metrics"):
        store.record("local", "testing", False, record_id=identifier)
    with pytest.raises(ValueError, match="different metrics"):
        store.record("other", "testing", True, record_id=identifier)
    assert store.path.read_bytes() == before


def test_legacy_evaluation_log_is_not_replayed_or_discarded(tmp_path):
    suite = EvaluationSuite(tmp_path)
    legacy = {"case_id": "old", "model": "local", "verified_success": True}
    suite.path.write_text(json.dumps(legacy) + "\n")
    assert suite.reconcile() == 0
    suite.run(EvaluationCase("new", "testing", "Run tests", {}), "local", lambda *args: {"verified_success": True})
    assert json.loads(suite.path.read_text().splitlines()[0]) == legacy


def test_truncated_log_is_preserved_and_not_extended(tmp_path):
    suite = EvaluationSuite(tmp_path)
    suite.path.write_text('{"case_id":')
    with pytest.raises(ValueError, match="corrupt"):
        suite.reconcile()
    with pytest.raises(ValueError, match="corrupt"):
        suite.run(EvaluationCase("new", "testing", "Run tests", {}), "local", lambda *args: {"verified_success": True})
    assert suite.path.read_text() == '{"case_id":'
    assert not suite.reputation.path.exists()


def test_duplicate_identical_record_is_applied_once(tmp_path):
    suite = EvaluationSuite(tmp_path)
    result = suite.run(EvaluationCase("case", "testing", "Run tests", {}), "local", lambda *args: {"verified_success": True})
    line = suite.path.read_text()
    suite.path.write_text(line + line)
    assert suite.reconcile() == 0
    assert result.evaluation_id


def test_process_exit_after_log_commit_is_recoverable(tmp_path):
    script = """
import os, sys
from pathlib import Path
from ghost_agent.evaluation import EvaluationCase, EvaluationSuite
suite = EvaluationSuite(Path(sys.argv[1]))
suite.reputation.record = lambda *args, **kwargs: os._exit(73)
suite.run(EvaluationCase('case', 'testing', 'Run tests', {}), 'local', lambda *args: {'verified_success': True})
"""
    child = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True, timeout=15)
    assert child.returncode == 73, child.stderr.decode(errors="replace")
    restarted = EvaluationSuite(tmp_path)
    assert restarted.reconcile() == 1
    assert restarted.reconcile() == 0
