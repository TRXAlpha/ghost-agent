import argparse
import json
import pytest

from ghost_agent.cli import _cmd_accomplish
from ghost_agent.autonomy import OutcomeStore


def args(**overrides):
    values = dict(goal=["Create app"], model="qwen2.5-coder:1.5b", research_model="research",
                  iterations=4, skip_research=True, dry_run=False, queue=True, require_file=["src/app.py"])
    return argparse.Namespace(**(values | overrides))


def test_queued_contract_persists_required_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _cmd_accomplish(args())
    job = OutcomeStore(tmp_path).list()[0]
    assert job.acceptance_contract["required_files"] == ["src/app.py"]


def test_dry_run_previews_required_files(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _cmd_accomplish(args(dry_run=True))
    result = json.loads(capsys.readouterr().out)
    assert result["preview"]["required_files"] == ["src/app.py"]


def test_invalid_contract_refused_before_artifacts(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _cmd_accomplish(args(require_file=["../outside"]))
    assert "refused" in capsys.readouterr().out
    assert not (tmp_path / ".ghost").exists()


def test_command_budgets_reach_queued_task(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _cmd_accomplish(args(command_budget_seconds=20, command_budget_calls=3))
    store = OutcomeStore(tmp_path)
    job = store.list()[0]
    constraints = store.make_task_payload(job, "", 4)["constraints"]
    assert constraints["command_budget_seconds"] == 20
    assert constraints["command_budget_calls"] == 3


def test_explicit_small_iteration_limit_is_not_increased(tmp_path, monkeypatch):
    from ghost_agent.runtime import Scheduler
    monkeypatch.chdir(tmp_path)
    _cmd_accomplish(args(iterations=1))
    assert OutcomeStore(tmp_path).list()[0].iterations == 1
    assert Scheduler(tmp_path).list()[0].budget["iterations"] == 1


@pytest.mark.parametrize("override", [{"command_budget_seconds": float("nan")},
    {"command_budget_calls": 0}, {"iterations": 1001}])
def test_invalid_limits_do_not_create_job(tmp_path, monkeypatch, override):
    monkeypatch.chdir(tmp_path)
    _cmd_accomplish(args(**override))
    assert not (tmp_path / ".ghost").exists()
