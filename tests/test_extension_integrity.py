import json

import pytest

from ghost_agent.extensions import ExtensionManager
from ghost_agent.approvals import ApprovalStore
from ghost_agent.runtime import EmergencyStop
from ghost_agent.security import SecurityMonitor
from ghost_agent.utils.storage import exclusive_lock


@pytest.mark.parametrize("change", ["code", "new_file", "delete", "permissions", "manifest"])
def test_changed_package_cannot_reuse_approval(tmp_path, change):
    manager = ExtensionManager(tmp_path)
    manager.scaffold("demo", "test")
    manager.mark_tested("demo")
    approval = manager.request_enable("demo")
    ApprovalStore(tmp_path).decide(approval, True)
    directory = manager.directory("demo")
    if change == "code":
        (directory / "adapter" / "__init__.py").write_text("changed = True\n")
    elif change == "new_file":
        (directory / "extra.py").write_text("new = True\n")
    elif change == "delete":
        (directory / "tests" / "test_adapter.py").unlink()
    elif change == "permissions":
        (directory / "permissions.json").write_text('{"network": true}')
    else:
        path = directory / "manifest.json"
        data = json.loads(path.read_text())
        data["permissions"]["network"] = True
        path.write_text(json.dumps(data))
    with pytest.raises(PermissionError):
        manager.enable("demo", approval)
    assert manager.load("demo").status == "tested"


def test_scaffold_retry_preserves_user_implementation(tmp_path):
    manager = ExtensionManager(tmp_path)
    manager.scaffold("demo", "test")
    path = manager.directory("demo") / "adapter" / "__init__.py"
    path.write_text("user_implementation = True\n")
    manager.scaffold("demo", "replacement goal")
    assert path.read_text() == "user_implementation = True\n"


def test_incomplete_scaffold_is_not_overwritten(tmp_path):
    manager = ExtensionManager(tmp_path)
    manager.propose("demo", "test")
    path = manager.directory("demo") / "permissions.json"
    path.write_text("user draft")
    with pytest.raises(ValueError, match="refusing to overwrite"):
        manager.scaffold("demo")
    assert path.read_text() == "user draft"
    assert manager.load("demo").status == "proposed"


def test_corrupt_manifest_is_preserved(tmp_path):
    manager = ExtensionManager(tmp_path)
    manager.propose("demo", "test")
    path = manager.directory("demo") / "manifest.json"
    path.write_text("{broken")
    with pytest.raises(ValueError, match="corrupt"):
        manager.scaffold("demo")
    assert path.read_text() == "{broken"


def test_emergency_stop_prevents_extension_enablement(tmp_path):
    manager = ExtensionManager(tmp_path)
    manager.scaffold("demo", "test")
    manager.mark_tested("demo")
    approval = manager.request_enable("demo")
    ApprovalStore(tmp_path).decide(approval, True)
    EmergencyStop(tmp_path).engage("test stop")
    with pytest.raises(PermissionError, match="Emergency stop"):
        manager.enable("demo", approval)


def approved_candidate(tmp_path):
    manager = ExtensionManager(tmp_path)
    manager.scaffold("demo", "test")
    manager.mark_tested("demo")
    approval = manager.request_enable("demo")
    ApprovalStore(tmp_path).decide(approval, True)
    return manager, approval


def test_enable_persists_one_final_state_with_approval(tmp_path, monkeypatch):
    manager, approval = approved_candidate(tmp_path)
    save = manager._save
    states = []
    def observe(manifest):
        states.append(manifest.status)
        save(manifest)
    monkeypatch.setattr(manager, "_save", observe)
    manager.enable("demo", approval)
    assert states == ["enabled"]
    assert manager.load("demo").enable_approval_id == approval


def test_failed_enable_commit_can_retry_without_intermediate_state(tmp_path, monkeypatch):
    manager, approval = approved_candidate(tmp_path)
    save = manager._save
    def fail(manifest):
        raise OSError("injected write failure")
    monkeypatch.setattr(manager, "_save", fail)
    with pytest.raises(OSError):
        manager.enable("demo", approval)
    assert manager.load("demo").status == "tested"
    monkeypatch.setattr(manager, "_save", save)
    assert manager.enable("demo", approval).status == "enabled"


def test_disabled_extension_requires_fresh_matching_approval(tmp_path):
    manager, approval = approved_candidate(tmp_path)
    enabled = manager.enable("demo", approval)
    manager.transition(enabled, "disabled")
    with pytest.raises(PermissionError):
        manager.enable("demo", approval)
    fresh = manager.request_enable("demo")
    ApprovalStore(tmp_path).decide(fresh, True)
    assert manager.enable("demo", fresh).enable_approval_id == fresh


def test_stale_manifest_cannot_overwrite_current_state(tmp_path):
    manager, approval = approved_candidate(tmp_path)
    stale = manager.load("demo")
    manager.enable("demo", approval)
    with pytest.raises(ValueError, match="state changed"):
        manager.transition(stale, "retired")
    assert manager.load("demo").status == "enabled"


def test_busy_extension_lock_refuses_mutation(tmp_path):
    manager, approval = approved_candidate(tmp_path)
    with exclusive_lock(manager.root / ".lock"):
        with pytest.raises(BlockingIOError):
            manager.enable("demo", approval)
    assert manager.load("demo").status == "tested"
    assert manager.enable("demo", approval).status == "enabled"


def test_active_extension_cannot_silently_add_secret_reference(tmp_path):
    manager, approval = approved_candidate(tmp_path)
    manager.enable("demo", approval)
    with pytest.raises(PermissionError, match="Disable"):
        manager.declare_secret("demo", "new", "GHOST_TEST_SECRET", "test")
    assert manager.load("demo").permissions["secrets"] == []


def test_modified_enabled_extension_is_tampered_on_verify(tmp_path):
    manager, approval = approved_candidate(tmp_path)
    manager.enable("demo", approval)
    (manager.directory("demo") / "adapter" / "__init__.py").write_text("changed = True\n")
    assert manager.verify_integrity() == [{"name": "demo", "status": "tampered"}]
    assert manager.load("demo").status == "tampered"
    events = SecurityMonitor(tmp_path).recent_events()
    assert any(event["kind"] == "extension.tampered" for event in events)


def test_unchanged_enabled_extension_stays_ok_on_verify(tmp_path):
    manager, approval = approved_candidate(tmp_path)
    manager.enable("demo", approval)
    assert manager.verify_integrity() == []
    assert manager.load("demo").status == "enabled"


def test_tampered_extension_cannot_be_reenabled_or_retested(tmp_path):
    manager, approval = approved_candidate(tmp_path)
    manager.enable("demo", approval)
    (manager.directory("demo") / "extra.py").write_text("new = True\n")
    assert manager.verify_integrity()
    with pytest.raises(ValueError, match="tampered; re-test and re-approve"):
        manager.mark_tested("demo")
    with pytest.raises(ValueError, match="tampered; re-test and re-approve"):
        manager.enable("demo", approval)
    with pytest.raises(ValueError, match="tampered; re-test and re-approve"):
        manager.request_enable("demo")
