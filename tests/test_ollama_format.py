import json

import pytest
import requests

from ghost_agent.models import ollama


class Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(self.text)

    def iter_lines(self):
        yield json.dumps(self.payload).encode()
        yield b'{"done":true}'


@pytest.mark.parametrize("format_value", [None, "json", {"type": "object"}])
@pytest.mark.parametrize("stream", [False, True])
def test_chat_format_is_forwarded_without_changing_messages(monkeypatch, format_value, stream):
    messages = [{"role": "system", "content": "policy"}, {"role": "user", "content": "task"}]
    def post(url, **kwargs):
        assert url.endswith("/api/chat")
        assert kwargs["json"]["messages"] == messages
        if format_value is None:
            assert "format" not in kwargs["json"]
        else:
            assert kwargs["json"]["format"] == format_value
        return Response({"message": {"content": '{"actions":[]}'}})
    monkeypatch.setattr(ollama.requests, "post", post)
    result = ollama.ollama_chat("test", messages, stream=stream, response_format=format_value)
    assert ("".join(result) if stream else result) == '{"actions":[]}'


def test_generate_fallback_preserves_schema(monkeypatch):
    calls = []
    schema = {"type": "object"}
    def post(url, **kwargs):
        calls.append(kwargs["json"])
        return Response({"error": "endpoint not found"}, 404) if len(calls) == 1 else Response({"response": "{}"})
    monkeypatch.setattr(ollama.requests, "post", post)
    assert ollama.ollama_chat("test", [], response_format=schema) == "{}"
    assert all(call["format"] == schema for call in calls)


def test_stream_connection_fallback_preserves_json_constraint(monkeypatch):
    calls = []
    def post(url, **kwargs):
        calls.append(kwargs["json"].copy())
        if len(calls) == 1:
            raise requests.ConnectionError("connection failed")
        return Response({"message": {"content": "{}"}})
    monkeypatch.setattr(ollama.requests, "post", post)
    assert "".join(ollama.ollama_chat_streaming("test", [], response_format="json")) == "{}"
    assert all(call["format"] == "json" for call in calls)


def test_unsupported_constraint_is_not_silently_removed(monkeypatch):
    calls = []
    def post(url, **kwargs):
        calls.append(url)
        return Response({"error": "unsupported schema"}, 400)
    monkeypatch.setattr(ollama.requests, "post", post)
    with pytest.raises(requests.HTTPError):
        ollama.ollama_chat("test", [], response_format={"type": "object"})
    assert len(calls) == 1


@pytest.mark.parametrize("value", [True, 123, "xml", []])
def test_invalid_format_rejected_before_network(value, monkeypatch):
    monkeypatch.setattr(ollama.requests, "post", lambda *a, **kw: pytest.fail("unexpected network"))
    with pytest.raises(ValueError, match="response_format"):
        ollama.ollama_chat("test", [], response_format=value)
