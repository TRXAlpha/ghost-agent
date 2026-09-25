from pathlib import Path

import pytest

from ghost_agent.extensions import ExtensionManager
from ghost_agent.secrets import SecretStore


def test_secret_store_persists_reference_not_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "not-written-to-disk")
    store = SecretStore(tmp_path)
    store.register("telegram.bot", "TELEGRAM_BOT_TOKEN", "Send approved notifications")

    assert store.resolve("telegram.bot") == "not-written-to-disk"
    assert "not-written-to-disk" not in store.path.read_text(encoding="utf-8")


def test_extension_can_only_declare_a_secret_reference(tmp_path: Path) -> None:
    manager = ExtensionManager(tmp_path)
    manager.scaffold("telegram", "Send notifications")

    manifest = manager.declare_secret("telegram", "telegram.bot", "TELEGRAM_BOT_TOKEN", "Bot API access")

    assert manifest.status == "scaffolded"
    assert manifest.permissions["secrets"] == ["telegram.bot"]
    assert (manager.directory("telegram") / "signature").is_file()
