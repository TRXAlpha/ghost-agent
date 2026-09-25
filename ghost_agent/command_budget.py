"""Durable per-outcome command admission; reserve time before execution."""
import json
import time
from contextlib import contextmanager
from pathlib import Path

from .utils.storage import atomic_json, exclusive_lock


class CommandBudget:
    def __init__(self, path: Path, seconds: float = 300, calls: int = 32):
        if type(seconds) not in (int, float) or not 0 < seconds <= 86400:
            raise ValueError("Command budget seconds must be positive and at most one day")
        if type(calls) is not int or not 1 <= calls <= 1000:
            raise ValueError("Command budget calls must be from 1 to 1000")
        self.path, self.seconds, self.calls = Path(path), seconds, calls

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return dict(seconds=self.seconds, calls=self.calls, used_seconds=0, used_calls=0)
        return self._validate(data)

    def _validate(self, data):
        if (not isinstance(data, dict) or set(data) != {"seconds", "calls", "used_seconds", "used_calls"}
                or type(data["seconds"]) not in (int, float) or data["seconds"] != self.seconds
                or type(data["calls"]) is not int or data["calls"] != self.calls
                or type(data["used_seconds"]) not in (int, float)
                or not 0 <= data["used_seconds"] <= self.seconds
                or type(data["used_calls"]) is not int or not 0 <= data["used_calls"] <= self.calls):
            raise ValueError("Command budget is corrupt or changed; preserve it for review")
        return data

    @classmethod
    def inspect(cls, path: Path):
        try:
            with Path(path).open("rb") as stream:
                raw = stream.read(4097)
        except FileNotFoundError:
            return None
        if len(raw) > 4096:
            raise ValueError("Command budget record exceeds size limit")
        data = json.loads(raw)
        if not isinstance(data, dict) or "seconds" not in data or "calls" not in data:
            raise ValueError("Invalid command budget record")
        cls(path, data["seconds"], data["calls"])._validate(data)
        return {**data, "remaining_seconds": data["seconds"] - data["used_seconds"],
                "remaining_calls": data["calls"] - data["used_calls"]}

    def exhausted(self) -> bool:
        data = self._load()
        return data["used_calls"] >= self.calls or data["used_seconds"] >= self.seconds

    @contextmanager
    def slot(self, timeout=30):
        if type(timeout) not in (int, float) or not 0 < timeout <= 86400:
            raise ValueError("Invalid command timeout")
        # Lock spans execution; concurrent admissions are refused, not queued.
        with exclusive_lock(self.path.with_suffix(".lock")):
            data = self._load()
            remaining = self.seconds - data["used_seconds"]
            if remaining <= 0 or data["used_calls"] >= self.calls:
                raise ValueError("Outcome command budget exhausted")
            granted = min(timeout, remaining)
            data["used_calls"] += 1
            data["used_seconds"] += granted
            atomic_json(self.path, data)
            started = time.monotonic()
            try:
                yield granted
            finally:
                elapsed = min(granted, max(0, time.monotonic() - started))
                data["used_seconds"] = max(0, data["used_seconds"] - granted + elapsed)
                atomic_json(self.path, data)
            # A killed supervisor cannot refund its reservation; resume remains
            # conservative rather than silently resetting the allowance.
