"""Focused tests for the new MVP service health checks and placeholder adapters.

These verify:
- Doctor.check() reports real Ollama and Docker daemon availability (read-only probes)
- Doctor.check() reports the optional integration adapters as not_configured placeholders
- The Telegram / Huawei / DeskSentinel adapters are clearly marked placeholders and do
  NOT fake a live connection or live data when credentials/hardware are absent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ghost_agent.runtime import Doctor
from ghost_agent import integrations as intg
from ghost_agent.integrations import (
    telegram,
    huawei,
    desksentinel,
)


# ── Service health checks (real probes; values depend on the local machine) ──

def test_doctor_reports_ollama_key(tmp_path: Path) -> None:
    """Ollama probe must be present (ok | not_running | error), never absent."""
    check = Doctor(tmp_path).check()
    assert "ollama" in check
    assert check["ollama"] in {"ok", "not_running", "error"}


def test_doctor_reports_docker_key(tmp_path: Path) -> None:
    """Docker probe must be present (ok | not_running | error), never absent."""
    check = Doctor(tmp_path).check()
    assert "docker" in check
    assert check["docker"] in {"ok", "not_running", "error"}


def test_doctor_available_models_is_list(tmp_path: Path) -> None:
    """available_models() must return a list (possibly empty), not raise on outage."""
    models = Doctor(tmp_path).available_models()
    assert isinstance(models, list)
    assert all(isinstance(m, str) and m for m in models)


# ── Optional integration adapters reported by Doctor ──

def test_doctor_reports_integration_placeholders(tmp_path: Path) -> None:
    """Doctor must surface all three integration adapters as not_configured."""
    check = Doctor(tmp_path).check()
    assert check["telegram"] == "not_configured"
    assert check["huawei"] == "not_configured"
    assert check["desksentinel"] == "not_configured"


# ── Telegram placeholder ──

def test_telegram_not_configured() -> None:
    assert telegram.is_configured() is False
    assert telegram.health_check()["status"] == "not_configured"


def test_telegram_send_raises_connection_error() -> None:
    with pytest.raises(ConnectionError, match="not configured"):
        telegram.send_message("123", "hi")


def test_telegram_health_check_not_fake() -> None:
    """We must not fabricate a successful Telegram connection."""
    note = telegram.health_check()["note"]
    assert "No live Telegram" in note


# ── Huawei placeholder ──

def test_huawei_not_configured() -> None:
    assert huawei.is_configured() is False
    assert huawei.health_check()["status"] == "not_configured"


def test_huawei_get_summary_raises_connection_error() -> None:
    with pytest.raises(ConnectionError):
        huawei.get_health_summary(days=7)


def test_huawei_health_check_not_fake() -> None:
    assert "No live Huawei" in huawei.health_check()["note"]


# ── DeskSentinel placeholder ──

def test_desksentinel_not_configured() -> None:
    assert desksentinel.is_configured() is False
    assert desksentinel.health_check()["status"] == "not_configured"


def test_desksentinel_get_status_is_mock() -> None:
    """get_status() returns a clearly-marked mock, not live telemetry."""
    status = desksentinel.get_status()
    assert isinstance(status, desksentinel.DeskSentinelStatus)
    # fields defined by the interface
    assert hasattr(status, "height_mm")
    assert hasattr(status, "target_height_mm")
    assert "ESP32" in desksentinel.health_check()["note"]
    assert "not live telemetry" in desksentinel.health_check()["note"]


def test_desksentinel_set_target_raises_connection_error() -> None:
    with pytest.raises(ConnectionError):
        desksentinel.set_target_height(1200.0)


# ── Integration package surface ──

def test_integration_package_exports_adapters() -> None:
    for name in ("telegram_health_check", "huawei_health_check",
                 "sentinel_health_check", "sentinel_configured"):
        assert name in intg.__all__, f"{name} missing from integrations.__all__"
