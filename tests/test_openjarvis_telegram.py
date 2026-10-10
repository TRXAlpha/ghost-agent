"""Pairing/delivery tests use real upstream message/transport classes, no Telegram account."""
import asyncio
import json
import threading
from itertools import chain, repeat
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("openjarvis")

from openjarvis.channels._stubs import ChannelMessage, ChannelStatus
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


def _write_receipt(tmp_path, message_id, status, finished_at=None):
    import hashlib

    from ghost_agent.utils.storage import atomic_json
    from ghost_agent.openjarvis_bridge.runtime import state_root
    identifier = hashlib.sha256(f"1234:{message_id}".encode()).hexdigest()
    path = state_root(tmp_path) / "telegram-receipts" / f"{identifier}.json"
    atomic_json(path, {"message_id": message_id, "received_at": 1.0,
                       "status": status, "finished_at": finished_at})
    return path


def test_reconcile_marks_stuck_sending_receipt_uncertain_once(tmp_path):
    from ghost_agent.openjarvis_bridge.telegram import reconcile_receipts
    pair(tmp_path, "1234", "1234")
    _write_receipt(tmp_path, "5", "sending")
    reconciled = reconcile_receipts(tmp_path)
    assert len(reconciled) == 1
    receipt = json.loads(
        (tmp_path / ".ghost/openjarvis/telegram-receipts").glob("*.json").__next__().read_text())
    assert receipt["status"] == "uncertain"
    assert receipt["reconciled_at"] > 0
    from ghost_agent.runtime import AttentionManager
    assert len(AttentionManager(tmp_path).pending()) == 1
    # A second reconcile must not duplicate the owner notification.
    reconcile_receipts(tmp_path)
    assert len(AttentionManager(tmp_path).pending()) == 1


def test_reconcile_leaves_finished_receipts_alone(tmp_path):
    from ghost_agent.openjarvis_bridge.telegram import reconcile_receipts
    _write_receipt(tmp_path, "5", "delivered", finished_at=2.0)
    _write_receipt(tmp_path, "6", "processing")  # no finished_at but reconciled too
    reconciled = reconcile_receipts(tmp_path)
    statuses = {r["message_id"]: r["status"] for r in reconciled}
    assert statuses == {"6": "uncertain"}
    delivered = next((tmp_path / ".ghost/openjarvis/telegram-receipts").glob("*.json")).read_text()
    assert "delivered" in delivered and "uncertain" not in json.loads(delivered)["status"]


def test_redelivered_message_with_uncertain_receipt_is_not_reprocessed(tmp_path):
    from ghost_agent.openjarvis_bridge.telegram import reconcile_receipts
    pair(tmp_path, "1234", "1234")
    channel = MagicMock()
    channel.send.return_value = True
    responder = MagicMock(return_value={"task_id": "task", "content": "hi"})
    bridge = TelegramBridge(tmp_path, channel, "tiny", responder=responder)
    # Crash mid-send simulation: receipt written, no finished_at.
    _write_receipt(tmp_path, "5", "sending")
    reconcile_receipts(tmp_path)
    bridge.handle(message())
    responder.assert_not_called()
    channel.send.assert_not_called()
    assert len(list((tmp_path / ".ghost/openjarvis/telegram-receipts").glob("*.json"))) == 1


def _monitor(tmp_path, channel, **kwargs):
    from ghost_agent.openjarvis_bridge.telegram import _monitor_transport
    stopped = threading.Event()
    timer = threading.Timer(5.0, stopped.set)
    timer.daemon = True
    timer.start()
    try:
        _monitor_transport(tmp_path, channel, stopped, **kwargs)
    finally:
        timer.cancel()


def test_transient_error_is_retried_with_growing_backoff(tmp_path):
    pair(tmp_path, "1234", "1234")
    delays = []
    # Two transient failures, then healthy; each error forces one reconnect.
    # status() is polled once per loop pass and once after each reconnect.
    statuses = chain([ChannelStatus.ERROR, ChannelStatus.ERROR, ChannelStatus.ERROR],
                     repeat(ChannelStatus.CONNECTED))
    reconnects = []
    reconnected = []
    channel = MagicMock()
    channel.status.side_effect = lambda: next(statuses)
    channel.connect.side_effect = lambda: reconnects.append(1)
    _monitor(tmp_path, channel,
             sleep=lambda s: delays.append(s) if s != 0.5 else None,
             after_reconnect=lambda: reconnected.append(None),
             )
    # 1 then 2 seconds, reset to 1 after a healthy status.
    assert delays == [1.0, 2.0]
    assert len(reconnects) == 2  # one reconnect per error pass
    assert reconnected == [None]  # after_reconnect was called back


def test_backoff_delay_resets_after_success_and_caps_at_limit(tmp_path):
    pair(tmp_path, "1234", "1234")
    delays = []
    statuses = iter([ChannelStatus.ERROR] * 7 + [ChannelStatus.CONNECTED])
    channel = MagicMock()
    channel.status.side_effect = lambda: next(statuses)
    with pytest.raises(StopIteration):
        _monitor(tmp_path, channel, sleep=delays.append, initial_delay=1.0, max_delay=60.0)
    assert delays[:6] == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0]
    assert delays[6] == 60.0  # capped, never grows past max_delay


def test_emergency_stop_ends_retry_loop(tmp_path):
    pair(tmp_path, "1234", "1234")
    EmergencyStop(tmp_path).engage("test")
    delays = []
    channel = MagicMock()
    channel.status.return_value = ChannelStatus.ERROR
    channel.connect.side_effect = AssertionError("must not reconnect under emergency stop")
    with pytest.raises(IntegrationError):
        _monitor(tmp_path, channel, sleep=delays.append)
    assert delays == []
    channel.disconnect.assert_not_called()
