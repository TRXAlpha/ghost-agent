"""Tests for TF2: richer device records (R11 fields, update, freshness, CLI)."""
import argparse
import json
import time

import pytest

from ghost_agent.cli import _cmd_device
from ghost_agent.devices import DeviceRegistry


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    reg = DeviceRegistry(tmp_path)
    reg.register("dev1", "Cam", "camera", ["pan_tilt"])
    return tmp_path


def test_update_validates_enums(root):
    reg = DeviceRegistry(root)
    with pytest.raises(ValueError):
        reg.update("dev1", transport="carrier_pigeon")
    with pytest.raises(ValueError):
        reg.update("dev1", pairing_state="half")
    with pytest.raises(ValueError):
        reg.update("dev1", last_seen="soon")
    with pytest.raises(ValueError):
        reg.update("dev1", verified_capabilities="pan_tilt")
    with pytest.raises(ValueError):
        reg.update("dev1", nonsense=1)


def test_pair_revoke_transitions(root):
    reg = DeviceRegistry(root)
    assert reg.get("dev1").pairing_state == "unpaired"
    reg.update("dev1", pairing_state="paired")
    assert reg.get("dev1").pairing_state == "paired"
    reg.update("dev1", pairing_state="revoked")
    assert reg.get("dev1").pairing_state == "revoked"


def test_freshness(root):
    reg = DeviceRegistry(root)
    assert reg.freshness("dev1") == "fresh"
    assert reg.freshness("missing") == "unknown"
    reg.update("dev1", last_seen=time.time() - 7200)
    assert reg.freshness("dev1") == "stale"


def test_legacy_devices_json_loads(root):
    legacy = [{
        "device_id": "old", "name": "Old", "kind": "computer",
        "capabilities": ["a"], "constraints": {}, "status": "registered",
        "owner": "user", "last_seen": 123.0,
    }]
    (root / ".ghost").mkdir(exist_ok=True)
    (root / ".ghost" / "devices.json").write_text(json.dumps(legacy))
    device = DeviceRegistry(root).get("old")
    assert device is not None
    assert device.transport == "unknown"
    assert device.pairing_state == "unpaired"
    assert device.verified_capabilities == []


def test_cli_register_with_transport_firmware(root, capsys):
    _cmd_device(argparse.Namespace(action="register", device_id="d2", name="Sim",
                                   kind="pan_tilt", capability=["pan_tilt"],
                                   transport="simulated", firmware="0.1"))
    data = json.loads(capsys.readouterr().out)
    assert data["transport"] == "simulated"
    assert data["firmware_version"] == "0.1"


def test_cli_pair_revoke_status(root, capsys):
    _cmd_device(argparse.Namespace(action="pair", device_id="dev1"))
    assert '"pairing_state": "paired"' in capsys.readouterr().out
    _cmd_device(argparse.Namespace(action="revoke", device_id="dev1"))
    assert '"pairing_state": "revoked"' in capsys.readouterr().out
    _cmd_device(argparse.Namespace(action="status", device_id="dev1"))
    data = json.loads(capsys.readouterr().out)
    assert data["freshness"] == "fresh"
    assert data["declared_capabilities"] == ["pan_tilt"]
    assert data["verified_capabilities"] == []
