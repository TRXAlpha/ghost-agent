"""Device telemetry validation: data can never become instructions.

Generalizes the HuaweiRoute privacy posture to all devices: strict schema,
unit allowlist, clock-skew bounds, and a size/newline cap on every string
field so injection payloads cannot ride in telemetry.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

from .devices import Device
from .utils.storage import exclusive_lock

MAX_STRING_LEN = 64
METRIC_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
UNIT_ALLOWLIST = {"deg", "deg_s", "celsius", "percent", "count", "steps",
                  "bpm", "seconds", "meters", "lux", "volts", "none"}
STRING_FIELDS = ("device_id", "metric", "unit")


class TelemetryError(ValueError):
    """A telemetry sample was refused; message must not echo its content."""


@dataclass(frozen=True)
class TelemetrySample:
    device_id: str
    metric: str
    value: float
    unit: str
    observed_at: float
    received_at: float


def _reject(reason: str) -> None:
    raise TelemetryError(reason)


def _check_string(name: str, raw: Any) -> str:
    if not isinstance(raw, str):
        _reject(f"{name} must be a string")
    if len(raw) > MAX_STRING_LEN:
        _reject(f"{name} too long")
    if "\n" in raw or "\r" in raw:
        _reject(f"{name} contains a newline")
    return raw


def validate_sample(raw: Any, *, device: Device, now: float,
                    max_skew_s: float = 300,
                    max_age_s: float = 86400) -> TelemetrySample:
    """Validate one raw telemetry dict against a device's capabilities."""
    if not isinstance(raw, dict):
        _reject("sample must be a JSON object")
    required = {"device_id", "metric", "value", "unit", "observed_at"}
    missing = required - set(raw)
    if missing:
        _reject(f"missing keys: {sorted(missing)}")
    extra = set(raw) - required
    if extra:
        _reject(f"extra keys: {sorted(extra)}")

    device_id = _check_string("device_id", raw["device_id"])
    if device_id != device.device_id:
        _reject("device_id does not match the ingesting device")

    metric = _check_string("metric", raw["metric"])
    if not METRIC_RE.match(metric):
        _reject("invalid metric name")
    allowed = set(device.verified_capabilities) or set(device.capabilities)
    if metric not in allowed:
        _reject("metric is not a declared capability")

    value = raw["value"]
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or value != value or value in (float("inf"), float("-inf")):
        _reject("value must be a finite number")

    unit = _check_string("unit", raw["unit"])
    if unit not in UNIT_ALLOWLIST:
        _reject("unit is not in the allowlist")

    observed_at = raw["observed_at"]
    if not isinstance(observed_at, (int, float)) or isinstance(observed_at, bool) \
            or observed_at != observed_at \
            or observed_at in (float("inf"), float("-inf")):
        _reject("observed_at must be a finite number")
    if observed_at > now + max_skew_s:
        _reject("observed_at is too far in the future (clock skew)")
    if now - observed_at > max_age_s:
        _reject("sample is too old (stale)")

    return TelemetrySample(device_id=device_id, metric=metric, value=float(value),
                           unit=unit, observed_at=float(observed_at),
                           received_at=now)


class TelemetryStore:
    """Append valid samples to .ghost/telemetry/<device_id>.jsonl."""

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root)
        self.dir = self.root / ".ghost" / "telemetry"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, device_id: str) -> Path:
        return self.dir / f"{device_id}.jsonl"

    def append(self, sample: TelemetrySample) -> None:
        line = json.dumps(asdict(sample))
        with exclusive_lock(self._path(sample.device_id).with_suffix(".lock")):
            with self._path(sample.device_id).open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def latest(self, device_id: str, metric: str) -> Optional[TelemetrySample]:
        path = self._path(device_id)
        if not path.exists():
            return None
        found: Optional[TelemetrySample] = None
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                if data.get("metric") != metric:
                    continue
                found = TelemetrySample(
                    device_id=data["device_id"], metric=data["metric"],
                    value=data["value"], unit=data["unit"],
                    observed_at=data["observed_at"],
                    received_at=data["received_at"])
        return found


def ingest_samples(raw: Any, *, device: Device, registry: Any,
                   now: Optional[float] = None) -> dict[str, Any]:
    """Validate a list of raw samples; store accepted ones, update last_seen."""
    if not isinstance(raw, list):
        raise TelemetryError("file must contain a JSON list of samples")
    ts = now if now is not None else time.time()
    accepted: list[TelemetrySample] = []
    rejected: dict[str, int] = {}

    store = TelemetryStore(registry.root)
    for item in raw:
        try:
            sample = validate_sample(item, device=device, now=ts)
        except TelemetryError as exc:
            reason = str(exc)
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        store.append(sample)
        registry.update(device.device_id, last_seen=ts)
        accepted.append(sample)

    return {"accepted": len(accepted), "rejected": len(rejected),
            "rejection_reasons": rejected,
            "metrics": sorted({s.metric for s in accepted})}
