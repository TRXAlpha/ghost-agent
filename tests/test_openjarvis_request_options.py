"""Ollama-only resource hints must not break OpenAI-compatible engines."""
import pytest

pytest.importorskip("openjarvis.core.types")

from ghost_agent.openjarvis_bridge.runtime import _request  # noqa: E402

MESSAGES = [{"role": "user", "content": "hi"}]


def test_num_ctx_dropped_for_openai_compatible_engine() -> None:
    _, kwargs = _request(MESSAGES, {"num_predict": 64, "num_ctx": 8192, "num_gpu": 1}, None, "openai-compat")
    assert "num_ctx" not in kwargs and "num_gpu" not in kwargs
    assert kwargs["max_tokens"] == 64


def test_num_ctx_kept_for_ollama_engine() -> None:
    _, kwargs = _request(MESSAGES, {"num_predict": 64, "num_ctx": 8192}, None, "ollama")
    assert kwargs["num_ctx"] == 8192


def test_engine_5xx_and_429_are_transient_connection_errors() -> None:
    from openjarvis.engine._base import EngineConnectionError
    from ghost_agent.openjarvis_bridge.runtime import UpstreamTransportError, _classify_engine_error
    for status in (502, 503, 429):
        err = _classify_engine_error(EngineConnectionError(f"openai-compat engine at https://x returned HTTP {status}: boom"))
        assert isinstance(err, UpstreamTransportError) and isinstance(err, ConnectionError)
    unreachable = _classify_engine_error(EngineConnectionError("openai-compat engine not reachable at https://x"))
    assert isinstance(unreachable, UpstreamTransportError)


def test_engine_4xx_and_context_length_are_configuration_errors() -> None:
    from openjarvis.engine._base import EngineConnectionError, EngineContextLengthError
    from ghost_agent.openjarvis_bridge.runtime import IntegrationError, _classify_engine_error
    assert isinstance(_classify_engine_error(EngineConnectionError("engine at x returned HTTP 401: no")), IntegrationError)
    assert isinstance(_classify_engine_error(EngineContextLengthError("too long")), IntegrationError)


def test_models_chat_retries_upstream_transport_error() -> None:
    from ghost_agent.models.chat import _retryable
    from ghost_agent.openjarvis_bridge.runtime import UpstreamTransportError
    assert _retryable(UpstreamTransportError("returned HTTP 502"))
