"""TC3: repeated-failure backoff for SelfImprover.propose."""
import json

import pytest

import ghost_agent.self_improver as si_module
from ghost_agent.self_improver import SelfImprover


def _signature(error_type: str = "TestError", subsystem: str = "orchestrator") -> dict:
    return {
        "error_type": error_type,
        "subsystem": subsystem,
        "count": 2,
        "normalized_error": "boom",
        "sample_error": "boom",
    }


@pytest.fixture
def no_queue(monkeypatch):
    """Stub queue_sandbox_outcome so propose() never touches the real worker."""
    captured: dict = {}

    class _Job:
        job_id = "job_1"

    def fake_queue(root, goal, model, **kwargs):
        captured["goal"] = goal
        return _Job()

    monkeypatch.setattr(
        si_module, "queue_sandbox_outcome", fake_queue, raising=False)
    # queue_sandbox_outcome is imported inside propose; patch the source module
    import ghost_agent.proactive as proactive
    monkeypatch.setattr(proactive, "queue_sandbox_outcome", fake_queue)
    return captured


def _seed_failures(improver: SelfImprover, key: str, count: int,
                   created_at: float) -> None:
    for _ in range(count):
        cand = improver.store.create_candidate(
            hypothesis="h", components=["orchestrator"], baseline={},
            plan="p", budget_hours=1.0, rollback="r")
        improver.store.update_candidate(cand.candidate_id, {
            "status": "failed", "signature_key": key,
            "created_at": created_at, "updated_at": created_at})


def test_three_recent_failures_skip_signature_and_choose_next(tmp_path, no_queue):
    improver = SelfImprover(tmp_path)
    key = "TestError|orchestrator|boom"
    _seed_failures(improver, key, 3, created_at=si_module.time.time() - 100)

    result = improver.propose(
        [_signature(), _signature(error_type="OtherError", subsystem="worker")],
        model="scripted")

    assert result is not None
    assert result.signature_key == "OtherError|worker|boom"


def test_two_recent_failures_still_propose(tmp_path, no_queue):
    improver = SelfImprover(tmp_path)
    key = "TestError|orchestrator|boom"
    _seed_failures(improver, key, 2, created_at=si_module.time.time() - 100)

    result = improver.propose([_signature()], model="scripted")

    assert result is not None
    assert result.signature_key == key


def test_failures_older_than_window_do_not_count(tmp_path, no_queue):
    improver = SelfImprover(tmp_path)
    key = "TestError|orchestrator|boom"
    old = si_module.time.time() - (si_module.FAILURE_WINDOW_SECONDS + 60)
    _seed_failures(improver, key, 3, created_at=old)

    result = improver.propose([_signature()], model="scripted")

    assert result is not None
    assert result.signature_key == key


def test_defer_decision_not_duplicated_on_repeat(tmp_path, no_queue):
    improver = SelfImprover(tmp_path)
    key = "TestError|orchestrator|boom"
    _seed_failures(improver, key, 3, created_at=si_module.time.time() - 100)

    sigs = [_signature()]
    first = improver.propose(sigs, model="scripted")
    second = improver.propose(sigs, model="scripted")

    assert first is None
    assert second is None
    decisions = []
    for cand in improver.store.list_candidates("failed"):
        decisions.extend(improver.store.list_decisions(cand.candidate_id))
    defer_decisions = [d for d in decisions
                       if d.reason == "deferred: 3 failures in 7 days"]
    assert len(defer_decisions) == 1
    assert defer_decisions[0].decision == "rejected"
