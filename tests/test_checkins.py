import json

import pytest

from ghost_agent.checkins import LocalCheckIns
from ghost_agent.runtime import AttentionManager, EmergencyStop


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setattr("ghost_agent.checkins.time.time", lambda: 1000.0)
    manager = LocalCheckIns(tmp_path)
    manager.configure(60, "Review goals", (0, 0))
    monkeypatch.setattr("ghost_agent.checkins.time.time", lambda: 1060.0)
    return manager


def test_unconfigured_is_read_only(tmp_path):
    manager = LocalCheckIns(tmp_path)
    assert manager.status() is None
    assert manager.tick() is None
    assert not (tmp_path / ".ghost").exists()


def test_due_and_pending_suppression(configured, monkeypatch):
    first = configured.tick()
    assert first.message == "Review goals"
    assert configured.tick() is None
    monkeypatch.setattr("ghost_agent.checkins.time.time", lambda: 1200.0)
    assert configured.tick() is None
    attention = AttentionManager(configured.root)
    attention.acknowledge(first.notification_id)
    second = configured.tick()
    assert second.notification_id != first.notification_id
    assert len(attention.pending()) == 1


def test_disable_preserves_existing_attention(configured):
    configured.tick()
    configured.disable()
    assert configured.status().enabled is False
    assert configured.tick() is None
    assert len(AttentionManager(configured.root).pending()) == 1


def test_emergency_stop(configured):
    EmergencyStop(configured.root).engage("test stop")
    assert configured.tick() is None
    assert not (configured.root / ".ghost" / "notifications.json").exists()


@pytest.mark.parametrize("hour,expected", [(21, True), (22, False), (7, False), (8, True)])
def test_quiet_hours(configured, monkeypatch, hour, expected):
    from types import SimpleNamespace
    data = json.loads(configured.path.read_text())
    data["quiet_hours"] = [22, 8]
    configured.path.write_text(json.dumps(data))
    monkeypatch.setattr("ghost_agent.checkins.time.localtime", lambda _: SimpleNamespace(tm_hour=hour))
    assert (configured.tick() is not None) is expected


@pytest.mark.parametrize("raw", ["{", "[]", '{"enabled": true}'])
def test_corruption_preserved(configured, raw):
    configured.path.write_text(raw)
    for action in (configured.tick, configured.disable, lambda: configured.configure(60, "New")):
        with pytest.raises(ValueError, match="corrupt"):
            action()
        assert configured.path.read_text() == raw


@pytest.mark.parametrize("interval", [True, 0, 59, 31536001, float("nan")])
def test_invalid_interval(tmp_path, interval):
    with pytest.raises(ValueError):
        LocalCheckIns(tmp_path).configure(interval, "Review goals")
    assert not (tmp_path / ".ghost").exists()


def test_notification_commit_schedule_failure_recovery(configured, monkeypatch):
    from ghost_agent.utils.storage import atomic_json
    def fail(*args):
        raise OSError("simulated schedule commit failure")
    monkeypatch.setattr("ghost_agent.checkins.atomic_json", fail)
    with pytest.raises(OSError):
        configured.tick()
    attention = AttentionManager(configured.root)
    note = attention.pending()[0]
    attention.acknowledge(note.notification_id)
    monkeypatch.setattr("ghost_agent.checkins.atomic_json", atomic_json)
    recovered = configured.tick()
    assert recovered.notification_id == note.notification_id
    assert recovered.status == "acknowledged"
    assert len(attention.pending(include_acknowledged=True)) == 1
    assert configured.status().next_due == 1120.0


def test_not_due(configured, monkeypatch):
    monkeypatch.setattr("ghost_agent.checkins.time.time", lambda: 1059.0)
    assert configured.tick() is None


def test_busy_lock(configured):
    from ghost_agent.utils.storage import exclusive_lock
    with exclusive_lock(configured.path.with_suffix(".lock")):
        with pytest.raises(BlockingIOError):
            configured.tick()
