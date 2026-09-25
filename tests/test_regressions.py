from pathlib import Path

from ghost_agent import cli
from ghost_agent.audit import audit_all
from ghost_agent.orchestrator import Orchestrator
from ghost_agent.schema import ActionResponse
from ghost_agent.utils.logging import JsonlLogger


def test_intent_classifier_does_not_match_search_inside_research(monkeypatch) -> None:
    monkeypatch.setattr(cli, "ollama_chat", lambda **kwargs: "research")

    assert cli._classify_intent("Tell me everything about compilers", "model") == "research"


def test_deduplicated_claim_sources_reach_verifier(monkeypatch) -> None:
    captured = {}

    def fake_chat(**kwargs):
        captured["messages"] = kwargs["messages"]
        return "[]"

    monkeypatch.setattr(cli, "ollama_chat", fake_chat)
    cli._verify_claims(
        [{
            "claim": "A claim",
            "source_indices": [1, 2],
            "source_quotes": ["first", "second"],
        }],
        [{"title": "One", "snippet": "first"}, {"title": "Two", "snippet": "second"}],
        "model",
    )

    prompt = captured["messages"][-1]["content"]
    assert "[1, 2]" in prompt
    assert "['first', 'second']" in prompt


def test_execute_actions_notifies_tui(tmp_path: Path) -> None:
    events = []

    class Callback:
        def on_action(self, tool, tool_input, result) -> None:
            events.append((tool, tool_input, result))

    orchestrator = Orchestrator(tmp_path, tui=Callback())
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    response = ActionResponse.model_validate({
        "thought": "inspect",
        "actions": [{"tool": "list_dir", "path": "."}],
    })
    results, touched = orchestrator._execute_actions(
        response, sandbox, JsonlLogger(tmp_path / "actions.log")
    )

    assert results == [[]]
    assert touched == []
    assert events[0][0] == "list_dir"
    assert events[0][1]["path"] == "."


def test_nonzero_command_result_is_an_error() -> None:
    orchestrator = object.__new__(Orchestrator)

    assert orchestrator._has_errors([{"returncode": 1, "output": "failed"}])
    assert not orchestrator._has_errors([{"returncode": 0, "output": "ok"}])


def test_absolute_action_paths_are_rejected_even_inside_workspace() -> None:
    orchestrator = object.__new__(Orchestrator)
    action = ActionResponse.model_validate({
        "thought": "write", "actions": [{
            "tool": "write_file", "path": "C:\\sandbox\\file.py", "content": "x = 1"
        }],
    }).actions[0]

    assert "must be relative" in orchestrator._validate_action_inputs(action)


def test_audit_all_handles_missing_workspace_directory(tmp_path: Path, capsys) -> None:
    audit_all(tmp_path / "missing")

    assert "No runs found" in capsys.readouterr().out
