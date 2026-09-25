"""Pairing/delivery tests use real upstream message/transport classes, no Telegram account."""
import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("openjarvis")

from openjarvis.channels._stubs import ChannelMessage
from openjarvis.channels.telegram import TelegramChannel

from ghost_agent.autonomy import OutcomeStore
from ghost_agent.openjarvis_bridge.runtime import IntegrationError
from ghost_agent.openjarvis_bridge.telegram import TelegramBridge, pair, pairing, revoke
from ghost_agent.runtime import EmergencyStop, Scheduler


def message(**changes):
    values = dict(channel="telegram", sender="1234", conversation_id="1234", message_id="5", content="hello")
    values.update(changes)
    return ChannelMessage(**values)


@pytest.mark.parametrize("user,chat", [("", ""), ("0", "0"), ("1234", "-999"), ("1234", "999")])
def test_private_identity_only(tmp_path, user, chat):
    with pytest.raises(IntegrationError):
        pair(tmp_path, user, chat)


def test_pair_revoke_and_corruption_fail_closed(tmp_path):
    assert pairing(tmp_path)["enabled"] is False
    pair(tmp_path, "1234", "1234")
    assert pairing(tmp_path)["enabled"]
    revoke(tmp_path)
    assert not pairing(tmp_path)["enabled"]
    (tmp_path / ".ghost/openjarvis/telegram-pairing.json").write_text("broken")
    with pytest.raises(IntegrationError):
        pairing(tmp_path)


def test_unpaired_sender_and_group_never_invoke_agent(tmp_path):
    pair(tmp_path, "1234", "1234")
    responder = MagicMock()
    channel = MagicMock()
    bridge = TelegramBridge(tmp_path, channel, "tiny", responder=responder)
    bridge.handle(message(sender="999"))
    bridge.handle(message(conversation_id="-1234"))
    responder.assert_not_called()
    channel.send.assert_not_called()


def test_duplicate_receipt_survives_restart(tmp_path):
    pair(tmp_path, "1234", "1234")
    channel = MagicMock()
    channel.send.return_value = True
    responder = MagicMock(return_value={"task_id": "task", "content": "hi"})
    TelegramBridge(tmp_path, channel, "tiny", responder=responder).handle(message())
    TelegramBridge(tmp_path, channel, "tiny", responder=responder).handle(message())
    assert responder.call_count == 1
    channel.send.assert_called_once_with("1234", "hi", conversation_id="5")
    assert responder.call_args.kwargs["identity"] == "owner:telegram"
    receipt = next((tmp_path / ".ghost/openjarvis/telegram-receipts").glob("*.json"))
    assert json.loads(receipt.read_text())["status"] == "delivered"


def test_revocation_during_inference_prevents_delivery(tmp_path):
    pair(tmp_path, "1234", "1234")
    def respond(*args, **kwargs):
        revoke(tmp_path)
        return {"task_id": "task", "content": "private"}
    channel = MagicMock()
    TelegramBridge(tmp_path, channel, "tiny", responder=respond).handle(message())
    channel.send.assert_not_called()


def test_shutdown_and_emergency_stop_prevent_delivery(tmp_path):
    pair(tmp_path, "1234", "1234")
    responder = MagicMock()
    bridge = TelegramBridge(tmp_path, MagicMock(), "tiny", responder=responder)
    bridge.stopped.set()
    bridge.handle(message())
    responder.assert_not_called()
    bridge.stopped.clear()
    EmergencyStop(tmp_path).engage("test")
    bridge.handle(message())
    responder.assert_not_called()


def test_failed_delivery_is_not_blindly_replayed(tmp_path):
    pair(tmp_path, "1234", "1234")
    channel = MagicMock()
    channel.send.return_value = False
    responder = MagicMock(return_value={"task_id": "task", "content": "hi"})
    bridge = TelegramBridge(tmp_path, channel, "tiny", responder=responder)
    bridge.handle(message())
    bridge.handle(message())
    assert channel.send.call_count == 1


def test_explicit_build_request_can_queue_sandbox_work(tmp_path):
    pair(tmp_path, "1234", "1234")
    channel = MagicMock()
    channel.send.return_value = True
    responder = MagicMock()
    bridge = TelegramBridge(tmp_path, channel, "tiny", responder=responder,
                            allow_sandbox_builds=True)
    bridge.handle(message(content="/build a Rust learning game for my current lesson"))
    responder.assert_not_called()
    channel.send.assert_called_once()
    scheduled = Scheduler(tmp_path).list()
    assert len(scheduled) == 1
    assert OutcomeStore(tmp_path).load(scheduled[0].job_id).goal == "a Rust learning game for my current lesson"


def test_build_queue_is_opt_in_and_default_telegram_stays_conversational(tmp_path):
    pair(tmp_path, "1234", "1234")
    channel = MagicMock()
    channel.send.return_value = True
    responder = MagicMock(return_value={"task_id": "task", "content": "chat response"})
    TelegramBridge(tmp_path, channel, "tiny", responder=responder).handle(
        message(content="build a Rust learning game"))
    responder.assert_called_once()
    assert Scheduler(tmp_path).list() == []


def test_patched_real_poll_loop_registers_awaitable_callback_off_loop_thread():
    channel = TelegramChannel(bot_token="test-only", allowed_chat_ids="1234")
    app = MagicMock()
    observed = []
    current_thread = threading.get_ident()
    channel.on_message(lambda msg: observed.append((msg.content, threading.get_ident())))

    async def lifecycle(_app, stop):
        handler = app.add_handler.call_args.args[0]
        update = SimpleNamespace(message=SimpleNamespace(from_user=SimpleNamespace(id=1234),
            chat=SimpleNamespace(id=1234), text="hello", message_id=5))
        result = handler.callback(update, None)
        assert asyncio.iscoroutine(result)
        await result

    channel._run_polling_lifecycle = lifecycle
    with patch("telegram.ext.ApplicationBuilder") as builder:
        builder.return_value.token.return_value.build.return_value = app
        channel._poll_loop()
    assert len(observed) == 1
    assert observed[0][0] == "hello"
    assert observed[0][1] != current_thread


def test_patched_upstream_preserves_pending_updates():
    import inspect
    assert "drop_pending_updates=False" in inspect.getsource(TelegramChannel._run_polling_lifecycle)
