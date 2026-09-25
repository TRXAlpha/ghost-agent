import json

import pytest

from ghost_agent import orchestrator as module


class Log:
    def __init__(self):
        self.records = []

    def log(self, record):
        self.records.append(record)


def runner(limit):
    # Exercise the actual request boundary without initializing repository RAG.
    value = object.__new__(module.Orchestrator)
    value.model = "scripted"
    value.base_url = None
    value.timeout = 1
    value.tui = None
    value.verbose = False
    value.max_input_chars = limit
    return value


def test_output_token_limit_is_sent_to_backend(monkeypatch):
    captured = []
    monkeypatch.setattr(module, "ollama_chat", lambda *args, **kwargs:
                        captured.append(kwargs) or '{"actions":[]}')
    value = runner(500)
    value.max_output_tokens = 512
    log = Log()
    value._model_turn([], "Implement", log, "")
    assert captured[0]["options"] == {"num_predict": 512}
    assert log.records[0]["max_output_tokens"] == 512


@pytest.mark.parametrize("limit", [0, True, 8193, float("inf")])
def test_invalid_output_token_limit_is_rejected(tmp_path, limit):
    with pytest.raises(ValueError, match="max_output_tokens"):
        module.Orchestrator(tmp_path, max_output_tokens=limit)


def test_complete_messages_fit_by_omitting_optional_retrieval(monkeypatch):
    captured = []
    monkeypatch.setattr(module, "ollama_chat", lambda model, messages, **kwargs: captured.append(messages) or '{"actions":[]}')
    messages = [{"role": "system", "content": "Never send messages"},
                {"role": "user", "content": "Task and constraints"},
                {"role": "user", "content": "Optional memory " * 1000}]
    original = json.dumps(messages)
    log = Log()
    runner(500)._model_turn(messages, "Repair the failed test", log, "Assertion failed")
    assert len(json.dumps(captured[0], ensure_ascii=False)) <= 500
    assert captured[0][:2] == messages[:2]
    assert captured[0][-2]["content"] == "Last feedback:\nAssertion failed"
    assert captured[0][-1]["content"] == "Repair the failed test"
    assert log.records[0]["omitted_optional_messages"] == 1
    assert json.dumps(messages) == original


@pytest.mark.parametrize("part", ["system", "task", "prompt", "feedback", "extra_system"])
def test_required_input_is_never_truncated_or_sent_over_budget(monkeypatch, part):
    monkeypatch.setattr(module, "ollama_chat", lambda *args, **kwargs: pytest.fail("Oversized input reached model"))
    messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "task"}]
    prompt, feedback = "implement", "failure"
    large = "x" * 1000
    if part == "system":
        messages[0]["content"] = large
    elif part == "task":
        messages[1]["content"] = large
    elif part == "prompt":
        prompt = large
    elif part == "feedback":
        feedback = large
    else:
        messages.append({"role": "system", "content": large})
    with pytest.raises(ValueError, match="input budget"):
        runner(500)._model_turn(messages, prompt, Log(), feedback)


@pytest.mark.parametrize("limit", [0, -1, True, "24000"])
def test_invalid_input_budget_fails_before_initialization(tmp_path, limit):
    with pytest.raises(ValueError, match="max_input_chars"):
        module.Orchestrator(tmp_path, max_input_chars=limit)
