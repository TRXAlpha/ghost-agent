"""OJ11/S06: focused tests for the OvernightRunner."""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ghost_agent.overnight import (
    OvernightRunner,
    OvernightCycleResult,
    in_quiet_hours,
    in_digest_window,
    DEFAULT_QUIET_HOURS,
    DEFAULT_DIGEST_WINDOW,
)


# -- helpers ----------------------------------------------------------------

def fake_tick(ok=True):
    def tick(*, auto_queue=True, require_idle=False, idle_after=900):
        return {"status": "queued" if ok else "error", "reason": ""}
    return tick


# -- window helpers ---------------------------------------------------------

class TestInQuietHours:
    def test_inside_window(self):
        assert in_quiet_hours(datetime(2026, 9, 28, 3, 0)) is True

    def test_outside_window(self):
        assert in_quiet_hours(datetime(2026, 9, 28, 12, 0)) is False

    def test_wrap_around(self):
        # quiet 23:00-05:00, current 02:00 -> inside
        assert in_quiet_hours(datetime(2026, 9, 28, 2, 0), (23, 5)) is True
        assert in_quiet_hours(datetime(2026, 9, 28, 12, 0), (23, 5)) is False

    def test_boundary_exclusive(self):
        # start is inclusive, end is exclusive
        assert in_quiet_hours(datetime(2026, 9, 28, 1, 0), (1, 7)) is True
        assert in_quiet_hours(datetime(2026, 9, 28, 7, 0), (1, 7)) is False


class TestInDigestWindow:
    def test_inside(self):
        assert in_digest_window(datetime(2026, 9, 28, 8, 0)) is True

    def test_outside(self):
        assert in_digest_window(datetime(2026, 9, 28, 12, 0)) is False


# -- OvernightRunner --------------------------------------------------------

class TestOvernightRunnerInit:
    def test_positive_interval_required(self):
        pytest.raises(ValueError, OvernightRunner, Path("/tmp"), "gemma4:26b", interval=0)
        pytest.raises(ValueError, OvernightRunner, Path("/tmp"), "gemma4:26b", interval=-1)

    def test_quiet_hours_tuple(self):
        r = OvernightRunner(Path("/tmp"), "gemma4:26b", quiet_hours=(22, 6))
        assert r.quiet_hours == (22, 6)


class TestCycleResultJsonl:
    def test_to_jsonl_all_fields(self, tmp_path):
        result = OvernightCycleResult(cycle=1, ts="2026-09-28T08:00:00",
                                      user_active=True, quiet_hours=True,
                                      emergency_stop=False, proactive="ok",
                                      proactive_reason="", worker="j1",
                                      worker_status="succeeded",
                                      errors=["info"])
        line = result.to_jsonl()
        parsed = json.loads(line)
        assert parsed["cycle"] == 1
        assert parsed["user_active"] is True
        assert parsed["quiet_hours"] is True
        assert parsed["proactive"] == "ok"
        assert parsed["worker"] == "j1"
        assert parsed["errors"] == ["info"]


class TestRunCycle:
    def test_cycle_increments(self, tmp_path):
        r = OvernightRunner(tmp_path, "gemma4:26b", interval=1,
                            proactive_tick=fake_tick())
        assert r._cycles == 0
        r.run_cycle(now=datetime(2026, 9, 28, 12, 0))
        assert r._cycles == 1

    def test_emergency_stop_exits_early(self, tmp_path):
        # engage emergency stop before cycle
        from ghost_agent.runtime import EmergencyStop
        EmergencyStop(tmp_path).engage("test")
        r = OvernightRunner(tmp_path, "gemma4:26b", proactive_tick=fake_tick())
        result = r.run_cycle(now=datetime(2026, 9, 28, 12, 0))
        assert result.emergency_stop is True
        assert result.proactive == "stopped"

    def test_quiet_hours_skip_proactive_run_worker(self, tmp_path):
        """During quiet hours the proactive tick is skipped; worker still runs if idle."""
        worker_called = False

        def fake_worker():
            nonlocal worker_called
            worker_called = True
            m = MagicMock()
            m.job_id = "job1"
            m.status = "succeeded"
            return m

        r = OvernightRunner(tmp_path, "gemma4:26b",
                            proactive_tick=fake_tick(),
                            worker_fn=fake_worker)
        with patch.object(r, "_user_active", return_value=False):
            result = r.run_cycle(now=datetime(2026, 9, 28, 3, 0))  # 03:00 = quiet
        assert result.quiet_hours is True
        assert result.proactive == "skipped"
        assert worker_called is True
        assert result.worker_status == "succeeded"

    def test_user_active_skips_proactive(self, tmp_path):
        r = OvernightRunner(tmp_path, "gemma4:26b",
                            proactive_tick=fake_tick(),
                            worker_fn=lambda: None)
        # 12:00 = not quiet hours, but override user_active via injected _user_active
        with patch.object(r, "_user_active", return_value=True):
            result = r.run_cycle(now=datetime(2026, 9, 28, 12, 0))
        assert result.user_active is True
        assert result.proactive == "skipped"

    def test_proactive_tick_called_when_idle_outside_quiet(self, tmp_path):
        called = False

        def tick():
            nonlocal called
            called = True
            return {"status": "ok"}

        r = OvernightRunner(tmp_path, "gemma4:26b",
                            proactive_tick=tick,
                            worker_fn=lambda: None)
        with patch.object(r, "_user_active", return_value=False):
            result = r.run_cycle(now=datetime(2026, 9, 28, 12, 0))
        assert called is True
        assert result.proactive == "ok"

    def test_proactive_error_is_caught(self, tmp_path):
        def bad_tick(*, auto_queue=True, require_idle=False, idle_after=900):
            raise RuntimeError("proactive exploded")

        r = OvernightRunner(tmp_path, "gemma4:26b",
                            proactive_tick=bad_tick,
                            worker_fn=lambda: None)
        with patch.object(r, "_user_active", return_value=False):
            result = r.run_cycle(now=datetime(2026, 9, 28, 12, 0))
        assert result.proactive == "error"
        assert any("proactive: RuntimeError" in e for e in result.errors)

    def test_jsonl_log_written(self, tmp_path):
        r = OvernightRunner(tmp_path, "gemma4:26b",
                            proactive_tick=fake_tick(),
                            worker_fn=lambda: None)
        r.run_cycle(now=datetime(2026, 9, 28, 12, 0))
        assert r.log_path.exists()
        lines = r.log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["cycle"] == 1


class TestDigest:
    def test_digest_not_written_outside_window(self, tmp_path):
        r = OvernightRunner(tmp_path, "gemma4:26b",
                            proactive_tick=fake_tick(),
                            worker_fn=lambda: None)
        r.run_cycle(now=datetime(2026, 9, 28, 12, 0))  # outside digest window
        digest_files = list(r.digest_dir.glob("*.md"))
        assert len(digest_files) == 0

    def test_digest_written_inside_window(self, tmp_path):
        r = OvernightRunner(tmp_path, "gemma4:26b",
                            proactive_tick=fake_tick(),
                            worker_fn=lambda: None)
        # write a log entry first so there's something to summarize
        r.log_path.parent.mkdir(parents=True, exist_ok=True)
        r.log_path.write_text(json.dumps({
            "ts": datetime(2026, 9, 28, 3, 0).isoformat(),
            "cycle": 1, "user_active": False, "quiet_hours": True,
            "emergency_stop": False, "proactive": "ok", "proactive_reason": "",
            "worker": "job1", "worker_status": "succeeded", "errors": [],
        }) + "\n", encoding="utf-8")
        path = r.write_digest_if_due(datetime(2026, 9, 28, 7, 30))
        assert path is not None
        digest_file = Path(path)
        assert digest_file.exists()
        assert "# Overnight digest" in digest_file.read_text(encoding="utf-8")
        assert "Cycles: 1" in digest_file.read_text(encoding="utf-8")

    def test_digest_only_once_per_day(self, tmp_path):
        r = OvernightRunner(tmp_path, "gemma4:26b")
        r.log_path.parent.mkdir(parents=True, exist_ok=True)
        r.log_path.write_text(json.dumps({
            "ts": datetime(2026, 9, 28, 3, 0).isoformat(),
            "cycle": 1, "user_active": False, "quiet_hours": True,
            "emergency_stop": False, "proactive": "ok", "proactive_reason": "",
            "worker": "", "worker_status": "", "errors": [],
        }) + "\n", encoding="utf-8")
        now = datetime(2026, 9, 28, 7, 30)
        path1 = r.write_digest_if_due(now)
        path2 = r.write_digest_if_due(now)
        assert path1 is not None
        assert path2 is None  # already written today

    def test_digest_skipped_when_no_entries(self, tmp_path):
        r = OvernightRunner(tmp_path, "gemma4:26b")
        path = r.write_digest_if_due(datetime(2026, 9, 28, 7, 30))
        assert path is None  # no log entries


class TestDigestOutcomesAndCoverage:
    def _runner(self, tmp_path):
        return OvernightRunner(tmp_path, "gemma4:26b",
                               proactive_tick=fake_tick(),
                               worker_fn=lambda: None)

    def _write_log(self, r, entries):
        r.log_path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for ts in entries:
            lines.append(json.dumps({
                "ts": ts, "cycle": len(lines) + 1, "user_active": False,
                "quiet_hours": True, "emergency_stop": False,
                "proactive": "ok", "proactive_reason": "",
                "worker": "", "worker_status": "", "errors": [],
            }))
        r.log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_outcomes_section_lists_completed_job(self, tmp_path):
        from ghost_agent.autonomy import OutcomeJob, OutcomeStore
        store = OutcomeStore(tmp_path)
        job = OutcomeJob(job_id="j1", goal="Build widget",
                         status="completed", result="tests_passed",
                         created_at=1.0, updated_at=1.0)
        store.save(job)
        r = self._runner(tmp_path)
        text = r._render_digest(datetime(2026, 9, 28, 7, 30), [])
        assert "## Outcomes" in text
        assert "Build widget" in text

    def test_coverage_reports_gap_longer_than_3x_interval(self, tmp_path):
        r = self._runner(tmp_path)
        r.interval = 60.0  # gaps > 180s are reported
        self._write_log(r, ["2026-09-28T02:00:00", "2026-09-28T03:00:00",
                            "2026-09-28T03:02:00"])
        text = r._render_digest(datetime(2026, 9, 28, 7, 30),
                                r._entries_since(datetime(2026, 9, 28, 1, 0)))
        assert "## Coverage" in text
        assert "no cycles from 02:00 to 03:00" in text
        assert "(PC asleep/offline or Ghost not running)" in text
        assert "First cycle: 02:00" in text
        assert "Last cycle: 03:02" in text
        assert "no cycles from 03:00 to 03:02" not in text

    def test_zero_cycles_says_ghost_did_not_run(self, tmp_path):
        r = self._runner(tmp_path)
        text = r._render_digest(datetime(2026, 9, 28, 7, 30), [])
        assert "Ghost did not run during this window." in text


class TestRunUntilIdle:
    def test_runs_one_cycle(self, tmp_path):
        r = OvernightRunner(tmp_path, "gemma4:26b",
                            proactive_tick=fake_tick(),
                            worker_fn=lambda: None)
        result = r.run_until_idle()
        assert result.cycle == 1
        assert r._cycles == 1


class TestRun:
    def test_max_cycles_stops(self, tmp_path):
        r = OvernightRunner(tmp_path, "gemma4:26b",
                            proactive_tick=fake_tick(),
                            worker_fn=lambda: None)
        # Drive the real run() logic without its infinite-loop risks: cycle
        # execution is a counted fake and EmergencyStop is patched inactive.
        call_count = {"n": 0}

        def counted_cycle(*, now=None):
            call_count["n"] += 1
            r._cycles += 1
            return MagicMock(emergency_stop=False, user_active=False)

        def bounded_cycle(*, now=None):
            if call_count["n"] >= 10:
                raise AssertionError("run() did not stop at max_cycles")
            return counted_cycle(now=now)

        with patch.object(r, "run_cycle", side_effect=bounded_cycle), \
             patch.object(r, "_sleep_between"):
            # Patch the module-level EmergencyStop used inside run()
            with patch("ghost_agent.overnight.EmergencyStop") as mock_es:
                mock_es.return_value.active.return_value = False
                cycles = r.run(max_cycles=3)
        assert cycles == 3
        assert call_count["n"] == 3