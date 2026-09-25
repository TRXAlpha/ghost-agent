from pathlib import Path

import pytest

from ghost_agent.kernel import (
    ActionProposal,
    CapabilityKernel,
    CapabilityManifest,
    GoalStore,
    TransactionJournal,
)


def test_goal_store_preserves_hierarchy_and_priority(tmp_path: Path) -> None:
    store = GoalStore(tmp_path)
    project = store.create("Build Ghost", kind="project", priority=10)
    outcome = store.create("Add dry-run", parent_id=project.goal_id, priority=90)

    assert store.get(outcome.goal_id).parent_id == project.goal_id
    assert store.active()[0].goal_id == outcome.goal_id
    assert store.set_status(outcome.goal_id, "completed").status == "completed"
    assert store.active()[0].goal_id == project.goal_id


def test_goal_store_dependencies_and_deadline_staleness(tmp_path: Path) -> None:
    store = GoalStore(tmp_path)
    prerequisite = store.create("Research", kind="task")
    outcome = store.create("Build", dependencies=[prerequisite.goal_id])
    expired = store.create("Old task", deadline_at=1)

    assert outcome not in store.ready()
    store.set_status(prerequisite.goal_id, "completed")
    assert store.ready()[0].goal_id == outcome.goal_id
    assert store.mark_stale(now=2)[0].goal_id == expired.goal_id


def test_capability_kernel_denies_live_escape_and_flags_side_effects(tmp_path: Path) -> None:
    kernel = CapabilityKernel()
    kernel.register(CapabilityManifest(
        "filesystem.write", ["path"], ["artifact"], side_effects=["writes file"],
    ))
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    denied = kernel.authorize(
        ActionProposal("filesystem.write", {}, target=str(tmp_path / "live.txt")), sandbox
    )
    pending = kernel.authorize(
        ActionProposal("filesystem.write", {}, target=str(sandbox / "file.txt")), sandbox
    )

    assert denied.decision == "denied"
    assert pending.decision == "needs_approval"


def test_capability_manifest_retains_resource_and_health_metadata() -> None:
    manifest = CapabilityManifest("device.command", ["command"], ["result"],
                                  resource_limits={"max_runtime_seconds": 30}, health_check="ping")
    assert manifest.resource_limits["max_runtime_seconds"] == 30
    assert manifest.health_check == "ping"


def test_transaction_requires_preview_before_commit_and_supports_rollback(tmp_path: Path) -> None:
    journal = TransactionJournal(tmp_path)
    tx = journal.prepare("goal_1", ActionProposal("filesystem.write", {}))
    with pytest.raises(ValueError):
        journal.transition(tx, "committed")

    journal.transition(tx, "previewed", {"files": ["sandbox/a.py"]})
    journal.transition(tx, "committed")
    journal.transition(tx, "rolled_back", {"reason": "verification failed"})

    assert tx.state == "rolled_back"
    assert len(tx.history) == 3


def test_stale_transaction_cannot_overwrite_new_state(tmp_path):
    journal = TransactionJournal(tmp_path)
    tx = journal.prepare("goal", ActionProposal("filesystem.promote", {}))
    stale = journal.get(tx.transaction_id)
    journal.transition(tx, "previewed", {"files": ["app.py"]})
    with pytest.raises(ValueError, match="changed"):
        journal.transition(stale, "rolled_back")
    assert journal.get(tx.transaction_id).state == "previewed"


def test_transaction_lookup_rejects_paths(tmp_path):
    assert TransactionJournal(tmp_path).get("../../outside") is None
