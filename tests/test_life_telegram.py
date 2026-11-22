"""Life layer over Telegram: owner.contact, owner facts, emergency stop, outbound send."""
from unittest.mock import MagicMock

import pytest

pytest.importorskip("openjarvis")

from openjarvis.channels._stubs import ChannelMessage

from ghost_agent.context_manager import ValidatedMemoryStore
from ghost_agent.openjarvis_bridge.telegram import TelegramBridge, pair, send_owner
from ghost_agent.runtime import EmergencyStop, EventBus


def message(content, message_id="5"):
    return ChannelMessage(channel="telegram", sender="1234", conversation_id="1234",
                          message_id=message_id, content=content)


def bridge(tmp_path):
    pair(tmp_path, "1234", "1234")
    channel = MagicMock()
    channel.send.return_value = True
    responder = MagicMock(return_value={"task_id": "t", "content": "answer"})
    return TelegramBridge(tmp_path, channel, "tiny", responder=responder), channel, responder


def test_statement_becomes_owner_fact_and_contact(tmp_path):
    b, channel, responder = bridge(tmp_path)
    b.handle(message("I got a new Samsung watch"))
    responder.assert_not_called()
    reply = channel.send.call_args[0][1]
    assert "Noted (device: Samsung watch)" in reply and "ghost why owner_" in reply
    assert ValidatedMemoryStore(tmp_path)._load()[0]["source"] == "owner:telegram"
    assert EventBus(tmp_path).recent(5, kind="owner.contact")[-1].payload["channel"] == "telegram"


def test_question_goes_to_responder(tmp_path):
    b, channel, responder = bridge(tmp_path)
    b.handle(message("Do I have a new watch?"))
    responder.assert_called_once()
    assert not (tmp_path / ".ghost/validated-memory.json").exists()


def test_stop_engages_emergency_stop_and_replies(tmp_path):
    b, channel, responder = bridge(tmp_path)
    b.handle(message("STOP"))
    assert EmergencyStop(tmp_path).active()
    assert "Emergency stop engaged" in channel.send.call_args[0][1]
    responder.assert_not_called()
    b.handle(message("I got a new phone", message_id="6"))  # stopped: ignored
    assert channel.send.call_count == 1


def test_send_owner_is_honest_without_token(tmp_path, monkeypatch):
    monkeypatch.delenv("GHOST_TELEGRAM_BOT_TOKEN", raising=False)
    assert send_owner(tmp_path, "hi")["status"] == "not_configured"  # not paired
    pair(tmp_path, "1234", "1234")
    assert send_owner(tmp_path, "hi")["status"] == "not_configured"  # no token
    monkeypatch.setenv("GHOST_TELEGRAM_BOT_TOKEN", "x")
    sent = []
    result = send_owner(tmp_path, "hi", sender=lambda t, c, text: sent.append((c, text)) or
                        {"ok": True, "result": {"message_id": 9}})
    assert result == {"status": "sent", "message_id": 9} and sent == [("1234", "hi")]
    EmergencyStop(tmp_path).engage("t")
    assert send_owner(tmp_path, "hi", sender=lambda *a: {"ok": True})["status"] == "refused"
