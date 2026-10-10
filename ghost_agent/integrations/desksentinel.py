"""DeskSentinel ESP32 integration adapter.

[MOCKUP] Interface and placeholder for DeskSentinel ESP32 desk sensor.
Real communication requires:
- ESP32 firmware flashed with DeskSentinel protocol
- Serial/BLE connection to the ESP32 device
- Protocol documentation (command format, response format)

The ESP32 firmware does not exist yet. This adapter defines the
expected interface and provides mock responses for development/testing.
When real hardware and firmware exist, implement the actual serial/BLE
communication in the methods below.

SAFETY (TF3): any physical device command must go through
ghost_agent.device_control.DeviceCommandGate first (axis limits, pairing,
emergency stop, rate limits). Do not add a direct hardware path here that
bypasses the gate.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import time

@dataclass
class DeskSentinelStatus:
    height_mm: float = 0.0
    target_height_mm: float = 0.0
    sitting_standing: str = "unknown"
    last_updated: float = 0.0

    def __repr__(self):
        return (f"DeskSentinelStatus(height={self.height_mm}mm, "
                f"target={self.target_height_mm}mm, posture={self.sitting_standing})")

def is_configured() -> bool:
    """Returns False — ESP32 hardware/firmware not available."""
    return False

def connect(port: str = "/dev/ttyUSB0", baud: int = 115200) -> bool:
    """Placeholder: raises ConnectionError."""
    raise ConnectionError(
        "DeskSentinel ESP32 is not available. ESP32 firmware has not been developed yet."
        " When hardware and firmware are ready, implement serial/BLE connection here."
    )

def get_status() -> DeskSentinelStatus:
    """Returns a MOCKUP status — NOT real hardware data."""
    return DeskSentinelStatus(
        height_mm=1100.0,
        target_height_mm=1100.0,
        sitting_standing="sitting",
        last_updated=time.time()
    )

def set_target_height(height_mm: float) -> None:
    """Placeholder: raises ConnectionError."""
    raise ConnectionError(
        "DeskSentinel ESP32 is not available. ESP32 firmware has not been developed yet."
    )

def health_check() -> dict:
    """Returns status dict: always 'not_configured'."""
    return {
        "status": "not_configured",
        "hardware": "ESP32 firmware not developed",
        "protocol": "not defined",
        "note": ("Mock status returned only. Real desk data requires ESP32 "
                 "hardware and firmware. This is not live telemetry.")
    }
