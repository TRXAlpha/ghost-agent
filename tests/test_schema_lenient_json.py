"""Small models put raw newlines inside JSON strings; the parser must accept them."""
from ghost_agent.schema import parse_action_response


def test_literal_newlines_inside_content_string_parse() -> None:
    raw = '{"thought": "write it", "actions": [{"tool": "write_file", "path": "app.py", "content": "def f():\n\treturn 1\n"}]}'
    response = parse_action_response(raw)
    assert response.actions[0].content == "def f():\n\treturn 1\n"


def test_strict_valid_json_still_parses() -> None:
    raw = '{"thought": "t", "actions": [{"tool": "write_file", "path": "a.py", "content": "x = 1\n"}]}'
    assert parse_action_response(raw).actions[0].content == "x = 1\n"
