"""OJ04/S01: Telegram live-readiness tests (credential honesty, rotation, live-check)."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ghost_agent.integrations import telegram as tg
from ghost_agent.openjarvis_bridge.runtime import IntegrationError
from ghost_agent.openjarvis_bridge import telegram as bt
from ghost_agent.runtime import EmergencyStop


@pytest.fixture(autouse=True)
def _clean_token_env(monkeypatch):
    monkeypatch.delenv("GHOST_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setattr(bt, "token_fingerprint", bt.token_fingerprint)  # no-op guard
    # Keep live-check from being accidentally real: tests patch requests.
    yield


@pytest.fixture()
def paired(tmp_path):
    bt.pair(tmp_path, "1234", "1234")
    return tmp_path


# --- 1. Credential-aware health surfacing -----------------------------------

def test_token_present_false_without_env_true_with_dummy(tmp_path, monkeypatch):
    assert tg.token_present() is False
    monkeypatch.setenv("GHOST_TELEGRAM_BOT_TOKEN", "123456:ABC-defGHI_dummy")
    assert tg.token_present() is True
    monkeypatch.delenv("GHOST_TELEGRAM_BOT_TOKEN")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABC-defGHI_dummy")
    assert tg.token_present() is True


def test_status_output_never_leaks_token_value(tmp_path, capsys, monkeypatch):
    token = "987654:XYZsecretVALUE_not_for_output"
    monkeypatch.setenv("GHOST_TELEGRAM_BOT_TOKEN", token)
    bt.pair(tmp_path, "1234", "1234")
    result = {**bt.pairing(tmp_path), **bt.token_health(tmp_path)}
    rendered = json.dumps(result)
    assert "token_present" in rendered and result["token_present"] is True
    assert token not in rendered
    assert token not in capsys.readouterr().out
    # The full 64-char SHA-256 also never appears; only the 16-char prefix.
    import hashlib
    full = hashlib.sha256(token.encode()).hexdigest()
    assert full not in rendered
    assert len(result["next_step"]) > 0


def test_health_check_next_step_progression(tmp_path, monkeypatch):
    # No token -> set token
    h = tg.health_check(tmp_path)
    assert h["token_present"] is False and h["pairing_present"] is False
    assert h["next_step"] == "set GHOST_TELEGRAM_BOT_TOKEN"
    assert h["status"] == "not_ready"

    # Token but no pairing -> pair owner id
    monkeypatch.setenv("GHOST_TELEGRAM_BOT_TOKEN", "123456:ABC")
    h = tg.health_check(tmp_path)
    assert h["token_present"] is True and h["pairing_present"] is False
    assert h["next_step"] == "pair owner id"

    # Token + pairing -> ready
    bt.pair(tmp_path, "1234", "1234")
    h = tg.health_check(tmp_path)
    assert h["token_present"] is True and h["pairing_present"] is True
    assert h["next_step"] == "ready for authorized live test"
    assert h["status"] == "ready"

    # Emergency stop engaged -> clear it first
    EmergencyStop(tmp_path).engage("test")
    from ghost_agent.openjarvis_bridge.telegram import token_health
    th = token_health(tmp_path)
    assert th["emergency_stop"] is True
    assert th["next_step"] == "clear the emergency stop"


def test_no_network_call_when_token_absent(tmp_path, monkeypatch):
    # Any accidental HTTP attempt fails the test.
    import requests
    monkeypatch.setattr(requests, "post",
                        MagicMock(side_effect=AssertionError("no network allowed")))
    monkeypatch.setattr(requests, "get",
                        MagicMock(side_effect=AssertionError("no network allowed")))
    # All read-only paths must succeed without network.
    assert tg.health_check(tmp_path)["token_present"] is False
    assert bt.token_health(tmp_path)["token_present"] is False
    assert bt.pairing(tmp_path)["enabled"] is False


# --- 2. Rotation: format check + fingerprint file ----------------------------

def test_rotate_token_check_rejects_malformed(tmp_path):
    for bad in ("", "no-colon", "abc:def", "123:", ":xyz", " 123456:ABC",
                "123456:ABC ", "123456:ABC;drop", "-1:abc"):
        result = bt.check_token_rotation(bad, tmp_path)
        assert result["well_formed"] is False, bad
        assert "error" in result


def test_fingerprint_file_stores_only_prefix_not_token(tmp_path):
    token = "123456:SuperSecretValueNeverStored"
    record = bt.record_token_fingerprint(token, tmp_path)
    path = tmp_path / ".ghost/openjarvis/telegram-token-fingerprint.json"
    assert path.exists()
    raw = path.read_text(encoding="utf-8")
    assert token not in raw
    assert record["fingerprint"] in raw
    assert len(record["fingerprint"]) == 16
    assert all(c in "0123456789abcdef" for c in record["fingerprint"])
    # Matches an independent computation of the prefix.
    import hashlib
    assert record["fingerprint"] == hashlib.sha256(token.encode()).hexdigest()[:16]


def test_rotate_token_check_reports_change_and_no_change(tmp_path):
    first = "123456:AAAfirst"
    second = "123456:BBBsecond"
    bt.record_token_fingerprint(first, tmp_path)
    same = bt.check_token_rotation(first, tmp_path)
    assert same["well_formed"] and same["same_as_recorded"] is True and same["changed"] is False
    different = bt.check_token_rotation(second, tmp_path)
    assert different["well_formed"] and different["changed"] is True


# --- 3. live-check gating -----------------------------------------------------

def test_live_check_refuses_without_token(monkeypatch, paired):
    # No token in env (fixture removed it). Any requests.post call must not happen.
    import requests
    post = MagicMock()
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(IntegrationError, match="TELEGRAM_BOT_TOKEN"):
        bt.live_check(paired, "1234")
    post.assert_not_called()


def test_live_check_refuses_wrong_chat_and_unpaired(tmp_path):
    bt.pair(tmp_path, "1234", "1234")
    import requests
    post = MagicMock()
    with patch("requests.post", post):
        with pytest.raises(IntegrationError, match="paired owner chat"):
            bt.live_check(tmp_path, "9999")
        post.assert_not_called()
    # Unpaired project: refuses before anything else.
    (tmp_path / ".ghost/openjarvis/telegram-pairing.json").unlink()
    with pytest.raises(IntegrationError, match="Pair the owner"):
        bt.live_check(tmp_path, "1234")


def test_live_check_refuses_when_emergency_stop_engaged(paired, monkeypatch):
    import requests
    post = MagicMock()
    monkeypatch.setattr(requests, "post", post)
    EmergencyStop(paired).engage("s01 drill")
    monkeypatch.setenv("GHOST_TELEGRAM_BOT_TOKEN", "123456:ABCemergency")
    with pytest.raises(IntegrationError, match="[Ee]mergency stop"):
        bt.live_check(paired, "1234")
    post.assert_not_called()


def test_live_check_success_recorded_with_mocked_200(paired, monkeypatch):
    token = "123456:ABClive"
    monkeypatch.setenv("GHOST_TELEGRAM_BOT_TOKEN", token)
    response = MagicMock(status_code=200)
    response.json.return_value = {"ok": True, "result": {"message_id": 77}}
    import requests
    post = MagicMock(return_value=response)
    monkeypatch.setattr(requests, "post", post)
    record = bt.live_check(paired, "1234")
    assert record["outcome"] == "success"
    assert record["chat_id"] == "1234"
    assert record["fingerprint"] == bt.token_fingerprint(token)
    assert record["checked_at"] > 0
    # Real Bot API endpoint used, exactly one call, paired chat only.
    assert post.call_count == 1
    url = post.call_args.args[0]
    assert url == f"https://api.telegram.org/bot{token}/sendMessage"
    assert "123456:ABClive" not in str(record)
    # Result persisted to the live-check marker file.
    marker = paired / ".ghost/openjarvis/telegram-live-check.json"
    assert marker.exists()
    persisted = json.loads(marker.read_text(encoding="utf-8"))
    assert persisted["outcome"] == "success"
    assert token not in marker.read_text(encoding="utf-8")


def test_live_check_records_failure_on_non_200(paired, monkeypatch):
    monkeypatch.setenv("GHOST_TELEGRAM_BOT_TOKEN", "123456:ABCfail")
    import requests
    response = MagicMock(status_code=401)
    response.json.return_value = {"ok": False, "description": "Unauthorized"}
    post = MagicMock(return_value=response)
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(IntegrationError):
        bt.live_check(paired, "1234")
    marker = paired / ".ghost/openjarvis/telegram-live-check.json"
    persisted = json.loads(marker.read_text(encoding="utf-8"))
    assert persisted["outcome"] == "failure"
    assert "401" in persisted["detail"]


# --- 4. Runbook ---------------------------------------------------------------

def test_runbook_exists_with_acceptance_items():
    path = Path(__file__).parent.parent / "docs/engineering/telegram-live-qualification.md"
    assert path.exists(), "runbook must exist at docs/engineering/telegram-live-qualification.md"
    content = path.read_text(encoding="utf-8")
    for required in ("BotFather", "TELEGRAM_BOT_TOKEN", "live-check", "pair",
                     "Acceptance", "Emergency stop".lower(), "rotate-token-check"):
        assert required in content or required.lower() in content.lower(), required
    # Never instructs committing the token.
    assert "never paste it into chat" in content.lower()
