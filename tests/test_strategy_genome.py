"""E1: the strategy genome is validated data; defaults reproduce historical prompts."""
import json

import pytest

import ghost_agent.orchestrator as module
from ghost_agent.orchestrator import Orchestrator
from ghost_agent.strategy import (DEFAULT_GENES, Strategy, StrategyError, StrategyStore,
                                  genes_digest, resolve_genes, validate_genes)


def test_defaults_validate_and_digest_is_stable():
    assert validate_genes({}) == DEFAULT_GENES
    assert genes_digest({}) == genes_digest(dict(DEFAULT_GENES))


@pytest.mark.parametrize("bad", [
    {"nope": 1},
    {"plan_enabled": "yes"},
    {"test_policy": "random"},
    {"repair_generic": "no placeholder here"},
    {"repair_tests_failed": "{feedback} {other}"},
    {"skill_k": 9},
    {"system_rules_extra": "x" * 2001},
])
def test_invalid_genes_refused(bad):
    with pytest.raises(StrategyError):
        validate_genes(bad)


def test_store_active_set_and_rollback(tmp_path):
    store = StrategyStore(tmp_path)
    assert store.active().origin == "default"
    s1 = store.save(Strategy("s1", {"plan_enabled": False}, origin="manual"))
    store.set_active("s1", reason="test")
    assert store.active().strategy_id == "s1"
    assert store.rollback().origin == "default"
    assert store.active().origin == "default"


def test_corrupt_active_falls_back_to_default(tmp_path):
    store = StrategyStore(tmp_path)
    store.dir.mkdir(parents=True)
    store.active_path.write_text("{broken", encoding="utf-8")
    assert store.active().origin == "default"


def test_explicit_contract_genes_win(tmp_path):
    genes = resolve_genes({"strategy_genes": {"test_policy": "tests_first"}}, tmp_path)
    assert genes["test_policy"] == "tests_first"


def _run(tmp_path, monkeypatch, genes, writer):
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps({"id": "g", "title": "Feature", "goal": "Implement app with tests",
                                     "context": {}, "constraints": {"iteration_limit": 4, "strategy_genes": genes}}))
    seen = []

    def model(_model, messages, **kwargs):
        seen.append(messages)
        return json.dumps(writer(messages, len(seen)))

    monkeypatch.setattr(module, "ollama_chat", model)
    Orchestrator(tmp_path, model="scripted").run_task(task_path)
    state = json.loads((tmp_path / "workspaces" / "g" / "state.json").read_text())
    return state, seen


GOOD = [{"tool": "write_file", "path": "app.py", "content": "ANSWER = 42\n"},
        {"tool": "write_file", "path": "test_app.py",
         "content": "from app import ANSWER\n\ndef test_a():\n    assert ANSWER == 42\n"}]


def test_plan_disabled_skips_plan_turn(tmp_path, monkeypatch):
    state, seen = _run(tmp_path, monkeypatch, {"plan_enabled": False},
                       lambda m, n: {"thought": "t", "actions": GOOD})
    assert state["last_result"] == "tests_passed"
    assert state["turns"]["PLAN"] == 0
    assert all("Create the plan now" not in m[-1]["content"] for m in seen)


def test_tests_first_prompt_and_rules_extra_reach_model(tmp_path, monkeypatch):
    genes = {"plan_enabled": False, "test_policy": "tests_first", "system_rules_extra": "RULE-XYZ"}
    state, seen = _run(tmp_path, monkeypatch, genes, lambda m, n: {"thought": "t", "actions": GOOD})
    assert "Start with the tests" in seen[0][-1]["content"]
    assert "RULE-XYZ" in seen[0][0]["content"]
    assert state["strategy_digest"] == genes_digest(genes)
