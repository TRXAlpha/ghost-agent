"""Standing owner policy modes (R04).

The owner sets autonomy modes once; they are enforced at the real
hand-offs.  A corrupt policy file fails closed: only "suggest" stays on.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict

from .utils.storage import atomic_json, exclusive_lock

MODES = ("suggest", "build", "operate", "improve", "idle_work")
DEFAULTS = {"suggest": True, "build": True, "operate": False,
            "improve": False, "idle_work": False}


class PolicyError(ValueError):
    """Raised for unknown modes or a corrupt policy file."""


class PolicyStore:
    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root).resolve()
        self.path = self.root / ".ghost" / "policy.json"
        self._corrupt = False

    # -- load ----------------------------------------------------------------
    def load(self) -> dict:
        self._corrupt = False
        if not self.path.exists():
            return dict(DEFAULTS)
        try:
            with exclusive_lock(self.path.with_suffix(".lock")):
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            modes = raw["modes"] if isinstance(raw, dict) else None
            if not isinstance(modes, dict):
                raise ValueError("modes is not an object")
            for name, value in modes.items():
                if not isinstance(value, bool):
                    raise ValueError("mode value is not a bool")
        except Exception:
            self._corrupt = True
            return {"suggest": True, **{m: False for m in MODES if m != "suggest"}}
        result = dict(DEFAULTS)
        result.update(modes)
        return result

    # -- set -------------------------------------------------------------------
    def set(self, mode: str, enabled: bool) -> dict:
        if mode not in MODES:
            raise PolicyError(f"unknown policy mode: {mode}")
        if self.path.exists():
            self.load()
            if self._corrupt:
                raise PolicyError("policy file is corrupt; inspect .ghost/policy.json")
        current = self.load()
        current[mode] = bool(enabled)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / ".ghost").mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "modes": current,
                   "updated_at": time.time(), "updated_by": "owner"}
        with exclusive_lock(self.path.with_suffix(".lock")):
            atomic_json(self.path, payload)
        return current

    # -- enforce -----------------------------------------------------------------
    def require(self, mode: str, action: str) -> None:
        if mode not in MODES:
            raise PolicyError(f"unknown policy mode: {mode}")
        current = self.load()
        if not current.get(mode):
            raise PolicyError(
                f"{action} needs '{mode}' mode; "
                f"enable with: ghost policy set {mode} on")
