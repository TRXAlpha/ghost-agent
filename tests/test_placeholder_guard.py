"""T09: Reject placeholder file content from the model.

Tests Orchestrator._placeholder_content_error and the end-to-end repair path.
"""
from __future__ import annotations

import json

import pytest

import ghost_agent.orchestrator as module
from ghost_agent.orchestrator import Orchestrator


# ── Unit tests for _placeholder_content_error ────────────────────────────────────

def _err(path: str, content: str) -> str | None:
    return Orchestrator._placeholder_content_error(path, content)


class TestPlaceholderContentError:
    # Rejected cases (Rule 1 – Python source, no code)
    def test_rule1_python_hash_only(self):
        assert _err("hello.py", "# Updated content") == (
            "write_file content for hello.py has no code (only comments/pass/...); "
            "write the real implementation"
        )

    def test_rule1_python_pass(self):
        assert _err("hello.py", "pass") == (
            "write_file content for hello.py has no code (only comments/pass/...); "
            "write the real implementation"
        )

    def test_rule1_python_comments_ellipsis(self):
        assert _err("hello.py", "# TODO\n\n...\n") == (
            "write_file content for hello.py has no code (only comments/pass/...); "
            "write the real implementation"
        )

    # Rejected cases (Rule 2 – known placeholder phrases)
    def test_rule2_txt_placeholder_todo(self):
        assert _err("notes.txt", "TODO") == (
            "write_file content for notes.txt is a placeholder ('TODO'); "
            "write the complete file content"
        )

    def test_rule2_js_placeholder_comment(self):
        assert _err("app.js", "// your code here") == (
            "write_file content for app.js is a placeholder ('// your code here'); "
            "write the complete file content"
        )

    def test_rule2_trailing_period(self):
        # Rule 1 runs first for .py files; "# Updated content." has no
        # meaningful line, so the Rule 1 "no code" message is returned.
        # The task only requires: rejected (returns a string).
        result = _err("hello.py", "# Updated content.")
        assert isinstance(result, str)
        assert "hello.py" in result

    def test_rule2_test_file_still_caught(self):
        # Rule 2 applies to test files (Rule 1 does not apply to tests, but Rule 2 does)
        assert _err("test_app.py", "# your code here") == (
            "write_file content for test_app.py is a placeholder ('# your code here'); "
            "write the complete file content"
        )

    def test_docstring_only_module_is_a_known_gap(self):
        # Known limitation: a docstring-only module passes Rule 1 because
        # "Docstring only" is a non-comment, non-pass line.
        assert _err('pkg/mod.py', '"""\nDocstring only\n"""') is None

    # Accepted cases
    def test_accepted_real_python(self):
        assert _err("hello.py", "def add(a, b):\n    return a + b\n") is None

    def test_accepted_init_empty(self):
        assert _err("pkg/__init__.py", "") is None

    def test_accepted_readme_md_placeholder(self):
        assert _err("README.md", "TODO") is None

    def test_accepted_plan_md_placeholder(self):
        assert _err("plan.md", "placeholder") is None

    def test_accepted_gitkeep_empty(self):
        assert _err(".gitkeep", "") is None

    def test_accepted_json(self):
        assert _err("config.json", "{}") is None


# ── End-to-end test ─────────────────────────────────────────────────────────────

def test_placeholder_write_triggers_repair_feedback(tmp_path, monkeypatch):
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps({
        "id": "placeholder-task",
        "title": "Placeholder guard e2e",
        "goal": "Implement a simple module with tests",
        "context": {},
        "constraints": {"iteration_limit": 4},
    }))

    call_count = 0
    captured_messages: list = []

    def model(_model, messages, **kwargs):
        nonlocal call_count
        captured_messages.append(messages)
        planning = "plan" in messages[-1]["content"].lower() or call_count == 0
        call_count += 1

        if planning:
            actions = [{"tool": "write_file", "path": "plan.md",
                        "content": "Implement the module"}]
        elif call_count == 2:
            # First implementation call: write placeholder content
            actions = [
                {"tool": "write_file", "path": "app.py", "content": "# Updated content"},
                {"tool": "write_file", "path": "test_app.py",
                 "content": "from app import add\ndef test_add():\n    assert add(1, 2) == 3\n"},
            ]
        else:
            # Subsequent calls: write real content
            actions = [
                {"tool": "write_file", "path": "app.py", "content": "def add(a, b):\n    return a + b\n"},
                {"tool": "write_file", "path": "test_app.py",
                 "content": "from app import add\ndef test_add():\n    assert add(1, 2) == 3\n"},
            ]
        return json.dumps({"thought": "done", "actions": actions})

    monkeypatch.setattr(module, "ollama_chat", model)
    agent = Orchestrator(tmp_path, model="scripted")
    agent.run_task(task_path)

    state_path = tmp_path / "workspaces" / "placeholder-task" / "state.json"
    state = json.loads(state_path.read_text())

    # Some message should contain the "no code" feedback from the guard
    any_has_no_code = any(
        "has no code" in str(m) for m in captured_messages
    )
    assert any_has_no_code, (
        f"'has no code' not found in any captured message. "
        f"Messages:\n{json.dumps(captured_messages, indent=2)}"
    )

    # Final result should be tests passed
    assert state["last_result"] == "tests_passed", (
        f"Expected tests_passed, got {state.get('last_result')}"
    )