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
