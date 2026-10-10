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
