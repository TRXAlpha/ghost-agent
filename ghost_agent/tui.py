"""Terminal UI for ghost-agent — progress bars, status line, action stream.

Renders a live-updating terminal interface during ghost run / ghost interactive.
Degrades gracefully to plain output when stdout is not a TTY.
"""
from __future__ import annotations

import sys
import time
from typing import Any, Dict, List, Optional

from .pet import GhostPet, PetState, phase_to_pet_state, result_to_pet_state

# ── ANSI helpers ─────────────────────────────────────────────────────────────

_IS_TTY = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

# If not a TTY, all color/formatting codes become empty strings
_RESET = "\033[0m" if _IS_TTY else ""
_BOLD = "\033[1m" if _IS_TTY else ""
_DIM = "\033[2m" if _IS_TTY else ""
_UNDERLINE = "\033[4m" if _IS_TTY else ""
_GREEN = "\033[32m" if _IS_TTY else ""
_RED = "\033[31m" if _IS_TTY else ""
_YELLOW = "\033[33m" if _IS_TTY else ""
_BLUE = "\033[34m" if _IS_TTY else ""
_CYAN = "\033[36m" if _IS_TTY else ""
_MAGENTA = "\033[35m" if _IS_TTY else ""
_BRIGHT_GREEN = "\033[92m" if _IS_TTY else ""
_BRIGHT_RED = "\033[91m" if _IS_TTY else ""
_BRIGHT_YELLOW = "\033[93m" if _IS_TTY else ""
_BRIGHT_CYAN = "\033[96m" if _IS_TTY else ""
_BRIGHT_MAGENTA = "\033[95m" if _IS_TTY else ""
_GRAY = "\033[90m" if _IS_TTY else ""

# Cursor control
_CLEAR_LINE = "\033[2K" if _IS_TTY else ""
_HOME = "\033[H" if _IS_TTY else ""
_HIDE_CURSOR = "\033[?25l" if _IS_TTY else ""
_SHOW_CURSOR = "\033[?25h" if _IS_TTY else ""


# ── Phase progress bar ──────────────────────────────────────────────────────

PHASES = ["INGEST", "PLAN", "IMPLEMENT", "VERIFY", "REPAIR", "DONE"]
PHASE_ICONS = {
    "INGEST": "📥",
    "PLAN": "📋",
    "IMPLEMENT": "🔨",
    "VERIFY": "✅",
    "REPAIR": "🔧",
    "DONE": "🏁",
}

# Phase index mapping
PHASE_INDEX = {p: i for i, p in enumerate(PHASES)}


def render_progress_bar(
    current_phase: str, width: int = 50, compact: bool = False
) -> str:
    """Render a colored phase progress bar."""
    current_idx = PHASE_INDEX.get(current_phase, 0)

    if compact:
        parts = []
        for i, phase in enumerate(PHASES):
            icon = PHASE_ICONS.get(phase, "?")
            if i < current_idx:
                parts.append(f"{_GREEN}{icon}{_RESET}")
            elif i == current_idx:
                parts.append(f"{_BRIGHT_CYAN}{_BOLD}{icon}{_RESET}")
            else:
                parts.append(f"{_GRAY}{icon}{_RESET}")
        return " → ".join(parts)

    # Full progress bar
    bar_parts = []
    for i, phase in enumerate(PHASES):
        icon = PHASE_ICONS.get(phase, "?")
        label = phase[:4]
        if i < current_idx:
            bar_parts.append(f"{_GREEN}[✓ {label}]{_RESET}")
        elif i == current_idx:
            bar_parts.append(
                f"{_BRIGHT_CYAN}{_BOLD}[▶ {label}]{_RESET}"
            )
        else:
            bar_parts.append(f"{_GRAY}[  {label}]{_RESET}")

    return " → ".join(bar_parts)


def render_status_line(
    iteration: int,
    max_iterations: int,
    phase: str,
    files_touched: List[str],
    elapsed: float,
    pet: Optional[GhostPet] = None,
) -> str:
    """Render the status line with metrics and pet."""
    elapsed_str = _format_elapsed(elapsed)
    iter_color = _BRIGHT_GREEN if iteration < max_iterations else _BRIGHT_RED

    status_parts = [
        f"{_DIM}Iter:{_RESET} {iter_color}{iteration}/{max_iterations}{_RESET}",
        f"{_DIM}Phase:{_RESET} {_BRIGHT_CYAN}{phase}{_RESET}",
        f"{_DIM}Files:{_RESET} {len(files_touched)}",
        f"{_DIM}Time:{_RESET} {elapsed_str}",
    ]

    if pet:
        status_parts.append(pet.render_compact())

    return "  ".join(status_parts)


def render_action_line(
    tool: str,
    tool_input: Dict[str, Any],
    result: Any,
    index: int,
) -> str:
    """Render a single tool call as a colored line."""
    # Tool icons
    icons = {
        "write_file": "📝",
        "read_file": "📖",
        "list_dir": "📂",
        "search_in_files": "🔍",
        "run_cmd": "⚡",
        "web_search": "🌐",
    }
    icon = icons.get(tool, "•")

    # Input summary
    if tool == "write_file":
        path = tool_input.get("path", "?")
        content = tool_input.get("content", "")
        lines = content.count("\n") + 1 if content else 0
        summary = f"{path} ({lines}L)"
    elif tool == "read_file":
        summary = tool_input.get("path", "?")
    elif tool == "list_dir":
        summary = tool_input.get("path", ".")
    elif tool == "search_in_files":
        summary = f"{tool_input.get('path', '?')} \"{tool_input.get('query', '?')}\""
    elif tool == "run_cmd":
        summary = tool_input.get("cmd", "?")
    elif tool == "web_search":
        summary = tool_input.get("query", "?")
    else:
        summary = str(tool_input)[:40]

    # Result status
    has_error = isinstance(result, dict) and "error" in result
    if has_error:
        status = f"{_BRIGHT_RED}✗{_RESET}"
        error_msg = result.get("error", "")[:60]
        return f"  {_DIM}{index:>3}.{_RESET} {icon} {tool} {_DIM}{summary}{_RESET}  {status} {_RED}{error_msg}{_RESET}"

    if tool == "run_cmd" and isinstance(result, dict):
        rc = result.get("returncode", -1)
        rc_color = _GREEN if rc == 0 else _RED
        status = f"{rc_color}exit {rc}{_RESET}"
        output_preview = ""
        output = result.get("output", "")
        if output:
            first_line = output.strip().split("\n")[0][:60]
            output_preview = f" {_DIM}{first_line}{_RESET}"
        return f"  {_DIM}{index:>3}.{_RESET} {icon} {tool} {_DIM}{summary}{_RESET}  {status}{output_preview}"

    return f"  {_DIM}{index:>3}.{_RESET} {icon} {tool} {_DIM}{summary}{_RESET}  {_GREEN}✓{_RESET}"


def render_llm_thought(thought: str, valid: bool = True) -> str:
    """Render an LLM thought/response indicator."""
    if valid:
        return f"  {_DIM}💭{_RESET} {_DIM}{thought[:80]}{_RESET}"
    else:
        return f"  {_DIM}⚠{_RESET} {_YELLOW}{thought[:80]}{_RESET}"


# ── Full TUI widget ─────────────────────────────────────────────────────────


class TUI:
    """Live-updating terminal UI for ghost-agent runs."""

    def __init__(self, task_id: str = "", verbose: bool = False) -> None:
        self.task_id = task_id
        self.verbose = verbose
        self.pet = GhostPet()
        self.start_time = time.time()
        self.iteration = 0
        self.max_iterations = 8
        self.phase = "INGEST"
        self.files_touched: List[str] = []
        self.action_count = 0
        self.error_count = 0
        self.messages: List[str] = []
        self._initialized = False

    def start(self, task_id: str = "", max_iterations: int = 8) -> None:
        """Initialize and show the TUI header."""
        self.task_id = task_id or self.task_id
        self.max_iterations = max_iterations
        self.start_time = time.time()
        self.pet.set_state(PetState.WELCOME)
        self._initialized = True

        if not _IS_TTY:
            self._print_plain(f"Ghost agent starting: {self.task_id}")
            return

        self._clear_screen()
        self._render_full()

    def _clear_screen(self) -> None:
        """Clear screen and move cursor to top."""
        if _IS_TTY:
            sys.stdout.write(f"{_HOME}{_CLEAR_LINE}")
            sys.stdout.flush()

    def _render_full(self) -> None:
        """Render the complete TUI layout."""
        if not _IS_TTY:
            return

        elapsed = time.time() - self.start_time
        lines = []

        # Header
        lines.append(f"{_BOLD}{_BRIGHT_CYAN}  👻 Ghost Agent{_RESET}  {_DIM}{self.task_id}{_RESET}")
        lines.append("")

        # Progress bar
        lines.append(f"  {render_progress_bar(self.phase)}")
        lines.append("")

        # Status line
        lines.append(
            f"  {render_status_line(
                self.iteration, self.max_iterations,
                self.phase, self.files_touched, elapsed,
                self.pet,
            )}"
        )
        lines.append("")

        # Action log (last 8 entries)
        if self.messages:
            lines.append(f"  {_DIM}{'─' * 60}{_RESET}")
            for msg in self.messages[-8:]:
                lines.append(msg)
            lines.append("")

        # Pet
        lines.append(f"  {self.pet.render_inline()}")

        # Write to terminal
        output = "\n".join(lines)
        sys.stdout.write(f"{_HOME}{_CLEAR_LINE}" + output + "\n")
        sys.stdout.flush()

    def on_phase_change(self, phase: str, iteration: int) -> None:
        """Called when the orchestrator enters a new phase."""
        self.phase = phase
        self.iteration = iteration
        self.pet.set_state(phase_to_pet_state(phase))
        self._render_full()

    def on_action(
        self, tool: str, tool_input: Dict[str, Any], result: Any
    ) -> None:
        """Called after a tool call completes."""
        self.action_count += 1
        if isinstance(result, dict) and "error" in result:
            self.error_count += 1

        line = render_action_line(tool, tool_input, result, self.action_count)
        self.messages.append(line)
        self._render_full()

    def on_llm_response(self, thought: str, valid: bool) -> None:
        """Called when the LLM produces a response."""
        line = render_llm_thought(thought, valid)
        self.messages.append(line)
        self._render_full()

    def on_file_touched(self, path: str) -> None:
        """Called when a file is modified."""
        if path not in self.files_touched:
            self.files_touched.append(path)

    def on_complete(self, result: str) -> None:
        """Called when the run finishes."""
        pet_state = result_to_pet_state(result)
        if pet_state:
            self.pet.set_state(pet_state)
        else:
            self.pet.set_state(PetState.SUCCESS)
        self._render_full()

    def finish(self) -> None:
        """Show the final state and clean up."""
        if _IS_TTY:
            sys.stdout.write(_SHOW_CURSOR)
            sys.stdout.flush()

    def _print_plain(self, text: str) -> None:
        """Fallback for non-TTY output."""
        print(text)


# ── Plain mode (no TTY) ─────────────────────────────────────────────────────


class PlainTUI:
    """Minimal TUI for non-TTY environments. Just prints messages."""

    def __init__(self, task_id: str = "", verbose: bool = False) -> None:
        self.task_id = task_id
        self.verbose = verbose
        self.pet = GhostPet()
        self.start_time = time.time()
        self.iteration = 0
        self.max_iterations = 8
        self.phase = "INGEST"
        self.files_touched: List[str] = []
        self.action_count = 0
        self.error_count = 0

    def start(self, task_id: str = "", max_iterations: int = 8) -> None:
        self.task_id = task_id or self.task_id
        self.max_iterations = max_iterations
        self.start_time = time.time()
        print(f"[ghost] Starting task: {self.task_id}")

    def on_phase_change(self, phase: str, iteration: int) -> None:
        self.phase = phase
        self.iteration = iteration
        print(f"[ghost] Phase: {phase} (iteration {iteration}/{self.max_iterations})")

    def on_action(self, tool: str, tool_input: Dict[str, Any], result: Any) -> None:
        self.action_count += 1
        status = "ok" if not (isinstance(result, dict) and "error" in result) else "ERROR"
        print(f"[ghost] {tool} -> {status}")

    def on_llm_response(self, thought: str, valid: bool) -> None:
        if self.verbose:
            print(f"[ghost] LLM: {thought[:80]}...")

    def on_file_touched(self, path: str) -> None:
        if path not in self.files_touched:
            self.files_touched.append(path)

    def on_complete(self, result: str) -> None:
        print(f"[ghost] Completed: {result}")

    def finish(self) -> None:
        pass


def create_tui(task_id: str = "", verbose: bool = False) -> TUI | PlainTUI:
    """Factory that returns the appropriate TUI based on terminal capabilities."""
    if _IS_TTY:
        return TUI(task_id=task_id, verbose=verbose)
    return PlainTUI(task_id=task_id, verbose=verbose)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _format_elapsed(seconds: float) -> str:
    """Format elapsed seconds as M:SS."""
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins}:{secs:02d}"
