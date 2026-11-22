"""Life layer LL3/LL3b: the fused situation record, ``.ghost/situation.json``.

Tier 1 of continuous attention: no model. Each adapter reads one existing
source (ActivityWatch REST, Windows idle, git commit times, Telegram contact
events, observations pushed by ghostpi or Home Assistant through Ghost Core)
and returns field observations. Every field carries value, confidence, source,
observed_at and ttl. A field older than its ttl reads as ``unknown``: nothing
is a fact unless a sensor measured it recently. Ghost Core is the only writer.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .runtime import Event, EventBus
from .utils.storage import atomic_json, exclusive_lock

FIELDS = ("place", "pc_activity", "current_project", "task_guess", "health", "owner_contact")
UNKNOWN = "unknown"
AW_URL = "http://localhost:5600/api/0"
_GAMES = ("factorio", "steam", "minecraft", "remoteplay", "ps remote", "gta", "chiaki")
_EDITORS = ("visual studio code", "code - insiders", "pycharm", "intellij", "android studio",
            "cursor", "notepad++", "neovim", "vim")
_SCHOOL = ("classroom", "assignment", "homework", "moodle", "canvas", "teams", "quiz")
# Fields a pushed observation may set (place from HA/AdGuard, health from HealthHub).
PUSHABLE = {"place", "health", "pc_activity", "current_project"}
_SOURCE_RE = re.compile(r"^[a-z0-9_.:-]{1,40}$")


def observation(value: Any, confidence: float, source: str, ttl: float,
                observed_at: Optional[float] = None) -> dict[str, Any]:
    """One field reading. Confidence is clamped to [0, 1]."""
    return {"value": value, "confidence": max(0.0, min(1.0, float(confidence))),
            "source": source, "observed_at": float(observed_at if observed_at is not None else time.time()),
            "ttl": float(ttl)}


def _unknown(reason: str) -> dict[str, Any]:
    return {"value": UNKNOWN, "confidence": 0.0, "source": reason, "observed_at": None, "ttl": 0.0}


def situation_path(root: Path) -> Path:
    return Path(root).resolve() / ".ghost" / "situation.json"


# -- adapters -----------------------------------------------------------------

def _http_json(url: str, timeout: float = 2.0) -> Any:
    import requests
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def activitywatch_adapter(fetch: Callable[[str], Any] = _http_json) -> Callable[[float], dict]:
    """PC activity from the ActivityWatch REST API (window + afk watchers)."""
    def read(now: float) -> dict[str, dict]:
        buckets = fetch(f"{AW_URL}/buckets/")
        window = next((b for b in buckets if b.startswith("aw-watcher-window")), None)
        afk = next((b for b in buckets if b.startswith("aw-watcher-afk")), None)
        out: dict[str, dict] = {}
        if afk:
            events = fetch(f"{AW_URL}/buckets/{afk}/events?limit=1")
            if events:
                status = events[0].get("data", {}).get("status", "")
                if status == "afk":
                    out["pc_activity"] = observation({"state": "afk"}, 0.9, "activitywatch", 300, now)
        if window and "pc_activity" not in out:
            events = fetch(f"{AW_URL}/buckets/{window}/events?limit=1")
            if events:
                data = events[0].get("data", {})
                app, title = str(data.get("app", ""))[:80], str(data.get("title", ""))[:160]
                out["pc_activity"] = observation({"state": "active", "app": app, "title": title},
                                                 0.9, "activitywatch", 300, now)
                project = project_from_title(app, title)
                if project:
                    out["current_project"] = observation(project, 0.7, "activitywatch", 900, now)
        return out
    return read


def project_from_title(app: str, title: str) -> str:
    """Folder name from an editor window title ("file - folder - Visual Studio Code")."""
    lower = f"{app} {title}".casefold()
    if not any(editor in lower for editor in _EDITORS):
        return ""
    parts = [part.strip() for part in re.split(r"\s[-—]\s", title) if part.strip()]
    parts = [p for p in parts if not any(editor in p.casefold() for editor in _EDITORS)]
    return parts[-1][:60] if parts else ""


def windows_idle_adapter(idle_fn: Optional[Callable[[], Optional[float]]] = None) -> Callable[[float], dict]:
    """Keyboard/mouse idle seconds from Windows. None (non-Windows) yields nothing."""
    def read(now: float) -> dict[str, dict]:
        fn = idle_fn
        if fn is None:
            from .proactive import idle_seconds
            fn = idle_seconds
        idle = fn()
        if idle is None:
            return {}
        state = "afk" if idle >= 600 else "active"
        return {"pc_activity": observation({"state": state, "idle_seconds": round(idle)},
                                           0.8, "windows_idle", 300, now)}
    return read


def git_adapter(repos: list[Path], run: Callable[[list[str]], str] | None = None) -> Callable[[float], dict]:
    """Current project from the most recent commit across the owner's repos (2 h window)."""
    def default_run(cmd: list[str]) -> str:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
    runner = run or default_run

    def read(now: float) -> dict[str, dict]:
        best: tuple[float, str] | None = None
        for repo in repos:
            try:
                stamp = float(runner(["git", "-C", str(repo), "log", "-1", "--format=%ct"]).strip() or 0)
            except (ValueError, OSError, subprocess.SubprocessError):
                continue
            if stamp and (best is None or stamp > best[0]):
                best = (stamp, Path(repo).name)
        if best and now - best[0] <= 7200:
            return {"current_project": observation(best[1], 0.6, "git", 7200 - (now - best[0]), best[0])}
        return {}
    return read


def owner_contact_adapter(root: Path) -> Callable[[float], dict]:
    """Last owner message from ``owner.contact`` events (Telegram, CLI)."""
    def read(now: float) -> dict[str, dict]:
        events = EventBus(root).recent(500, kind="owner.contact")
        if not events:
            return {}
        last = events[-1]
        return {"owner_contact": observation({"channel": last.payload.get("channel", ""),
                                              "at": last.created_at}, 1.0, "owner.contact",
                                             30 * 86400, last.created_at)}
    return read


def pushed_adapter(root: Path) -> Callable[[float], dict]:
    """Observations pushed through :func:`push_observation` (ghostpi, HA, HealthHub)."""
    def read(now: float) -> dict[str, dict]:
        path = Path(root).resolve() / ".ghost" / "situation-pushed.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            return {}  # unreadable pushes are not evidence
        return {k: v for k, v in data.items() if k in PUSHABLE and isinstance(v, dict)}
    return read


def ghostpi_place_adapter(host: str = "ghostpi", run: Callable[[list[str]], str] | None = None) -> Callable[[float], dict]:
    """Place from ghostpi's ghost-place timer (Telegram location, Tailscale, AdGuard); home/away only."""
    def default_run(cmd: list[str]) -> str:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout
    runner = run or default_run

    def read(now: float) -> dict[str, dict]:
        raw = runner(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host,
                      "cat ~/ghost-hub/state/ghost-place.json"])
        data = json.loads(raw)
        if data.get("value") not in {"home", "away", "unknown"} or type(data.get("observed_at")) not in (int, float):
            raise ValueError("ghost-place.json has an unexpected shape")
        if data["value"] == "unknown":
            return {}
        return {"place": observation(data["value"], data.get("confidence", 0.0), f"ghostpi:{data.get('source', '')}"[:60],
                                     min(float(data.get("ttl", 900)), 3600), data["observed_at"])}
    return read


def push_observation(root: Path, field: str, value: Any, *, confidence: float, source: str,
                     ttl: float) -> dict[str, Any]:
    """Record one externally measured field (e.g. place from HA Companion)."""
    if field not in PUSHABLE:
        raise ValueError(f"Field {field} cannot be pushed")
    if not _SOURCE_RE.fullmatch(source or ""):
        raise ValueError("Observation source must be a short identifier")
    if type(ttl) not in (int, float) or not 0 < ttl <= 7 * 86400:
        raise ValueError("ttl must be between 0 and 7 days")
    if len(json.dumps(value)) > 2000:
        raise ValueError("Observation value is too large")
    path = Path(root).resolve() / ".ghost" / "situation-pushed.json"
    with exclusive_lock(path.with_suffix(".lock")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data[field] = observation(value, confidence, source, ttl)
        atomic_json(path, data)
    return data[field]


def owner_repos(root: Path) -> list[Path]:
    """Repo paths from ``.ghost/owner/repos.txt`` (one per line), plus this project."""
    repos = [Path(root).resolve()]
    path = Path(root).resolve() / ".ghost" / "owner" / "repos.txt"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and Path(line).is_dir():
                repos.append(Path(line))
    return repos


def default_adapters(root: Path) -> dict[str, Callable[[float], dict]]:
    """Adapters in priority order: later ones only fill fields earlier ones left empty."""
    return {"pushed": pushed_adapter(root), "ghostpi_place": ghostpi_place_adapter(),
            "activitywatch": activitywatch_adapter(),
            "windows_idle": windows_idle_adapter(), "git": git_adapter(owner_repos(root)),
            "owner_contact": owner_contact_adapter(root)}


# -- fusion -------------------------------------------------------------------

def guess_task(fields: dict[str, dict]) -> dict[str, Any]:
    """Deterministic task_guess from pc_activity and current_project."""
    activity, project = fields.get("pc_activity", {}), fields.get("current_project", {})
    value = activity.get("value")
    if not isinstance(value, dict):
        return _unknown("no_pc_activity")
    conf = activity.get("confidence", 0.0) * 0.8
    if value.get("state") == "afk":
        return observation("idle", conf, "derived", activity.get("ttl", 300), activity.get("observed_at"))
    text = f"{value.get('app', '')} {value.get('title', '')}".casefold()
    proj = str(project.get("value", "")).casefold() if project.get("value") != UNKNOWN else ""
    if any(game in text for game in _GAMES):
        guess = "gaming"
    elif "ftc" in proj or "ftc" in text or "robot" in proj:
        guess = "ftc"
    elif any(word in text for word in _SCHOOL):
        guess = "assignment"
    elif proj or any(editor in text for editor in _EDITORS):
        guess = f"coding:{proj}" if proj else "coding"
    else:
        return observation(UNKNOWN, 0.0, "derived", activity.get("ttl", 300), activity.get("observed_at"))
    return observation(guess, conf, "derived", activity.get("ttl", 300), activity.get("observed_at"))


def decay(record: dict[str, Any], now: Optional[float] = None) -> dict[str, Any]:
    """Return a copy where stale fields read as unknown (last value kept as stale_value)."""
    now = time.time() if now is None else now
    out = dict(record)
    fields = {}
    for name in FIELDS:
        item = dict(record.get("fields", {}).get(name) or _unknown("no_source"))
        stamp = item.get("observed_at")
        if item.get("value") != UNKNOWN and (stamp is None or now - stamp > item.get("ttl", 0)):
            item = {**_unknown("stale"), "stale_value": item.get("value"), "last_source": item.get("source"),
                    "last_observed_at": stamp}
        if item.get("observed_at") is not None:
            item["age_seconds"] = round(now - item["observed_at"])
        fields[name] = item
    out["fields"] = fields
    return out


def rebuild(root: Path, adapters: Optional[dict[str, Callable[[float], dict]]] = None,
            now: Optional[float] = None) -> dict[str, Any]:
    """Run every adapter, fuse fields, write situation.json, emit situation.changed on change."""
    root = Path(root).resolve()
    now = time.time() if now is None else now
    adapters = default_adapters(root) if adapters is None else adapters
    fields: dict[str, dict] = {}
    sources: dict[str, str] = {}
    for name, adapter in adapters.items():
        try:
            readings = adapter(now) or {}
            sources[name] = "ok" if readings else "no_data"
        except Exception as exc:  # a dead source is unknown, never fatal
            sources[name] = f"unavailable: {type(exc).__name__}"
            continue
        for field, item in readings.items():
            if field in FIELDS and field not in fields:
                fields[field] = item
    fields["task_guess"] = guess_task(fields)
    fields.setdefault("place", _unknown("no_place_source: install HA Companion or push from ghostpi"))
    fields.setdefault("health", _unknown("no_health_source: approve a HealthHub endpoint"))
    record = decay({"updated_at": now, "fields": fields, "sources": sources}, now)
    path = situation_path(root)
    with exclusive_lock(path.with_suffix(".lock")):
        previous = load(root, raw=True)
        atomic_json(path, record)
    _append_history(root, record, now)
    before = {k: (previous.get("fields", {}).get(k) or {}).get("value") for k in ("place", "task_guess")}
    after = {k: record["fields"][k]["value"] for k in ("place", "task_guess")}
    if before != after:
        EventBus(root).emit(Event("situation.changed", {"before": before, "after": after},
                                  source="situation", sensitivity="personal"))
    return record


HISTORY_MAX_LINES = 20_000  # ~70 days at one rebuild per 5 min


def _append_history(root: Path, record: dict[str, Any], now: float) -> None:
    """Compact sample per rebuild (known/unknown, confidence, source) for freshness reports and patterns."""
    path = Path(root).resolve() / ".ghost" / "situation-history.jsonl"
    sample = {"t": now, "f": {name: [item.get("value") if isinstance(item.get("value"), str) else
                                     (item.get("value") or {}).get("state", "known"),
                                     round(float(item.get("confidence", 0.0)), 2), item.get("source", "")]
                              for name, item in record["fields"].items()}}
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(sample) + "\n")
    if path.stat().st_size > HISTORY_MAX_LINES * 400:  # rough cap; keep the newest half
        lines = path.read_text(encoding="utf-8").splitlines()[-HISTORY_MAX_LINES // 2:]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def history(root: Path, since: float = 0.0) -> list[dict[str, Any]]:
    """Situation samples newer than `since`; unreadable lines are skipped."""
    path = Path(root).resolve() / ".ghost" / "situation-history.jsonl"
    out = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                sample = json.loads(line)
            except ValueError:
                continue
            if sample.get("t", 0) >= since:
                out.append(sample)
    return out


def load(root: Path, *, raw: bool = False, now: Optional[float] = None) -> dict[str, Any]:
    """Read situation.json with freshness decay applied (raw=True skips decay)."""
    try:
        record = json.loads(situation_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = {"updated_at": None, "fields": {}, "sources": {}}
    return record if raw else decay(record, now)


def summary(root: Path, now: Optional[float] = None) -> str:
    """One line per known field for the digest and heartbeat prompt."""
    record = load(root, now=now)
    parts = []
    for name in FIELDS:
        item = record["fields"][name]
        value = item.get("value")
        if name == "health" and value != UNKNOWN:
            value = "measured (see HealthHub)"  # facts stay out of plain-text summaries
        if name == "owner_contact" and isinstance(value, dict):
            minutes = round(((now or time.time()) - float(value.get("at", 0))) / 60)
            value = f"{value.get('channel', '?')} {minutes} min ago"
        elif isinstance(value, dict):
            value = ", ".join(f"{k}={v}" for k, v in value.items() if k != "title")
        parts.append(f"{name}: {value} ({item.get('confidence', 0):.1f}, {item.get('source')})")
    return "Situation: " + "; ".join(parts)
