"""TN1: composite initiative score with recorded reasons."""
from __future__ import annotations

import warnings

import pytest

from ghost_agent.initiative import (DEFAULT_WEIGHTS, QUEUE_THRESHOLD,
                                    kind_success_rate, load_weights, score)
from ghost_agent.proactive import Opportunity


def make_opp(**overrides) -> Opportunity:
    fields = dict(
        opportunity_id="opp_test", title="T", goal="Do a thing",
        rationale="Because", kind="research", confidence=0.9,
        requires_user_input=False, status="proposed",
        trigger="tick", goal_ids=[], evidence_items=[],
        cost_effort="small", benefit="high",
    )
    fields.update(overrides)
    return Opportunity(**fields)


def factor_value(reasons: list[str], factor: str) -> float:
    entry = next(r for r in reasons if r.startswith(f"{factor} "))
    start = entry.rfind("(") + 1
    return float(entry[start:entry.rfind(")")][:-1].replace(" ", "")) / 1.0


# --- factor values for each enum / branch ------------------------------------

def test_goal_relevance_matched_vs_unmatched():
    assert score(make_opp(goal_ids=["g1"]))[1][0].startswith("goal_relevance matched (+0.25)")
    assert score(make_opp(goal_ids=[]))[1][0].startswith("goal_relevance none")


def test_urgency_deadline_stale_and_tick():
    reasons = score(make_opp(trigger="deadline"))[1]
    assert reasons[1] == "urgency deadline (+0.20)"
    assert score(make_opp(trigger="stale_goal"))[1][1] == "urgency stale_goal (+0.14)"
    assert score(make_opp(trigger="tick"))[1][1] == "urgency tick (+0.06)"


def test_confidence_is_clamped_to_unit_range():
    value, _ = score(make_opp(confidence=1.7))
    high, _ = score(make_opp(confidence=1.0))
    assert value == high


def test_benefit_and_effort_enum_values():
    low, _ = score(make_opp(benefit="low"))
    medium, _ = score(make_opp(benefit="medium"))
    high, _ = score(make_opp(benefit="high"))
    assert low < medium < high
    small, _ = score(make_opp(cost_effort="small"))
    medium_e, _ = score(make_opp(cost_effort="medium"))
    large, _ = score(make_opp(cost_effort="large"))
    assert small > medium_e > large  # cheaper is better


def test_interruption_penalizes_user_input():
    with_input, _ = score(make_opp(requires_user_input=True))
    without, _ = score(make_opp(requires_user_input=False))
    assert with_input < without


# --- weighted sum -------------------------------------------------------------

def test_weighted_sum_matches_hand_computation():
    opp = make_opp(goal_ids=["g1"], trigger="deadline", confidence=0.5,
                   benefit="medium", cost_effort="medium", requires_user_input=True)
    expected = (0.25 * 1.0 + 0.2 * 1.0 + 0.2 * 0.5 + 0.2 * 0.6
                + 0.1 * 0.6 + 0.05 * 0.2)
    value, _ = score(opp)
    assert value == round(expected, 3)


def test_kind_success_tuning_scales_base():
    opp = make_opp()
    base, _ = score(opp)
    tuned, reasons = score(opp, kind_success=1.0)
    assert tuned == base
    assert any("kind_success" in reason for reason in reasons)
    halved, _ = score(opp, kind_success=0.0)
    assert halved == round(base * 0.5, 3)


# --- weights file: override plus invalid fallback -----------------------------

def test_load_weights_file_overrides_defaults(tmp_path):
    ghost = tmp_path / ".ghost"
    ghost.mkdir()
    (ghost / "initiative_weights.json").write_text(
        '{"benefit": 0.5, "threshold": 0.7}', encoding="utf-8")
    weights = load_weights(tmp_path)
    assert weights["benefit"] == 0.5
    assert weights["goal_relevance"] == DEFAULT_WEIGHTS["goal_relevance"]
    assert weights["threshold"] == 0.7


def test_load_weights_invalid_file_falls_back_with_warning(tmp_path):
    ghost = tmp_path / ".ghost"
    ghost.mkdir()
    (ghost / "initiative_weights.json").write_text("not json{", encoding="utf-8")
    with pytest.warns(UserWarning):
        weights = load_weights(tmp_path)
    assert weights == DEFAULT_WEIGHTS


def test_load_weights_non_dict_file_falls_back(tmp_path):
    ghost = tmp_path / ".ghost"
    ghost.mkdir()
    (ghost / "initiative_weights.json").write_text("[1, 2]", encoding="utf-8")
    with pytest.warns(UserWarning):
        assert load_weights(tmp_path) == DEFAULT_WEIGHTS


# --- threshold blocks a high-confidence / low-score proposal ------------------

def test_below_threshold_blocks_queueing(tmp_path, monkeypatch):
    from ghost_agent.proactive import ProactiveEngine
    from tests.test_proactive import proposal
    # benefit low, large effort, no goals, plain tick -> low score
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *a, **k: proposal(
        kind="research", confidence=0.9))
    engine._score_weights = lambda: {"goal_relevance": 0.25, "urgency": 0.2,
                                     "confidence": 0.2, "benefit": 0.2,
                                     "effort": 0.1, "interruption": 0.05,
                                     "threshold": 0.99}
    result = engine.tick()
    assert result["status"] == "proposed"
    record = result["opportunity"]
    assert record["score_reasons"][-1] == "below queue threshold 0.99"
    assert "heuristic ranking; tuned only by recorded outcomes" in record["score_reasons"]


def test_legacy_records_load_with_default_score_fields(tmp_path):
    import json
    from ghost_agent.proactive import OpportunityStore
    store = OpportunityStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    legacy = [{"opportunity_id": "opp_legacy", "title": "Old", "goal": "Do a thing",
               "rationale": "Because", "kind": "research", "confidence": 0.5,
               "requires_user_input": False}]
    store.path.write_text(json.dumps(legacy), encoding="utf-8")
    loaded = store.list_all()
    assert loaded[0].score == 0.0
    assert loaded[0].score_reasons == []
    store.save(loaded[0])  # still saveable


def test_reasons_contain_every_factor(tmp_path):
    value, reasons = score(make_opp())
    for factor in ("goal_relevance", "urgency", "confidence", "benefit",
                   "effort", "interruption"):
        assert any(reason.startswith(factor) for reason in reasons)
    assert reasons[-1] == "heuristic ranking; tuned only by recorded outcomes"
    assert QUEUE_THRESHOLD == 0.55


# --- TN2: kind_success_rate over recorded outcomes ----------------------------

_rec_n = 0


def make_recorded_opp(kind="research", status="completed", result="",
                      less=False, **overrides):
    global _rec_n
    # unique id: store.save upserts by opportunity_id
    overrides.setdefault("opportunity_id", f"opp_rec_{_rec_n:04d}")
    _rec_n += 1
    return make_opp(kind=kind, status=status, result=result,
                    less_like_this=less, **overrides)


def test_kind_success_rate_none_with_fewer_than_three_decided(tmp_path):
    from ghost_agent.proactive import OpportunityStore
    store = OpportunityStore(tmp_path)
    for result in ("tests_passed", "tests_failed"):
        store.save(make_recorded_opp(result=result))
    assert kind_success_rate(store, "research") is None


def test_kind_success_rate_mixes_results(tmp_path):
    from ghost_agent.proactive import OpportunityStore
    store = OpportunityStore(tmp_path)
    store.save(make_recorded_opp(result="tests_passed"))
    store.save(make_recorded_opp(result="tests_passed"))
    store.save(make_recorded_opp(result="tests_failed"))
    store.save(make_recorded_opp(result="error"))
    # 2 successes out of 4 decided items
    assert kind_success_rate(store, "research") == pytest.approx(0.5)


def test_kind_success_rate_counts_less_like_this_as_failure(tmp_path):
    from ghost_agent.proactive import OpportunityStore
    store = OpportunityStore(tmp_path)
    store.save(make_recorded_opp(result="tests_passed"))
    store.save(make_recorded_opp(status="rejected", less=True))
    store.save(make_recorded_opp(status="dismissed", less=True))
    assert kind_success_rate(store, "research") == pytest.approx(1 / 3)


def test_kind_success_rate_ignores_undecided_records(tmp_path):
    from ghost_agent.proactive import OpportunityStore
    store = OpportunityStore(tmp_path)
    # decided: 3 failures -> rate 0.0
    for _ in range(3):
        store.save(make_recorded_opp(result="tests_failed"))
    # undecided noise must not change the count
    store.save(make_recorded_opp(result="", status="queued"))
    store.save(make_recorded_opp(result="", status="proposed"))
    store.save(make_recorded_opp(kind="focus", result="tests_passed"))
    assert kind_success_rate(store, "research") == 0.0


def test_tick_passes_kind_success_to_score(tmp_path, monkeypatch):
    import ghost_agent.initiative as initiative
    from ghost_agent.proactive import OpportunityStore, ProactiveEngine
    from tests.test_proactive import proposal
    store = OpportunityStore(tmp_path)
    for _ in range(3):
        store.save(make_recorded_opp(kind="research", result="tests_failed"))
    seen: dict = {}

    real_score = initiative.score

    def spy_score(opp, **kwargs):
        seen["kind_success"] = kwargs.get("kind_success")
        return real_score(opp, **kwargs)

    monkeypatch.setattr(initiative, "score", spy_score)
    engine = ProactiveEngine(tmp_path, "tiny",
                             chat_fn=lambda *a, **k: proposal(
                                 kind="research", confidence=0.9))
    result = engine.tick()
    assert seen["kind_success"] == pytest.approx(0.0)
    record = result["opportunity"]
    assert any("kind_success=0.00" in reason
               for reason in record["score_reasons"])


# --- TN3: bound active initiatives + ranking CLI -------------------------------

def test_active_limit_blocks_queueing(tmp_path):
    from ghost_agent.proactive import OpportunityStore, ProactiveEngine
    from tests.test_proactive import proposal
    store = OpportunityStore(tmp_path)
    # two already-active initiatives: one queued, one running
    store.save(make_recorded_opp(status="queued"))
    store.save(make_recorded_opp(status="running"))
    engine = ProactiveEngine(tmp_path, "tiny",
                             chat_fn=lambda *a, **k: proposal(confidence=0.9))
    result = engine.tick()
    record = result["opportunity"]
    assert record["status"] == "proposed"
    assert any(r.startswith("active initiative limit reached (2/2)")
               for r in record["score_reasons"])
    # exactly the two pre-existing active items; nothing new queued
    statuses = [i.status for i in OpportunityStore(tmp_path).list_all()]
    assert statuses.count("queued") == 1
    assert statuses.count("running") == 1


def test_active_limit_override_from_weights_file(tmp_path):
    from ghost_agent.proactive import OpportunityStore, ProactiveEngine
    from tests.test_proactive import proposal
    (tmp_path / ".ghost").mkdir()
    (tmp_path / ".ghost/initiative_weights.json").write_text(
        '{"max_active": 0}', encoding="utf-8")
    store = OpportunityStore(tmp_path)
    engine = ProactiveEngine(tmp_path, "tiny",
                             chat_fn=lambda *a, **k: proposal(confidence=0.9))
    result = engine.tick()
    assert result["opportunity"]["status"] == "proposed"
    assert any(r.startswith("active initiative limit reached (0/0)")
               for r in result["opportunity"]["score_reasons"])


def test_ranking_command_ordered_with_reasons(tmp_path, capsys):
    from ghost_agent.cli import _cmd_proactive_ranking
    from ghost_agent.proactive import OpportunityStore
    store = OpportunityStore(tmp_path)
    low = make_recorded_opp(status="proposed", opportunity_id="opp_low")
    low.score = 0.2
    low.score_reasons = ["effort large (+0.03)"]
    high = make_recorded_opp(status="queued", opportunity_id="opp_high")
    high.score = 0.8
    high.score_reasons = ["benefit high (+0.20)"]
    store.save(low)
    store.save(high)
    decided = make_recorded_opp(status="completed", opportunity_id="opp_decided")
    decided.score = 0.99
    store.save(decided)
    _cmd_proactive_ranking(tmp_path, limit=10, as_json=False)
    out = capsys.readouterr().out
    assert out.index("opp_high") < out.index("opp_low")
    assert "opp_decided" not in out
    assert "benefit high (+0.20)" in out
    assert "effort large (+0.03)" in out


def test_ranking_command_json_and_limit(tmp_path, capsys):
    import json as _json
    from ghost_agent.cli import _cmd_proactive_ranking
    from ghost_agent.proactive import OpportunityStore
    store = OpportunityStore(tmp_path)
    for i in range(3):
        item = make_recorded_opp(status="proposed", opportunity_id=f"opp_{i}")
        item.score = 0.1 * i
        store.save(item)
    _cmd_proactive_ranking(tmp_path, limit=2, as_json=True)
    rows = _json.loads(capsys.readouterr().out)
    assert [r["opportunity_id"] for r in rows] == ["opp_2", "opp_1"]
