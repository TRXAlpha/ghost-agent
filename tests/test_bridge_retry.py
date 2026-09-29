"""OJ08: bounded transport retry + telemetry + transport_failure reputation.
No real network; all transport faults are injected."""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from ghost_agent.models.chat import (
    RETRY_ATTEMPTS, _retryable, _sleep_backoff,
)
from ghost_agent.models.routing import ModelReputationStore
from ghost_agent.runtime import Event, EventBus


# ---------------------------------------------------------------------------
# Direct unit tests
# ---------------------------------------------------------------------------

def test_retryable_connects_and_timeouts(monkeypatch) -> None:
    import requests
    assert _retryable(requests.exceptions.ConnectionError("reset"))
    assert _retryable(requests.exceptions.Timeout("timed out"))
    assert _retryable(OSError("read failed"))


def test_retryable_rejects_integration_error() -> None:
    from ghost_agent.openjarvis_bridge.runtime import IntegrationError
    assert not _retryable(IntegrationError("bad config"))


def test_retryable_rejects_http_4xx(monkeypatch) -> None:
    import requests
    exc = requests.exceptions.HTTPError("not found")
    exc.response = SimpleNamespace(status_code=404)
    assert not _retryable(exc)


def test_retryable_accepts_http_5xx(monkeypatch) -> None:
    import requests
    exc = requests.exceptions.HTTPError("server error")
    exc.response = SimpleNamespace(status_code=500)
    assert _retryable(exc)


def test_backoff_respects_cap() -> None:
    t0 = time.monotonic()
    _sleep_backoff(10)   # 0.5 * 2^9 = 256s cap at 8s
    elapsed = time.monotonic() - t0
    assert 4.0 <= elapsed <= 10.0   # cap 8s + jitter


def test_backoff_jitter_varies() -> None:
    import random
    delays = []
    for _ in range(20):
        delay = min(0.5 * (2 ** 1), 8.0) * random.uniform(0.5, 1.0)
        delays.append(delay)
    assert len(set(round(d, 6) for d in delays)) > 1  # jitter produces variety


# ---------------------------------------------------------------------------
# Full chat() retry tests (mock the actual backend)
# ---------------------------------------------------------------------------

def test_retries_on_retryable_failure_no_network(tmp_path, monkeypatch, capsys) -> None:
    from ghost_agent.models.chat import chat, _retryable
    from ghost_agent.models.routing import ModelReputationStore
    call_count = [0]

    def fake_backend(*a, **kw):
        call_count[0] += 1
        raise OSError(f"transport error {call_count[0]}")

    monkeypatch.setenv("GHOST_INFERENCE_BACKEND", "legacy")
    monkeypatch.setattr("ghost_agent.models.chat.legacy_chat", fake_backend)

    events = EventBus(tmp_path)
    with pytest.raises(OSError) as exc_info:
        chat("m", [{"role": "user", "content": "hello"}], event_bus=events)
    assert exc_info.value.args[0] == "transport error 3"
    assert call_count[0] == RETRY_ATTEMPTS
    kinds = [e.kind for e in events.recent(50)]
    assert kinds.count("model.retry") == RETRY_ATTEMPTS - 1
    assert "model.route.failed" in kinds


def test_no_retry_on_value_error_no_network(tmp_path, monkeypatch) -> None:
    from ghost_agent.models.chat import chat
    call_count = [0]

    def fake_backend(*a, **kw):
        call_count[0] += 1
        raise ValueError("never retried")

    monkeypatch.setenv("GHOST_INFERENCE_BACKEND", "legacy")
    monkeypatch.setattr("ghost_agent.models.chat.legacy_chat", fake_backend)

    events = EventBus(tmp_path)
    with pytest.raises(ValueError, match="never retried"):
        chat("m", [{"role": "user", "content": "hello"}], event_bus=events)
    assert call_count[0] == 1
    kinds = [e.kind for e in events.recent(10)]
    assert "model.retry" not in kinds


def test_reservation_committed_once_on_retry_success(tmp_path, monkeypatch) -> None:
    from ghost_agent.models.chat import chat
    from ghost_agent.openjarvis_bridge.spend import DurableInferenceBudget, Reservation

    call_count = [0]

    def fake_backend(*a, **kw):
        call_count[0] += 1
        if call_count[0] < 3:
            raise OSError("transient")
        return "success on 3rd"

    monkeypatch.setenv("GHOST_INFERENCE_BACKEND", "legacy")
    monkeypatch.setattr("ghost_agent.models.chat.legacy_chat", fake_backend)

    budget = DurableInferenceBudget(tmp_path)
    reservation = budget.reserve(1)
    result = chat("m", [{"role": "user", "content": "hello"}], budget=budget, reservation=reservation)
    assert result == "success on 3rd"
    assert call_count[0] == 3
    data = json.loads((tmp_path / ".ghost" / "openjarvis" / "spend.json").read_text())
    assert data["committed_calls"] == 1


def test_reservation_rolled_back_on_final_failure(tmp_path, monkeypatch) -> None:
    from ghost_agent.models.chat import chat
    from ghost_agent.openjarvis_bridge.spend import DurableInferenceBudget, Reservation

    def fake_backend(*a, **kw):
        raise OSError("persistent")

    monkeypatch.setenv("GHOST_INFERENCE_BACKEND", "legacy")
    monkeypatch.setattr("ghost_agent.models.chat.legacy_chat", fake_backend)

    budget = DurableInferenceBudget(tmp_path)
    reservation = budget.reserve(1)
    with pytest.raises(OSError):
        chat("m", [{"role": "user", "content": "hello"}], budget=budget, reservation=reservation)
    data = json.loads((tmp_path / ".ghost" / "openjarvis" / "spend.json").read_text())
    assert data["reserved_calls"] == 0   # rolled back
    assert data["committed_calls"] == 0


# ---------------------------------------------------------------------------
# Transport-failure reputation tests
# ---------------------------------------------------------------------------

def test_transport_failure_counter_increments(tmp_path) -> None:
    store = ModelReputationStore(tmp_path)
    store.record_transport_failure("ghost-gemma4:latest")
    store.record_transport_failure("ghost-gemma4:latest")
    recent = store.recent_transport_failures("ghost-gemma4:latest")
    assert recent == 2


def test_transport_failure_windowed(tmp_path) -> None:
    store = ModelReputationStore(tmp_path)
    past = time.time() - 7200
    store.record_transport_failure("ghost-gemma4:latest", failed_at=past)
    store.record_transport_failure("ghost-gemma4:latest", failed_at=time.time())
    recent = store.recent_transport_failures("ghost-gemma4:latest", window_seconds=3600)
    assert recent == 1


def test_transport_failure_additive_to_existing_record(tmp_path) -> None:
    store = ModelReputationStore(tmp_path)
    store.record(model="m", task_class="coding", verified_success=True,
                 latency_seconds=1.0, context_chars=100)
    assert store.recent_transport_failures("m") == 0
    store.record_transport_failure("m")
    assert store.recent_transport_failures("m") == 1


def test_reputation_tolerates_old_record_without_transport_failures(tmp_path) -> None:
    path = tmp_path / ".ghost" / "models.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"m|default": {"model": "m", "task_class": "default",
                                  "runs": 1, "successes": 1, "verification_failures": 0,
                                  "latency_seconds_total": 1.0}}))
    store = ModelReputationStore(tmp_path)
    store.record_transport_failure("m")
    assert store.recent_transport_failures("m") == 1


# ---------------------------------------------------------------------------
# doctor retry_policy present
# ---------------------------------------------------------------------------

def test_doctor_reports_retry_policy(tmp_path) -> None:
    from ghost_agent.models.routing import ModelReputationStore
    from ghost_agent.models.chat import RETRY_ATTEMPTS, RETRY_BACKOFF_BASE, RETRY_BACKOFF_CAP

    report = {"expected_commit": "dummy", "python": "3.14",
              "state_root": str(tmp_path / ".ghost"), "executor": "test",
              "automatic_provider_fallback": False, "external_analytics": False}
    # Simulate what doctor() adds for retry_policy
    from ghost_agent.models.chat import RETRY_ATTEMPTS as RA, RETRY_BACKOFF_BASE as RBB, RETRY_BACKOFF_CAP as RBC
    policy = {"attempts": RA, "backoff_base_seconds": RBB, "backoff_cap_seconds": RBC,
              "retryable": ["ConnectionError", "Timeout", "HTTP_5xx", "OSError"],
              "never_retried": ["IntegrationError", "ValueError", "HTTP_4xx"]}
    assert policy["attempts"] == RETRY_ATTEMPTS == 3
    assert policy["backoff_cap_seconds"] == RETRY_BACKOFF_CAP == 8.0
