# Track TF: devices, device safety, Huawei route wiring (R11, S02, S03)

Audit: `audit/A8.md`. Branch `c2/TF`, worktree `C:/Users/chris/Documents/Ghost/ghost_c2_TF`.
Files this track owns: `ghost_agent/devices.py`, `ghost_agent/huawei_route.py`,
`ghost_agent/integrations/desksentinel.py`, new `ghost_agent/device_control.py`,
`cli.py` (new blocks only), and tests. Never send any command to real hardware.

---

## TF1: `ghost huawei` CLI over HuaweiRoute (review: 2)

**Goal:** the complete, privacy-guarded `HuaweiRoute` (test-only today)
becomes reachable from production. It uses **synthetic data only** until real
credentials exist (S02).

**Decided design:** read `huawei_route.py` fully first. Add a `huawei` parser
block after the `device` block, with one sub-command per existing public
HuaweiRoute capability: the route record/status, synthetic ingest from a
JSON file, owner correction (illness, travel, unworn watch, rest day), trend
summary, and the integration state. Use the methods as they exist; map one
CLI action to one method. Do **not** add network calls. Every output that
includes observations must go through the route's existing privacy guard. If
the route has no method for one of these, skip that action and list it in the
report. The help text states "synthetic/manual data only until Huawei
credentials are configured".

**Tests:** each sub-command in a tmp project; synthetic ingest then trend; a
correction appears in the trend; status shows not-configured honestly.

---

## TF2: richer device records

**Goal:** device records carry the R11 fields, with declared and verified
capabilities kept separate.

**Decided design:** add these fields to the `Device` dataclass in devices.py
(read it; it's about lines 13–22), with defaults:
`transport: str = "unknown"` (`unknown|usb_serial|ble|wifi|cloud_api|simulated`),
`firmware_version: str = ""`, `pairing_state: str = "unpaired"`
(`unpaired|paired|revoked`), `last_seen: Optional[float] = None`,
`verified_capabilities: list[str] = field(default_factory=list)`.
Add `DeviceRegistry.update(device_id, **fields)` with validation (enums,
types), and `DeviceRegistry.freshness(device_id, max_age_s=3600) -> "fresh"|"stale"|"unknown"`.
CLI: `ghost device register` gains `--transport`, `--firmware`;
add `ghost device pair <id>`, `ghost device revoke <id>`, `ghost device status <id>`
(shows freshness and declared vs verified capabilities).
Old devices.json loads.

**Tests:** enums validated; pair/revoke transitions; freshness; legacy load.

---

## TF3: device command gate and pan/tilt simulator (review: 2)

**Goal:** physical-command safety exists and is tested before any hardware:
limits, pairing, emergency stop, disconnect → safe state, cancellation (S03,
R11).

**Decided design:** new module `ghost_agent/device_control.py`:

```python
@dataclass(frozen=True)
class AxisLimits: min_deg: float; max_deg: float; max_rate_deg_s: float; safe_deg: float
@dataclass(frozen=True)
class PanTiltCommand: pan_deg: float; tilt_deg: float
class CommandRefused(ValueError): ...
class SimulatedPanTilt:   # in-memory device
    def __init__(self, limits: dict[str, AxisLimits]); position: dict; connected: bool
    def apply(self, cmd: PanTiltCommand, duration_s: float) -> None
    def disconnect(self) -> None   # on disconnect the device moves to safe_deg on both axes
class DeviceCommandGate:
    def __init__(self, root: Path, registry: DeviceRegistry, device_id: str, limits: dict[str, AxisLimits])
    def send(self, device: SimulatedPanTilt, cmd: PanTiltCommand, duration_s: float) -> dict
```

`send` refuses (raises `CommandRefused`) if: EmergencyStop is active; the
device's `pairing_state != "paired"`; any target is outside `[min,max]`; the
required rate `abs(target-current)/duration_s` exceeds `max_rate_deg_s`; or the
device is disconnected. On success it records `Event("device.command", {...})`.
Limits are **only** what the gate is given. Load them from the device record's
`constraints` (devices already have constraints) using keys
`pan_min_deg` … `tilt_safe_deg`. Missing limits → every command is refused
("no configured limits").
`integrations/desksentinel.py` keeps raising `ConnectionError` for real
hardware, but its docstring points to the gate as the required path.
CLI: `ghost device simulate <id> --pan X --tilt Y --duration S [--disconnect]`
runs the gate against a fresh `SimulatedPanTilt` and prints the result or the
refusal.

**Tests:** every refusal reason; success emits the event; disconnect puts the
simulator in the safe position; no limits → refused; emergency stop wins over
everything.

---

## TF4: health observation retention (parallel-ok with TF2)

**Goal:** health observations have selective retention and a purge (R11).

**Decided design:** in huawei_route.py, add a `retention_days` setting
(default 90, range 1–3650) stored with the route state. Add
`purge_expired(now=None) -> int`, which removes observations older than the
retention window, and `purge_all() -> int`. Call `purge_expired` at the start
of every ingest. CLI (TF1's group): `ghost huawei retention --days N`,
`ghost huawei purge [--all]`. Recording the purge count as an event is
allowed; observation values must never appear in events.

**Tests:** old observations are purged on ingest; purge-all; retention
validation; the event contains no values.
