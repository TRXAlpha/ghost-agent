"""Run replay & transparency report for ghost-agent.

`ghost audit <task_id>` produces a beautiful, colored terminal report
showing exactly what the agent saw, decided, and did during a run.

No other local coding agent shows its work like this.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── ANSI colors ──────────────────────────────────────────────────────────────
# We do NOT use any external dependency for this.  Pure stdlib.


class C:
    """ANSI color helpers.  Disabled when stdout is not a tty."""

    _enabled = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    UNDERLINE = "\033[4m"

    # Foreground
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    GRAY = "\033[90m"

    # Bright foreground
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"

    @classmethod
    def _(cls, code: str, text: str) -> str:
        if not cls._enabled:
            return text
        return f"{code}{text}{cls.RESET}"

    @classmethod
    def bold(cls, text: str) -> str:
        return cls._(cls.BOLD, text)

    @classmethod
    def dim(cls, text: str) -> str:
        return cls._(cls.DIM, text)

    @classmethod
    def red(cls, text: str) -> str:
        return cls._(cls.RED, text)

    @classmethod
    def green(cls, text: str) -> str:
        return cls._(cls.GREEN, text)

    @classmethod
    def yellow(cls, text: str) -> str:
        return cls._(cls.YELLOW, text)

    @classmethod
    def blue(cls, text: str) -> str:
        return cls._(cls.BLUE, text)

    @classmethod
    def magenta(cls, text: str) -> str:
        return cls._(cls.MAGENTA, text)

    @classmethod
    def cyan(cls, text: str) -> str:
        return cls._(cls.CYAN, text)

    @classmethod
    def gray(cls, text: str) -> str:
        return cls._(cls.GRAY, text)

    @classmethod
    def bright_green(cls, text: str) -> str:
        return cls._(cls.BRIGHT_GREEN, text)

    @classmethod
    def bright_yellow(cls, text: str) -> str:
        return cls._(cls.BRIGHT_YELLOW, text)

    @classmethod
    def bright_red(cls, text: str) -> str:
        return cls._(cls.BRIGHT_RED, text)

    @classmethod
    def bright_cyan(cls, text: str) -> str:
        return cls._(cls.BRIGHT_CYAN, text)


# ── Phase visualization ──────────────────────────────────────────────────────

PHASE_ICONS = {
    "INGEST": "📥",
    "PLAN": "📋",
    "IMPLEMENT": "🔨",
    "VERIFY": "✅",
    "REPAIR": "🔧",
    "DONE": "🏁",
}

PHASE_COLORS = {
    "INGEST": C.cyan,
    "PLAN": C.blue,
    "IMPLEMENT": C.green,
    "VERIFY": C.yellow,
    "REPAIR": C.red,
    "DONE": C.bright_green,
}

RESULT_COLORS = {
    "tests_passed": C.bright_green,
    "plan_ok": C.green,
    "implement_ok": C.green,
    "repair_ok": C.green,
    "no_test_cmd": C.yellow,
    "tests_failed": C.bright_red,
    "implement_failed": C.red,
    "repair_failed": C.red,
    "plan_failed": C.red,
    "plan_missing": C.red,
    "iteration_limit": C.bright_yellow,
    "unknown_phase": C.red,
}


def _truncate(text: str, max_len: int = 120) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _indent(text: str, prefix: str = "    ") -> str:
    lines = text.split("\n")
    return "\n".join(prefix + line for line in lines)


def _hr(char: str = "-", width: int = 72) -> str:
    return C.dim(char * width)


# ── Data loading ─────────────────────────────────────────────────────────────


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Load a JSONL file into a list of dicts."""
    entries = []
    if not path.exists():
        return entries
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _load_task(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _load_plan(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


# ── Report rendering ─────────────────────────────────────────────────────────


def _print_header(task: Dict[str, Any], workspace: Path) -> None:
    print()
    print(_hr("═"))
    print(
        C.bold("  👻  GHOST AGENT — RUN AUDIT REPORT")
    )
    print(_hr("═"))
    print()

    task_id = task.get("id", "unknown")
    title = task.get("title", "")
    goal = task.get("goal", "")

    print(f"  {C.bold('Task ID:')}   {C.bright_cyan(task_id)}")
    if title:
        print(f"  {C.bold('Title:')}     {title}")
    if goal:
        print(f"  {C.bold('Goal:')}      {_truncate(goal, 200)}")
    print(f"  {C.bold('Workspace:')} {C.dim(str(workspace))}")
    print()


def _print_task_constraints(task: Dict[str, Any]) -> None:
    constraints = task.get("constraints", {})
    if not constraints:
        return
    print(f"  {C.bold('Constraints:')}")
    for key, value in constraints.items():
        print(f"    {C.dim(key + ':')} {value}")
    print()


def _print_state_summary(state: Dict[str, Any]) -> None:
    if not state:
        print(f"  {C.red('No state.json found — run may not have completed.')}")
        print()
        return

    phase = state.get("phase", "unknown")
    iteration = state.get("iteration", 0)
    last_result = state.get("last_result", "none")
    files_touched = state.get("files_touched", [])

    phase_color = PHASE_COLORS.get(phase, C.gray)
    result_color = RESULT_COLORS.get(last_result, C.gray)
    icon = PHASE_ICONS.get(phase, "❓")

    print(f"  {C.bold('Final State')}")
    print(f"    {C.dim('Phase:')}     {phase_color(f'{icon} {phase}')}  "
          f"{C.dim('Iterations:')} {iteration}")
    print(f"    {C.dim('Result:')}    {result_color(last_result)}")
    print(f"    {C.dim('Files:')}     {len(files_touched)} touched")
    if files_touched:
        for f in files_touched:
            print(f"      {C.bright_cyan(f)}")
    print()


def _print_plan(plan_text: str) -> None:
    if not plan_text.strip():
        return
    print(_hr("─"))
    print(f"  {C.bold('📋  PLAN')}")
    print(_hr("─"))
    print()
    for line in plan_text.strip().split("\n"):
        print(f"    {C.dim('│')} {line}")
    print()


def _print_llm_turns(llm_entries: List[Dict[str, Any]]) -> None:
    if not llm_entries:
        return
    print(_hr("─"))
    print(f"  {C.bold('🧠  LLM INTERACTIONS')}  {C.dim(f'({len(llm_entries)} entries)')}")
    print(_hr("─"))
    print()

    for i, entry in enumerate(llm_entries):
        if "request" in entry:
            # This is a request entry
            messages = entry["request"]
            # Show the last user message (the actual prompt)
            last_user = ""
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    last_user = msg.get("content", "")
                    break
            print(f"  {C.bold(f'Turn {i + 1}:')} {C.cyan('Prompt')}")
            prompt_preview = _truncate(last_user.replace("\n", " "), 100)
            print(f"    {C.dim(prompt_preview)}")

        elif "response" in entry:
            response = entry["response"]
            # Detect if it was valid JSON or had issues
            is_valid = False
            is_fence_wrapped = False
            preview = _truncate(response.replace("\n", " "), 150)
            if response.strip().startswith("```"):
                is_fence_wrapped = True
            try:
                # Quick check — try to parse
                stripped = response.strip()
                if stripped.startswith("```"):
                    lines = stripped.split("\n")
                    stripped = "\n".join(lines[1:-1]) if len(lines) > 2 else stripped
                json.loads(stripped)
                is_valid = True
            except (json.JSONDecodeError, ValueError):
                pass

            if is_valid:
                print(f"    {C.green('✓ Valid response')}  {C.dim(preview)}")
            elif is_fence_wrapped:
                print(f"    {C.yellow('⚠ Fence-wrapped')}  {C.dim(preview)}")
            else:
                print(f"    {C.red('✗ Invalid response')}  {C.dim(preview)}")
            print()

        elif "parse_error" in entry:
            error = entry["parse_error"]
            print(f"    {C.bright_red(f'Parse Error: {error}')}")
            print()


def _print_tool_actions(action_entries: List[Dict[str, Any]]) -> None:
    if not action_entries:
        return
    print(_hr("─"))
    print(f"  {C.bold('🔧  TOOL CALLS')}  {C.dim(f'({len(action_entries)} calls)')}")
    print(_hr("─"))
    print()

    for i, entry in enumerate(action_entries):
        tool = entry.get("tool", "unknown")
        tool_input = entry.get("input", {})
        result = entry.get("result", {})

        # Tool icon
        tool_icons = {
            "write_file": "📝",
            "read_file": "📖",
            "list_dir": "📂",
            "search_in_files": "🔍",
            "run_cmd": "⚡",
            "web_search": "🌐",
        }
        icon = tool_icons.get(tool, "•")

        # Format input summary
        input_summary = ""
        if tool == "write_file":
            path = tool_input.get("path", "?")
            content = tool_input.get("content", "")
            lines = content.count("\n") + 1 if content else 0
            input_summary = f"{path} ({lines} lines)"
        elif tool == "read_file":
            input_summary = tool_input.get("path", "?")
        elif tool == "list_dir":
            input_summary = tool_input.get("path", ".")
        elif tool == "search_in_files":
            input_summary = f"{tool_input.get('path', '?')} → \"{tool_input.get('query', '?')}\""
        elif tool == "run_cmd":
            input_summary = tool_input.get("cmd", "?")
        elif tool == "web_search":
            input_summary = tool_input.get("query", "?")

        # Color based on success/error
        has_error = isinstance(result, dict) and "error" in result
        if has_error:
            status_color = C.bright_red
            status = "✗"
        else:
            status_color = C.bright_green
            status = "✓"

        print(
            f"  {C.dim(f'{i + 1:>3}.')} {icon} "
            f"{C.bold(tool)} "
            f"{C.dim(_truncate(input_summary, 60))}  "
            f"{status_color(status)}"
        )

        # Show result details for errors or interesting results
        if has_error:
            error_msg = result.get("error", "unknown error")
            print(f"       {C.red('Error:')} {_truncate(error_msg, 100)}")
        elif tool == "run_cmd" and isinstance(result, dict):
            rc = result.get("returncode", -1)
            output = result.get("output", "")
            rc_color = C.green if rc == 0 else C.red
            print(f"       {C.dim('exit')} {rc_color(str(rc))}", end="")
            if output:
                # Show first 2-3 lines of output
                output_lines = output.strip().split("\n")
                for line in output_lines[:3]:
                    print(f"\n       {C.dim(_truncate(line, 80))}", end="")
                if len(output_lines) > 3:
                    print(f"\n       {C.dim(f'... +{len(output_lines) - 3} more lines')}", end="")
            print()
        elif tool == "write_file":
            pass  # Success is enough
        elif tool == "list_dir" and isinstance(result, list):
            items = result[:5]
            suffix = f" {C.dim(f'(+{len(result) - 5} more)')}" if len(result) > 5 else ""
            print(f"       {C.dim(', '.join(str(x) for x in items))}{suffix}")

        print()


def _print_stats(
    action_entries: List[Dict[str, Any]],
    llm_entries: List[Dict[str, Any]],
    state: Dict[str, Any],
) -> None:
    print(_hr("─"))
    print(f"  {C.bold('📊  STATISTICS')}")
    print(_hr("─"))
    print()

    iterations = state.get("iteration", 0)
    files_touched = state.get("files_touched", [])
    llm_calls = sum(1 for e in llm_entries if "request" in e)
    tool_calls = len(action_entries)
    errors = sum(1 for e in action_entries if isinstance(e.get("result"), dict) and "error" in e["result"])
    parse_errors = sum(1 for e in llm_entries if "parse_error" in e)
    fence_wraps = sum(1 for e in llm_entries if "response" in e and e["response"].strip().startswith("```"))

    print(f"    {C.dim('Iterations:')}        {iterations}")
    print(f"    {C.dim('LLM calls:')}         {llm_calls}")
    print(f"    {C.dim('Tool calls:')}        {tool_calls}")
    print(f"    {C.dim('Tool errors:')}       {C.red(str(errors)) if errors else C.green('0')}")
    print(f"    {C.dim('Parse errors:')}      {C.red(str(parse_errors)) if parse_errors else C.green('0')}")
    print(f"    {C.dim('Fence-wrapped:')}     {C.yellow(str(fence_wraps)) if fence_wraps else C.green('0')}")
    print(f"    {C.dim('Files modified:')}    {len(files_touched)}")
    print()


def _print_footer() -> None:
    print(_hr("═"))
    print(
        f"  {C.bright_cyan('👻')}  "
        f"{C.dim('This report shows exactly what Ghost saw and did.')}"
    )
    print(
        f"  {C.dim('Full LLM logs:')} "
        f"{C.cyan('cat <workspace>/llm.log')}"
    )
    print(_hr("═"))
    print()


# ── Aggregate audit across all runs ──────────────────────────────────────────


def _print_aggregate_stats(workspaces_root: Path) -> None:
    print()
    print(_hr("═"))
    print(C.bold("  👻  GHOST AGENT — AGGREGATE AUDIT"))
    print(_hr("═"))
    print()

    if not workspaces_root.exists():
        print(f"  {C.yellow('No runs found.')}")
        print()
        return

    run_dirs = sorted(
        [d for d in workspaces_root.iterdir() if d.is_dir()],
        key=lambda d: d.name,
    )

    if not run_dirs:
        print(f"  {C.yellow('No runs found.')}")
        print()
        return

    total_runs = 0
    successes = 0
    failures = 0
    total_iterations = 0
    total_tool_calls = 0
    total_parse_errors = 0
    total_fence_wraps = 0
    all_files_touched: List[str] = []

    for run_dir in run_dirs:
        state = _load_state(run_dir / "state.json")
        task = _load_task(run_dir / "task.json")
        if not state:
            continue
        total_runs += 1
        last_result = state.get("last_result", "")
        if last_result in ("tests_passed", "plan_ok", "no_test_cmd"):
            successes += 1
        else:
            failures += 1
        total_iterations += state.get("iteration", 0)
        all_files_touched.extend(state.get("files_touched", []))

        action_entries = _load_jsonl(run_dir / "actions.log")
        llm_entries = _load_jsonl(run_dir / "llm.log")
        total_tool_calls += len(action_entries)
        total_parse_errors += sum(1 for e in llm_entries if "parse_error" in e)
        total_fence_wraps += sum(
            1 for e in llm_entries
            if "response" in e and e["response"].strip().startswith("```")
        )

    print(f"  {C.bold('Runs:')}           {total_runs}  "
          f"({C.bright_green(f'{successes} passed')}, "
          f"{C.bright_red(f'{failures} failed')})")
    print(f"  {C.dim('Total iterations:')} {total_iterations}")
    print(f"  {C.dim('Total tool calls:')} {total_tool_calls}")
    print(f"  {C.dim('Parse errors:')}     {C.red(str(total_parse_errors)) if total_parse_errors else C.green('0')}")
    print(f"  {C.dim('Fence-wrapped:')}    {C.yellow(str(total_fence_wraps)) if total_fence_wraps else C.green('0')}")
    print(f"  {C.dim('Unique files:')}     {len(set(all_files_touched))}")
    print()

    # Per-run summary table
    print(f"  {C.bold('Per-Run Summary')}")
    print(f"  {'ID':<30} {'Phase':<12} {'Result':<20} {'Iters':<6}")
    print(f"  {'─' * 68}")

    for run_dir in run_dirs:
        state = _load_state(run_dir / "state.json")
        task = _load_task(run_dir / "task.json")
        if not state:
            continue
        run_id = task.get("id", run_dir.name)
        phase = state.get("phase", "?")
        result = state.get("last_result", "?")
        iters = state.get("iteration", 0)

        phase_color = PHASE_COLORS.get(phase, C.gray)
        result_color = RESULT_COLORS.get(result, C.gray)

        print(
            f"  {C.bright_cyan(run_id):<40} "
            f"{phase_color(phase):<20} "
            f"{result_color(result):<30} "
            f"{iters}"
        )

    print()
    print(_hr("═"))
    print()


# ── Main entry point ─────────────────────────────────────────────────────────


def _ensure_utf8_output() -> None:
    """Reconfigure stdout to UTF-8 on Windows for Unicode display."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


def audit_run(workspace: Path) -> None:
    """Print a full transparency report for a single run."""
    _ensure_utf8_output()
    task = _load_task(workspace / "task.json")
    state = _load_state(workspace / "state.json")
    plan = _load_plan(workspace / "plan.md")
    llm_entries = _load_jsonl(workspace / "llm.log")
    action_entries = _load_jsonl(workspace / "actions.log")

    _print_header(task, workspace)
    _print_task_constraints(task)
    _print_state_summary(state)
    _print_plan(plan)
    _print_llm_turns(llm_entries)
    _print_tool_actions(action_entries)
    _print_stats(action_entries, llm_entries, state)
    _print_footer()


def audit_all(workspaces_root: Path) -> None:
    """Print aggregate stats across all runs."""
    _ensure_utf8_output()
    _print_aggregate_stats(workspaces_root)
