"""Persistent device registry; device capabilities remain policy-gated proposals."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .runtime import Event, EventBus, Observation, WorldModel


TRANSPORTS = {"unknown", "usb_serial", "ble", "wifi", "cloud_api", "simulated"}
PAIRING_STATES = {"unpaired", "paired", "revoked"}


@dataclass
class Device:
    device_id: str
    name: str
    kind: str
    capabilities: list[str]
    constraints: dict[str, Any] = field(default_factory=dict)
    status: str = "registered"
    owner: str = "user"
    last_seen: float = field(default_factory=time.time)
    transport: str = "unknown"
    firmware_version: str = ""
    pairing_state: str = "unpaired"
    verified_capabilities: list[str] = field(default_factory=list)


class DeviceRegistry:
    """Register devices and route only based on declared, fresh capabilities."""

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root)
        self.path = self.root / ".ghost" / "devices.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.events = EventBus(self.root)
        self.world = WorldModel(self.root)

    def _load(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []
        except (OSError, json.JSONDecodeError):
            return []

    def _save(self, devices: list[dict[str, Any]]) -> None:
        self.path.write_text(json.dumps(devices, indent=2), encoding="utf-8")

    def register(self, device_id: str, name: str, kind: str, capabilities: list[str],
                 constraints: Optional[dict[str, Any]] = None) -> Device:
        if not device_id.strip() or not name.strip() or not kind.strip():
            raise ValueError("Device id, name, and kind are required")
        device = Device(device_id.strip(), name.strip(), kind.strip(), sorted(set(capabilities)), constraints or {})
        data = [item for item in self._load() if item.get("device_id") != device.device_id]
        data.append(asdict(device))
        self._save(data)
        self.world.observe(Observation(f"device.{device.device_id}", "device", {
            "name": device.name, "status": device.status, "capabilities": device.capabilities,
            "constraints": device.constraints,
        }, source="device_registry", owner=device.owner))
        self.events.emit(Event("device.registered", {"device_id": device.device_id,
                                                       "capabilities": device.capabilities}, source="device_registry"))
        return device

    def list(self) -> list[Device]:
        return [Device(**item) for item in self._load()]

    def get(self, device_id: str) -> Optional[Device]:
        for device in self.list():
            if device.device_id == device_id:
                return device
        return None

    def update(self, device_id: str, **fields: Any) -> Device:
        """Validate and update R11 fields on an existing device record."""
        allowed = {"transport", "firmware_version", "pairing_state", "last_seen",
                   "verified_capabilities", "status", "constraints"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unknown device fields: {sorted(unknown)}")
        if "transport" in fields and fields["transport"] not in TRANSPORTS:
            raise ValueError("Invalid transport")
        if "pairing_state" in fields and fields["pairing_state"] not in PAIRING_STATES:
            raise ValueError("Invalid pairing state")
        if "last_seen" in fields and fields["last_seen"] is not None \
                and not isinstance(fields["last_seen"], (int, float)):
            raise ValueError("last_seen must be a number")
        if "verified_capabilities" in fields:
            caps = fields["verified_capabilities"]
            if not isinstance(caps, list) or not all(isinstance(c, str) for c in caps):
                raise ValueError("verified_capabilities must be a list of strings")
            fields["verified_capabilities"] = sorted(set(caps))
        data = self._load()
        for item in data:
            if item.get("device_id") == device_id:
                item.update(fields)
                self._save(data)
                return Device(**item)
        raise ValueError(f"Unknown device: {device_id}")

    def freshness(self, device_id: str, max_age_s: float = 3600) -> str:
        device = self.get(device_id)
        if device is None or device.last_seen is None:
            return "unknown"
        return "fresh" if time.time() - device.last_seen <= max_age_s else "stale"

    def eligible(self, capability: str, constraints: Optional[dict[str, Any]] = None) -> list[Device]:
        required = constraints or {}
        eligible: list[Device] = []
        for device in self.list():
            if device.status != "registered" or capability not in device.capabilities:
                continue
            if all(device.constraints.get(key) == value for key, value in required.items()):
                eligible.append(device)
        return eligible
