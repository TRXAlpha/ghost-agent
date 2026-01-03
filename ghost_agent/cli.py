import argparse
import itertools
import os
import threading
import time
from pathlib import Path

from .orchestrator import Orchestrator, Task
from .utils.watch import FileWatcher


def main() -> None:
    parser = argparse.ArgumentParser(prog="ghost", description="Run Ghost agent tasks")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="Run a task.json file")
    run_parser.add_argument("task_path", help="Path to task.json")

    resume_parser = sub.add_parser("resume", help="Resume a task by id")
    resume_parser.add_argument("task_id", help="Task id to resume")

    interactive_parser = sub.add_parser("interactive", help="Interactive mode")
    interactive_parser.add_argument(
        "--project-root",
        default=".",
        help="Project root to edit (default: current directory)",
    )
    interactive_parser.add_argument(
        "--watch",
        dest="watch",
        action="store_true",
        help="Watch for file changes and auto-run",
    )
    interactive_parser.add_argument(
        "--no-watch",
        dest="watch",
        action="store_false",
        help="Disable watch mode",
    )
    interactive_parser.set_defaults(watch=True)
    interactive_parser.add_argument(
        "--test-cmd",
        default="pytest -q",
        help="Test command to run in VERIFY",
    )
    interactive_parser.add_argument(
        "--iteration-limit",
        type=int,
        default=8,
        help="Iteration limit per run",
    )
    interactive_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Disable verbose output in interactive mode",
    )

    args = parser.parse_args()
    repo_root = Path.cwd()
    model = os.getenv("GHOST_MODEL", "vibethinker:1.5b")
    base_url = os.getenv("GHOST_OLLAMA_BASE_URL")
    timeout_str = os.getenv("GHOST_OLLAMA_TIMEOUT", "60")
    try:
        timeout = int(timeout_str)
    except ValueError:
        timeout = 60
    orchestrator = Orchestrator(
        repo_root=repo_root,
        model=model,
        base_url=base_url,
        timeout=timeout,
    )

    if args.command == "run":
        orchestrator.run_task(Path(args.task_path))
    elif args.command == "resume":
        orchestrator.resume_task(args.task_id)
    elif args.command == "interactive":
        project_root = Path(args.project_root).resolve()
        interactive_orchestrator = Orchestrator(
            repo_root=project_root,
            model=model,
            base_url=base_url,
            timeout=timeout,
            verbose=not args.quiet,
        )
        _run_interactive(
            interactive_orchestrator,
            project_root,
            test_cmd=args.test_cmd,
            iteration_limit=args.iteration_limit,
            watch_enabled=args.watch,
        )


def _run_interactive(
    orchestrator: Orchestrator,
    project_root: Path,
    test_cmd: str,
    iteration_limit: int,
    watch_enabled: bool,
) -> None:
    artifacts_base = project_root / ".ghost" / "workspaces"
    artifacts_base.mkdir(parents=True, exist_ok=True)
    ignore_dirs = {
        ".ghost",
        ".git",
        ".venv",
        "__pycache__",
        "node_modules",
        "workspaces",
        "memories",
    }
    watcher = FileWatcher(project_root, ignore_dirs=ignore_dirs)
    run_lock = threading.Lock()
    state_lock = threading.Lock()
    last_run_time = {"value": 0.0}
    running = {"value": False}
    counter = itertools.count(1)

    def run_goal(goal: str, auto: bool) -> None:
        with run_lock:
            running["value"] = True
            task_id = f"{time.strftime('live_%Y%m%d_%H%M%S')}_{next(counter)}"
            task = Task(
                task_id=task_id,
                title="Interactive task",
                goal=goal,
                constraints={
                    "test_cmd": test_cmd,
                    "iteration_limit": iteration_limit,
                },
                context={"repo_path": str(project_root), "notes": "interactive"},
            )
            artifacts_root = artifacts_base / task_id
            orchestrator.run_task_with_roots(
                task, workspace_root=project_root, artifacts_root=artifacts_root
            )
            with state_lock:
                last_run_time["value"] = time.time()
            running["value"] = False
        if auto:
            print("[ghost] Auto-run complete.\n")

    def watch_loop() -> None:
        while True:
            time.sleep(2.0)
            if not watch_enabled:
                continue
            if running["value"]:
                continue
            with state_lock:
                if time.time() - last_run_time["value"] < 2.0:
                    continue
            changes = watcher.poll()
            if not changes:
                continue
            summary = "; ".join(changes)
            auto_goal = (
                "User edited files detected: "
                f"{summary}. Review these changes and improve or fix issues. "
                "Focus only on the changed files unless necessary."
            )
            if run_lock.locked():
                continue
            run_goal(auto_goal, auto=True)

    watcher_thread = threading.Thread(target=watch_loop, daemon=True)
    watcher_thread.start()

    print("Ghost interactive mode. Type a goal or /help.")
    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            break
        if not line:
            continue
        if line in {"/exit", "/quit"}:
            break
        if line.startswith("/watch"):
            watch_enabled = "off" not in line.lower()
            print(f"[ghost] watch={'on' if watch_enabled else 'off'}")
            continue
        if line.startswith("/help"):
            print(
                "Commands: /help, /exit, /watch on, /watch off\n"
                "Type any other text to run a task."
            )
            continue
        run_goal(line, auto=False)


if __name__ == "__main__":
    main()
