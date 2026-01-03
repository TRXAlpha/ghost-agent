import pytest

from ghost_agent.schema import parse_action_response


def test_parse_action_response_invalid_json() -> None:
    with pytest.raises(ValueError):
        parse_action_response("{not-json}")


def test_parse_action_response_invalid_action() -> None:
    bad = '{"thought": "x", "actions": [{"tool": "write_file"}]}'
    with pytest.raises(ValueError):
        parse_action_response(bad)


def test_parse_action_response_rejects_extra_text() -> None:
    bad = 'Here is the response: {"thought": "x", "actions": []}'
    with pytest.raises(ValueError):
        parse_action_response(bad)
