"""TB3: ghost export — versioned, secret-free personal-state export (R14)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ghost_agent.cli import _cmd_export
from ghost_agent.control_plane import GhostSDK
from ghost_agent.kernel import GoalStore
from ghost_agent.security import SECRET_PATTERNS


def _plant_goal_with_secret(tmp_path: Path) -> None:
    # Plant a string matching a real SECRET_PATTERNS entry via its pattern.
    from ghost_agent.policy import PolicyStore
    PolicyStore(tmp_path).set("build", True)
    goal = GoalStore(tmp_path).create("token sk-abcdef1234567890abcdef1234 leak")
    assert "sk-" in goal.title


def _run_export(tmp_path: Path, *extra: str) -> dict:
    from ghost_agent.cli import main as _skip  # noqa: F401  (keep import style)
    from ghost_agent.control_plane import GhostSDK as _S  # noqa: F401
    out = tmp_path / "export.json"
    argv = ["ghost", "export", "--out", str(out), *extra]
    from ghost_agent import cli
    ns = argparse.Namespace(out=str(out), only=extra[1] if extra else "")
    _cmd_export(ns)
    return json.loads(out.read_text(encoding="utf-8"))


def test_export_includes_all_sections(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from ghost_agent.policy import PolicyStore
    PolicyStore(tmp_path).set("build", True)
    GoalStore(tmp_path).create("write the report")
    payload = _run_export(tmp_path)
    assert payload["format"] == "ghost-export" and payload["version"] == 1
    expected = {"goals", "approvals", "notifications", "validated_memories",
                "devices", "opportunities", "scheduler_jobs", "capability_gaps",
                "policy"}
    assert set(payload["sections"]) == expected
    assert [g["title"] for g in payload["sections"]["goals"]] == ["write the report"]


def test_export_redacts_secret_in_goal_title(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _plant_goal_with_secret(tmp_path)
    payload = _run_export(tmp_path, "--only", "goals")
    title = payload["sections"]["goals"][0]["title"]
    assert "sk-abcdef" not in title
    assert "[REDACTED:" in title


def test_export_refuses_overwrite(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "export.json"
    out.write_text("{}")
    ns = argparse.Namespace(out=str(out), only="")
    _cmd_export(ns)
    assert "already exists" in capsys.readouterr().out


def test_export_only_filters(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = _run_export(tmp_path, "--only", "goals,devices")
    assert set(payload["sections"]) == {"goals", "devices"}


def test_export_unknown_section_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    ns = argparse.Namespace(out=str(tmp_path / "x.json"), only="bogus")
    _cmd_export(ns)
    assert "unknown export section" in capsys.readouterr().out
