"""Selected inference path shared by coding, research, and protocol benchmarks."""
from __future__ import annotations

import os
import random
import time

from .ollama import ollama_chat as legacy_chat
from ..openjarvis_bridge.spend import DurableInferenceBudget, Reservation  # OJ08 budget/retry discipline

# OJ08: bounded transport retry policy (shared by legacy and openjarvis backends).
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_BASE = 0.5
RETRY_BACKOFF_CAP = 8.0


def _retryable(exc: BaseException) -> bool:
    """Transport-level failures only. Config errors (ValueError/IntegrationError
    and HTTP 4xx) are never retried; 5xx and connection/read failures are.
    Local-resource errors (missing binary, permissions) are config, not transport."""
    import requests
    if isinstance(exc, requests.exceptions.HTTPError):
        response = getattr(exc, "response", None)
        return response is not None and getattr(response, "status_code", 0) >= 500
    if isinstance(exc, (FileNotFoundError, PermissionError)):
        return False
    return isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout, OSError))


def _sleep_backoff(attempt: int) -> None:
    delay = min(RETRY_BACKOFF_BASE * (2 ** (attempt - 1)), RETRY_BACKOFF_CAP)
    time.sleep(delay * random.uniform(0.5, 1.0))


def route_identity() -> dict:
    """Persist routing intent without credentials; workers cannot silently switch it."""
    backend = os.environ.get("GHOST_INFERENCE_BACKEND", "legacy")
    if backend == "legacy":
        return {"backend": "legacy"}
    if backend != "openjarvis":
        raise ValueError(f"Unknown GHOST_INFERENCE_BACKEND: {backend}")
    from ..openjarvis_bridge.runtime import EngineSettings
    settings = EngineSettings.from_env()
    return {"backend": backend, "engine": settings.engine, "endpoint": settings.endpoint,
            "allow_remote": settings.allow_remote}


def chat(model, messages, options=None, *, budget=None, reservation=None,
         job_id=None, event_bus=None, reputation=None, **kwargs):
    """Dispatch one bounded-retry inference call on the configured backend.

    budget/reservation: (DurableInferenceBudget, Reservation) from spend.py.
    The reservation stays PENDING across retries; is committed ONCE on first
    success (with real input_chars), rolled back ONCE on final failure. Never
    double-committed or silently burned. budget without its matching
    reservation is refused rather than synthesized.
    """
    import json
    from ..runtime import Event
    committed = False
    last_error: BaseException | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            backend = os.environ.get("GHOST_INFERENCE_BACKEND", "legacy")
            if backend == "legacy":
                result = legacy_chat(model, messages, options, **kwargs)
            elif backend == "openjarvis":
                from ..openjarvis_bridge.runtime import chat as upstream_chat
                result = upstream_chat(model, messages, options, **kwargs)
            else:
                raise ValueError(f"Unknown GHOST_INFERENCE_BACKEND: {backend}")
            if budget is not None and not committed:
                if not isinstance(budget, DurableInferenceBudget):
                    raise TypeError("budget must be a DurableInferenceBudget")
                if reservation is None:
                    raise ValueError(
                        "budget requires its matching reservation from budget.reserve(); "
                        "a synthetic reservation would corrupt durable call accounting")
                input_chars = len(json.dumps(messages)) if messages else 0
                budget.commit(reservation, input_chars=input_chars)
                committed = True
            return result
        except BaseException as exc:  # noqa: BLE001 - re-raised in all non-retry paths
            last_error = exc
            if attempt < RETRY_ATTEMPTS and _retryable(exc):
                if event_bus is not None:
                    try:
                        event_bus.emit(Event("model.retry", {
                            "job_id": job_id, "attempt": attempt,
                            "error_class": type(exc).__name__,
                        }))
                    except Exception:
                        pass  # telemetry must never mask the inference error
                _sleep_backoff(attempt)
                continue
            if budget is not None and not committed:
                budget.rollback(reservation or Reservation("pending", 0))
            if event_bus is not None:
                try:
                    event_bus.emit(Event("model.route.failed", {
                        "job_id": job_id, "attempt": attempt,
                        "error_class": type(exc).__name__,
                        "retryable": _retryable(exc),
                    }))
                except Exception:
                    pass  # telemetry must never mask the inference error
            if reputation is not None and _retryable(exc):
                try:
                    reputation.record_transport_failure(model=model)
                except Exception:
                    pass
            raise
    raise last_error  # unreachable
