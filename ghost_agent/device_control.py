"""TF3: device command gate and pan/tilt simulator (S03, R11).

Physical-command safety before any hardware exists: axis limits, pairing,
emergency stop, rate limits, disconnect -> safe state. Every physical command
must pass through DeviceCommandGate; no component may bypass it.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .devices import DeviceRegistry
from .runtime import EmergencyStop, Event, EventBus


@dataclass(frozen=True)
class AxisLimits:
    min_deg: float
    max_deg: float
    max_rate_deg_s: float
    safe_deg: float


@dataclass(frozen=True)
class PanTiltCommand:
    pan_deg: float
    tilt_deg: float


class CommandRefused(ValueError):
    """A physical device command was refused by the safety gate."""


LIMIT_KEYS = {
    "pan": {"min": "pan_min_deg", "max": "pan_max_deg",
            "rate": "pan_max_rate_deg_s", "safe": "pan_safe_deg"},
    "tilt": {"min": "tilt_min_deg", "max": "tilt_max_deg",
             "rate": "tilt_max_rate_deg_s", "safe": "tilt_safe_deg"},
}


def limits_from_constraints(constraints: dict[str, Any]) -> dict[str, AxisLimits]:
    """Build axis limits from a device record's constraints.

    Missing limits for an axis mean every command touching that axis is
    refused ("no configured limits"); nothing is ever defaulted.
    """
    limits: dict[str, AxisLimits] = {}
    for axis, keys in LIMIT_KEYS.items():
        try:
            limits[axis] = AxisLimits(
                min_deg=float(constraints[keys["min"]]),
                max_deg=float(constraints[keys["max"]]),
                max_rate_deg_s=float(constraints[keys["rate"]]),
                safe_deg=float(constraints[keys["safe"]]))
        except (KeyError, TypeError, ValueError):
            continue  # missing/unusable limits -> axis stays unconfigured
    return limits


class SimulatedPanTilt:
    """In-memory pan/tilt device used instead of real hardware."""

    def __init__(self, limits: dict[str, AxisLimits]) -> None:
        self.limits = limits
        self.connected = False
        self.position: dict[str, float] = {
            axis: (limits[axis].safe_deg if axis in limits else 0.0)
            for axis in ("pan", "tilt")}

    def apply(self, cmd: PanTiltCommand, duration_s: float) -> None:
        if not self.connected:
            raise CommandRefused("device is disconnected")
        self.position["pan"] = cmd.pan_deg
        self.position["tilt"] = cmd.tilt_deg

    def disconnect(self) -> None:
        self.connected = False
        for axis, limit in self.limits.items():
            self.position[axis] = limit.safe_deg


class DeviceCommandGate:
    """The only sanctioned path for physical device commands."""

    def __init__(self, root: Path, registry: DeviceRegistry, device_id: str,
                 limits: dict[str, AxisLimits]) -> None:
        self.root = Path(root)
        self.registry = registry
        self.device_id = device_id
        self.limits = limits
        self.events = EventBus(self.root)

    def send(self, device: SimulatedPanTilt, cmd: PanTiltCommand,
             duration_s: float) -> dict:
        if EmergencyStop(self.root).active():
            raise CommandRefused("emergency stop is engaged")
        record = self.registry.get(self.device_id)
        if record is None:
            raise CommandRefused("device is not registered")
        if record.pairing_state != "paired":
            raise CommandRefused("device is not paired")
        if not device.connected:
            raise CommandRefused("device is disconnected")
        if not self.limits:
            raise CommandRefused("no configured limits")
        targets = {"pan": cmd.pan_deg, "tilt": cmd.tilt_deg}
        for axis, target in targets.items():
            limit = self.limits.get(axis)
            if limit is None:
                raise CommandRefused(f"no configured limits for axis '{axis}'")
            if not limit.min_deg <= target <= limit.max_deg:
                raise CommandRefused(
                    f"{axis} target {target}deg outside [{limit.min_deg}, {limit.max_deg}]")
            if duration_s <= 0:
                raise CommandRefused("duration must be positive")
            current = device.position.get(axis, limit.safe_deg)
            required_rate = abs(target - current) / duration_s
            if required_rate > limit.max_rate_deg_s:
                raise CommandRefused(
                    f"{axis} required rate {required_rate:.3f}deg/s exceeds "
                    f"max {limit.max_rate_deg_s}deg/s")
        device.apply(cmd, duration_s)
        self.events.emit(Event("device.command", {
            "device_id": self.device_id,
            "axes": sorted(targets),
            "duration_s": duration_s,
        }, source="device_command_gate"))
        return {"accepted": True, "position": dict(device.position)}
