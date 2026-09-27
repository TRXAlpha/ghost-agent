"""Telegram integration adapter.

[MOCKUP] This module contains a placeholder implementation. Real Telegram
integration requires a TELEGRAM_BOT_TOKEN environment variable and a valid
bot paired to a specific Telegram user ID. Without these credentials, no live
Telegram operations are possible.

To set up real Telegram:
1. Create a bot via @BotFather on Telegram
2. Store the token: export TELEGRAM_BOT_TOKEN=... (never commit)
3. Pair via: ghost integration plan telegram
4. Review the extension proposal in .ghost/extensions/
5. Explicitly approve before enabling
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional

# No live Telegram without TELEGRAM_BOT_TOKEN
_TELEGRAM_BOT_TOKEN = None  # type: ignore

def is_configured() -> bool:
    """Returns False — real Telegram needs TELEGRAM_BOT_TOKEN."""
    return False

def send_message(chat_id: str, text: str) -> dict:
    """Placeholder: raises ConnectionError."""
    raise ConnectionError(
        "Telegram is not configured. Set TELEGRAM_BOT_TOKEN and run "
        "`ghost integration plan telegram` to set up. Without credentials, "
        "live Telegram messaging is not available."
    )

def get_updates() -> list[dict]:
    """Placeholder: raises ConnectionError."""
    raise ConnectionError(
        "Telegram is not configured. Set TELEGRAM_BOT_TOKEN."
    )

def health_check() -> dict:
    """Returns status dict: always 'not_configured' with current setup instructions."""
    return {
        "status": "not_configured",
        "required": ["TELEGRAM_BOT_TOKEN"],
        "instructions": "Run `ghost integration plan telegram` for setup steps.",
        "note": "No live Telegram calls are made without valid credentials."
    }
