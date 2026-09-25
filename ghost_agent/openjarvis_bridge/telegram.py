"""Owner-paired, read-only Ghost sessions over OpenJarvis's Telegram transport.

No second Telegram API client/poller and no remote shell. Pair locally before
serving. Ambiguous deliveries are retained for review, never blindly replayed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

from ..proactive import queue_sandbox_outcome
from ..runtime import EmergencyStop
from ..security import redact_secrets
from ..utils.storage import atomic_json, exclusive_lock
from .runtime import IntegrationError, require_upstream, state_root


def pair(root: Path, user_id: str, chat_id: str) -> dict:
    # Private chats only for this first qualification. Group membership alone
    # must never become an identity/permission grant to the owner's assistant.
    if not user_id.isdecimal() or int(user_id) <= 0 or chat_id != user_id:
        raise IntegrationError("Pair a positive Telegram user ID with that user's matching private-chat ID.")
    value = {"user_id": user_id, "chat_id": chat_id, "enabled": True,
             "scope": "read_only_chat", "paired_at": time.time()}
    with exclusive_lock(state_root(root) / "telegram-pairing.lock"):
        atomic_json(state_root(root) / "telegram-pairing.json", value)
    return value


def pairing(root: Path) -> dict:
    try:
        value = json.loads((state_root(root) / "telegram-pairing.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"enabled": False}
    except (OSError, ValueError) as exc:
        raise IntegrationError("Telegram pairing is unreadable; refusing access.") from exc
    if not isinstance(value, dict) or type(value.get("enabled")) is not bool:
        raise IntegrationError("Invalid Telegram pairing; refusing access.")
    if value["enabled"] and (value.get("scope") != "read_only_chat" or
            not isinstance(value.get("user_id"), str) or not value["user_id"].isdecimal() or
            int(value["user_id"]) <= 0 or value.get("chat_id") != value["user_id"]):
        raise IntegrationError("Invalid Telegram owner identity.")
    return value


def revoke(root: Path) -> dict:
    with exclusive_lock(state_root(root) / "telegram-pairing.lock"):
        value = pairing(root)
        value.update(enabled=False, revoked_at=time.time())
        atomic_json(state_root(root) / "telegram-pairing.json", value)
    return value


class TelegramBridge:
    def __init__(self, root: Path, channel, model: str, *, memory=False, responder=None,
                 allow_sandbox_builds=False):
        self.root = Path(root).resolve()
        self.channel = channel
        self.model = model
        self.memory = memory
        self.allow_sandbox_builds = allow_sandbox_builds
        self.stopped = threading.Event()
        if responder is None:
            from .assistant import ask
            responder = ask
        self.responder = responder

    @staticmethod
    def _sandbox_request(content: str):
        """Extract an explicit build-shaped request; ordinary chat stays read-only."""
        match = re.match(r"^\s*(?:please\s+)?(?:/)?(build|accomplish|implement|prototype|create|fix)\b(?:\s*[:\-]?\s*)(.+?)\s*$",
                         content, flags=re.IGNORECASE | re.DOTALL)
        return match.group(2).strip() if match else None

    def authorized(self, message) -> bool:
        owner = pairing(self.root)
        return (not self.stopped.is_set() and owner["enabled"] and message.channel == "telegram"
                and message.sender == owner["user_id"] and message.conversation_id == owner["chat_id"]
                and not EmergencyStop(self.root).active())

    def handle(self, message):
        if (not self.authorized(message) or not message.message_id.isdecimal()
                or not isinstance(message.content, str) or not 0 < len(message.content) <= 12_000):
            return
        identifier = hashlib.sha256(f"{message.conversation_id}:{message.message_id}".encode()).hexdigest()
        path = state_root(self.root) / "telegram-receipts" / f"{identifier}.json"
        # No model output can edit this ledger: the read-only agent exposes only
        # imported-document retrieval. It has no arbitrary filesystem tools.
        with exclusive_lock(path.with_suffix(".lock")):
            if path.exists():
                return  # duplicate, failed, or interrupted: review instead of replay
            receipt = {"message_id": message.message_id, "received_at": time.time(), "status": "processing"}
            atomic_json(path, receipt)
            try:
                build_goal = self._sandbox_request(message.content) if self.allow_sandbox_builds else None
                if build_goal:
                    job = queue_sandbox_outcome(self.root, build_goal, self.model,
                                                source="telegram", priority=35)
                    result = {"task_id": job.job_id,
                              "content": f"Queued a sandbox build ({job.job_id}). Ghost will research, implement, test, and report; nothing is promoted live automatically."}
                else:
                    result = self.responder(self.root, message.content, self.model, memory=self.memory,
                                            channel="telegram", identity="owner:telegram")
                receipt["task_id"] = result["task_id"]
                if not self.authorized(message):
                    receipt["status"] = "revoked_before_delivery"
                    return
                # Persist BEFORE sending: a crash after Telegram accepts a send
                # is ambiguous; automatic replay could duplicate notifications.
                receipt["status"] = "sending"
                atomic_json(path, receipt)
                delivered = self.channel.send(message.conversation_id, redact_secrets(result["content"]),
                                              conversation_id=message.message_id)
                receipt["status"] = "delivered" if delivered else "delivery_failed"
            except Exception as exc:
                receipt.update(status="failed", error_type=type(exc).__name__)
            finally:
                receipt["finished_at"] = time.time()
                atomic_json(path, receipt)


def serve(root: Path, model: str, *, memory=False, allow_sandbox_builds=False,
          run_worker=False):
    require_upstream()
    from openjarvis.channels.telegram import TelegramChannel
    from openjarvis.channels._stubs import ChannelStatus

    owner = pairing(root)
    if not owner["enabled"]:
        raise IntegrationError("Pair Telegram locally before starting its transport.")
    token = os.environ.get("GHOST_TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise IntegrationError("Set GHOST_TELEGRAM_BOT_TOKEN in this process; do not put it in a task or document.")
    # The upstream callback must be async for python-telegram-bot 22.8. The
    # bootstrap applies the small, versioned patch; fail closed if omitted.
    import inspect
    source = inspect.getsource(TelegramChannel._poll_loop)
    if "async def _handle_msg" not in source or "await asyncio.to_thread(handler, cm)" not in source:
        raise IntegrationError("Apply patches/openjarvis-telegram.patch using the bootstrap before serving Telegram.")
    channel = TelegramChannel(bot_token=token, allowed_chat_ids=owner["chat_id"], parse_mode="")
    bridge = TelegramBridge(root, channel, model, memory=memory,
                            allow_sandbox_builds=allow_sandbox_builds)
    channel.on_message(bridge.handle)
    worker_stop = threading.Event()
    worker_thread = None
    if run_worker:
        if not allow_sandbox_builds:
            raise IntegrationError("--run-worker requires --allow-sandbox-builds.")
        from ..cli import _execute_queued_outcome
        from ..worker import OutcomeWorker

        def worker_loop():
            worker = OutcomeWorker(root)
            while not worker_stop.is_set():
                try:
                    result = worker.run_next(lambda scheduled: _execute_queued_outcome(root, scheduled))
                    if result is not None:
                        print(f"Telegram worker {result.status}: {result.job_id} ({result.detail})",
                              flush=True)
                    else:
                        worker_stop.wait(1.0)
                except Exception as exc:
                    # The durable job/worker records the normal failure path;
                    # keep the transport alive for later requests if the loop
                    # itself encounters an unexpected boundary error.
                    print(f"Telegram worker loop error: {type(exc).__name__}", flush=True)
                    worker_stop.wait(1.0)

    token_id = hashlib.sha256(token.encode()).hexdigest()
    # One bot poller across Ghost projects, not just one per working directory.
    lock = Path.home() / ".ghost" / "telegram-locks" / f"{token_id}.lock"
    with exclusive_lock(lock):
        channel.connect()
        if run_worker:
            worker_thread = threading.Thread(target=worker_loop, name="ghost-telegram-worker", daemon=True)
            worker_thread.start()
        mode = "read-only + sandbox build queue"
        if run_worker:
            mode += " + worker"
        elif not allow_sandbox_builds:
            mode = "read-only"
        print(f"Telegram transport started (paired private chat, {mode}). Ctrl+C stops it.", flush=True)
        try:
            while True:
                if not pairing(root)["enabled"]:
                    break
                if channel.status() == ChannelStatus.ERROR:
                    raise IntegrationError("Telegram transport failed; check credentials/network. No automatic duplicate poller started.")
                time.sleep(0.5)
        finally:
            bridge.stopped.set()
            worker_stop.set()
            channel.disconnect()
            if worker_thread is not None:
                worker_thread.join(timeout=2.0)
