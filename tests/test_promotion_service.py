from pathlib import Path
import json
import subprocess
import sys

import pytest

from ghost_agent.approvals import ApprovalStore
from ghost_agent.promotion import PromotionService
from ghost_agent.runtime import EmergencyStop


def test_promotion_service_requires_matching_approval(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "new.py").write_text("value = 1\n", encoding="utf-8")
    service = PromotionService(tmp_path)
    tx_id, approval_id, files = service.request("goal_1", sandbox)

    assert files == ["new.py"]
    with pytest.raises(PermissionError):
        service.commit(tx_id, approval_id)
    ApprovalStore(tmp_path).decide(approval_id, True)
    promotion_id = service.commit(tx_id, approval_id)

    assert (tmp_path / "new.py").read_text(encoding="utf-8") == "value = 1\n"
    assert promotion_id.startswith("promotion_")


def test_promotion_service_refuses_empty_preview(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    with pytest.raises(ValueError, match="no promotable"):
        PromotionService(tmp_path).request("goal_1", sandbox)


def prepared_promotion(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    sandbox = live / ".ghost" / "outcomes" / "sample" / "run" / "sandbox"
    sandbox.mkdir(parents=True)
    (live / "app.py").write_text("old", encoding="utf-8")
    (sandbox / "app.py").write_text("new", encoding="utf-8")
    service = PromotionService(live)
    tx, approval, files = service.request("goal_1", sandbox)
    service.approvals.decide(approval, True)
    return service, sandbox, tx, approval, files


def test_canonical_outcome_sandbox_is_promotable(tmp_path):
    service, sandbox, tx, approval, files = prepared_promotion(tmp_path)
    assert files == ["app.py"]
    promotion = service.commit(tx, approval)
    assert (service.root / "app.py").read_text() == "new"
    service.rollback(promotion)
    assert (service.root / "app.py").read_text() == "old"
    assert service.transactions.get(tx).state == "rolled_back"
    assert service.provenance.all()[-1].kind == "promotion.rolled_back"


@pytest.mark.parametrize("changed", ["source", "destination", "extra_file"])
def test_approval_does_not_authorize_drift(tmp_path, changed):
    # Use an external sandbox too, so the previous .ghost path bug cannot hide drift.
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "app.py").write_text("approved", encoding="utf-8")
    service = PromotionService(tmp_path / "live")
    (service.root / "app.py").write_text("original", encoding="utf-8")
    tx, approval, _ = service.request("goal", sandbox)
    service.approvals.decide(approval, True)
    if changed == "source":
        (sandbox / "app.py").write_text("not approved", encoding="utf-8")
    elif changed == "destination":
        (service.root / "app.py").write_text("user edit", encoding="utf-8")
    else:
        (sandbox / "extra.py").write_text("not previewed", encoding="utf-8")
    expected = (service.root / "app.py").read_bytes()
    with pytest.raises((ValueError, PermissionError)):
        service.commit(tx, approval)
    assert (service.root / "app.py").read_bytes() == expected
    assert not (service.root / "extra.py").exists()


def test_stop_blocks_live_promotion(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "new.py").write_text("new", encoding="utf-8")
    service = PromotionService(tmp_path)
    tx, approval, _ = service.request("goal", sandbox)
    service.approvals.decide(approval, True)
    EmergencyStop(tmp_path).engage("stop")
    with pytest.raises(PermissionError):
        service.commit(tx, approval)
    assert not (tmp_path / "new.py").exists()


def test_rollback_preserves_later_user_edits(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "new.py").write_text("new", encoding="utf-8")
    service = PromotionService(tmp_path)
    tx, approval, _ = service.request("goal", sandbox)
    service.approvals.decide(approval, True)
    promotion = service.commit(tx, approval)
    (tmp_path / "new.py").write_text("user edit", encoding="utf-8")
    with pytest.raises(ValueError, match="conflict"):
        service.promotions.rollback(promotion)
    assert (tmp_path / "new.py").read_text() == "user edit"


def test_low_level_promotion_rejects_invented_approval(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "new.py").write_text("new", encoding="utf-8")
    service = PromotionService(tmp_path)
    with pytest.raises(PermissionError):
        service.promotions.promote(sandbox, ["new.py"], "approval_invented")
    assert not (tmp_path / "new.py").exists()


def test_rollback_rejects_untrusted_journal_paths(tmp_path):
    service = PromotionService(tmp_path / "live")
    outside = tmp_path / "do_not_delete.txt"
    outside.write_text("preserve", encoding="utf-8")
    backup = service.promotions.backups / "promotion_untrusted"
    backup.mkdir()
    (backup / "journal.json").write_text(json.dumps({"files": [
        {"file": "../do_not_delete.txt", "existed": False}
    ]}), encoding="utf-8")
    with pytest.raises((ValueError, PermissionError)):
        service.promotions.rollback("promotion_untrusted")
    assert outside.read_text() == "preserve"


def test_process_death_leaves_a_recoverable_promotion(tmp_path):
    service, sandbox, tx, approval, _ = prepared_promotion(tmp_path)
    script = '''
import os, sys
from pathlib import Path
from ghost_agent import promotion
root, tx, approval = sys.argv[1:]
original = promotion.atomic_write
def interrupt_after_write(path, content):
    original(path, content)
    if Path(path) == Path(root) / "app.py":
        os._exit(73)
promotion.atomic_write = interrupt_after_write
promotion.PromotionService(Path(root)).commit(tx, approval)
'''
    result = subprocess.run([sys.executable, "-c", script, str(service.root), tx, approval],
                            stdin=subprocess.DEVNULL, capture_output=True, timeout=15)
    assert result.returncode == 73, result.stderr
    promotion_id = "promotion_" + approval
    journal = service.promotions.backups / promotion_id / "journal.json"
    assert json.loads(journal.read_text())["status"] == "prepared"
    assert (service.root / "app.py").read_text() == "new"
    service.rollback(promotion_id)
    assert (service.root / "app.py").read_text() == "old"
    assert json.loads(journal.read_text())["status"] == "rolled_back"
    assert service.transactions.get(tx).state == "rolled_back"
    service.promotions.rollback(promotion_id)  # Recovery is idempotent.


def test_write_failure_rolls_back_already_applied_files(tmp_path, monkeypatch):
    from ghost_agent import promotion
    service, sandbox, _, _, _ = prepared_promotion(tmp_path)
    (sandbox / "second.py").write_text("second", encoding="utf-8")
    tx, approval, _ = service.request("goal", sandbox)
    service.approvals.decide(approval, True)
    original = promotion.atomic_write

    def fail_second(path, content):
        if path == service.root / "second.py":
            raise OSError("injected write failure")
        original(path, content)

    monkeypatch.setattr(promotion, "atomic_write", fail_second)
    with pytest.raises(OSError, match="injected"):
        service.commit(tx, approval)
    assert (service.root / "app.py").read_text() == "old"
    assert not (service.root / "second.py").exists()
    assert service.transactions.get(tx).state == "rolled_back"


def test_corrupt_backup_refuses_rollback_without_partial_changes(tmp_path):
    service, _, tx, approval, _ = prepared_promotion(tmp_path)
    promotion_id = service.commit(tx, approval)
    backup = service.promotions.backups / promotion_id / "files" / "app.py"
    backup.write_text("corrupt", encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        service.promotions.rollback(promotion_id)
    assert (service.root / "app.py").read_text() == "new"


def test_approval_cannot_be_replayed_after_undo(tmp_path):
    service, sandbox, tx, approval, files = prepared_promotion(tmp_path)
    promotion_id = service.commit(tx, approval)
    service.promotions.rollback(promotion_id)
    with pytest.raises(FileExistsError):
        service.promotions.promote(sandbox, files, approval)
    assert (service.root / "app.py").read_text() == "old"


def test_busy_promotion_lock_prevents_dispatch(tmp_path):
    from ghost_agent.utils.storage import exclusive_lock
    service, _, tx, approval, _ = prepared_promotion(tmp_path)
    with exclusive_lock(service.promotions.backups / ".lock"):
        with pytest.raises(BlockingIOError):
            service.commit(tx, approval)
    assert (service.root / "app.py").read_text() == "old"
    service.commit(tx, approval)
