"""TB4: API job controls pause/resume/cancel through the shared code path."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from ghost_agent.api_server import ControlPlaneAPI, create_server
from ghost_agent.control_plane import apply_job_action
from ghost_agent.runtime import ScheduledJob, Scheduler


def _queued_job(tmp_path: Path, job_id: str = "job-1") -> ScheduledJob:
    job = ScheduledJob(job_id=job_id, goal_id="scheduler-own")
    Scheduler(tmp_path).enqueue(job)
    return job


def test_pause_queued_job_via_api(tmp_path: Path) -> None:
    _queued_job(tmp_path)
    api = ControlPlaneAPI(tmp_path)
    status, body = api.dispatch("POST", "/v1/jobs/job-1/pause")
    assert status == 200
    assert body["status"] == "paused"
    assert Scheduler(tmp_path).list()[0].status == "paused"


def test_disallowed_transition_gives_409(tmp_path: Path) -> None:
    _queued_job(tmp_path)
    api = ControlPlaneAPI(tmp_path)
    api.dispatch("POST", "/v1/jobs/job-1/pause")
    status, body = api.dispatch("POST", "/v1/jobs/job-1/pause")
    assert status == 409
    assert "error" in body


def test_unknown_job_gives_404(tmp_path: Path) -> None:
    api = ControlPlaneAPI(tmp_path)
    status, body = api.dispatch("POST", "/v1/jobs/nope/cancel")
    assert status == 404
    assert body == {"error": "job not found"}


def test_cancel_via_api_and_resume(tmp_path: Path) -> None:
    _queued_job(tmp_path)
    api = ControlPlaneAPI(tmp_path)
    assert api.dispatch("POST", "/v1/jobs/job-1/cancel")[0] == 200
    assert Scheduler(tmp_path).list()[0].status == "cancelled"
    with pytest.raises(ValueError):
        apply_job_action(tmp_path, "job-1", "resume")


def test_unknown_action_is_404(tmp_path: Path) -> None:
    _queued_job(tmp_path)
    api = ControlPlaneAPI(tmp_path)
    status, body = api.dispatch("POST", "/v1/jobs/job-1/detonate")
    assert status == 404


def test_cli_still_pauses_via_shared_path(tmp_path: Path, monkeypatch, capsys) -> None:
    from ghost_agent.cli import _cmd_jobs
    monkeypatch.chdir(tmp_path)
    _queued_job(tmp_path)
    _cmd_jobs(__import__("argparse").Namespace(action="pause", job_id="job-1",
                                               recurrence="", goal_id="", priority=50,
                                               iterations=1))
    record = json.loads(capsys.readouterr().out)
    assert record["status"] == "paused"


def test_http_pause_end_to_end(tmp_path: Path) -> None:
    _queued_job(tmp_path)
    server = create_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        import http.client
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("POST", f"/v1/jobs/job-1/pause", body=b"{}",
                           headers={"Host": f"127.0.0.1:{server.server_port}",
                                    "Content-Type": "application/json", "Content-Length": "2"})
        response = connection.getresponse()
        body = json.loads(response.read())
        assert response.status == 200
        assert body["status"] == "paused"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
