from pathlib import Path

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
