from ghost_agent.checkins import LocalCheckIns
from ghost_agent.control_plane import GhostSDK


def test_unconfigured_snapshot_does_not_enable_schedule(tmp_path):
    sdk = GhostSDK(tmp_path)
    assert sdk.snapshot()["check_in"] == {"status": "not_configured", "schedule": None}
    assert not (tmp_path / ".ghost" / "checkins.json").exists()
    assert sdk.attention.pending() == []


def test_configured_snapshot_preserves_schedule_without_ticking(tmp_path):
    manager = LocalCheckIns(tmp_path)
    schedule = manager.configure(60, "Review goals", (22, 8))
    before = manager.path.read_bytes()
    sdk = GhostSDK(tmp_path)
    assert sdk.snapshot()["check_in"] == {"status": "enabled", "schedule": schedule.__dict__}
    assert manager.path.read_bytes() == before
    assert sdk.attention.pending() == []
    manager.disable()
    assert sdk.check_in_status()["status"] == "disabled"


def test_corrupt_schedule_does_not_break_snapshot(tmp_path):
    sdk = GhostSDK(tmp_path)
    path = tmp_path / ".ghost" / "checkins.json"
    path.write_text("{broken")
    assert sdk.snapshot()["check_in"] == {"status": "invalid", "schedule": None}
    assert path.read_text() == "{broken"
