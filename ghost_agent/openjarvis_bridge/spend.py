"""OJ08: durable inference spend/reservation for the OpenJarvis bridge.

Ghost records a whole-project inference meter atomically and persists it, so
consumption is reserved before a call, survives resume/restart, and never falls
through to an unconfigured or extra-model backend. Limits and corrupt state are
handled deterministically: an over-budget call is refused, and a corrupt meter
is never treated as an empty permissive budget.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from ..utils.storage import atomic_json, exclusive_lock

_METER_FIELDS = ("reserved_calls", "committed_calls", "input_chars", "usage_units")


class BudgetExceeded(RuntimeError):
    """Inference spend would exceed the durable project budget."""


class CorruptBudgetError(RuntimeError):
    """Persisted budget state is corrupt or unsafe; never trust it."""


def _int_nonnegative(value) -> bool:
    return type(value) is int and value >= 0


def _int_or_none(value) -> bool:
    return value is None or _int_nonnegative(value)


@dataclass
class Reservation:
    reservation_id: str
    calls: int


class DurableInferenceBudget:
    """Persisted, atomic inference meter with optional per-project caps."""

    def __init__(self, root: Path, *, max_calls: Optional[int] = None,
                 max_input_chars: Optional[int] = None, max_usage_units: Optional[int] = None,
                 path: Optional[Path] = None) -> None:
        if not _int_or_none(max_calls) or not _int_or_none(max_input_chars) or not _int_or_none(max_usage_units):
            raise ValueError("Budget caps must be nonnegative integers or None")
        self.root = Path(root).resolve()
        self.path = Path(path) if path else self.root / ".ghost" / "openjarvis" / "spend.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_calls = max_calls
        self.max_input_chars = max_input_chars
        self.max_usage_units = max_usage_units

    # -- persistence ------------------------------------------------------
    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"reserved_calls": 0, "committed_calls": 0, "input_chars": 0, "usage_units": 0}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise CorruptBudgetError("Budget state is unreadable; refusing to trust it.") from exc
        if not isinstance(data, dict):
            raise CorruptBudgetError("Budget state must be an object.")
        for field in _METER_FIELDS:
            if not _int_nonnegative(data.get(field)):
                raise CorruptBudgetError(f"Budget field {field!r} is invalid.")
        return {field: data[field] for field in _METER_FIELDS}

    def _save(self, data: Dict[str, Any]) -> None:
        atomic_json(self.path, data)

    def _snapshot(self) -> Dict[str, Any]:
        return {"path": str(self.path), "max_calls": self.max_calls,
                "max_input_chars": self.max_input_chars, "max_usage_units": self.max_usage_units,
                "reserved_calls": self._load()["reserved_calls"]}

    # -- operations -------------------------------------------------------
    def reserve(self, calls: int = 1) -> Reservation:
        """Atomically reserve inference-call capacity; refuse if over cap."""
        if type(calls) is not int or calls <= 0:
            raise ValueError("Reserved calls must be a positive integer.")
        with exclusive_lock(self.path.with_suffix(".lock")):
            data = self._load()
            if self.max_calls is not None:
                if data["reserved_calls"] + calls > self.max_calls:
                    raise BudgetExceeded(
                        f"Inference budget exhausted ({data['reserved_calls']}/{self.max_calls} calls reserved).")
            data["reserved_calls"] += calls
            self._save(data)
        digest = (str(time.time_ns()) + repr(data["reserved_calls"]))[-16:]
        return Reservation(f"res_{digest}", calls)

    def commit(self, reservation: Reservation, *, input_chars: int = 0, usage_units: int = 0) -> None:
        """Record actual consumed input/usage within a reservation."""
        if type(input_chars) is not int or input_chars < 0 or type(usage_units) is not int or usage_units < 0:
            raise ValueError("Committed usage must be nonnegative integers.")
        with exclusive_lock(self.path.with_suffix(".lock")):
            data = self._load()
            if self.max_input_chars is not None and data["input_chars"] + input_chars > self.max_input_chars:
                raise BudgetExceeded("Input-character budget exhausted.")
            if self.max_usage_units is not None and data["usage_units"] + usage_units > self.max_usage_units:
                raise BudgetExceeded("Usage budget exhausted.")
            data["committed_calls"] += reservation.calls
            data["input_chars"] += input_chars
            data["usage_units"] += usage_units
            self._save(data)

    def rollback(self, reservation: Reservation) -> None:
        """Undo a reservation's call capacity (e.g. on failure/refusal)."""
        with exclusive_lock(self.path.with_suffix(".lock")):
            data = self._load()
            data["reserved_calls"] = max(0, data["reserved_calls"] - reservation.calls)
            self._save(data)

    def remaining(self) -> int:
        if self.max_calls is None:
            return -1  # no cap
        data = self._load()
        return max(0, self.max_calls - data["reserved_calls"])
