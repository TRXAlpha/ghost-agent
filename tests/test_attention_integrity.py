import pytest

from ghost_agent.runtime import AttentionManager, Notification
from ghost_agent.utils.storage import exclusive_lock


def test_notification_retry_does_not_duplicate_or_resurrect(tmp_path):
    manager = AttentionManager(tmp_path)
    manager.notify(Notification("normal", "Check in", notification_id="check-in"))
    manager.notify(Notification("normal", "Check in", notification_id="check-in"))
    assert len(manager.pending()) == 1
    manager.acknowledge("check-in")
    assert manager.acknowledge("check-in").status == "acknowledged"
    assert manager.notify(Notification("normal", "Check in", notification_id="check-in")).status == "acknowledged"
    assert manager.pending() == []
    with pytest.raises(ValueError, match="different content"):
        manager.notify(Notification("urgent", "Different", notification_id="check-in"))


@pytest.mark.parametrize("content", ["{broken", "{}", "[null]"])
def test_corrupt_notification_storage_is_preserved(tmp_path, content):
    manager = AttentionManager(tmp_path)
    manager.path.write_text(content)
    with pytest.raises(ValueError, match="corrupt"):
        manager.notify(Notification("normal", "Check in"))
    assert manager.path.read_text() == content


def test_notification_mutations_use_process_lock(tmp_path):
    manager = AttentionManager(tmp_path)
    with exclusive_lock(manager.path.with_suffix(".lock")):
        with pytest.raises(BlockingIOError):
            manager.notify(Notification("normal", "Check in"))
    assert manager.pending() == []


@pytest.mark.parametrize("hours", [(24, 7), (-1, 7), (True, 7), (1,), "22-7"])
def test_invalid_quiet_hours_refused(tmp_path, hours):
    with pytest.raises(ValueError, match="Quiet hours"):
        AttentionManager(tmp_path).deliverable(hours, now_hour=23)


def test_quiet_hours_end_boundary_and_urgent_bypass(tmp_path):
    manager = AttentionManager(tmp_path)
    manager.notify(Notification("normal", "Check in"))
    manager.notify(Notification("urgent", "Failure needs attention"))
    manager.notify(Notification("silent", "Background information"))
    assert [item.priority for item in manager.deliverable((22, 7), 22)] == ["urgent"]
    assert len(manager.deliverable((22, 7), 7)) == 2
