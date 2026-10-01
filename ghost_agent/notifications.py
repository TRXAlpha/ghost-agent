"""Notification system for Ghost AI-OS.

Provides: unified routing, quiet hours, batching, priority levels, escalation,
per-channel preferences, and Telegram integration for alerts.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .utils.storage import atomic_json, exclusive_lock


@dataclass
class Notification:
    notification_id: str
    title: str
    body: str
    priority: str = "normal"   # low | normal | high | urgent
    channel: str = "telegram"  # telegram | email | desktop
    created_at: float = field(default_factory=time.time)
    sent_at: float = 0.0
    failed_at: float = 0.0
    failure_reason: str = ""
    acknowledged_at: float = 0.0


@dataclass
class QuietHours:
    enabled: bool = False
    start_hour: int = 22
    end_hour: int = 8
    suppress_urgent: bool = False   # urgent bypass quiet hours even when enabled


@dataclass
class ChannelPreferences:
    channel: str
    enabled: bool = True
    priority_min: str = "low"   # minimum priority to deliver on this channel


class NotificationStore:
    """Persistent notification state under .ghost/notifications/."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.dir_path = self.root / ".ghost" / "notifications"
        self.queue_path = self.dir_path / "queue.json"
        self.sent_path = self.dir_path / "sent.json"
        self.config_path = self.dir_path / "config.json"
        self.dir_path.mkdir(parents=True, exist_ok=True)
        self._ensure_files()

    def _ensure_files(self) -> None:
        for path in (self.queue_path, self.sent_path):
            if not path.exists():
                path.write_text("[]", encoding="utf-8")
        if not self.config_path.exists():
            self.config_path.write_text(json.dumps({
                "quiet_hours": asdict(QuietHours()),
                "channels": [asdict(ChannelPreferences("telegram", True, "normal"))],
                "batch_interval_seconds": 300,
            }), encoding="utf-8")

    # -- persistence --------------------------------------------------------

    @staticmethod
    def _load_list(path: Path) -> list[dict[str, Any]]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    @staticmethod
    def _save_list(path: Path, items: list[dict[str, Any]]) -> None:
        atomic_json(path, items)

    # -- queue --------------------------------------------------------------

    def enqueue(self, title: str, body: str,
                priority: str = "normal",
                channel: str = "telegram") -> Notification:
        nid = f"n_{hashlib.sha1(f'{title}|{body}|{time.time_ns()}'.encode()).hexdigest()[:12]}"
        notif = Notification(
            notification_id=nid, title=title[:200], body=body[:2000],
            priority=priority, channel=channel)
        with exclusive_lock(self.queue_path.with_suffix(".lock")):
            items = self._load_list(self.queue_path)
            items.append(asdict(notif))
            self._save_list(self.queue_path, items)
        return notif

    def dequeue(self, limit: int = 10) -> list[Notification]:
        with exclusive_lock(self.queue_path.with_suffix(".lock")):
            items = self._load_list(self.queue_path)
            notifications = [Notification(**item) for item in items[:limit]]
            self._save_list(self.queue_path, items[limit:])
        return notifications

    def mark_sent(self, notification_id: str) -> None:
        with exclusive_lock(self.queue_path.with_suffix(".lock")):
            queue = self._load_list(self.queue_path)
            queue = [i for i in queue if i.get("notification_id") != notification_id]
            self._save_list(self.queue_path, queue)
        with exclusive_lock(self.sent_path.with_suffix(".lock")):
            sent = self._load_list(self.sent_path)
            for item in sent:
                if item.get("notification_id") == notification_id:
                    return
            for item in queue if "queue" in dir() else []:
                pass
            # Mark from the dequeued item
            item = {"notification_id": notification_id, "sent_at": time.time()}
            sent.append(item)
            self._save_list(self.sent_path, sent[-1000:])  # keep last 1000

    def mark_failed(self, notification_id: str, reason: str) -> None:
        with exclusive_lock(self.queue_path.with_suffix(".lock")):
            items = self._load_list(self.queue_path)
            for item in items:
                if item.get("notification_id") == notification_id:
                    item["failed_at"] = time.time()
                    item["failure_reason"] = reason[:400]
                    break
            self._save_list(self.queue_path, items)

    def acknowledge(self, notification_id: str) -> None:
        with exclusive_lock(self.queue_path.with_suffix(".lock")):
            items = self._load_list(self.queue_path)
            for item in items:
                if item.get("notification_id") == notification_id:
                    item["acknowledged_at"] = time.time()
                    break
            self._save_list(self.queue_path, items)

    # -- config -------------------------------------------------------------

    def get_quiet_hours(self) -> QuietHours:
        try:
            cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
            return QuietHours(**cfg.get("quiet_hours", {}))
        except (OSError, ValueError):
            return QuietHours()

    def set_quiet_hours(self, quiet_hours: QuietHours) -> None:
        cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
        cfg["quiet_hours"] = asdict(quiet_hours)
        self.config_path.write_text(json.dumps(cfg), encoding="utf-8")

    def get_channel_prefs(self, channel: str) -> Optional[ChannelPreferences]:
        try:
            cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
            for ch in cfg.get("channels", []):
                if ch.get("channel") == channel:
                    return ChannelPreferences(**ch)
        except (OSError, ValueError):
            pass
        return None

    def set_channel_prefs(self, prefs: ChannelPreferences) -> None:
        cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
        channels = cfg.get("channels", [])
        for i, ch in enumerate(channels):
            if ch.get("channel") == prefs.channel:
                channels[i] = asdict(prefs)
                cfg["channels"] = channels
                self.config_path.write_text(json.dumps(cfg), encoding="utf-8")
                return
        channels.append(asdict(prefs))
        cfg["channels"] = channels
        self.config_path.write_text(json.dumps(cfg), encoding="utf-8")

    # -- routing -----------------------------------------------------------

    def should_deliver(self, notification: Notification) -> bool:
        prefs = self.get_channel_prefs(notification.channel)
        if not prefs or not prefs.enabled:
            return False
        priority_order = {"low": 0, "normal": 1, "high": 2, "urgent": 3}
        min_priority = priority_order.get(prefs.priority_min, 0)
        notif_priority = priority_order.get(notification.priority, 1)
        if notif_priority < min_priority:
            return False
        # Quiet hours check (wraps midnight: e.g. 22 -> 8 covers 22:00-23:59 and 00:00-07:59)
        qh = self.get_quiet_hours()
        if qh.enabled:
            now = time.localtime()
            hour = now.tm_hour
            if qh.start_hour == qh.end_hour:
                in_quiet = False  # equal endpoints disable quiet hours
            elif qh.start_hour < qh.end_hour:
                in_quiet = qh.start_hour <= hour < qh.end_hour
            else:
                # Wrap-around: quiet hours span midnight (e.g. 22:00–08:00).
                # Hour 8 is IN the range (00:00–07:59 = quiet, 08:00–21:59 = not quiet).
                in_quiet = hour >= qh.start_hour or hour < qh.end_hour
            if in_quiet:
                if notification.priority != "urgent" or not qh.suppress_urgent:
                    return False
        return True

    # -- sending (Telegram) -------------------------------------------------

    def send_telegram(self, notification: Notification) -> bool:
        """Send via Telegram. Returns True on success."""
        if notification.channel != "telegram":
            return False
        # Lazy import to avoid requiring Telegram unless used
        try:
            from .integrations.telegram import TelegramAdapter
            adapter = TelegramAdapter(self.root)
            adapter.send_message(
                f"*{notification.title}*\n\n{notification.body}",
                parse_mode="Markdown")
            return True
        except Exception:
            return False

    def process_queue(self) -> dict[str, Any]:
        """Deliver pending notifications, respecting preferences."""
        results = {"sent": 0, "failed": 0, "deferred": 0, "pending": 0}
        pending = self._load_list(self.queue_path)
        for item in pending:
            notif = Notification(**item)
            if self.should_deliver(notif):
                success = self.send_telegram(notif)
                if success:
                    self.mark_sent(notif.notification_id)
                    results["sent"] += 1
                else:
                    self.mark_failed(notif.notification_id, "send failed")
                    results["failed"] += 1
            else:
                results["deferred"] += 1
        results["pending"] = len(self._load_list(self.queue_path))
        return results

    # -- stats --------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        queue = self._load_list(self.queue_path)
        sent = self._load_list(self.sent_path)
        return {
            "pending": len(queue),
            "sent_today": sum(1 for s in sent if s.get("sent_at", 0) > time.time() - 86400),
            "total_sent": len(sent),
            "quiet_hours": asdict(self.get_quiet_hours()),
        }