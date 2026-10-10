"""TM2: dashboard operational controls, token entry, and snapshot jobs."""
from __future__ import annotations

import re

from ghost_agent.control_plane import GhostSDK
from ghost_agent.dashboard import render_dashboard


def test_dashboard_contains_all_operational_sections_and_buttons() -> None:
    page = render_dashboard()
    for section_id in ("estop-status", "policy-modes", "notifications-list",
                       "held-count", "focus-current", "jobs-body", "why-output",
                       "token-input", "goals", "approvals", "health",
                       "provenance", "security"):
        assert section_id in page, section_id
    for label in ("Engage", "Disengage", "Ack", "Start focus", "Stop",
                  "Pause", "Resume", "Cancel", "Show"):
        assert label in page, label
    for endpoint in ("/v1/policy", "/v1/notifications/", "/v1/focus/start",
                     "/v1/focus/stop", "/v1/jobs/", "/v1/why/",
                     "/v1/emergency-stop/engage", "/v1/emergency-stop/disengage"):
        assert endpoint in page, endpoint


def test_dashboard_token_uses_session_storage_only() -> None:
    page = render_dashboard()
    assert "sessionStorage" in page
    assert "localStorage" not in page


def test_dashboard_never_assigns_innerhtml() -> None:
    page = render_dashboard()
    assert "innerHTML" not in page
    # Document the reason the requirement is met: values go through textContent.
    assert "textContent" in page


def test_snapshot_includes_scheduler_jobs(tmp_path) -> None:
    from ghost_agent.runtime import ScheduledJob

    sdk = GhostSDK(tmp_path)
    sdk.scheduler.enqueue(ScheduledJob(job_id="job-1", goal_id="demo"))
    snapshot = sdk.snapshot()
    assert "scheduler_jobs" in snapshot
    assert [job["job_id"] for job in snapshot["scheduler_jobs"]] == ["job-1"]
