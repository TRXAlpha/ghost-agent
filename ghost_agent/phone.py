"""Ghost Companion (Android, ghost-mobile flavor v8companion) as a sensor and actuator.

The phone logs GPS and app/screen/unlock usage and serves them over Tailscale with a
bearer token (GHOST_PHONE_URL, GHOST_PHONE_TOKEN). Ghost Core pulls, keeps the raw log
under .ghost/phone/, and derives situation fields. Coordinates never leave this PC and
never reach a model: only home/away and app/screen state do.
Actuation (DND during the owner's quiet windows) needs the care.phone_actions grant.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from .utils.storage import atomic_json

FRESH = 900


def _dir(root: Path) -> Path:
    path = Path(root).resolve() / ".ghost" / "phone"
    path.mkdir(parents=True, exist_ok=True)
    return path


def configured() -> bool:
    return bool(os.environ.get("GHOST_PHONE_URL") and os.environ.get("GHOST_PHONE_TOKEN"))


def _http(method: str, path: str, body: Optional[dict] = None) -> dict:
    import requests
    url = os.environ["GHOST_PHONE_URL"].rstrip("/") + path
    headers = {"Authorization": "Bearer " + os.environ["GHOST_PHONE_TOKEN"]}
    response = requests.request(method, url, json=body, headers=headers, timeout=10)
    response.raise_for_status()
    return response.json()


def pull(root: Path, http: Callable[..., dict] = _http) -> list[dict[str, Any]]:
    """Fetch events after the stored cursor and append them to .ghost/phone/events.jsonl."""
    folder = _dir(root)
    cursor_path = folder / "cursor.json"
    try:
        since = int(json.loads(cursor_path.read_text(encoding="utf-8")).get("seq", 0))
    except (OSError, ValueError):
        since = 0
    data = http("GET", f"/events?since={since}&limit=2000")
    events = [e for e in data.get("events", []) if isinstance(e, dict) and isinstance(e.get("seq"), int)]
    if int(data.get("last_seq", since)) < since:
        since, events = 0, []  # app reinstalled: sequence restarted; next pull starts over
    if events:
        with (folder / "events.jsonl").open("a", encoding="utf-8") as stream:
            for event in events:
                stream.write(json.dumps(event) + "\n")
        since = max(e["seq"] for e in events)
    atomic_json(cursor_path, {"seq": since, "pulled_at": time.time()})
    return events


def _recent(root: Path, kinds: set[str], limit: int = 4000) -> list[dict[str, Any]]:
    path = _dir(root) / "events.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("kind") in kinds:
            out.append(event)
    return out


def _distance_m(a: dict, b: dict) -> float:
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lon"] - a["lon"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371000.0 * math.asin(math.sqrt(h))


def home_point(root: Path, run: Optional[Callable[[list[str]], str]] = None) -> Optional[dict]:
    """Home coordinates: .ghost/owner/home.json, else copied once from ghostpi's /sethome file."""
    path = Path(root).resolve() / ".ghost" / "owner" / "home.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    runner = run or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout)
    try:
        home = json.loads(runner(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "ghostpi",
                                  "cat ~/ghost-hub/state/ghost-home.json"]))
        point = {"lat": float(home["lat"]), "lon": float(home["lon"])}
    except Exception:
        return None
    atomic_json(path, point)
    return point


def adapter(root: Path, http: Callable[..., dict] = _http,
            run: Optional[Callable[[list[str]], str]] = None) -> Callable[[float], dict]:
    """Situation adapter: phone_activity always; place when a fresh GPS fix and a home point exist."""
    from .situation import observation

    def read(now: float) -> dict[str, dict]:
        if http is _http and not configured():
            return {}
        pull(root, http)
        out: dict[str, dict] = {}
        usage = _recent(root, {"app", "screen_on", "screen_off", "unlock"})
        if usage:
            last = usage[-1]
            screen = next((e["kind"] for e in reversed(usage) if e["kind"] in {"screen_on", "screen_off"}), "")
            unlock = next((e["at"] / 1000 for e in reversed(usage) if e["kind"] == "unlock"), None)
            app = next((e["data"].get("pkg", "") for e in reversed(usage) if e["kind"] == "app"), "")
            out["phone_activity"] = observation(
                {"screen": screen.replace("screen_", "") or "unknown", "app": app[:80],
                 "last_unlock_min_ago": None if unlock is None else round((now - unlock) / 60)},
                0.9, "phone_companion", FRESH, last["at"] / 1000)
        fixes = _recent(root, {"location"}, limit=500)
        home = home_point(root, run) if fixes else None
        if fixes and home and now - fixes[-1]["at"] / 1000 <= FRESH:
            near = _distance_m(fixes[-1]["data"], home) <= 150
            out["place"] = observation("home" if near else "away", 0.95, "phone_gps", FRESH, fixes[-1]["at"] / 1000)
        return out
    return read


def sync_quiet_dnd(root: Path, now: float, http: Callable[..., dict] = _http) -> str:
    """DND on during the owner's weekly quiet windows, off after; only on transitions."""
    from .heartbeat import load_grants
    from .owner import quiet_windows
    if not load_grants(root)["care.phone_actions"]["enabled"] or (http is _http and not configured()):
        return "off"
    stamp = datetime.fromtimestamp(now)
    minute = stamp.hour * 60 + stamp.minute
    want = any(day == stamp.weekday() and s <= minute < e for day, s, e in quiet_windows(root))
    state_path = _dir(root) / "dnd.json"
    try:
        have = json.loads(state_path.read_text(encoding="utf-8")).get("on")
    except (OSError, ValueError):
        have = None
    if have == want:
        return "unchanged"
    try:
        http("POST", "/dnd", {"on": want})
        if want or have is not None:
            http("POST", "/ringer", {"mode": "silent" if want else "normal"})
    except Exception as exc:
        return f"unreachable: {type(exc).__name__}"  # phone off/away from tailnet: retry next tick
    atomic_json(state_path, {"on": want, "at": now})
    return "on" if want else "cleared"
