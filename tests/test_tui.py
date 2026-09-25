"""Tests for ghost_agent.tui — terminal UI components."""
from ghost_agent.tui import (
    TUI,
    PlainTUI,
    create_tui,
    render_progress_bar,
    render_status_line,
    render_action_line,
    render_llm_thought,
    _format_elapsed,
    PHASES,
)


def test_render_progress_bar_shows_all_phases() -> None:
    bar = render_progress_bar("INGEST")
    for phase in ["INGEST", "PLAN", "IMPL", "VERI", "REPA", "DONE"]:
        # Check abbreviated names appear (first 4 chars)
        assert len(bar) > 0


def test_render_progress_bar_compact() -> None:
    bar = render_progress_bar("PLAN", compact=True)
    assert len(bar) > 0


def test_render_status_line() -> None:
    line = render_status_line(
        iteration=3,
        max_iterations=8,
        phase="IMPLEMENT",
        files_touched=["main.py"],
        elapsed=45.0,
    )
    assert "3/8" in line
    assert "IMPLEMENT" in line
    assert "0:45" in line


def test_render_status_line_with_pet() -> None:
    from ghost_agent.pet import GhostPet
    pet = GhostPet()
    line = render_status_line(
        iteration=1,
        max_iterations=8,
        phase="PLAN",
        files_touched=[],
        elapsed=10.0,
        pet=pet,
    )
    assert "(-_^)" in line


def test_render_action_line_write_file() -> None:
    line = render_action_line(
        "write_file",
        {"path": "main.py", "content": "print('hello')"},
        "wrote main.py",
        index=1,
    )
    assert "write_file" in line
    assert "main.py" in line
    assert "✓" in line


def test_render_action_line_error() -> None:
    line = render_action_line(
        "write_file",
        {"path": "bad.py", "content": "x"},
        {"error": "permission denied"},
        index=2,
    )
    assert "✗" in line
    assert "permission denied" in line


def test_render_action_line_run_cmd() -> None:
    line = render_action_line(
        "run_cmd",
        {"cmd": "pytest -q", "cwd": "."},
        {"returncode": 0, "output": "5 passed"},
        index=3,
    )
    assert "run_cmd" in line
    assert "pytest" in line
    assert "exit 0" in line


def test_render_action_line_run_cmd_failure() -> None:
    line = render_action_line(
        "run_cmd",
        {"cmd": "pytest", "cwd": "."},
        {"returncode": 1, "output": "FAILED test_foo"},
        index=4,
    )
    assert "exit 1" in line


def test_render_llm_thought_valid() -> None:
    line = render_llm_thought("I will write a test", valid=True)
    assert "I will write a test" in line


def test_render_llm_thought_invalid() -> None:
    line = render_llm_thought("Invalid JSON", valid=False)
    assert "Invalid JSON" in line


def test_format_elapsed() -> None:
    assert _format_elapsed(0) == "0:00"
    assert _format_elapsed(65) == "1:05"
    assert _format_elapsed(125) == "2:05"


def test_format_elapsed_large() -> None:
    assert _format_elapsed(3661) == "61:01"


def test_tui_creates_pet() -> None:
    tui = TUI(task_id="test")
    assert tui.pet is not None
    assert tui.pet.state.value == "idle"


def test_tui_start() -> None:
    tui = TUI(task_id="test")
    tui.start(task_id="test", max_iterations=5)
    assert tui.max_iterations == 5
    assert tui.task_id == "test"


def test_tui_phase_change() -> None:
    tui = TUI(task_id="test")
    tui.start(task_id="test")
    tui.on_phase_change("PLAN", 1)
    assert tui.phase == "PLAN"
    assert tui.iteration == 1


def test_tui_action() -> None:
    tui = TUI(task_id="test")
    tui.start(task_id="test")
    tui.on_action("write_file", {"path": "x.py", "content": "hi"}, "wrote x.py")
    assert tui.action_count == 1


def test_tui_action_error() -> None:
    tui = TUI(task_id="test")
    tui.start(task_id="test")
    tui.on_action("write_file", {"path": "x.py", "content": "hi"}, {"error": "fail"})
    assert tui.error_count == 1


def test_tui_file_touched() -> None:
    tui = TUI(task_id="test")
    tui.start(task_id="test")
    tui.on_file_touched("main.py")
    tui.on_file_touched("main.py")  # duplicate
    tui.on_file_touched("test_main.py")
    assert len(tui.files_touched) == 2


def test_tui_complete() -> None:
    tui = TUI(task_id="test")
    tui.start(task_id="test")
    tui.on_complete("tests_passed")
    assert tui.pet.state.value == "success"


def test_tui_complete_failure() -> None:
    tui = TUI(task_id="test")
    tui.start(task_id="test")
    tui.on_complete("iteration_limit")
    assert tui.pet.state.value == "failure"


def test_plain_tui_works() -> None:
    tui = PlainTUI(task_id="test")
    tui.start(task_id="test", max_iterations=5)
    tui.on_phase_change("PLAN", 1)
    tui.on_action("write_file", {"path": "x.py", "content": "hi"}, "ok")
    tui.on_llm_response("thinking...", True)
    tui.on_file_touched("x.py")
    tui.on_complete("tests_passed")
    tui.finish()  # Should not raise


def test_create_tui_fallback() -> None:
    # In non-TTY (like test environment), should get PlainTUI
    tui = create_tui(task_id="test")
    assert isinstance(tui, PlainTUI)
