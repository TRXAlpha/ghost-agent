from pathlib import Path

import pytest

from ghost_agent.extensions import ExtensionManager
from ghost_agent.approvals import ApprovalStore


def test_extension_scaffold_has_manifest_permissions_tests_health_and_rollback(tmp_path: Path) -> None:
    manager = ExtensionManager(tmp_path)
    manifest = manager.scaffold("Telegram Bridge", "Send approved Telegram notifications")
    root = manager.directory("telegram-bridge")

    assert manifest.status == "scaffolded"
    assert (root / "manifest.json").is_file()
    assert (root / "permissions.json").is_file()
    assert (root / "tests" / "test_adapter.py").is_file()
    assert (root / "health" / "README.txt").is_file()
    assert (root / "rollback" / "README.txt").is_file()


def test_extension_requires_valid_lifecycle_transitions(tmp_path: Path) -> None:
    manager = ExtensionManager(tmp_path)
    manifest = manager.propose("demo", "demo capability")
    with pytest.raises(PermissionError):
        manager.transition(manifest, "enabled")

    manager.scaffold("demo")
    manifest = manager.load("demo")
    manager.transition(manifest, "tested")
    with pytest.raises(PermissionError):
        manager.transition(manifest, "approved")
    manifest.status = "approved"
    with pytest.raises(PermissionError):
        manager.transition(manifest, "enabled")
    assert manager.load("demo").status == "tested"


def test_duplicate_proposal_preserves_existing_lifecycle_state(tmp_path: Path) -> None:
    manager = ExtensionManager(tmp_path)
    manager.scaffold("workaround", "first proposal")

    manifest = manager.propose("workaround", "new wording")

    assert manifest.status == "scaffolded"
    assert manifest.goal == "first proposal"


def test_extension_enable_requires_matching_approval(tmp_path: Path) -> None:
    manager = ExtensionManager(tmp_path)
    manager.scaffold("reviewed", "Integration")
    manager.mark_tested("reviewed")
    approval_id = manager.request_enable("reviewed")

    with pytest.raises(PermissionError):
        manager.enable("reviewed", approval_id)
    ApprovalStore(tmp_path).decide(approval_id, True)
    assert manager.enable("reviewed", approval_id).status == "enabled"
