"""Telegram integration adapter: credential-aware health surfacing, no network.

The polling transport lives in `openjarvis_bridge.telegram` (owner-paired,
read-only). This adapter only reports configuration health: whether a bot token
is present in the environment (never its value), whether an owner pairing is
enabled, and the single next setup step. It never contacts Telegram and never
emits credential material.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

# The bridge serve path reads GHOST_TELEGRAM_BOT_TOKEN; the integration planner
# documents TELEGRAM_BOT_TOKEN. Both are honored for presence checks.
BRIDGE_TOKEN_ENV = "GHOST_TELEGRAM_BOT_TOKEN"
LEGACY_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
TOKEN_ENVS = (BRIDGE_TOKEN_ENV, LEGACY_TOKEN_ENV)

# BotFather format: <numeric bot id>:<alphanumeric secret>. Structural sanity
# check only - no network validation.
_BOT_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]+$")

_FINGERPRINT_FILE = "telegram-token-fingerprint.json"


def _state_root(root: Path | None = None) -> Path:
    """Mirror openjarvis_bridge.runtime.state_root without importing it."""
    base = Path(root) if root is not None else Path(
        os.environ.get("GHOST_PROJECT_ROOT") or Path.cwd())
    return Path(base).resolve() / ".ghost" / "openjarvis"


def token_value() -> str:
    """The configured token, or '' (read-only; never logged or persisted)."""
    for name in TOKEN_ENVS:
        value = os.environ.get(name, "")
        if value.strip():
            return value
    return ""


def token_present() -> bool:
    """True when a bot token env var is set (bool only - never the value)."""
    return bool(token_value())


def token_format_error(token: str) -> str:
    """Return '' when the token matches BotFather format, else the reason."""
    if not token or token.strip() != token or ":" not in token:
        return "Token must be nonempty with no surrounding whitespace and contain ':'."
    if not _BOT_TOKEN_RE.match(token):
        return "Token does not match BotFather format <numeric-id>:<alphanumeric>."
    return ""


def token_fingerprint(token: str) -> str:
    """First 16 hex chars of SHA-256 - safe to store/display, never reversible."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def fingerprint_path(root: Path | None = None) -> Path:
    return _state_root(root) / _FINGERPRINT_FILE


def save_token_fingerprint(token: str, root: Path | None = None) -> dict:
    """Persist only the SHA-256 prefix of the token; never the token itself."""
    from ..utils.storage import atomic_json

    fingerprint = token_fingerprint(token)
    atomic_json(fingerprint_path(root), {"fingerprint": fingerprint})
    return {"fingerprint": fingerprint}


def check_token_rotation(new_token: str, root: Path | None = None) -> dict:
    """Compare a newly provided token against the stored fingerprint (no network).

    Returns well_formed bool, changed bool (True when no prior fingerprint or it
    differs), and the fingerprint prefix. Never includes the token or full hash.
    """
    error = token_format_error(new_token)
    if error:
        return {"well_formed": False, "error": error}
    stored = ""
    try:
        stored = str(json.loads(fingerprint_path(root).read_text(encoding="utf-8"))
                     .get("fingerprint", ""))
    except FileNotFoundError:
        stored = ""
    except (OSError, ValueError):
        return {"well_formed": True,
                "error": "Stored fingerprint file is unreadable; delete it and re-record."}
    fingerprint = token_fingerprint(new_token)
    return {"well_formed": True, "changed": fingerprint != stored,
            "same_as_recorded": fingerprint == stored, "fingerprint": fingerprint}


def pairing_state(root: Path | None = None) -> dict:
    """Enabled-bool snapshot of the bridge pairing; invalid files fail closed."""
    from ..openjarvis_bridge.runtime import IntegrationError
    from ..openjarvis_bridge.telegram import pairing

    base = Path(root) if root is not None else Path(
        os.environ.get("GHOST_PROJECT_ROOT") or Path.cwd())
    try:
        value = pairing(Path(base).resolve())
    except IntegrationError as exc:
        return {"enabled": False, "error": str(exc)}
    return {"enabled": bool(value.get("enabled"))}


def health_check(root: Path | None = None) -> dict:
    """Credential-aware, network-free Telegram health snapshot.

    No Telegram API call is made in any state; with no token configured the
    result is purely local env/file inspection.
    """
    token_ok = token_present()
    pairing = pairing_state(root)
    pairing_ok = pairing["enabled"]

    if not token_ok:
        next_step = "set GHOST_TELEGRAM_BOT_TOKEN"
    elif pairing.get("error"):
        next_step = "repair or re-pair Telegram pairing (pairing file failed validation)"
    elif not pairing_ok:
        next_step = "pair owner id"
    else:
        next_step = "ready for authorized live test"

    result = {
        "status": "ready" if (token_ok and pairing_ok) else "not_ready",
        "token_present": token_ok,
        "pairing_present": pairing_ok,
        "next_step": next_step,
    }
    if pairing.get("error"):
        result["pairing_error"] = pairing["error"]
    return result


def is_configured() -> bool:
    """True only when a token is present AND an owner pairing is enabled."""
    return health_check()["status"] == "ready"


def send_message(chat_id: str, text: str) -> dict:
    """Placeholder: raises ConnectionError."""
    raise ConnectionError(
        "Telegram is not configured. Set GHOST_TELEGRAM_BOT_TOKEN, pair the "
        "owner (`ghost openjarvis telegram pair --user-id <id> --chat-id <id>`), "
        "and use `ghost openjarvis telegram live-check --chat-id <id>`; without "
        "credentials live Telegram messaging is not available."
    )


def get_updates() -> list[dict]:
    """Placeholder: raises ConnectionError."""
    raise ConnectionError(
        "Telegram is not configured. Set GHOST_TELEGRAM_BOT_TOKEN."
    )
