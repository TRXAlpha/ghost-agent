"""Run in the legacy environment too: no mandatory upstream import."""
import pytest

from ghost_agent.models import chat
from ghost_agent.openjarvis_bridge.runtime import EngineSettings, IntegrationError


@pytest.mark.parametrize("endpoint", ["file:///tmp/model", "http://user:secret@localhost:1", "https://example.com", "http://192.168.1.4:8000", "http://localhost/?key=secret", "http://localhost/#x", "http://localhost:bad"])
def test_refuse_implicit_remote_or_credentialed_endpoint(endpoint):
    with pytest.raises(IntegrationError):
        EngineSettings(endpoint=endpoint)


def test_remote_requires_explicit_https():
    assert EngineSettings(endpoint="https://inference.example/v1", allow_remote=True)
    with pytest.raises(IntegrationError):
        EngineSettings(endpoint="http://inference.example", allow_remote=True)


@pytest.mark.parametrize("timeout", [0, -1, 301, True, float("nan"), float("inf")])
def test_timeout_validation(timeout):
    with pytest.raises(IntegrationError):
        EngineSettings(timeout=timeout)


def test_legacy_route_is_preserved(monkeypatch):
    monkeypatch.delenv("GHOST_INFERENCE_BACKEND", raising=False)
    monkeypatch.setattr(chat, "legacy_chat", lambda *a, **k: "legacy")
    assert chat.chat("model", []) == "legacy"


def test_invalid_backend_does_not_fall_back(monkeypatch):
    monkeypatch.setenv("GHOST_INFERENCE_BACKEND", "typo")
    with pytest.raises(ValueError, match="Unknown"):
        chat.chat("model", [])


def test_explicit_non_ollama_endpoint(monkeypatch):
    monkeypatch.setenv("GHOST_OPENJARVIS_ENGINE", "lmstudio")
    monkeypatch.delenv("GHOST_OPENJARVIS_URL", raising=False)
    with pytest.raises(IntegrationError, match="explicitly"):
        EngineSettings.from_env()
    monkeypatch.setenv("GHOST_OPENJARVIS_URL", "http://127.0.0.1:1234/v1")
    assert EngineSettings.from_env().endpoint == "http://127.0.0.1:1234"


def test_queued_route_cannot_silently_switch_provider(tmp_path, monkeypatch):
    from ghost_agent.autonomy import OutcomeStore
    from ghost_agent.cli import _execute_queued_outcome
    from ghost_agent.runtime import ScheduledJob
    store = OutcomeStore(tmp_path)
    job = store.create("Some coding work")
    job.acceptance_contract["inference_route"] = {"backend": "openjarvis", "engine": "ollama", "endpoint": "http://127.0.0.1:11434", "allow_remote": False}
    store.save(job)
    monkeypatch.setenv("GHOST_INFERENCE_BACKEND", "legacy")
    result = _execute_queued_outcome(tmp_path, ScheduledJob(job.job_id, ""))
    assert result.status == "failed"
    assert "no provider was contacted" in result.detail


def test_version_drift_is_not_silently_accepted(monkeypatch):
    from ghost_agent.openjarvis_bridge import runtime
    monkeypatch.setattr(runtime.sys, "version_info", (3, 13))
    monkeypatch.setattr(runtime.importlib.metadata, "version", lambda _: "9.0.0")
    with pytest.raises(IntegrationError, match="Unqualified"):
        runtime.require_upstream()
