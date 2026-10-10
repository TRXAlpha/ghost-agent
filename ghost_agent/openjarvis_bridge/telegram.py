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
from typing import Callable

from ..proactive import queue_sandbox_outcome
from ..runtime import AttentionManager, EmergencyStop, Notification
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


def token_fingerprint(token: str) -> str:
    """SHA-256 prefix (16 hex chars) — safe to store/display; never the token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def check_token_rotation(new_token: str, root: Path) -> dict:
    """BotFather-format sanity check plus stored-fingerprint comparison.

    No network call. The fingerprint file stores only a SHA-256 prefix; the
    token itself is never written or echoed.
    """
    from ..integrations.telegram import token_format_error

    error = token_format_error(new_token)
    if error:
        return {"well_formed": False, "error": error}
    fingerprint = token_fingerprint(new_token)
    stored = ""
    path = state_root(root) / "telegram-token-fingerprint.json"
    try:
        stored = str(json.loads(path.read_text(encoding="utf-8")).get("fingerprint", ""))
    except FileNotFoundError:
        stored = ""
    except (OSError, ValueError):
        return {"well_formed": True,
                "error": "Stored fingerprint file is unreadable; delete it and re-record."}
    return {"well_formed": True, "changed": fingerprint != stored,
            "same_as_recorded": fingerprint == stored, "fingerprint": fingerprint}


def record_token_fingerprint(token: str, root: Path) -> dict:
    """Persist the SHA-256 prefix of the (new) token; never the token itself."""
    from ..integrations.telegram import token_format_error

    error = token_format_error(token)
    if error:
        raise IntegrationError(f"Refusing to record: {error}")
    fingerprint = token_fingerprint(token)
    atomic_json(state_root(root) / "telegram-token-fingerprint.json", {"fingerprint": fingerprint})
    return {"fingerprint": fingerprint}


def token_health(root: Path) -> dict:
    """Credential-aware, network-free health snapshot for the bridge.

    token_present is bool-only (never the value). pairing_present mirrors the
    bridge pairing file. next_step names the single missing setup step. With no
    token in the environment no Bot API contact of any kind is attempted.
    """
    from ..integrations.telegram import token_present

    owner = pairing(root)
    token_ok = token_present()
    emergency = EmergencyStop(root).active()
    if not token_ok:
        next_step = "set TELEGRAM_BOT_TOKEN"
    elif not owner["enabled"]:
        next_step = "pair owner id"
    elif emergency:
        next_step = "clear the emergency stop"
    else:
        next_step = "ready for authorized live test"
    return {"token_present": token_ok, "pairing_present": bool(owner["enabled"]),
            "emergency_stop": emergency, "next_step": next_step}


def _bot_api_send(token: str, chat_id: str, text: str) -> dict:
    """One real Bot API sendMessage. Returns the parsed JSON response.

    Uses requests (already a Ghost dependency) against the official endpoint;
    raises on network errors and non-200 responses.
    """
    import requests

    response = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                             json={"chat_id": chat_id, "text": text}, timeout=15)
    if response.status_code != 200:
        raise IntegrationError(f"Telegram Bot API returned HTTP {response.status_code}.")
    return response.json()


def live_check(root: Path, chat_id: str, *, requester: Callable[[], dict] | None = None) -> dict:
    """Authorized single-ping live qualification.

    Refuses (IntegrationError, no network) unless ALL of: token present, owner
    pairing enabled, chat_id equals the paired chat, emergency stop clear.
    Sends exactly ONE harmless message and records the real result.
    """
    owner = pairing(root)
    if not owner["enabled"]:
        raise IntegrationError("Pair the owner first: ghost openjarvis telegram pair --user-id <id> --chat-id <id>.")
    if chat_id != owner["chat_id"]:
        raise IntegrationError("live-check only sends to the paired owner chat.")
    if EmergencyStop(root).active():
        raise IntegrationError("Emergency stop is engaged; clear it before a live check.")
    token = os.environ.get("GHOST_TELEGRAM_BOT_TOKEN", "").strip() or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise IntegrationError("Set TELEGRAM_BOT_TOKEN before a live check.")

    if requester is None:
        requester = lambda: _bot_api_send(token, owner["chat_id"], "Ghost live qualification ping")
    try:
        response = requester()
        ok = bool(response.get("ok")) and response.get("result", {}).get("message_id") is not None
        outcome = "success" if ok else "failure"
        detail = "" if ok else f"Bot API ok=false: {json.dumps(response)[:200]}"
    except Exception as exc:
        outcome = "failure"
        detail = f"{type(exc).__name__}: {exc}"
    record = {"outcome": outcome, "detail": detail, "chat_id": chat_id,
              "checked_at": time.time(),
              "fingerprint": token_fingerprint(token)}
    atomic_json(state_root(root) / "telegram-live-check.json", record)
    if outcome != "success":
        raise IntegrationError(f"live-check failed: {detail or 'non-ok Bot API response'}")
    return record


def revoke(root: Path) -> dict:
    with exclusive_lock(state_root(root) / "telegram-pairing.lock"):
        value = pairing(root)
        value.update(enabled=False, revoked_at=time.time())
        atomic_json(state_root(root) / "telegram-pairing.json", value)
    return value


def reconcile_receipts(root: Path) -> list[dict]:
    """Mark interrupted sends as explicitly uncertain and tell the owner once.

    A receipt still in processing/sending without finished_at means Ghost
    stopped mid-send: the owner message may or may not have been answered.
    """
    receipts_dir = state_root(root) / "telegram-receipts"
    reconciled: list[dict] = []
    for path in sorted(receipts_dir.glob("*.json")):
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # an unreadable receipt is not evidence; leave it for review
        if not isinstance(receipt, dict) or receipt.get("finished_at") is not None:
            continue
        if receipt.get("status") not in ("processing", "sending"):
            continue
        message_id = str(receipt.get("message_id", "unknown"))
        receipt["status"] = "uncertain"
        receipt["reconciled_at"] = time.time()
        atomic_json(path, receipt)
        AttentionManager(root).notify(Notification(
            "important",
            f"Telegram message {message_id} may or may not have been answered (Ghost stopped mid-send). Check the chat.",
            notification_id=f"tg-uncertain-{message_id}", source="telegram"))
        reconciled.append(receipt)
    return reconciled


def list_receipts(root: Path, status: str = "") -> list[dict]:
    """List delivery receipts, newest first; optionally filter by status."""
    receipts_dir = state_root(root) / "telegram-receipts"
    receipts: list[dict] = []
    for path in sorted(receipts_dir.glob("*.json")):
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(receipt, dict) and (not status or receipt.get("status") == status):
            receipts.append(receipt)
    return sorted(receipts, key=lambda r: float(r.get("received_at", 0.0)), reverse=True)


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
                # Also skip uncertain receipts: a redelivery of a message Ghost
                # may have already answered must never be processed again.
                return  # duplicate, failed, uncertain, or interrupted: review instead of replay
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
    # Before polling: any send interrupted by a previous crash becomes an
    # explicit uncertain record the owner is told about, never a silent replay.
    reconcile_receipts(root)
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
