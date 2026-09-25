"""Ghost Agent Orchestrator — the brain that drives everything.

Now with:
- RAG context injection (knows your codebase)
- Knowledge graph integration (learns from every run)
- Parallel sub-agent support (decomposes complex tasks)
- Streaming LLM support (real-time thinking display)
- Smarter repair loops with error pattern matching
"""
from __future__ import annotations

import json
import re
import shutil
import stat
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, List, Optional, Protocol, Tuple

from .context import CodeIndex
from .command_budget import CommandBudget
from .test_discovery import discover_test_files
from .knowledge import KnowledgeGraph
from .identity import IdentityStore
from .kernel import ActionProposal, CapabilityKernel, CapabilityManifest
from .provenance import ProvenanceLedger
from .security import SecurityMonitor, redact_secrets
from .runtime import EmergencyStop
from .memory.store import MemoryStore
from .models.chat import chat as ollama_chat
from .schema import ActionResponse, parse_action_response
from .tools import cmd_tools, fs_tools, web_tools
from .utils.logging import JsonlLogger
from .utils.storage import atomic_json


class TUICallback(Protocol):
    """Protocol for TUI callback objects."""
    def on_phase_change(self, phase: str, iteration: int) -> None: ...
    def on_action(self, tool: str, tool_input: Dict[str, Any], result: Any) -> None: ...
    def on_llm_response(self, thought: str, valid: bool) -> None: ...
    def on_file_touched(self, path: str) -> None: ...
    def on_complete(self, result: str) -> None: ...


SYSTEM_PROMPT = """You are Ghost, a coding agent. You respond with valid JSON only.

SCHEMA:
{"thought": "short reasoning", "actions": [{"tool": "write_file", "path": "f.py", "content": "print('hi')"}]}

AVAILABLE TOOLS:
- write_file: {"tool": "write_file", "path": "...", "content": "..."}
- read_file: {"tool": "read_file", "path": "..."}
- list_dir: {"tool": "list_dir", "path": "."}
- search_in_files: {"tool": "search_in_files", "path": ".", "query": "..."}
- run_cmd: {"tool": "run_cmd", "cmd": "pytest -q", "cwd": "."}
- web_search: {"tool": "web_search", "query": "search terms here"}

RULES:
- Output ONLY a JSON object. No markdown fences, no prose, no explanation outside JSON.
- No placeholder values. Use real file paths and commands.
- Use pytest-style tests unless the task says otherwise.
- Stay inside the workspace root. Use only allowed commands.
- If no actions needed: {"thought": "...", "actions": []}

STRATEGY:
- Read relevant files first before writing
- Make minimal, targeted changes
- Run tests after implementation
- If tests fail, fix the specific issues shown
- Add focused tests for behavior you change, then run the broadest relevant test command.
- Use web_search when you need current information, documentation, or solutions from the internet
- If you do not know something, ALWAYS use web_search instead of hallucinating.
- If asked to come up with a new idea, FIRST use web_search to find what is already done, identify problems in the field, and THEN start working if there is no existing solution.

EXAMPLES:

Example 1 - Create a file:
{"thought": "Create hello.py with a greeting function", "actions": [{"tool": "write_file", "path": "hello.py", "content": "def greet(name):\\n    return f\\\"Hello, {name}!\\\"\\n\\nif __name__ == '__main__':\\n    print(greet('World'))"}]}

Example 2 - Read then write:
{"thought": "Read the existing file to understand its structure", "actions": [{"tool": "read_file", "path": "main.py"}, {"tool": "write_file", "path": "main.py", "content": "# updated content"}]}

Example 3 - Run tests:
{"thought": "Run the test suite to verify the implementation", "actions": [{"tool": "run_cmd", "cmd": "pytest -q", "cwd": "."}]}

Example 4 - Search codebase:
{"thought": "Find all references to the deprecated function", "actions": [{"tool": "search_in_files", "path": "src", "query": "old_function"}]}

Example 5 - Search the web:
{"thought": "Look up the latest FastAPI docs for dependency injection", "actions": [{"tool": "web_search", "query": "FastAPI dependency injection latest docs"}]}
"""


@dataclass
class Task:
    task_id: str
    title: str
    goal: str
    constraints: Dict[str, Any]
    context: Dict[str, Any]

    def __post_init__(self):
        if not isinstance(self.constraints, dict) or not isinstance(self.context, dict):
            raise ValueError("Task constraints and context must be objects")
        self.required_files()
        self.iteration_limit()

    def iteration_limit(self) -> int:
        for key in ("iteration_limit", "time_budget_iters"):
            if key in self.constraints:
                value = self.constraints[key]
                if type(value) is not int or not 1 <= value <= 1000:
                    raise ValueError(f"{key} must be an integer from 1 to 1000")
        return self.constraints.get("iteration_limit", self.constraints.get("time_budget_iters", 8))

    def required_files(self) -> List[str]:
        paths = self.constraints.get("required_files", [])
        return self.validate_relative_files(paths)

    @staticmethod
    def validate_relative_files(paths: List[str]) -> List[str]:
        if not isinstance(paths, list) or len(paths) > 256:
            raise ValueError("Required files must be a list of at most 256 relative paths")
        seen = set()
        for value in paths:
            if not isinstance(value, str) or not value.strip() or len(value) > 1024:
                raise ValueError("Invalid required file path")
            normalized = value.replace("\\", "/")
            parts = normalized.split("/")
            if (PureWindowsPath(value).drive or normalized.startswith("/")
                    or any(part in {"", ".", ".."} for part in parts)
                    or any(ord(char) < 32 or char in ':*?"<>|' for char in value)):
                raise ValueError("Required files must be workspace-relative file paths")
            key = normalized.casefold()
            if key in seen:
                raise ValueError("Duplicate required file path")
            seen.add(key)
        return paths


class Orchestrator:
    def __init__(
        self,
        repo_root: Path,
        model: str = "qwen2.5-coder:1.5b",
        base_url: Optional[str] = None,
        timeout: int = 60,
        verbose: bool = False,
        tui: Optional[TUICallback] = None,
        max_input_chars: int = 24_000,
        max_output_tokens: int = 2048,
    ) -> None:
        if type(max_input_chars) is not int or max_input_chars <= 0:
            raise ValueError("max_input_chars must be a positive integer")
        self.max_input_chars = max_input_chars
        if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 8192:
            raise ValueError("max_output_tokens must be an integer from 1 to 8192")
        self.max_output_tokens = max_output_tokens
        self.repo_root = Path(repo_root).resolve()
        self.workspaces_root = self.repo_root / "workspaces"
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self.verbose = verbose
        self.tui = tui
        self._goal_prints = False

        # ── Knowledge systems ────────────────────────────────────────────
        self.memory = MemoryStore(self.repo_root / "memories")
        self.identity = IdentityStore(self.repo_root)
        self.kernel = CapabilityKernel()
        self.provenance = ProvenanceLedger(self.repo_root)
        self._provenance_parent_id: Optional[str] = None
        self._register_sandbox_capabilities()
        self.knowledge = KnowledgeGraph(self.repo_root / "memories")
        self.code_index: Optional[CodeIndex] = None
        self._build_code_index()

    def _register_sandbox_capabilities(self) -> None:
        """The LLM proposes tools; this fixed local policy controls them."""
        self.kernel.register(CapabilityManifest("filesystem.write", ["path", "content"], ["artifact"],
                                                 filesystem_scopes=["sandbox/**"], rollback="discard sandbox"))
        self.kernel.register(CapabilityManifest("filesystem.read", ["path"], ["content"],
                                                 filesystem_scopes=["sandbox/**"]))
        self.kernel.register(CapabilityManifest("filesystem.list", ["path"], ["entries"],
                                                 filesystem_scopes=["sandbox/**"]))
        self.kernel.register(CapabilityManifest("filesystem.search", ["path", "query"], ["matches"],
                                                 filesystem_scopes=["sandbox/**"]))
        self.kernel.register(CapabilityManifest("command.execute", ["cmd", "cwd"], ["result"],
                                                 filesystem_scopes=["sandbox/**"], rollback="discard sandbox"))
        self.kernel.register(CapabilityManifest("network.search", ["query"], ["results"],
                                                 network_allowed=True, audit_required=True))

    def _build_code_index(self) -> None:
        """Build the RAG code index for the workspace."""
        try:
            index = CodeIndex()
            exclude = {
                ".git", ".venv", "__pycache__", "node_modules",
                ".ghost", "memories", "workspaces",
            }
            index.build(self.repo_root, exclude_dirs=exclude)
            if index.summaries:
                self.code_index = index
                # Attach to memory for code-aware retrieval
                self.memory.set_code_index(index)
                self.memory.set_knowledge_graph(self.knowledge)
                # Build knowledge graph from code index
                self.knowledge.build_from_index(index)
        except Exception:
            # Non-fatal: run without RAG if indexing fails
            self.code_index = None

    def run_task(self, task_path: Path) -> None:
        task = self._load_task(task_path)
        self._provenance_parent_id = self.provenance.record(
            "outcome.started", {"task_id": task.task_id, "goal": task.goal, "model": self.model}
        ).record_id
        workspace = self._init_workspace(task, task_path)
        state = {
            "phase": "INGEST",
            "iteration": 0,
            "last_result": None,
            "open_items": [],
            "files_touched": [],
        }
        self._run_loop(task, workspace, state)

    def run_task_with_roots(
        self, task: Task, workspace_root: Path, artifacts_root: Path
    ) -> None:
        self._provenance_parent_id = self.provenance.record(
            "outcome.started", {"task_id": task.task_id, "goal": task.goal, "model": self.model}
        ).record_id
        artifacts = self._init_artifacts_root(task, artifacts_root)
        sandbox = self._create_sandbox(workspace_root, artifacts / "sandbox")
        state = {
            "phase": "INGEST",
            "iteration": 0,
            "last_result": None,
            "open_items": [],
            "files_touched": [],
        }
        self._run_loop(task, sandbox, state, artifacts_root=artifacts)

    def resume_task_with_roots(self, task: Task, artifacts_root: Path) -> None:
        """Operator-requested continuation of a preserved outcome workspace."""
        artifacts = Path(artifacts_root).absolute()
        if artifacts.resolve() != artifacts or artifacts == self.repo_root or not artifacts.is_relative_to(self.repo_root):
            raise ValueError("Invalid outcome artifact path")
        stored = self._load_task(artifacts / "task.json")
        if (stored.task_id, stored.title, stored.goal, stored.constraints) != (
                task.task_id, task.title, task.goal, task.constraints):
            raise ValueError("Outcome contract changed; create a new outcome instead of resuming")
        sandbox = artifacts / "sandbox"
        if not sandbox.is_dir() or sandbox.resolve() != sandbox:
            raise ValueError("Preserved sandbox is missing or redirected")
        state = json.loads((artifacts / "state.json").read_text(encoding="utf-8"))
        self._prepare_resume(state, sandbox)
        self._provenance_parent_id = self.provenance.record(
            "outcome.resumed", {"task_id": task.task_id, "model": self.model,
                                "prior_iterations": state["prior_iterations"]}
        ).record_id
        self._run_loop(task, sandbox, state, artifacts_root=artifacts)

    def resume_task(self, task_id: str) -> None:
        workspace = self._task_workspace(task_id)
        task_path = workspace / "task.json"
        state_path = workspace / "state.json"
        if not task_path.exists():
            raise FileNotFoundError(f"task.json not found for {task_id}")
        task = self._load_task(task_path)
        self._provenance_parent_id = self.provenance.record(
            "outcome.resumed", {"task_id": task.task_id, "goal": task.goal, "model": self.model}
        ).record_id
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self._prepare_resume(state, workspace)
        else:
            state = {
                "phase": "INGEST",
                "iteration": 0,
                "last_result": None,
                "open_items": [],
                "files_touched": [],
            }
        self._run_loop(task, workspace, state)

    @staticmethod
    def _prepare_resume(state: Dict[str, Any], workspace: Optional[Path] = None) -> None:
        if (not isinstance(state, dict)
                or not isinstance(state.get("phase"), str)
                or state["phase"] not in {"INGEST", "PLAN", "IMPLEMENT", "VERIFY", "REPAIR", "DONE"}
                or any(type(value) is not int or value < 0 for value in
                       (state.get("iteration"), state.get("prior_iterations", 0)))
                or not isinstance(state.get("files_touched"), list)
                or any(not isinstance(path, str) or not path.strip() for path in state["files_touched"])
                or not isinstance(state.get("last_feedback", ""), str)
                or state.get("last_result") is not None and not isinstance(state["last_result"], str)):
            raise ValueError("Invalid outcome checkpoint; manual review required")
        Task.validate_relative_files(state["files_touched"])
        if workspace is not None:
            root = workspace.resolve()
            for value in state["files_touched"]:
                candidate = root / value.replace("\\", "/")
                if candidate.resolve() != candidate:
                    raise ValueError("Checkpoint file path follows a link or escapes the workspace")
        state["prior_iterations"] = state.get("prior_iterations", 0) + state["iteration"]
        state["iteration"] = 0
        if state["phase"] == "DONE":
            state["phase"] = "VERIFY" if state.get("last_result") == "tests_passed" else "REPAIR"

    def _load_task(self, task_path: Path) -> Task:
        data = json.loads(task_path.read_text(encoding="utf-8"))
        return Task(
            task_id=data["id"],
            title=data.get("title", ""),
            goal=data.get("goal", ""),
            constraints=data.get("constraints", {}),
            context=data.get("context", {}),
        )

    def _init_workspace(self, task: Task, task_path: Path) -> Path:
        workspace = self._task_workspace(task.task_id)
        workspace = self._create_sandbox(self.repo_root, workspace)
        (workspace / "task.json").write_text(
            task_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        (workspace / "notes.md").write_text("", encoding="utf-8")
        return workspace

    def _task_workspace(self, task_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", task_id):
            raise ValueError("Invalid task ID; expected an identifier, not a path")
        expected = self.workspaces_root / task_id
        workspace = expected.resolve()
        if workspace != expected or not workspace.is_relative_to(self.workspaces_root):
            raise ValueError("Invalid task ID; workspace escapes its directory or follows a link")
        return workspace

    @staticmethod
    def _sandbox_ignore(_: str, names: List[str]) -> set[str]:
        """Keep dependency caches, prior runs, and VCS metadata out of a run."""
        ignored = {
            ".git", ".ghost", ".venv", "__pycache__", ".pytest_cache",
            "node_modules", "workspaces", "memories", "outputs",
        }
        return {name for name in names
                if name.lower() in ignored or name.lower() == ".env" or name.lower().startswith(".env.")
                or Path(name).suffix.lower() in {".pyc", ".pem", ".key", ".p12", ".pfx"}}

    def _create_sandbox(self, source_root: Path, target: Path) -> Path:
        """Copy a project into a NEW workspace; this is not OS containment."""
        source = Path(source_root).resolve()
        target = Path(target).resolve()
        allowed_root = self.repo_root.resolve()
        if target == allowed_root or not target.is_relative_to(allowed_root):
            raise ValueError(f"Sandbox path escapes project: {target}")
        if source == target or source.is_relative_to(target):
            raise ValueError("Sandbox must not replace or contain its source")
        if target.exists():
            raise FileExistsError(f"Workspace already exists; preserve it and resume explicitly: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        def ignore(directory: str, names: List[str]) -> set[str]:
            excluded = self._sandbox_ignore(directory, names)
            for name in set(names) - excluded:
                item = Path(directory) / name
                attributes = getattr(item.lstat(), "st_file_attributes", 0)
                if item.is_symlink() or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise ValueError(f"Snapshot cannot follow links: {item}")
                if target.is_relative_to(item.resolve()):
                    excluded.add(name)
            return excluded
        shutil.copytree(source, target, ignore=ignore)
        return target

    def _init_artifacts_root(self, task: Task, artifacts_root: Path) -> Path:
        artifacts_root = Path(artifacts_root).resolve()
        if artifacts_root == self.repo_root or not artifacts_root.is_relative_to(self.repo_root):
            raise ValueError("Artifacts must be in a new directory inside the project")
        artifacts_root.mkdir(parents=True, exist_ok=False)
        payload = {
            "id": task.task_id,
            "title": task.title,
            "goal": task.goal,
            "constraints": task.constraints,
            "context": task.context,
        }
        (artifacts_root / "task.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        (artifacts_root / "notes.md").write_text("", encoding="utf-8")
        return artifacts_root

    def _run_loop(
        self,
        task: Task,
        workspace: Path,
        state: Dict[str, Any],
        artifacts_root: Optional[Path] = None,
    ) -> None:
        iteration_limit = task.iteration_limit()
        goal_lower = task.goal.lower()
        self._goal_prints = "print" in goal_lower or "prints" in goal_lower
        artifacts_root = artifacts_root or workspace
        actions_log = JsonlLogger(artifacts_root / "actions.log")
        llm_log = JsonlLogger(artifacts_root / "llm.log")
        plan_path = workspace / "plan.md"
        state_path = artifacts_root / "state.json"
        self._command_budget = CommandBudget(artifacts_root / "command_budget.json",
            task.constraints.get("command_budget_seconds", 300),
            task.constraints.get("command_budget_calls", 32))

        # ── Build context with RAG + knowledge ───────────────────────────
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": self._build_system_prompt(task)},
            {"role": "user", "content": self._build_context(task, workspace)},
        ]

        # Greenfield tasks are harmed by unrelated repository summaries. Only
        # load code history when the user explicitly asks to change existing
        # behavior (or opts in through task context).
        use_existing_context = self._needs_existing_code_context(task)

        # Inject codebase context via RAG
        if self.code_index and use_existing_context:
            code_context = self.code_index.build_context(
                f"{task.title} {task.goal}", max_tokens=2000
            )
            if code_context and "No relevant code" not in code_context:
                messages.append({
                    "role": "user",
                    "content": f"Relevant codebase context:\n{code_context}",
                })

        # Inject knowledge graph suggestions
        suggestions = self.knowledge.get_task_suggestions(task.goal) if use_existing_context else {}
        if suggestions.get("similar_tasks", 0) > 0:
            sug_text = (
                f"Past experience with similar tasks:\n"
                f"- {suggestions['similar_tasks']} similar tasks found\n"
                f"- Success rate: {suggestions.get('success_rate', 0):.0%}\n"
            )
            if suggestions.get("suggested_strategy"):
                sug_text += f"- Suggested strategy: {suggestions['suggested_strategy']}\n"
            if suggestions.get("common_files"):
                sug_text += f"- Commonly involved files: {', '.join(suggestions['common_files'])}\n"
            messages.append({"role": "user", "content": sug_text})

        # Inject memories
        memory_notes = []
        if use_existing_context:
            memory_notes = self.memory.retrieve(
                f"{task.title} {task.goal} {task.context.get('notes', '')}",
                use_code_context=False,  # Already injected above
                use_knowledge=False,     # Already injected above
            )
        if memory_notes:
            messages.append({
                "role": "user",
                "content": "Relevant memories:\n" + "\n\n".join(memory_notes),
            })

        last_feedback = state.get("last_feedback", "")
        strategy = "default"
        while True:
            if state["phase"] != "DONE" and EmergencyStop(self.repo_root).active():
                state["phase"] = "DONE"
                state["last_result"] = "emergency_stop"
            if state["phase"] != "DONE":
                try:
                    if self._command_budget.exhausted():
                        state["phase"] = "DONE"
                        state["last_result"] = "command_budget_exhausted"
                except (OSError, ValueError) as exc:
                    state["phase"] = "DONE"
                    state["last_result"] = "command_budget_invalid"
                    last_feedback = f"Command budget refused: {exc}"
                if state["phase"] == "DONE":
                    self._write_state(state_path, state, last_feedback)
            if state["phase"] != "DONE" and state["iteration"] >= iteration_limit:
                state["phase"] = "DONE"
                state["last_result"] = "iteration_limit"
                self._write_state(state_path, state)
                self._write_memory(task, workspace, state, strategy)
                self._record_knowledge(task, state, strategy)
                self._tui_complete(state["last_result"])
                break

            phase = state["phase"]
            if phase == "INGEST":
                state["phase"] = "PLAN"
                self._tui_phase("PLAN", state["iteration"])
                self._write_state(state_path, state)
                continue

            if phase == "PLAN":
                prompt = (
                    "Create the plan now. Output exactly ONE JSON object containing exactly "
                    "ONE write_file action: path must be plan.md and content must list concise "
                    "implementation steps plus a verification strategy. Do not read files or "
                    "perform any other action in this turn."
                )
                action_response, parse_error = self._model_turn(
                    messages, prompt, llm_log, last_feedback
                )
                self._tui_llm(action_response.thought, parse_error is None)
                results, touched = self._execute_actions(
                    action_response, workspace, actions_log
                )
                for path in touched:
                    self._tui_file(path)
                if parse_error:
                    results.append({"error": parse_error})
                state["files_touched"] = self._merge_touched(
                    state["files_touched"], touched
                )
                if self._has_errors(results):
                    state["phase"] = "REPAIR"
                    state["last_result"] = "plan_failed"
                    last_feedback = self._format_results(results)
                    strategy = "plan_and_repair"
                else:
                    if not plan_path.exists():
                        state["phase"] = "REPAIR"
                        state["last_result"] = "plan_missing"
                        last_feedback = "plan.md was not created."
                    else:
                        state["phase"] = "IMPLEMENT"
                        state["last_result"] = "plan_ok"
                        strategy = "plan_first"
                self._tui_phase(state["phase"], state["iteration"])
                state["iteration"] += 1
                self._write_state(state_path, state, last_feedback)
                continue

            if phase == "IMPLEMENT":
                prompt = "Implement the task. Use tools to read/write files as needed."
                # Add file change context if knowledge graph knows affected files
                touched_files = state.get("files_touched", [])
                if self.code_index and touched_files:
                    impact = []
                    for f in touched_files:
                        affected = self.knowledge.get_change_impact(f)
                        impact.extend(affected)
                    if impact:
                        prompt += (
                            f"\nFiles potentially affected by changes to {touched_files}: "
                            f"{', '.join(impact[:10])}. Consider reading them."
                        )

                action_response, parse_error = self._model_turn(
                    messages, prompt, llm_log, last_feedback
                )
                self._tui_llm(action_response.thought, parse_error is None)
                results, touched = self._execute_actions(
                    action_response, workspace, actions_log
                )
                for path in touched:
                    self._tui_file(path)
                if parse_error:
                    results.append({"error": parse_error})
                state["files_touched"] = self._merge_touched(
                    state["files_touched"], touched
                )
                if self._has_errors(results):
                    state["phase"] = "REPAIR"
                    state["last_result"] = "implement_failed"
                    last_feedback = self._format_results(results)
                else:
                    state["phase"] = "VERIFY"
                    state["last_result"] = "implement_ok"
                self._tui_phase(state["phase"], state["iteration"])
                state["iteration"] += 1
                self._write_state(state_path, state, last_feedback)
                continue

            if phase == "VERIFY":
                test_commands = self._verification_commands(task, workspace)
                missing_files = self._missing_required_files(task, workspace)
                if missing_files:
                    state["phase"] = "REPAIR"
                    state["last_result"] = "required_files_missing"
                    last_feedback = (
                        "Required deliverables are missing: " + ", ".join(missing_files) + ". "
                        "Create exactly those relative files before verification."
                    )
                elif self._requires_focused_tests(task) and not self._has_task_test_change(state):
                    state["phase"] = "REPAIR"
                    state["last_result"] = "tests_missing"
                    last_feedback = (
                        "The goal explicitly requires tests, but this run has not written or "
                        "updated a test file. Add focused Python or Node test coverage for the changed behavior."
                    )
                elif test_commands:
                    gates = [("focused", command) for command in self._focused_test_commands(state)]
                    gates += [("suite", command) for command in test_commands]
                    state["verification_gates"] = []
                    for verification_stage, command in gates:
                        gate = {"stage": verification_stage, "command": redact_secrets(command),
                                "status": "running"}
                        state["verification_gates"].append(gate)
                        self._write_state(state_path, state, last_feedback)
                        result = self._run_verification_command(command, workspace, actions_log)
                        gate.update(result=redact_secrets(result),
                                    status="passed" if self._command_succeeded(result) else "failed")
                        self._write_state(state_path, state, last_feedback)
                        if not self._command_succeeded(result):
                            break
                    if self._command_succeeded(result):
                        missing_after_tests = self._missing_required_files(task, workspace)
                        if missing_after_tests:
                            verification_stage = "deliverables"
                            result = {"returncode": 1, "output":
                                "Required deliverables disappeared during verification: "
                                + ", ".join(missing_after_tests)}
                            state["verification_gates"].append({"stage": "deliverables",
                                "status": "failed", "result": result})
                            self._write_state(state_path, state, last_feedback)
                    if self._command_succeeded(result):
                        state["phase"] = "DONE"
                        state["last_result"] = "tests_passed"
                        last_feedback = self._format_results([result])
                    else:
                        state["phase"] = "REPAIR"
                        state["last_result"] = (
                            "required_files_missing" if verification_stage == "deliverables" else
                            "focused_tests_failed" if verification_stage == "focused" else "tests_failed"
                        )
                        last_feedback = self._format_results([result])
                        # Check knowledge graph for error pattern fixes
                        output = str(result.get("output", ""))
                        error_type = self._classify_error(output)
                        if error_type:
                            suggested_fix = self.knowledge.get_suggested_fix(
                                error_type, output
                            )
                            if suggested_fix:
                                last_feedback += (
                                    f"\n💡 Knowledge graph suggests: {suggested_fix}"
                                )

                        output = str(result.get("output", ""))
                        if "Captured stdout" in output and "None !=" in output:
                            last_feedback += (
                                "\nHint: The code is printing output. "
                                "Update tests to capture stdout (pytest capsys or subprocess) "
                                "instead of expecting return values."
                            )
                        if "no tests ran" in output.lower():
                            last_feedback += (
                                "\nHint: Create a pytest test file named test_*.py "
                                "with at least one def test_* function."
                            )
                else:
                    state["phase"] = "REPAIR"
                    state["last_result"] = "verification_missing"
                    last_feedback = (
                        "No explicit test command or discovered test files were found. Create focused "
                        "pytest tests and run them before declaring the task complete."
                    )
                self._tui_phase(state["phase"], state["iteration"])
                state["iteration"] += 1
                self._write_state(state_path, state, last_feedback)
                continue

            if phase == "REPAIR":
                prompt = self._build_repair_prompt(state, last_feedback)
                action_response, parse_error = self._model_turn(
                    messages, prompt, llm_log, last_feedback
                )
                self._tui_llm(action_response.thought, parse_error is None)
                results, touched = self._execute_actions(
                    action_response, workspace, actions_log
                )
                for path in touched:
                    self._tui_file(path)
                if parse_error:
                    results.append({"error": parse_error})
                state["files_touched"] = self._merge_touched(
                    state["files_touched"], touched
                )
                # Record error pattern for learning
                if parse_error:
                    self.knowledge.record_error(
                        "json_parse_error", parse_error[:100],
                        "Repair model response was not valid JSON; no verified fix",
                        worked=not self._has_errors(results),
                    )
                if self._has_errors(results):
                    state["phase"] = "REPAIR"
                    state["last_result"] = "repair_failed"
                    last_feedback = self._format_results(results)
                else:
                    state["phase"] = "VERIFY"
                    state["last_result"] = "repair_ok"
                    last_feedback = self._format_results(results)
                self._tui_phase(state["phase"], state["iteration"])
                state["iteration"] += 1
                self._write_state(state_path, state, last_feedback)
                continue

            if phase == "DONE":
                self._write_memory(task, workspace, state, strategy)
                self._record_knowledge(task, state, strategy)
                self._write_state(state_path, state)
                self._tui_complete(state.get("last_result", "done"))
                break

            state["phase"] = "DONE"
            state["last_result"] = "unknown_phase"
            self._write_state(state_path, state)
            break

    def _build_system_prompt(self, task: Task) -> str:
        """Build a system prompt enriched with codebase knowledge."""
        prompt = SYSTEM_PROMPT

        # Add codebase structure summary if available
        if self.code_index and self._needs_existing_code_context(task):
            stats = self.code_index.stats()
            prompt += (
                f"\n\nCODEBASE INFO:\n"
                f"- {stats['files']} Python files, {stats['total_lines']} lines\n"
                f"- {stats['functions']} functions, {stats['classes']} classes\n"
            )

            # Add key files
            hot_files = self.knowledge.get_hot_files(5)
            if hot_files:
                prompt += f"- Frequently modified: {', '.join(f for f, _ in hot_files)}\n"

        return prompt

    @staticmethod
    def _needs_existing_code_context(task: Task) -> bool:
        if task.context.get("use_existing_context") is True:
            return True
        text = f"{task.title} {task.goal}".lower()
        return any(word in text for word in (
            "fix", "bug", "repair", "refactor", "modify", "update",
            "existing", "regression", "review", "integrate", "replace",
        ))

    @staticmethod
    def _verification_command(task: Task, workspace: Path) -> Optional[str]:
        commands = Orchestrator._verification_commands(task, workspace)
        return commands[0] if len(commands) == 1 else None

    @staticmethod
    def _verification_commands(task: Task, workspace: Path) -> List[str]:
        """Choose a meaningful default verifier instead of silently skipping tests."""
        explicit = task.constraints.get("test_cmd")
        if isinstance(explicit, str) and explicit.strip():
            return [explicit.strip()]
        try:
            tests = discover_test_files(workspace)
        except (OSError, ValueError):
            return []  # Never choose a verifier from a partial scan.
        python_tests = [path for path in tests if Orchestrator._is_python_test(path)]
        node_tests = sorted(path for path in tests if Orchestrator._is_node_test(path))
        commands = []
        if python_tests:
            commands.append("python -m pytest -q")
        if node_tests:
            commands.append(cmd_tools.join_command(["node", "--test", *node_tests]))
        # Compilation is a syntax check, not an outcome acceptance gate.
        # Without discovered tests or an explicit user contract, refuse to
        # manufacture a tests_passed result from a source-only project.
        return commands

    @staticmethod
    def _requires_focused_tests(task: Task) -> bool:
        text = f"{task.title} {task.goal}".lower()
        return any(token in text for token in ("test", "pytest", "coverage"))

    @staticmethod
    def _has_task_test_change(state: Dict[str, Any]) -> bool:
        return any(
            Orchestrator._is_python_test(path) or Orchestrator._is_node_test(path)
            for path in state.get("files_touched", [])
        )

    @staticmethod
    def _focused_test_command(state: Dict[str, Any]) -> Optional[str]:
        commands = Orchestrator._focused_test_commands(state)
        return commands[0] if len(commands) == 1 else None

    @staticmethod
    def _focused_test_commands(state: Dict[str, Any]) -> List[str]:
        node_paths = [path for path in state.get("files_touched", []) if Orchestrator._is_node_test(path)]
        paths = [
            path for path in state.get("files_touched", [])
            if Orchestrator._is_python_test(path)
        ]
        commands = []
        if paths:
            commands.append(cmd_tools.join_command(["python", "-m", "pytest", "-q", "--", *paths]))
        if node_paths:
            commands.append(cmd_tools.join_command(["node", "--test", *node_paths]))
        return commands

    @staticmethod
    def _is_node_test(path: str) -> bool:
        return Path(path).name.endswith((".test.js", ".test.cjs", ".test.mjs"))

    @staticmethod
    def _is_python_test(path: str) -> bool:
        name = Path(path).name
        return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))

    @staticmethod
    def _missing_required_files(task: Task, workspace: Path) -> List[str]:
        root = workspace.resolve()
        missing = []
        for value in task.required_files():
            candidate = root / value.replace("\\", "/")
            if candidate.resolve() != candidate:
                raise ValueError("Required deliverable follows a link or escapes the workspace")
            if not candidate.is_file():
                missing.append(value)
        return missing

    def _run_verification_command(
        self, command: str, workspace: Path, actions_log: JsonlLogger
    ) -> Dict[str, Any]:
        response = ActionResponse.model_validate({
            "thought": "Run the harness verification gate",
            "actions": [{"tool": "run_cmd", "cmd": command, "cwd": "."}],
        })
        results, _ = self._execute_actions(response, workspace, actions_log)
        if len(results) != 1 or not isinstance(results[0], dict):
            return {"returncode": 1, "output": "Invalid verification result: expected one command record"}
        result = results[0]
        if "error" in result:
            return {"returncode": 1, "output": result["error"]}
        if type(result.get("returncode")) is not int or not isinstance(result.get("output"), str):
            return {"returncode": 1, "output": "Invalid verification result: integer exit code and text output required"}
        return result

    @staticmethod
    def _command_succeeded(result: Dict[str, Any]) -> bool:
        return (isinstance(result, dict) and "error" not in result
                and type(result.get("returncode")) is int and result["returncode"] == 0
                and isinstance(result.get("output"), str))

    def _classify_error(self, output: str) -> Optional[str]:
        """Classify an error output into a type."""
        output_lower = output.lower()
        if "syntaxerror" in output_lower:
            return "syntax_error"
        if "importerror" in output_lower or "modulenotfounderror" in output_lower:
            return "import_error"
        if "failed" in output_lower or "error" in output_lower:
            if "test" in output_lower:
                return "test_failure"
        if "json" in output_lower and ("decode" in output_lower or "parse" in output_lower):
            return "json_error"
        if "typeerror" in output_lower:
            return "type_error"
        if "attributeerror" in output_lower:
            return "attribute_error"
        return None

    def _record_knowledge(self, task: Task, state: Dict[str, Any],
                          strategy: str) -> None:
        """Record task completion in the knowledge graph."""
        try:
            self.knowledge.record_task(
                task_id=task.task_id,
                goal=task.goal,
                result=state.get("last_result", "unknown"),
                strategy=strategy,
                files_touched=state.get("files_touched", []),
                iterations=state.get("iteration", 0),
                parse_errors=0,  # TODO: track this
                tags=task.context.get("tags", []),
            )
        except Exception:
            pass  # Non-fatal

    def _build_context(self, task: Task, workspace: Path) -> str:
        testing_hint = ""
        goal_lower = task.goal.lower()
        if "print" in goal_lower or "prints" in goal_lower:
            testing_hint = (
                "Testing hint: if the goal mentions printing, "
                "tests should capture stdout instead of expecting return values. "
                "In pytest you can use the capsys fixture.\n"
            )
        return (
            "Workspace root: "
            f"{workspace}\n"
            f"Task id: {task.task_id}\n"
            f"Title: {task.title}\n"
            f"Goal: {task.goal}\n"
            f"Constraints: {json.dumps(task.constraints)}\n"
            f"Context: {json.dumps(task.context)}\n"
            "Tool rules: paths and cwd must stay inside the workspace.\n"
            f"{testing_hint}"
        )

    def _model_turn(
        self,
        messages: List[Dict[str, str]],
        prompt: str,
        llm_log: JsonlLogger,
        last_feedback: str,
    ) -> Tuple[ActionResponse, Optional[str]]:
        turn_messages = list(messages)
        if last_feedback:
            turn_messages.append(
                {"role": "user", "content": f"Last feedback:\n{last_feedback}"}
            )
        turn_messages.append({"role": "user", "content": prompt})
        # _run_loop puts the full system instructions and task contract first;
        # later seed messages are optional retrieval/history context. Preserve
        # the current prompt and feedback intact as well.
        limit = self.max_input_chars
        omitted = 0
        optional_indices = [index for index in range(2, len(messages))
                            if turn_messages[index].get("role") != "system"]
        while len(json.dumps(turn_messages, ensure_ascii=False)) > limit and optional_indices:
            del turn_messages[optional_indices.pop()]
            omitted += 1
        input_chars = len(json.dumps(turn_messages, ensure_ascii=False))
        llm_log.log({"input_chars": input_chars, "max_input_chars": limit,
                     "omitted_optional_messages": omitted,
                     "max_output_tokens": getattr(self, "max_output_tokens", 2048)})
        if input_chars > limit:
            raise ValueError("Model input budget cannot fit the task, policies, prompt, and feedback; reduce optional task context or increase max_input_chars")
        llm_log.log({"request": turn_messages})

        # Use streaming when TUI is available for real-time display
        response_text = ""
        try:
            result = ollama_chat(
                self.model,
                turn_messages,
                options={"num_predict": getattr(self, "max_output_tokens", 2048)},
                base_url=self.base_url,
                timeout=self.timeout,
                stream=bool(self.tui),
                on_token=None,
            )
            if isinstance(result, str):
                response_text = result
            elif result is not None:
                # Streaming iterator — consume it
                response_text = ""
                for chunk in result:
                    response_text += chunk
        except TypeError:
            # Fallback if streaming not supported
            response_text = ollama_chat(
                self.model,
                turn_messages,
                options={"num_predict": getattr(self, "max_output_tokens", 2048)},
                base_url=self.base_url,
                timeout=self.timeout,
            )
        except Exception as exc:
            # ConnectionError, Timeout, HTTPError, etc.
            # Let it propagate to the caller's except handler
            raise

        llm_log.log({"response": response_text})
        if self.verbose:
            self._print_verbose("LLM response", response_text)
        try:
            return parse_action_response(response_text), None
        except ValueError as exc:
            llm_log.log({"parse_error": str(exc)})
            return ActionResponse(thought="invalid_json", actions=[]), str(exc)

    def _execute_actions(
        self,
        action_response: ActionResponse,
        workspace: Path,
        actions_log: JsonlLogger,
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        results: List[Dict[str, Any]] = []
        touched: List[str] = []
        for action in action_response.actions:
            try:
                if action.tool in {"write_file", "run_cmd", "web_search"} and EmergencyStop(self.repo_root).active():
                    raise ValueError("Emergency stop is engaged; consequential action blocked")
                policy_error = self._policy_error(action)
                if policy_error:
                    raise ValueError(policy_error)
                authorization = self._authorize_action(action, workspace)
                if authorization:
                    raise ValueError(authorization)
                placeholder_error = self._validate_action_inputs(action)
                if placeholder_error:
                    raise ValueError(placeholder_error)
                if action.tool == "write_file":
                    result = fs_tools.write_file(
                        workspace, action.path, action.content
                    )
                    touched.append(action.path)
                    # Update knowledge graph with file changes
                    self._update_file_knowledge(action.path, action.content)
                elif action.tool == "read_file":
                    result = fs_tools.read_file(workspace, action.path)
                elif action.tool == "list_dir":
                    result = fs_tools.list_dir(workspace, action.path)
                elif action.tool == "search_in_files":
                    result = fs_tools.search_in_files(
                        workspace, action.path, action.query
                    )
                elif action.tool == "run_cmd":
                    result = self._run_command(action.cmd, action.cwd, workspace)
                elif action.tool == "web_search":
                    search_results = web_tools.web_search(action.query)
                    result = web_tools.format_search_results(search_results, action.query)
                else:
                    result = {"error": f"Unknown tool {action.tool}"}
            except Exception as exc:
                result = {"error": str(exc)}
            tool_input = action.model_dump()
            payload = {"tool": action.tool, "input": tool_input, "result": result}
            actions_log.log(payload)
            if action.tool == "web_search":
                SecurityMonitor(self.repo_root).record_event("network.search", {"query": action.query})
            elif action.tool in {"write_file", "run_cmd"}:
                SecurityMonitor(self.repo_root).record_event("sandbox.action", {"tool": action.tool})
            provenance = getattr(self, "provenance", None)
            if provenance:
                provenance.record("tool.action", payload, getattr(self, "_provenance_parent_id", None))
            self._tui_action(action.tool, tool_input, result)
            if self.verbose:
                self._print_verbose(
                    f"Tool {action.tool}",
                    json.dumps(payload, indent=2, ensure_ascii=True),
                )
            results.append(result)
        return results, touched

    def _authorize_action(self, action: Any, workspace: Path) -> Optional[str]:
        """Apply the capability kernel before every concrete tool call."""
        mapping = {
            "write_file": "filesystem.write",
            "read_file": "filesystem.read",
            "list_dir": "filesystem.list",
            "search_in_files": "filesystem.search",
            "run_cmd": "command.execute",
            "web_search": "network.search",
        }
        capability = mapping.get(action.tool)
        if not capability:
            return "Tool is not backed by a registered capability"
        if not hasattr(self, "kernel"):
            return "Capability denied: kernel is not initialized"
        raw = action.model_dump()
        proposal = ActionProposal(
            capability,
            {key: value for key, value in raw.items() if value is not None},
            target=str((workspace / getattr(action, "path", getattr(action, "cwd", "."))).resolve()),
            reason=f"Model requested {action.tool}",
        )
        decision = self.kernel.authorize(proposal, workspace)
        if decision.decision == "denied":
            return f"Capability denied: {decision.reason}"
        # Sandbox actions are registered as reversible. Any future capability
        # marked consequential must stop here until the approval layer grants it.
        if decision.approval_required:
            return f"Capability requires explicit approval: {decision.reason}"
        return None

    def _policy_error(self, action: Any) -> Optional[str]:
        """Enforce explicit user prohibitions before an action reaches a tool."""
        try:
            rules = self.identity.load().prohibitions
        except Exception:
            return "User policy is unavailable or invalid; action blocked"
        action_text = {
            "write_file": "write file modify files",
            "read_file": "read file",
            "list_dir": "list files",
            "search_in_files": "search files",
            "run_cmd": "run command execute command",
            "web_search": "network web search",
        }.get(action.tool, action.tool)
        for rule in rules:
            normalized = rule.lower().replace("do not", "").strip()
            keywords = [
                word.rstrip("s") for word in normalized.replace("-", " ").split()
                if len(word.rstrip("s")) >= 4
            ]
            if keywords and any(word in action_text for word in keywords):
                return f"Blocked by user policy: {rule}"
        return None

    def _update_file_knowledge(self, rel_path: str, content: str) -> None:
        """Update the knowledge graph when a file is written."""
        if self.code_index:
            try:
                # Re-parse the file to update indices
                from .context import parse_file
                fpath = self.repo_root / rel_path
                if fpath.exists():
                    summary = parse_file(fpath, self.repo_root)
                    if summary:
                        self.code_index.summaries[rel_path] = summary
                        self.knowledge.update_file(
                            rel_path=rel_path,
                            functions=[f.name for f in summary.functions],
                            classes=[c.name for c in summary.classes],
                            imports=[],  # Simplified
                        )
            except Exception:
                pass

    def _has_errors(self, results: List[Dict[str, Any]]) -> bool:
        for result in results:
            if isinstance(result, dict):
                if "error" in result:
                    return True
                if "returncode" in result and not self._command_succeeded(result):
                    return True
        return False

    def _run_command(self, command: str, cwd: str, workspace: Path) -> Dict[str, Any]:
        budget = getattr(self, "_command_budget", None)
        def execute(timeout):
            return cmd_tools.run_cmd(command, cwd, root=workspace, timeout=timeout,
                should_stop=lambda: EmergencyStop(self.repo_root).active())
        if budget is None:
            return execute(30)
        with budget.slot() as timeout:
            return execute(timeout)

    def _format_results(self, results: List[Dict[str, Any]]) -> str:
        try:
            return json.dumps(results, indent=2)
        except TypeError:
            return str(results)

    def _merge_touched(self, existing: List[str], new: List[str]) -> List[str]:
        merged = list(existing)
        for path in new:
            if path not in merged:
                merged.append(path)
        return merged

    def _write_state(self, path: Path, state: Dict[str, Any], feedback: Optional[str] = None) -> None:
        if feedback is not None:
            state["last_feedback"] = redact_secrets(feedback)
        atomic_json(path, state)

    def _write_memory(self, task: Task, workspace: Path, state: Dict[str, Any],
                      strategy: str = "default") -> None:
        """Write a structured lesson to memory."""
        result = state.get("last_result", "unknown")
        success = result == "tests_passed"
        assessed = self.knowledge.is_assessed_result(result)

        what_worked = []
        what_failed = []

        if success:
            what_worked.append(f"Task completed with strategy: {strategy}")
            if state.get("files_touched"):
                what_worked.append(f"Modified {len(state['files_touched'])} files")
        elif assessed:
            what_failed.append(f"Task failed with result: {result}")
            if state.get("iteration", 0) >= 8:
                what_failed.append("Hit iteration limit")

        self.memory.write_structured_lesson(
            task_id=task.task_id,
            goal=task.goal,
            result=result,
            strategy=strategy,
            what_worked=what_worked,
            what_failed=what_failed,
            files_touched=state.get("files_touched", []),
            iterations=state.get("iteration", 0),
            key_insight=f"Strategy '{strategy}' {'succeeded' if success else 'failed' if assessed else 'was not assessed'} "
                       f"after {state.get('iteration', 0)} iterations",
        )
        self.provenance.record("outcome.completed", {
            "task_id": task.task_id,
            "result": result,
            "strategy": strategy,
            "files_touched": state.get("files_touched", []),
            "iterations": state.get("iteration", 0),
        }, self._provenance_parent_id)

    def _build_repair_prompt(self, state: Dict[str, Any], last_feedback: str) -> str:
        last_result = state.get("last_result", "")
        feedback_lower = last_feedback.lower()

        if "invalid json" in feedback_lower or "parse error" in feedback_lower:
            return (
                "Your last response was not valid JSON. "
                "You MUST output ONLY a raw JSON object — no markdown fences, "
                "no text before or after. Just { ... }. "
                "Try again now."
            )
        elif "plan_missing" in last_result:
            return (
                "The plan was not created. Output exactly ONE JSON object with exactly ONE "
                "write_file action to create plan.md containing the implementation plan."
            )
        elif "required_files_missing" in last_result:
            return (
                f"{last_feedback}\nOutput JSON actions that write the exact missing files. "
                "Use relative paths and do not substitute different filenames."
            )
        elif "tests_missing" in last_result:
            return (
                "The goal requires tests and none were written in this run. Output JSON actions "
                "that write focused coverage for the behavior you implemented: pytest for Python, "
                "or node:test in *.test.js/*.test.cjs/*.test.mjs for JavaScript."
            )
        elif (
            "tests_failed" in last_result
            or "implement_failed" in last_result
            or "verification_missing" in last_result
        ):
            return (
                f"Previous step failed with errors:\n{last_feedback}\n\n"
                "Fix the issues listed above. Use tools to update files, "
                "then let VERIFY re-run the tests."
            )
        else:
            return (
                f"Fix the issues from the last step:\n{last_feedback}\n\n"
                "Use tools to update files."
            )

    def _validate_action_inputs(self, action: Any) -> Optional[str]:
        def is_placeholder(value: Optional[str]) -> bool:
            if value is None:
                return True
            stripped = value.strip()
            return stripped == "" or stripped == "..."

        if action.tool == "write_file":
            if is_placeholder(action.path):
                return "write_file path is missing or placeholder"
            if Path(action.path).is_absolute():
                return "write_file path must be relative to the sandbox workspace"
            lower_path = action.path.replace("\\", "/").lower()
            if lower_path.endswith("pytest.ini"):
                if not action.content.lstrip().lower().startswith("[pytest]"):
                    return "pytest.ini must start with [pytest]"
            if self._is_python_test(lower_path):
                content = action.content or ""
                if "def test_" not in content:
                    return "test file must include pytest-style test functions"
                if "unittest" in content:
                    return "test file must use pytest, not unittest"
                if self._goal_prints:
                    if (
                        "capsys" not in content
                        and "capfd" not in content
                        and "subprocess" not in content
                    ):
                        return "tests must capture stdout (capsys/capfd or subprocess)"
        if action.tool == "read_file":
            if is_placeholder(action.path):
                return "read_file path is missing or placeholder"
            if Path(action.path).is_absolute():
                return "read_file path must be relative to the sandbox workspace"
        if action.tool == "list_dir":
            if is_placeholder(action.path):
                return "list_dir path is missing or placeholder"
            if Path(action.path).is_absolute():
                return "list_dir path must be relative to the sandbox workspace"
        if action.tool == "search_in_files":
            if is_placeholder(action.path) or is_placeholder(action.query):
                return "search_in_files path/query is missing or placeholder"
            if Path(action.path).is_absolute():
                return "search_in_files path must be relative to the sandbox workspace"
        if action.tool == "run_cmd":
            if is_placeholder(action.cmd) or is_placeholder(action.cwd):
                return "run_cmd cmd/cwd is missing or placeholder"
        if action.tool == "web_search":
            if is_placeholder(action.query):
                return "web_search query is missing or placeholder"
        return None

    def _print_verbose(self, label: str, message: str, limit: int = 4000) -> None:
        trimmed = message
        if len(trimmed) > limit:
            trimmed = trimmed[:limit] + "\n...[truncated]"
        print(f"[ghost] {label}:\n{trimmed}\n")

    # ── TUI hooks ───────────────────────────────────────────────────────

    def _tui_phase(self, phase: str, iteration: int) -> None:
        if self.tui:
            try:
                self.tui.on_phase_change(phase, iteration)
            except Exception:
                pass

    def _tui_action(self, tool: str, tool_input: Dict[str, Any], result: Any) -> None:
        if self.tui:
            try:
                self.tui.on_action(tool, tool_input, result)
            except Exception:
                pass

    def _tui_llm(self, thought: str, valid: bool) -> None:
        if self.tui:
            try:
                self.tui.on_llm_response(thought, valid)
            except Exception:
                pass

    def _tui_file(self, path: str) -> None:
        if self.tui:
            try:
                self.tui.on_file_touched(path)
            except Exception:
                pass

    def _tui_complete(self, result: str) -> None:
        if self.tui:
            try:
                self.tui.on_complete(result)
            except Exception:
                pass
