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
from ..runtime import AttentionManager, EmergencyStop, Event, EventBus, Notification
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


def send_owner(root: Path, text: str, *, sender: Callable[[str, str, str], dict] | None = None) -> dict:
    """Send one assistant-lane message to the paired owner chat.

    Callers (the heartbeat) must already hold the care grant and cap. This
    refuses without the pairing, token, or with the emergency stop engaged;
    "not_configured" is an honest result, never a fake success.
    """
    try:
        owner = pairing(root)
    except IntegrationError as exc:
        return {"status": "refused", "detail": str(exc)}
    if not owner.get("enabled"):
        return {"status": "not_configured", "detail": "Telegram owner is not paired"}
    if EmergencyStop(root).active():
        return {"status": "refused", "detail": "emergency stop engaged"}
    token = os.environ.get("GHOST_TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return {"status": "not_configured", "detail": "GHOST_TELEGRAM_BOT_TOKEN is not set"}
    try:
        response = (sender or _bot_api_send)(token, owner["chat_id"], redact_secrets(text))
    except Exception as exc:
        return {"status": "failed", "detail": f"{type(exc).__name__}"}
    ok = bool(response.get("ok"))
    return {"status": "sent" if ok else "failed",
            "message_id": (response.get("result") or {}).get("message_id")}


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

    @staticmethod
    def _stop_request(content: str) -> bool:
        """Owner emergency stop words. Resume stays local on purpose."""
        return content.strip().casefold().rstrip("!.") in {"stop", "/stop", "emergency stop", "/emergency_stop"}

    def _owner_fact(self, content: str):
        """Statements (not questions) about the owner become owner facts (LL0)."""
        from ..owner import parse_statement, tell
        text = content.strip()
        if "?" in text or len(text) > 1000 or parse_statement(text)["kind"] == "note":
            return None
        return tell(self.root, text, source="telegram")

    def _is_owner(self, message) -> bool:
        """Paired private owner chat, transport not shut down (emergency stop not considered)."""
        owner = pairing(self.root)
        return (not self.stopped.is_set() and owner["enabled"] and message.channel == "telegram"
                and message.sender == owner["user_id"] and message.conversation_id == owner["chat_id"])

    def authorized(self, message) -> bool:
        return self._is_owner(message) and not EmergencyStop(self.root).active()

    @staticmethod
    def _resume_request(content: str) -> bool:
        """Owner words that clear the emergency stop (owner decision 2026-11-22)."""
        return content.strip().casefold().rstrip("!.") in {"resume", "/resume", "clear stop", "/clear_stop"}

    def _resume(self, message) -> None:
        """Clear the emergency stop from the paired owner chat, exactly once per message."""
        identifier = hashlib.sha256(f"{message.conversation_id}:{message.message_id}".encode()).hexdigest()
        path = state_root(self.root) / "telegram-receipts" / f"{identifier}.json"
        with exclusive_lock(path.with_suffix(".lock")):
            if path.exists():
                return
            receipt = {"message_id": message.message_id, "received_at": time.time(), "status": "sending",
                       "task_id": "emergency_resume"}
            atomic_json(path, receipt)
            from ..runtime import Scheduler
            released = Scheduler(self.root).release_emergency_stop_holds()
            EmergencyStop(self.root).clear()
            EventBus(self.root).emit(Event("owner.contact", {"channel": "telegram", "message_id": message.message_id},
                                           source="owner:telegram"))
            delivered = self.channel.send(message.conversation_id,
                                          f"Emergency stop cleared from Telegram. {len(released)} job(s) requeued.",
                                          conversation_id=message.message_id)
            receipt.update(status="delivered" if delivered else "delivery_failed", finished_at=time.time())
            atomic_json(path, receipt)

    def handle(self, message):
        if (not message.message_id.isdecimal() or not isinstance(message.content, str)
                or not 0 < len(message.content) <= 12_000):
            return
        if (EmergencyStop(self.root).active() and self._is_owner(message)
                and self._resume_request(message.content)):
            self._resume(message)
            return
        if not self.authorized(message):
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
            # LL2: every authorized owner message is a presence signal.
            EventBus(self.root).emit(Event("owner.contact", {"channel": "telegram",
                                                             "message_id": message.message_id},
                                           source="owner:telegram"))
            try:
                if self._stop_request(message.content):
                    EmergencyStop(self.root).engage("owner via telegram")
                    from ..runtime import Scheduler
                    Scheduler(self.root).hold_all("owner via telegram")  # same as `ghost emergency-stop engage`
                    receipt["task_id"] = "emergency_stop"
                    receipt["status"] = "sending"
                    atomic_json(path, receipt)
                    delivered = self.channel.send(
                        message.conversation_id,
                        "Emergency stop engaged. Ghost holds all consequential work. Send 'resume' to clear it (or locally: ghost emergency-stop clear)",
                        conversation_id=message.message_id)
                    receipt["status"] = "delivered" if delivered else "delivery_failed"
                    return
                idea = re.fullmatch(r"\s*/?build\s+([1-9])\s*", message.content, flags=re.IGNORECASE)
                build_goal = None if idea else (
                    self._sandbox_request(message.content) if self.allow_sandbox_builds else None)
                fact = None if (build_goal or idea) else self._owner_fact(message.content)
                if idea:
                    from ..briefing import BriefingError, build as build_idea
                    if not self.allow_sandbox_builds:
                        content = "Sandbox builds are off for this Telegram session (serve --allow-sandbox-builds)."
                    else:
                        try:
                            queued = build_idea(self.root, int(idea.group(1)), self.model)
                            content = (f"Queued prototype {idea.group(1)} ({queued.get('title', '')}) as "
                                       f"{queued['job_id']}. I'll report the tested result; nothing goes live.")
                        except BriefingError as exc:
                            content = f"Not queued: {exc}"
                    result = {"task_id": "owner_build", "content": content}
                elif fact:
                    result = {"task_id": fact["memory_id"],
                              "content": f"Noted ({fact['kind']}: {fact['subject']}). "
                                         f"I'll work on it in the next idle window. Why: ghost why {fact['memory_id']}"}
                elif build_goal:
                    job = queue_sandbox_outcome(self.root, build_goal, self.model,
                                                source="telegram", priority=35)
                    result = {"task_id": job.job_id,
                              "content": f"Queued a sandbox build ({job.job_id}). Ghost will research, implement, test, and report; nothing is promoted live automatically."}
                else:
                    try:
                        from . import session_tools
                        extra = {"code_session": True} if session_tools.enabled(self.root) else {}
                        result = self.responder(self.root, message.content, self.model, memory=self.memory,
                                                channel="telegram", identity="owner:telegram", **extra)
                    except Exception as exc:
                        # Tell the owner instead of failing silently; nothing was sent yet.
                        result = {"task_id": "responder_unavailable",
                                  "content": f"I can't answer chat questions right now ({type(exc).__name__}: "
                                             "no model reachable). Facts, 'stop' and 'build N' still work."}
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


def _monitor_transport(root: Path, channel, stopped: threading.Event, *, sleep=time.sleep,
                       initial_delay: float = 1.0, max_delay: float = 60.0,
                       after_reconnect: Callable[[], None] | None = None) -> None:
    """Watch the transport and reconnect with exponential backoff on failure.

    Delays grow 1, 2, 4 ... capped at max_delay seconds and reset after a
    healthy status. The emergency stop is checked on every retry and ends the
    loop; upstream redeliveries stay safe because the receipt ledger dedups.
    """
    from openjarvis.channels._stubs import ChannelStatus
    delay = initial_delay
    while not stopped.is_set():
        if not pairing(root)["enabled"]:
            return
        if channel.status() == ChannelStatus.ERROR:
            if EmergencyStop(root).active():
                raise IntegrationError("Emergency stop engaged during transport failure; not retrying.")
            sleep(delay)
            if stopped.is_set():
                return
            delay = min(delay * 2, max_delay)
            channel.disconnect()
            channel.connect()
            if after_reconnect is not None and channel.status() != ChannelStatus.ERROR:
                after_reconnect()
            continue
        delay = initial_delay
        sleep(0.5)


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
    if run_worker and not allow_sandbox_builds:
        raise IntegrationError("--run-worker requires --allow-sandbox-builds.")
    token_id = hashlib.sha256(token.encode()).hexdigest()
    # One bot poller across Ghost projects, not just one per working directory.
    lock = Path.home() / ".ghost" / "telegram-locks" / f"{token_id}.lock"
    with exclusive_lock(lock):
        channel.connect()
        if run_worker:
            worker_thread = _start_worker(root, worker_stop)
        mode = "read-only + sandbox build queue"
        if run_worker:
            mode += " + worker"
        elif not allow_sandbox_builds:
            mode = "read-only"
        print(f"Telegram transport started (paired private chat, {mode}). Ctrl+C stops it.", flush=True)
        try:
            _monitor_transport(root, channel, bridge.stopped)
        finally:
            bridge.stopped.set()
            worker_stop.set()
            channel.disconnect()
            if worker_thread is not None:
                worker_thread.join(timeout=2.0)


def _start_worker(root: Path, stop: threading.Event) -> threading.Thread:
    """Run Ghost's existing bounded worker beside a Telegram transport until `stop` is set."""
    from ..cli import _execute_queued_outcome
    from ..worker import OutcomeWorker

    def worker_loop():
        worker = OutcomeWorker(root)
        while not stop.is_set():
            try:
                result = worker.run_next(lambda scheduled: _execute_queued_outcome(root, scheduled))
                if result is not None:
                    print(f"Telegram worker {result.status}: {result.job_id} ({result.detail})", flush=True)
                else:
                    stop.wait(1.0)
            except Exception as exc:
                # The durable job/worker records the normal failure path; keep the
                # transport alive if the loop itself hits an unexpected boundary error.
                print(f"Telegram worker loop error: {type(exc).__name__}", flush=True)
                stop.wait(1.0)

    thread = threading.Thread(target=worker_loop, name="ghost-telegram-worker", daemon=True)
    thread.start()
    return thread


# -- ghost-bot relay (one poller per token stays on ghostpi) ----------------------

RELAY_INBOX = "~/ghost-hub/state/ghost-inbox.jsonl"


class RelayMessage:
    """The fields TelegramBridge.handle reads, built from one ghost-bot inbox row."""

    def __init__(self, row: dict) -> None:
        self.channel = "telegram"
        self.sender = str(row.get("from_id", ""))
        self.conversation_id = str(row.get("chat_id", ""))
        self.message_id = str(row.get("message_id", ""))
        self.content = row.get("text", "")


class BotApiChannel:
    """Reply path for relayed messages: Bot API sendMessage only (no polling)."""

    def __init__(self, token: str, sender: Callable[[str, str, str], dict] | None = None) -> None:
        self.token = token
        self.sender = sender or _bot_api_send

    def send(self, chat_id: str, text: str, conversation_id: str = "") -> bool:
        try:
            parts = [text[i:i + 3900] for i in range(0, max(len(text), 1), 3900)]
            return all(bool(self.sender(self.token, chat_id, part).get("ok")) for part in parts)
        except Exception:
            return False


def _relay_cursor_path(root: Path) -> Path:
    return state_root(root) / "telegram-relay-cursor.json"


def _dispatch(bridge: "TelegramBridge", message, executor=None) -> None:
    """Hand one message to the bridge. With an executor everything except stop/resume is queued
    (one worker, in order), so a long code session can never delay the emergency stop."""
    content = message.content
    urgent = isinstance(content, str) and (bridge._stop_request(content) or bridge._resume_request(content))
    if executor is None or urgent:
        bridge.handle(message)
    else:
        executor.submit(bridge.handle, message)


def relay_lines(root: Path, bridge: "TelegramBridge", lines, executor=None) -> int:
    """Feed inbox rows (JSON lines) to the bridge, committing a line cursor after each.

    Receipts dedup by chat:message_id, so a row re-read after a crash is never
    answered twice. Malformed rows are skipped but still advance the cursor.
    """
    path = _relay_cursor_path(root)
    try:
        line_no = int(json.loads(path.read_text(encoding="utf-8")).get("line", 0))
    except (OSError, ValueError):
        line_no = 0
    handled = 0
    for raw in lines:
        line_no += 1
        try:
            row = json.loads(raw)
        except ValueError:
            row = None
        if isinstance(row, dict) and isinstance(row.get("text"), str):
            _dispatch(bridge, RelayMessage(row), executor)
            handled += 1
        atomic_json(path, {"line": line_no, "updated_at": time.time()})
    return handled


def relay_serve(root: Path, model: str, *, host: str = "ghostpi", memory=False,
                allow_sandbox_builds=False, run_worker=False, popen=None, sleep=time.sleep,
                rounds: int | None = None, fallback_after: float = 180.0, fallback=None,
                clock=time.monotonic) -> None:
    """Follow ghost-bot's owner inbox on ghostpi over one SSH `tail -F` and answer via Bot API.

    ghostpi keeps the only getUpdates poller for the shared token; Ghost Core
    stays the only authority (pairing, receipts, emergency stop, policy).
    """
    import subprocess
    owner = pairing(root)
    if not owner["enabled"]:
        raise IntegrationError("Pair Telegram locally before relaying.")
    if run_worker and not allow_sandbox_builds:
        raise IntegrationError("--run-worker requires --allow-sandbox-builds.")
    token = os.environ.get("GHOST_TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise IntegrationError("Set GHOST_TELEGRAM_BOT_TOKEN in this process.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", host):
        raise IntegrationError("Relay host must be a plain SSH host alias.")
    reconcile_receipts(root)
    bridge = TelegramBridge(root, BotApiChannel(token), model, memory=memory,
                            allow_sandbox_builds=allow_sandbox_builds)
    worker_stop = threading.Event()
    worker = _start_worker(root, worker_stop) if run_worker else None
    popen = popen or subprocess.Popen
    delay = 1.0
    last_healthy = clock()
    from concurrent.futures import ThreadPoolExecutor
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ghost-chat")
    fallback = fallback or (lambda: fallback_poll(root, bridge, token, host, popen=popen, executor=executor))
    print(f"Telegram relay following {host}:{RELAY_INBOX}. Ctrl+C stops it.", flush=True)
    try:
        # Keeps listening during an emergency stop: the bridge drops everything
        # except the owner's 'resume'. `rounds` bounds SSH sessions (tests).
        while rounds is None or rounds > 0:
            rounds = None if rounds is None else rounds - 1
            try:
                start = int(json.loads(_relay_cursor_path(root).read_text(encoding="utf-8")).get("line", 0))
            except (OSError, ValueError):
                start = 0
            proc = popen(["ssh", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=30", host,
                          f"touch {RELAY_INBOX} && tail -n +{start + 1} -F {RELAY_INBOX}"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
            opened = clock()
            try:
                if relay_lines(root, bridge, proc.stdout, executor):
                    delay = 1.0
            finally:
                proc.kill()
            if clock() - opened >= 30:
                last_healthy = clock()  # the ssh session lived: the Pi was there
            elif clock() - last_healthy >= fallback_after:
                # ghostpi (and ghost-bot, the only poller) has been unreachable for minutes:
                # poll Telegram from the PC until the Pi answers again, then hand back.
                print("ghostpi unreachable; PC fallback is polling Telegram", flush=True)
                fallback()
                last_healthy, delay = clock(), 1.0
                continue
            sleep(delay)  # ssh dropped (PC slept, Wi-Fi): reconnect with backoff
            delay = min(delay * 2, 60.0)
    finally:
        if rounds is None:
            bridge.stopped.set()  # service exit: revoke the in-flight reply and drop queued messages
        executor.shutdown(wait=True, cancel_futures=rounds is None)  # bounded (test) runs flush the queue first
        bridge.stopped.set()
        worker_stop.set()
        if worker is not None:
            worker.join(timeout=2.0)


_PI_COMMANDS = ("/status", "/wake", "/dns", "/setphone", "/honeypot", "/print", "/brief", "/fw", "/sethome", "/claim")


def fallback_poll(root: Path, bridge: "TelegramBridge", token: str, host: str, *,
                  get=None, probe=None, popen=None, probe_every: float = 60.0, clock=time.monotonic,
                  executor=None) -> str:
    """PC-side getUpdates while ghostpi is down. Returns why it stopped ("pi_back" or "conflict").

    Only safe because ghost-bot (the normal sole poller) is unreachable: a 409 Conflict means
    another poller is back and Ghost hands polling over at once. Owner messages go through the
    same bridge as relayed ones; Pi hub commands get an honest "ghostpi is offline" reply.
    """
    import subprocess
    if get is None:
        import requests

        def get(offset):
            response = requests.get(f"https://api.telegram.org/bot{token}/getUpdates",
                                    params={"offset": offset, "timeout": 25,
                                            "allowed_updates": json.dumps(["message"])}, timeout=40)
            return response.status_code, response.json()
    if probe is None:
        def probe():
            return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host, "true"],
                                  capture_output=True, timeout=15).returncode == 0
    path = state_root(root) / "telegram-fallback-offset.json"
    try:
        offset = int(json.loads(path.read_text(encoding="utf-8")).get("offset", 0))
    except (OSError, ValueError):
        offset = 0
    owner = pairing(root)
    EventBus(root).emit(Event("telegram.fallback", {"state": "started"}, source="relay"))
    last_probe = clock()
    reason = "pi_back"
    while True:
        if clock() - last_probe >= probe_every:
            last_probe = clock()
            if probe():
                break
        try:
            status, data = get(offset)
        except Exception:
            time.sleep(5)
            continue
        if status == 409:
            reason = "conflict"
            break
        for update in data.get("result", []) if isinstance(data, dict) else []:
            offset = max(offset, int(update.get("update_id", 0)) + 1)
            m = update.get("message") or {}
            text, chat = m.get("text", ""), (m.get("chat") or {}).get("id")
            if text and str(chat) == owner.get("chat_id") and text.split()[0].split("@")[0].lower() in _PI_COMMANDS:
                bridge.channel.send(str(chat), "ghostpi is offline, so hub commands are unavailable. "
                                    "Ghost on the PC is answering everything else.")
            elif text:
                _dispatch(bridge, RelayMessage({"message_id": m.get("message_id"), "chat_id": chat,
                                                "from_id": (m.get("from") or {}).get("id"), "text": text}), executor)
            atomic_json(path, {"offset": offset, "updated_at": time.time()})
    EventBus(root).emit(Event("telegram.fallback", {"state": "stopped", "reason": reason}, source="relay"))
    return reason
