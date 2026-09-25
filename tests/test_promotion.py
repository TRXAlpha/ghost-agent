from pathlib import Path

from ghost_agent.promotion import PromotionManager, PromotionService


def test_promotion_preview_apply_and_rollback(tmp_path: Path) -> None:
    live = tmp_path / "live"
    live.mkdir()
    (live / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    (sandbox / "new.py").write_text("NEW = True\n", encoding="utf-8")
    manager = PromotionManager(live)

    assert manager.preview(sandbox) == ["app.py", "new.py"]
    service = PromotionService(live)
    transaction, approval, _ = service.request("goal", sandbox)
    service.approvals.decide(approval, True)
    promotion = service.commit(transaction, approval)
    assert (live / "app.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert (live / "new.py").exists()

    manager.rollback(promotion)
    assert (live / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not (live / "new.py").exists()
