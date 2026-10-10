"""TB1: standing policy modes (R04)."""
from __future__ import annotations

import json

import pytest

from ghost_agent.cli import main as cli_main
from ghost_agent.overnight import OvernightRunner
from ghost_agent.policy import MODES, DEFAULTS, PolicyError, PolicyStore
from ghost_agent.proactive import ProactiveError, queue_sandbox_outcome


def test_defaults_when_file_missing(tmp_path):
    assert PolicyStore(tmp_path).load() == dict(DEFAULTS)


def test_set_load_round_trip(tmp_path):
    store = PolicyStore(tmp_path)
    result = store.set("operate", True)
    assert result["operate"] is True
    assert PolicyStore(tmp_path).load()["operate"] is True
    raw = json.loads((tmp_path / ".ghost" / "policy.json").read_text())
    assert raw["version"] == 1 and raw["updated_by"] == "owner"


def test_corrupt_file_fails_closed(tmp_path):
    (tmp_path / ".ghost").mkdir()
    (tmp_path / ".ghost" / "policy.json").write_text("{not json")
    store = PolicyStore(tmp_path)
    assert store.load() == {"suggest": True, **{m: False for m in MODES if m != "suggest"}}
    with pytest.raises(PolicyError, match="corrupt"):
        store.set("build", True)


def test_non_bool_mode_fails_closed(tmp_path):
    (tmp_path / ".ghost").mkdir()
    (tmp_path / ".ghost" / "policy.json").write_text('{"version": 1, "modes": {"build": "yes"}}')
    assert PolicyStore(tmp_path).load()["build"] is False


def test_unknown_mode_rejected(tmp_path):
    store = PolicyStore(tmp_path)
    with pytest.raises(PolicyError):
        store.set("chaos", True)
    with pytest.raises(PolicyError):
        store.require("chaos", "x")


def test_queue_sandbox_outcome_requires_build(tmp_path):
    PolicyStore(tmp_path).set("build", False)
    with pytest.raises(ProactiveError, match="needs 'build' mode"):
        queue_sandbox_outcome(tmp_path, "do a thing", "m", source="proactive")


def test_queue_sandbox_outcome_self_improver_needs_improve(tmp_path):
    store = PolicyStore(tmp_path)
    store.set("build", True)
    with pytest.raises(ProactiveError, match="needs 'improve' mode"):
        queue_sandbox_outcome(tmp_path, "improve a thing", "m", source="self_improver")


def test_queue_sandbox_outcome_allowed_when_on(permissive_policy):
    scheduled = queue_sandbox_outcome(permissive_policy, "do a thing", "m")
    assert scheduled is not None


def test_overnight_run_refuses_without_idle_work(tmp_path, monkeypatch):
    calls = []
    runner = OvernightRunner(tmp_path, "m", proactive_tick=lambda *a, **k: calls.append(1) or {})
    with pytest.raises(PolicyError, match="idle_work"):
        runner.run(max_cycles=1)
    assert calls == []


def test_cli_policy_show_and_set(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["ghost", "policy", "show"])
    cli_main()
    out = capsys.readouterr().out
    assert json.loads(out) == dict(DEFAULTS)
    monkeypatch.setattr("sys.argv", ["ghost", "policy", "set", "operate", "on"])
    cli_main()
    out = capsys.readouterr().out
    assert json.loads(out)["operate"] is True
    monkeypatch.setattr("sys.argv", ["ghost", "policy", "set", "bogus", "on"])
    cli_main()
    assert "unknown policy mode" in capsys.readouterr().out
