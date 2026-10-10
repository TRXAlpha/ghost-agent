"""Tests for TF3: device command gate and pan/tilt simulator."""
import argparse
import json
import time

import pytest

from ghost_agent.cli import _cmd_device
from ghost_agent.device_control import (AxisLimits, CommandRefused,
                                        DeviceCommandGate, PanTiltCommand,
                                        SimulatedPanTilt, limits_from_constraints)
from ghost_agent.devices import DeviceRegistry
from ghost_agent.runtime import EmergencyStop

LIMITS = {
    "pan": AxisLimits(min_deg=-90.0, max_deg=90.0, max_rate_deg_s=45.0, safe_deg=0.0),
    "tilt": AxisLimits(min_deg=-30.0, max_deg=30.0, max_rate_deg_s=20.0, safe_deg=0.0),
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    reg = DeviceRegistry(tmp_path)
    reg.register("pt1", "PanTilt", "pan_tilt", ["pan_tilt"],
                 constraints={"pan_min_deg": -90, "pan_max_deg": 90,
                              "pan_max_rate_deg_s": 45, "pan_safe_deg": 0,
                              "tilt_min_deg": -30, "tilt_max_deg": 30,
                              "tilt_max_rate_deg_s": 20, "tilt_safe_deg": 0})
    reg.update("pt1", pairing_state="paired")
    reg2 = DeviceRegistry(tmp_path)
    reg2.register("pt_nolimits", "NoLimits", "pan_tilt", ["pan_tilt"])
    reg2.update("pt_nolimits", pairing_state="paired")
    sim = SimulatedPanTilt(LIMITS)
    sim.connected = True
    return tmp_path, reg, sim


def _gate(root, reg, device_id="pt1", limits=LIMITS):
    return DeviceCommandGate(root, reg, device_id, limits)


def test_success_emits_event_and_moves(env):
    root, reg, sim = env
    result = _gate(root, reg).send(sim, PanTiltCommand(10.0, 5.0), 1.0)
    assert result["accepted"] is True
    assert sim.position == {"pan": 10.0, "tilt": 5.0}


def test_refused_outside_limits(env):
    root, reg, sim = env
    with pytest.raises(CommandRefused):
        _gate(root, reg).send(sim, PanTiltCommand(95.0, 0.0), 1.0)
    with pytest.raises(CommandRefused):
        _gate(root, reg).send(sim, PanTiltCommand(0.0, -40.0), 1.0)


def test_refused_rate_too_high(env):
    root, reg, sim = env
    # 90deg in 1s = 90deg/s > 45deg/s max
    with pytest.raises(CommandRefused):
        _gate(root, reg).send(sim, PanTiltCommand(90.0, 0.0), 1.0)
    # same move over 3s is fine (30deg/s)
    _gate(root, reg).send(sim, PanTiltCommand(90.0, 0.0), 3.0)


def test_refused_not_paired(env):
    root, reg, sim = env
    reg.update("pt1", pairing_state="unpaired")
    with pytest.raises(CommandRefused):
        _gate(root, reg).send(sim, PanTiltCommand(0.0, 0.0), 1.0)


def test_refused_disconnected(env):
    root, reg, sim = env
    sim.connected = False
    with pytest.raises(CommandRefused):
        _gate(root, reg).send(sim, PanTiltCommand(0.0, 0.0), 1.0)


def test_refused_no_limits(env):
    root, reg, _ = env
    sim = SimulatedPanTilt({})
    sim.connected = True
    with pytest.raises(CommandRefused):
        _gate(root, reg, device_id="pt_nolimits", limits={}).send(
            sim, PanTiltCommand(0.0, 0.0), 1.0)
    # missing limits for one axis only
    sim2 = SimulatedPanTilt({"pan": LIMITS["pan"]})
    sim2.connected = True
    with pytest.raises(CommandRefused):
        _gate(root, reg, limits={"pan": LIMITS["pan"]}).send(
            sim2, PanTiltCommand(0.0, 0.0), 1.0)


def test_emergency_stop_wins(env):
    root, reg, sim = env
    EmergencyStop(root).engage("test")
    try:
        with pytest.raises(CommandRefused):
            _gate(root, reg).send(sim, PanTiltCommand(0.0, 0.0), 1.0)
    finally:
        EmergencyStop(root).clear()


def test_disconnect_moves_to_safe(env):
    root, reg, sim = env
    _gate(root, reg).send(sim, PanTiltCommand(45.0, 20.0), 2.0)
    sim.disconnect()
    assert sim.connected is False
    assert sim.position == {"pan": 0.0, "tilt": 0.0}


def test_limits_from_constraints(tmp_path):
    got = limits_from_constraints({"pan_min_deg": -90, "pan_max_deg": 90,
                                   "pan_max_rate_deg_s": 45, "pan_safe_deg": 0})
    assert got["pan"] == AxisLimits(-90.0, 90.0, 45.0, 0.0)
    assert "tilt" not in got
    assert limits_from_constraints({}) == {}


def test_cli_simulate_success_and_refusal(env, capsys):
    root, reg, _ = env
    _cmd_device(argparse.Namespace(action="simulate", device_id="pt1", pan=5.0,
                                   tilt=2.0, duration=1.0, disconnect=False))
    data = json.loads(capsys.readouterr().out)
    assert data["accepted"] is True
    _cmd_device(argparse.Namespace(action="simulate", device_id="pt1", pan=500.0,
                                   tilt=0.0, duration=1.0, disconnect=False))
    assert "Command refused" in capsys.readouterr().out
