import pytest

from ghost_agent.schema import parse_action_response


# ── Multi-strategy parsing tests ────────────────────────────────────────────


def test_parse_action_response_invalid_json() -> None:
    with pytest.raises(ValueError):
        parse_action_response("{not-json}")


def test_parse_action_response_invalid_action() -> None:
    bad = '{"thought": "x", "actions": [{"tool": "write_file"}]}'
    with pytest.raises(ValueError):
        parse_action_response(bad)


def test_parse_action_response_extracts_json_from_prose() -> None:
    """Local models often wrap JSON in prose. Our parser extracts it."""
    wrapped = 'Here is the response: {"thought": "x", "actions": []}'
    result = parse_action_response(wrapped)
    assert result.thought == "x"
    assert result.actions == []


def test_parse_strips_markdown_fences() -> None:
    """The #1 failure mode: models wrap output in ```json fences."""
    fenced = '{"thought": "ok", "actions": []}'
    result = parse_action_response(fenced)
    assert result.thought == "ok"


def test_parse_strips_fences_with_language_tag() -> None:
    fenced = '{"thought": "ok", "actions": []}'
    result = parse_action_response(fenced)
    assert result.thought == "ok"


def test_parse_extracts_json_from_mixed_output() -> None:
    """Model emits prose before and after the JSON object."""
    json_part = '{"thought": "writing file", "actions": [{"tool": "write_file", "path": "a.py", "content": "x"}]}'
    mixed = "I will now implement the solution.\n" + json_part + "\nDone!"
    result = parse_action_response(mixed)
    assert result.thought == "writing file"
    assert len(result.actions) == 1


def test_parse_empty_response_raises() -> None:
    with pytest.raises(ValueError):
        parse_action_response("")


def test_parse_handles_tool_calls_key() -> None:
    """Some models use 'tool_calls' instead of 'actions'."""
    data = '{"thought": "plan", "tool_calls": []}'
    result = parse_action_response(data)
    assert result.thought == "plan"


def test_parse_recovers_unescaped_windows_path_from_local_model() -> None:
    data = '{"thought": "read", "actions": [{"tool": "read_file", "path": "src\\app.py"}]}'

    result = parse_action_response(data)

    assert result.actions[0].path == "src\\app.py"


def test_parse_recovers_triple_quoted_multiline_file_content() -> None:
    data = '''{"thought": "write", "actions": [{"tool": "write_file", "path": "a.py", "content": """def hello():
    print("hi")
"""}]}'''

    result = parse_action_response(data)

    assert result.actions[0].content == 'def hello():\n    print("hi")\n'


# ── Raw file blocks (content_ref) ───────────────────────────────────────────


def test_parse_file_block_round_trips_hard_characters() -> None:
    body = 'x = "quoted"\ny = 1 \\\\ 2\ndata = {"k": [1, 2]}\n'
    text = '{"thought": "w", "actions": [{"tool": "write_file", "path": "a.py", "content_ref": "F1"}]}\n<<<F1\n' + body + "\nF1>>>"

    result = parse_action_response(text)

    assert result.actions[0].content == body


def test_parse_multiple_file_blocks() -> None:
    text = (
        '{"thought": "w", "actions": ['
        '{"tool": "write_file", "path": "a.py", "content_ref": "F1"},'
        '{"tool": "write_file", "path": "b.py", "content_ref": "F2"}]}\n'
        "<<<F1\nfirst = True\nF1>>>\n"
        "<<<F2\nsecond = {\"x\": 1}\nF2>>>"
    )

    result = parse_action_response(text)

    assert result.actions[0].content == "first = True"
    assert result.actions[1].content == 'second = {"x": 1}'


def test_parse_file_block_missing_ref_raises() -> None:
    text = '{"thought": "w", "actions": [{"tool": "write_file", "path": "a.py", "content_ref": "F1"}]}'
    with pytest.raises(ValueError, match="content_ref F1 has no matching block"):
        parse_action_response(text)


def test_parse_file_block_with_content_and_ref_raises() -> None:
    text = (
        '{"thought": "w", "actions": ['
        '{"tool": "write_file", "path": "a.py", "content": "x", "content_ref": "F1"}]}\n'
        "<<<F1\nbody\nF1>>>"
    )
    with pytest.raises(ValueError, match="both content and content_ref"):
        parse_action_response(text)


def test_parse_plain_content_form_unchanged() -> None:
    text = '{"thought": "w", "actions": [{"tool": "write_file", "path": "a.py", "content": "print(1)\\n"}]}'

    result = parse_action_response(text)

    assert result.actions[0].content == "print(1)\n"
    assert result.actions[0].content_ref is None


def test_parse_file_block_body_passes_placeholder_guard(tmp_path, monkeypatch) -> None:
    """Resolved block content flows through the T09 placeholder guard."""
    import json
    import ghost_agent.orchestrator as module
    from ghost_agent.orchestrator import Orchestrator

    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps({"id": "example", "title": "Feature",
        "goal": "Implement with tests", "context": {},
        "constraints": {"iteration_limit": 3}}))

    def model(_model, messages, **kwargs):
        planning = "Create the plan now" in messages[-1]["content"]
        if planning:
            return ('{"thought": "plan", "actions": [{"tool": "write_file", '
                    '"path": "plan.md", "content_ref": "F1"}]}\n'
                    "<<<F1\nStep 1: implement\nVerification: run pytest\nF1>>>")
        return ('{"thought": "implement", "actions": ['
                '{"tool": "write_file", "path": "app.py", "content_ref": "F1"},'
                '{"tool": "write_file", "path": "test_app.py", "content_ref": "F2"}]}\n'
                "<<<F1\nanswer = 42\nF1>>>\n"
                "<<<F2\nfrom app import answer\ndef test_answer():\n"
                "    assert answer == 42\nF2>>>")

    monkeypatch.setattr(module, "ollama_chat", model)
    agent = Orchestrator(tmp_path, model="scripted")
    agent.run_task(task_path)
    state = json.loads((tmp_path / "workspaces" / "example" / "state.json").read_text())
    assert state["last_result"] == "tests_passed"
    app = (tmp_path / "workspaces" / "example" / "app.py").read_text()
    assert app == "answer = 42"
