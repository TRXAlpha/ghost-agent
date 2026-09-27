"""Huawei integration adapter.

[MOCKUP] Placeholder for Huawei Health Kit integration. Real Huawei integration
requires:
- Huawei Developer Console account
- Health Kit app credentials (AppID, AppSecret)
- User authorization flow
- Network access to Huawei's Health Kit APIs

Without these credentials and device access, no live Huawei health data
can be fetched or used.
"""
from __future__ import annotations
from typing import Optional

def is_configured() -> bool:
    """Returns False — Huawei Health Kit needs credentials."""
    return False

def get_health_summary(days: int = 7) -> dict:
    """Placeholder: raises ConnectionError."""
    raise ConnectionError(
        "Huawei Health Kit is not configured. Requires Huawei Developer Console "
        "credentials and user authorization. No live health data is available."
    )

def health_check() -> dict:
    """Returns status dict: always 'not_configured'."""
    return {
        "status": "not_configured",
        "required": ["HUAWEI_APP_ID", "HUAWEI_APP_SECRET"],
        "note": "No live Huawei API calls are made without credentials."
    }
