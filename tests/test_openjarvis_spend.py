"""OJ08: durable inference spend/reservation for the OpenJarvis bridge.

Whole-project inference consumption is reserved atomically, persisted across
resume/restart, and refused when it would exceed a cap. No silent fallback to
an unconfigured or extra-model backend: an over-budget call raises BudgetExceeded.
"""
from __future__ import annotations

import pytest

from ghost_agent.openjarvis_bridge.spend import (
    BudgetExceeded,
    CorruptBudgetError,
    DurableInferenceBudget,
)


def _spend_path(tmp_path):
    return tmp_path / ".ghost" / "openjarvis" / "spend.json"


def test_initial_remaining_is_unlimited_when_no_cap(tmp_path):
    budget = DurableInferenceBudget(tmp_path)
    assert budget.remaining() == -1  # no cap (read-only; file not yet created)
    assert not _spend_path(tmp_path).exists()
    budget.reserve(1)  # a mutating op materializes the meter
    assert _spend_path(tmp_path).read_text().startswith("{")


def test_reserve_commit_records_usage(tmp_path):
    budget = DurableInferenceBudget(tmp_path, max_calls=2)
    r1 = budget.reserve(1)
    budget.commit(r1, input_chars=10, usage_units=100)
    r2 = budget.reserve(1)
    budget.commit(r2, input_chars=5, usage_units=50)
    data = budget._load()
    assert data["reserved_calls"] == 2
    assert data["committed_calls"] == 2
    assert data["input_chars"] == 15
    assert data["usage_units"] == 150
    assert budget.remaining() == 0


def test_over_cap_refuses(tmp_path):
    budget = DurableInferenceBudget(tmp_path, max_calls=1)
    budget.reserve(1)
    with pytest.raises(BudgetExceeded, match="budget exhausted"):
        budget.reserve(1)


def test_rollback_releases_capacity(tmp_path):
    budget = DurableInferenceBudget(tmp_path, max_calls=2)
    r1 = budget.reserve(1)
    r2 = budget.reserve(1)
    budget.rollback(r2)
    assert budget.remaining() == 1
    r3 = budget.reserve(1)  # should be allowed again
    budget.commit(r3)


def test_spend_persists_cross_instance(tmp_path):
    first = DurableInferenceBudget(tmp_path, max_calls=3)
    r = first.reserve(1)
    first.commit(r, input_chars=20, usage_units=200)
    # A fresh instance (simulating resumer/restart) sees the same meter
    second = DurableInferenceBudget(tmp_path, max_calls=3)
    assert second.remaining() == 2
    assert second._load()["usage_units"] == 200


def test_corrupt_state_fails_closed(tmp_path):
    _spend_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    _spend_path(tmp_path).write_text("{ not json", encoding="utf-8")
    budget = DurableInferenceBudget(tmp_path, max_calls=5)
    with pytest.raises(CorruptBudgetError):
        budget.reserve(1)
    # never treated as permissive empty budget
    with pytest.raises(CorruptBudgetError):
        budget._load()


def test_input_char_budget_refused(tmp_path):
    budget = DurableInferenceBudget(tmp_path, max_input_chars=10)
    r = budget.reserve(1)
    with pytest.raises(BudgetExceeded, match="Input-character budget exhausted"):
        budget.commit(r, input_chars=11)
    # reservation persists until commit/rollback
    budget.rollback(r)


def test_invalid_caps_rejected(tmp_path):
    with pytest.raises(ValueError):
        DurableInferenceBudget(tmp_path, max_calls=-1)
    with pytest.raises(ValueError):
        DurableInferenceBudget(tmp_path, max_calls=1.5)


# -- wiring: BoundedEngine honors a durable budget ------------------------------

class _FakeEngine:
    def generate(self, messages, **kwargs):
        return {"content": "ok", "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def test_bounded_engine_enforces_durable_budget(tmp_path):
    from ghost_agent.openjarvis_bridge.assistant import BoundedEngine
    from ghost_agent.openjarvis_bridge.runtime import IntegrationError

    budget = DurableInferenceBudget(tmp_path, max_calls=1)
    bounded = BoundedEngine(_FakeEngine(), tmp_path, max_calls=5, spend=budget)
    bounded.generate([])
    # Second call exceeds the durable cap even though the per-run max is 5
    with pytest.raises(IntegrationError) as exc:
        bounded.generate([])
    assert "budget exhausted" in str(exc.value)
    assert budget._load()["committed_calls"] == 1


def test_bounded_engine_without_spend_unchanged(tmp_path):
    from ghost_agent.openjarvis_bridge.assistant import BoundedEngine
    bounded = BoundedEngine(_FakeEngine(), tmp_path, max_calls=5)
    bounded.generate([])
    bounded.generate([])  # per-run max 5; no durable cap
    assert bounded.calls == 2
    assert bounded.spend is None
