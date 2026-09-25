"""Persistent configuration for Ghost Agent.

Stores user preferences (model, etc.) in ~/.ghost/config.json.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


_CONFIG_DIR = Path.home() / ".ghost"
_CONFIG_FILE = _CONFIG_DIR / "config.json"

_DEFAULTS: Dict[str, Any] = {
    "research_model": "gemma4:e4b-Claude",
    "model": None,  # No default — user must pick on first boot
}


def _ensure_dir() -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> Dict[str, Any]:
    """Load the config file, returning defaults for missing keys."""
    if not _CONFIG_FILE.exists():
        return dict(_DEFAULTS)
    try:
        data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(_DEFAULTS)
    merged = dict(_DEFAULTS)
    merged.update(data)
    return merged


def save_config(data: Dict[str, Any]) -> None:
    """Write config to disk."""
    _ensure_dir()
    _CONFIG_FILE.write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8"
    )


def get_model() -> Optional[str]:
    """Get the persisted model name, or None if not set."""
    return load_config().get("model")


def set_model(model: str) -> None:
    """Persist the selected model name."""
    cfg = load_config()
    cfg["model"] = model
    save_config(cfg)


def get_research_model() -> Optional[str]:
    """Get the model reserved for evidence extraction and research planning."""
    return load_config().get("research_model")


def set_research_model(model: str) -> None:
    """Persist a research model without changing the coding model."""
    cfg = load_config()
    cfg["research_model"] = model
    save_config(cfg)
