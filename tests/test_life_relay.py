"""ghost-bot relay: owner messages from ghostpi's inbox reach TelegramBridge exactly once."""
import io
import json
from unittest.mock import MagicMock

import pytest

from ghost_agent.openjarvis_bridge import telegram as tg
from ghost_agent.runtime import EmergencyStop, EventBus

OWNER = 5309400589


def row(message_id, text, sender=OWNER):
    return json.dumps({"message_id": message_id, "chat_id": sender, "from_id": sender,
                       "date": 0, "text": text})


def bridge(tmp_path, sent):
    tg.pair(tmp_path, str(OWNER), str(OWNER))
    channel = tg.BotApiChannel("t", sender=lambda token, chat, text: sent.append((chat, text)) or {"ok": True})
    responder = MagicMock(return_value={"task_id": "t", "content": "answer"})
    return tg.TelegramBridge(tmp_path, channel, "tiny", responder=responder), responder


def test_relayed_statement_becomes_fact_and_reply(tmp_path):
    sent = []
    b, responder = bridge(tmp_path, sent)
    assert tg.relay_lines(tmp_path, b, [row(10, "I got a new Samsung watch")]) == 1
    assert sent and sent[0][0] == str(OWNER) and "Noted (device: Samsung watch)" in sent[0][1]
    assert EventBus(tmp_path).recent(5, kind="owner.contact")


def test_cursor_dedup_malformed_and_stranger(tmp_path):
    sent = []
    b, responder = bridge(tmp_path, sent)
    lines = [row(1, "what's up?"), "{broken", row(2, "hi", sender=999)]
    tg.relay_lines(tmp_path, b, lines)
    assert responder.call_count == 1  # stranger ignored by pairing, broken row skipped
    cursor = json.loads((tmp_path / ".ghost/openjarvis/telegram-relay-cursor.json").read_text())
    assert cursor["line"] == 3
    # replay of the same message after a restart is not answered twice (receipt ledger)
    b2, responder2 = bridge(tmp_path, sent)
    tg.relay_lines(tmp_path, b2, [row(1, "what's up?")])
    responder2.assert_not_called()


def test_stop_over_relay_engages_and_halts(tmp_path):
    sent = []
    b, responder = bridge(tmp_path, sent)
    handled = tg.relay_lines(tmp_path, b, [row(1, "stop"), row(2, "I got a new phone")])
    assert EmergencyStop(tmp_path).active() and handled == 1
    assert "Emergency stop engaged" in sent[0][1]


def test_relay_serve_follows_ssh_stream(tmp_path, monkeypatch):
    tg.pair(tmp_path, str(OWNER), str(OWNER))
    monkeypatch.setenv("GHOST_TELEGRAM_BOT_TOKEN", "x")
    sent, commands = [], []
    monkeypatch.setattr(tg, "_bot_api_send", lambda token, chat, text: sent.append(text) or {"ok": True})

    class Proc:
        def __init__(self, cmd, **kw):
            commands.append(cmd)
            self.stdout = io.StringIO(row(1, "I got a new Samsung watch") + "\n" + row(2, "stop") + "\n")

        def kill(self):
            pass

    tg.relay_serve(tmp_path, "tiny", popen=Proc, sleep=lambda s: None)
    assert commands[0][:3] == ["ssh", "-o", "BatchMode=yes"] and "tail -n +1 -F" in commands[0][-1]
    assert any("Noted" in s for s in sent) and EmergencyStop(tmp_path).active()


def test_relay_refuses_without_pairing_token_or_bad_host(tmp_path, monkeypatch):
    with pytest.raises(tg.IntegrationError):
        tg.relay_serve(tmp_path, "tiny")
    tg.pair(tmp_path, str(OWNER), str(OWNER))
    monkeypatch.delenv("GHOST_TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(tg.IntegrationError):
        tg.relay_serve(tmp_path, "tiny")
    monkeypatch.setenv("GHOST_TELEGRAM_BOT_TOKEN", "x")
    with pytest.raises(tg.IntegrationError):
        tg.relay_serve(tmp_path, "tiny", host="ghostpi; rm -rf /")


def test_statement_without_leading_i_is_a_fact():
    from ghost_agent.owner import parse_statement
    assert parse_statement("got a new Samsung watch")["kind"] == "device"
    assert parse_statement("just bought a new ring")["subject"] == "ring"
    assert parse_statement("got it, thanks")["kind"] == "note"


def test_responder_failure_is_told_not_silent(tmp_path):
    sent = []
    b, responder = bridge(tmp_path, sent)
    responder.side_effect = ConnectionError("engine down")
    tg.relay_lines(tmp_path, b, [row(1, "how are you?")])
    assert "can't answer chat questions right now" in sent[0][1]
