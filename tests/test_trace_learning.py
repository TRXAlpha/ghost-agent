"""OJ15/S08: focused tests for trace-based learning (never auto-applies)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ghost_agent.trace_learning import (
    FailureSignature,
    ImprovementProposer,
    TraceAnalysis,
    TraceAnalyzer,
    classify_error_text,
    normalize_error_text,
    run_trace_learning,
)


def _sig(error_type: str = "generic_error", normalized: str = "x",
         subsystem: str = "worker", count: int = 1) -> FailureSignature:
    return FailureSignature(error_type=error_type, normalized_error=normalized,
                            subsystem=subsystem, count=count,
                            sample_error="sample", sample_job_id="job1")


def _analysis(*signatures: FailureSignature) -> TraceAnalysis:
    return TraceAnalysis(generated_at="2026-09-28T00:00:00",
                         scanned=sum(s.count for s in signatures),
                         failed=len(signatures),
                         top_signatures=list(signatures))


def _write_outcome(root: Path, job_id: str, *, status: str, result: str,
                   errors: list | None = None) -> None:
    d = root / ".ghost" / "outcomes" / job_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "outcome.json").write_text(json.dumps({
        "job_id": job_id, "status": status, "result": result,
        "errors": errors or [],
    }), encoding="utf-8")


# -- normalization ----------------------------------------------------------

class TestNormalizeErrorText:
    def test_numbers_collapse(self):
        assert normalize_error_text("read timeout=300") == "read timeout=N"
        assert normalize_error_text("read timeout=60") == normalize_error_text(
            "read timeout=300")

    def test_posix_paths_collapse(self):
        a = normalize_error_text("error in /c/Users/x/file.py: bad")
        b = normalize_error_text("error in /c/Users/y/file.py: bad")
        assert a == b
        assert "<path>" in a

    def test_windows_paths_collapse(self):
        a = normalize_error_text(r"failed at C:\Users\chris\one\two")
        b = normalize_error_text(r"failed at C:\Users\chris\other\dir")
        assert a == b
        assert "<path>" in a

    def test_hashes_collapse(self):
        assert normalize_error_text("commit deadbeefcafe missing") == \
            "commit <hash> missing"

    def test_urls_collapse(self):
        assert normalize_error_text("POST http://localhost:11434/api/chat") == \
            "POST <url>"


class TestClassifyErrorText:
    def test_ollama_timeout(self):
        assert classify_error_text(
            "worker: HTTPConnectionPool(host='localhost', port=11434): "
            "Read timed out. (read timeout=300)") == "ollama_read_timeout"

    def test_generic(self):
        assert classify_error_text("something odd happened") == "generic_error"


# -- analyzer ---------------------------------------------------------------

class TestTraceAnalyzer:
    def test_empty_dir_is_zero(self, tmp_path):
        analysis = TraceAnalyzer(tmp_path).analyze()
        assert analysis.scanned == 0
        assert analysis.top_signatures == []

    def test_recurring_signature_counts(self, tmp_path):
        _write_outcome(tmp_path, "j1", status="failed", result="",
                       errors=["worker: read timed out (timeout=300)"])
        _write_outcome(tmp_path, "j2", status="failed", result="",
                       errors=["worker: read timed out (timeout=60)"])
        analysis = TraceAnalyzer(tmp_path).analyze()
        assert analysis.scanned == 2
        assert analysis.failed == 2
        assert len(analysis.top_signatures) == 1
        assert analysis.top_signatures[0].count == 2

    def test_successes_do_not_count(self, tmp_path):
        _write_outcome(tmp_path, "ok1", status="completed", result="tests_passed")
        analysis = TraceAnalyzer(tmp_path).analyze()
        assert analysis.failed == 0
        assert analysis.top_signatures == []

    def test_result_only_signature(self, tmp_path):
        _write_outcome(tmp_path, "j1", status="needs_review",
                       result="iteration_limit")
        _write_outcome(tmp_path, "j2", status="needs_review",
                       result="iteration_limit")
        analysis = TraceAnalyzer(tmp_path).analyze()
        top = analysis.top_signatures[0]
        assert top.error_type == "result_iteration_limit"
        assert top.count == 2

    def test_corrupt_json_skipped(self, tmp_path):
        bad = tmp_path / ".ghost" / "outcomes" / "bad" / "outcome.json"
        bad.parent.mkdir(parents=True)
        bad.write_text("{not json", encoding="utf-8")
        analysis = TraceAnalyzer(tmp_path).analyze()
        assert analysis.scanned == 0


# -- proposer ---------------------------------------------------------------

class TestImprovementProposer:
    def test_below_threshold_no_action(self, tmp_path):
        analysis = _analysis(_sig(count=1))
        proposer = ImprovementProposer(tmp_path, min_count=2)
        assert proposer.propose_from(analysis) == []

    def test_capability_gap_registered(self, tmp_path):
        analysis = _analysis(_sig(error_type="result_iteration_limit",
                                  normalized="iteration_limit", count=3))
        actions = ImprovementProposer(tmp_path).propose_from(analysis)
        assert len(actions) == 1
        assert actions[0]["action"] == "capability_gap"
        # The gap is really in the on-disk registry
        data = json.loads(
            (tmp_path / ".ghost" / "capabilities.json").read_text())
        assert any(g.get("id") == actions[0]["gap_id"] for g in data["gaps"])

    def test_repair_class_reported_not_auto_applied(self, tmp_path):
        analysis = _analysis(_sig(error_type="ollama_read_timeout",
                                  normalized="read timeout=N", count=3))
        actions = ImprovementProposer(tmp_path).propose_from(analysis)
        # Repairable classes are flagged for orchestrator review; this module
        # never creates or executes a repair itself.
        assert all(a["action"] != "auto_apply" for a in actions)

    def test_no_auto_apply_apis_called(self, tmp_path):
        """Explicit no-auto-apply: proposer may only record_gap / report."""
        # Behaviorally: only capability_gap actions ever land on disk; no
        # self-repair proposals file may be created by proposing.
        analysis = _analysis(_sig(error_type="result_iteration_limit",
                                  normalized="iteration_limit", count=2))
        ImprovementProposer(tmp_path).propose_from(analysis)
        assert not (tmp_path / ".ghost" / "self-repair.json").exists()

    def test_min_count_validation(self, tmp_path):
        with pytest.raises(ValueError):
            ImprovementProposer(tmp_path, min_count=0)


# -- end-to-end -------------------------------------------------------------

class TestRunTraceLearning:
    def test_persists_last_report(self, tmp_path):
        _write_outcome(tmp_path, "j1", status="failed", result="",
                       errors=["worker: read timed out (timeout=5)"])
        _write_outcome(tmp_path, "j2", status="failed", result="",
                       errors=["worker: read timed out (timeout=9)"])
        summary = run_trace_learning(tmp_path)
        report = tmp_path / ".ghost" / "trace_learning" / "last_report.json"
        assert report.exists()
        data = json.loads(report.read_text(encoding="utf-8"))
        assert data["analysis"]["scanned"] == 2
        assert any(s["count"] == 2
                   for s in data["analysis"]["top_signatures"])

    def test_no_persist_option(self, tmp_path):
        summary = run_trace_learning(tmp_path, persist=False)
        assert summary["analysis"]["scanned"] == 0
        assert not (tmp_path / ".ghost" / "trace_learning").exists()
