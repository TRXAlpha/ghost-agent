from pathlib import Path
import json

from ghost_agent.approvals import ApprovalStore
from ghost_agent.control_plane import GhostSDK


def test_control_plane_snapshot_aggregates_goals_events_and_approvals(tmp_path: Path) -> None:
    sdk = GhostSDK(tmp_path)
    goal = sdk.create_goal("Build dashboard")
    sdk.emit("goal.created", {"goal_id": goal["goal_id"]})
    sdk.register_device("desktop", "Precision", "computer", ["inference.run"])
    ApprovalStore(tmp_path).request("filesystem.write", "Promote file", {}, "medium", "restore backup")

    snapshot = sdk.snapshot()

    assert snapshot["goals"][0]["goal_id"] == goal["goal_id"]
    assert snapshot["recent_events"][0]["kind"] == "goal.created"
    assert snapshot["pending_approvals"][0]["capability"] == "filesystem.write"
    assert snapshot["devices"][0]["device_id"] == "desktop"
    assert snapshot["self_repair"] == []
    assert snapshot["security_events"] == []


def test_cli_control_plane_prints_json_snapshot(tmp_path, monkeypatch, capsys) -> None:
    import json as _json
    from ghost_agent.cli import _cmd_control_plane

    monkeypatch.chdir(tmp_path)
    _cmd_control_plane()
    snapshot = _json.loads(capsys.readouterr().out)
    assert "goals" in snapshot and "outcome_count" in snapshot


def test_cli_control_plane_views_print_valid_json(tmp_path, monkeypatch, capsys) -> None:
    import json as _json
    import argparse as _argparse
    from ghost_agent.cli import _cmd_control_plane

    monkeypatch.chdir(tmp_path)
    for view in ("snapshot", "goals", "opportunities", "resources",
                 "integrations", "notifications"):
        capsys.readouterr()
        args = _argparse.Namespace(view=view)
        _cmd_control_plane(args)
        parsed = _json.loads(capsys.readouterr().out)
        assert isinstance(parsed, (dict, list))


def _queued_proactive_job(tmp_path: Path):
    from ghost_agent.proactive import ProactiveEngine
    from ghost_agent.autonomy import OutcomeStore
    proposal = {"title": "Rust practice experiment",
                "goal": "Research how to turn my Rust lessons into a small practice game.",
                "rationale": "The project context mentions learning Rust.",
                "kind": "learning", "confidence": 0.9, "requires_user_input": False}
    engine = ProactiveEngine(tmp_path, "scripted-model",
                             chat_fn=lambda *args, **kwargs: json.dumps(proposal))
    result = engine.tick()
    assert result["status"] == "queued"
    return OutcomeStore(tmp_path).list()[0]


def test_explain_proactive_job_includes_opportunity_goal_intent_events(tmp_path: Path) -> None:
    sdk = GhostSDK(tmp_path)
    job = _queued_proactive_job(tmp_path)
    sdk.emit("job.queued", {"job_id": job.job_id, "goal_id": job.goal_id})

    detail = sdk.explain(job.job_id)

    assert detail is not None
    assert detail["job"]["job_id"] == job.job_id
    assert detail["job"]["status"] == job.status
    linked_id = job.acceptance_contract["opportunity_id"]
    assert detail["opportunity"]["opportunity_id"] == linked_id
    assert detail["goal"]["goal_id"] == job.goal_id
    assert detail["intent"]["goal_id"] == job.goal_id
    assert any(event["payload"].get("job_id") == job.job_id for event in detail["events"])


def test_explain_accepts_opportunity_id_directly(tmp_path: Path) -> None:
    from ghost_agent.proactive import OpportunityStore
    sdk = GhostSDK(tmp_path)
    job = _queued_proactive_job(tmp_path)
    opportunity_id = job.acceptance_contract["opportunity_id"]

    detail = sdk.explain(opportunity_id)

    assert detail is not None
    assert detail["opportunity"]["opportunity_id"] == opportunity_id
    assert detail["job"]["job_id"] == job.job_id
    assert OpportunityStore(tmp_path).find_any(opportunity_id) is not None


def test_explain_unknown_id_returns_none(tmp_path: Path) -> None:
    sdk = GhostSDK(tmp_path)
    assert sdk.explain("outcome_does_not_exist") is None


def test_cli_why_prints_summary_then_json_and_json_mode(tmp_path, monkeypatch, capsys) -> None:
    import json as _json
    from ghost_agent.cli import _cmd_why
    import argparse as _argparse

    monkeypatch.chdir(tmp_path)
    job = _queued_proactive_job(tmp_path)

    _cmd_why(_argparse.Namespace(item_id=job.job_id, json=False))
    out = capsys.readouterr().out
    assert f"Job {job.job_id}" in out
    detail = _json.loads(out[out.index("{"):])
    assert detail["job"]["job_id"] == job.job_id

    _cmd_why(_argparse.Namespace(item_id=job.job_id, json=True))
    json_out = capsys.readouterr().out
    assert f"Job {job.job_id}" not in json_out
    assert _json.loads(json_out)["job"]["job_id"] == job.job_id


def test_cli_why_unknown_id_prints_friendly_message(tmp_path, monkeypatch, capsys) -> None:
    from ghost_agent.cli import _cmd_why
    import argparse as _argparse

    monkeypatch.chdir(tmp_path)
    _cmd_why(_argparse.Namespace(item_id="nope", json=False))
    assert "No job or opportunity with id nope" in capsys.readouterr().out
