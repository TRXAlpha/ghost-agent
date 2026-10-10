"""TD5 (S06): durable daily inference budget for unattended proactive loops."""
from datetime import date

import pytest

import ghost_agent.proactive as proactive_module
from ghost_agent.overnight import OvernightRunner
from ghost_agent.openjarvis_bridge.spend import DurableInferenceBudget
from ghost_agent.proactive import ProactiveEngine


def _proposal() -> str:
    import json
    return json.dumps({"title": "Try a thing", "goal": "Explore bounded experiment",
                       "rationale": "test", "kind": "learning", "confidence": 0.9,
                       "requires_user_input": False})


def _daily_budget(root, max_calls: int) -> DurableInferenceBudget:
    return DurableInferenceBudget(
        root, max_calls=max_calls,
        path=root / ".ghost" / "inference" / f"{date.today():%Y-%m-%d}.json")


def test_exhausted_budget_defers_without_calling_model(tmp_path, monkeypatch):
    calls = []
    engine = ProactiveEngine(tmp_path, "tiny",
                             chat_fn=lambda *a, **k: calls.append(1) or _proposal(),
                             inference_budget=_daily_budget(tmp_path, 1))
    first = engine.tick(require_idle=False)
    assert first["status"] in {"proposed", "queued"}
    second = engine.tick(require_idle=False)
    assert second == {"status": "deferred", "reason": "inference_budget_exhausted"}
    assert len(calls) == 1


def test_new_day_creates_new_budget_file(tmp_path):
    budget = _daily_budget(tmp_path, 1)
    assert budget.path == tmp_path / ".ghost" / "inference" / f"{date.today():%Y-%m-%d}.json"
    assert not budget.path.exists()
    budget.reserve(1)
    assert budget.path.exists()
    assert budget.path.name == f"{date.today():%Y-%m-%d}.json"


def test_cli_default_48_reaches_engine(tmp_path, monkeypatch):
    import ghost_agent.proactive as proactive_module
    from ghost_agent.cli import _daily_inference_budget
    seen = {}

    class _Engine:
        def __init__(self, root, model, **kwargs):
            seen["kwargs"] = kwargs
            self.tick = lambda **k: {"status": "deferred", "reason": "test"}

    monkeypatch.setattr(proactive_module, "ProactiveEngine", _Engine)
    runner = OvernightRunner(tmp_path, "tiny",
                             inference_budget=_daily_inference_budget(tmp_path, 48))
    runner._proactive()()
    assert seen["kwargs"].get("inference_budget") is not None
