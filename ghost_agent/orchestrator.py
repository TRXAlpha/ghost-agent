from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from .memory.store import MemoryStore
from .models.ollama import ollama_chat
from .schema import ActionResponse, parse_action_response
from .tools import cmd_tools, fs_tools
from .utils.logging import JsonlLogger


SYSTEM_PROMPT = """You are Ghost, a coding agent.
You MUST respond with ONLY valid JSON matching this schema:
{
  "thought": "string",
  "actions": [
    { "tool": "write_file", "path": "...", "content": "..." },
    { "tool": "read_file", "path": "..." },
    { "tool": "list_dir", "path": "..." },
    { "tool": "search_in_files", "path": "...", "query": "..." },
    { "tool": "run_cmd", "cmd": "...", "cwd": "..." }
  ]
}
No prose, no markdown, no extra keys. Do not use code fences.
If no actions are needed, return {"thought": "...", "actions": []}.
Do not use placeholder values like "...". Provide real paths, commands, and queries.
Use pytest-style tests unless the task explicitly asks for another framework.
Do not create or modify pytest.ini unless the task explicitly requests it.
Respect tool rules: stay inside the workspace root and use only allowed commands.
"""


@dataclass
class Task:
    task_id: str
    title: str
    goal: str
    constraints: Dict[str, Any]
    context: Dict[str, Any]


class Orchestrator:
    def __init__(
        self,
        repo_root: Path,
        model: str = "vibethinker:1.5b",
        base_url: Optional[str] = None,
        timeout: int = 60,
        verbose: bool = False,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.workspaces_root = self.repo_root / "workspaces"
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self.verbose = verbose
        self.memory = MemoryStore(self.repo_root / "memories")
        self._goal_prints = False

    def run_task(self, task_path: Path) -> None:
        task = self._load_task(task_path)
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
        artifacts = self._init_artifacts_root(task, artifacts_root)
        state = {
            "phase": "INGEST",
            "iteration": 0,
            "last_result": None,
            "open_items": [],
            "files_touched": [],
        }
        self._run_loop(task, workspace_root, state, artifacts_root=artifacts)

    def resume_task(self, task_id: str) -> None:
        workspace = self.workspaces_root / task_id
        task_path = workspace / "task.json"
        state_path = workspace / "state.json"
        if not task_path.exists():
            raise FileNotFoundError(f"task.json not found for {task_id}")
        task = self._load_task(task_path)
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
        else:
            state = {
                "phase": "INGEST",
                "iteration": 0,
                "last_result": None,
                "open_items": [],
                "files_touched": [],
            }
        self._run_loop(task, workspace, state)

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
        workspace = self.workspaces_root / task.task_id
        if workspace.exists():
            shutil.rmtree(workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "task.json").write_text(
            task_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        (workspace / "notes.md").write_text("", encoding="utf-8")
        return workspace

    def _init_artifacts_root(self, task: Task, artifacts_root: Path) -> Path:
        artifacts_root = Path(artifacts_root)
        if artifacts_root.exists():
            shutil.rmtree(artifacts_root)
        artifacts_root.mkdir(parents=True, exist_ok=True)
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
        iteration_limit = (
            task.constraints.get("iteration_limit")
            or task.constraints.get("time_budget_iters")
            or 8
        )
        goal_lower = task.goal.lower()
        self._goal_prints = "print" in goal_lower or "prints" in goal_lower
        artifacts_root = artifacts_root or workspace
        actions_log = JsonlLogger(artifacts_root / "actions.log")
        llm_log = JsonlLogger(artifacts_root / "llm.log")
        plan_path = artifacts_root / "plan.md"
        state_path = artifacts_root / "state.json"

        messages: List[Dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": self._build_context(task, workspace)},
        ]
        memory_notes = self.memory.retrieve(
            f"{task.title} {task.goal} {task.context.get('notes', '')}"
        )
        if memory_notes:
            messages.append(
                {
                    "role": "user",
                    "content": "Relevant memories:\n" + "\n\n".join(memory_notes),
                }
            )

        last_feedback = ""
        while True:
            if state["iteration"] >= iteration_limit:
                state["phase"] = "DONE"
                state["last_result"] = "iteration_limit"
                self._write_state(state_path, state)
                self._write_memory(task, workspace, state)
                break

            phase = state["phase"]
            if phase == "INGEST":
                state["phase"] = "PLAN"
                self._write_state(state_path, state)
                continue

            if phase == "PLAN":
                prompt = (
                    "Create a short plan with steps and a verification strategy, "
                    "then write it to plan.md using write_file."
                )
                action_response, parse_error = self._model_turn(
                    messages, prompt, llm_log, last_feedback
                )
                results, touched = self._execute_actions(
                    action_response, workspace, actions_log
                )
                if parse_error:
                    results.append({"error": parse_error})
                state["files_touched"] = self._merge_touched(
                    state["files_touched"], touched
                )
                if self._has_errors(results):
                    state["phase"] = "REPAIR"
                    state["last_result"] = "plan_failed"
                    last_feedback = self._format_results(results)
                else:
                    if not plan_path.exists():
                        state["phase"] = "REPAIR"
                        state["last_result"] = "plan_missing"
                        last_feedback = "plan.md was not created."
                    else:
                        state["phase"] = "IMPLEMENT"
                        state["last_result"] = "plan_ok"
                state["iteration"] += 1
                self._write_state(state_path, state)
                continue

            if phase == "IMPLEMENT":
                prompt = "Implement the task. Use tools to read/write files as needed."
                action_response, parse_error = self._model_turn(
                    messages, prompt, llm_log, last_feedback
                )
                results, touched = self._execute_actions(
                    action_response, workspace, actions_log
                )
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
                state["iteration"] += 1
                self._write_state(state_path, state)
                continue

            if phase == "VERIFY":
                test_cmd = task.constraints.get("test_cmd")
                if test_cmd:
                    try:
                        result = cmd_tools.run_cmd(
                            test_cmd, cwd=".", root=workspace
                        )
                    except Exception as exc:
                        result = {"returncode": 1, "output": str(exc)}
                    actions_log.log(
                        {"tool": "run_cmd", "cmd": test_cmd, "result": result}
                    )
                    if result.get("returncode") == 0:
                        state["phase"] = "DONE"
                        state["last_result"] = "tests_passed"
                        last_feedback = self._format_results([result])
                    else:
                        state["phase"] = "REPAIR"
                        state["last_result"] = "tests_failed"
                        last_feedback = self._format_results([result])
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
                    state["phase"] = "DONE"
                    state["last_result"] = "no_test_cmd"
                state["iteration"] += 1
                self._write_state(state_path, state)
                continue

            if phase == "REPAIR":
                prompt = (
                    "Fix the issues from the last step. Use tools to update files."
                )
                action_response, parse_error = self._model_turn(
                    messages, prompt, llm_log, last_feedback
                )
                results, touched = self._execute_actions(
                    action_response, workspace, actions_log
                )
                if parse_error:
                    results.append({"error": parse_error})
                state["files_touched"] = self._merge_touched(
                    state["files_touched"], touched
                )
                if self._has_errors(results):
                    state["phase"] = "REPAIR"
                    state["last_result"] = "repair_failed"
                    last_feedback = self._format_results(results)
                else:
                    state["phase"] = "VERIFY"
                    state["last_result"] = "repair_ok"
                    last_feedback = self._format_results(results)
                state["iteration"] += 1
                self._write_state(state_path, state)
                continue

            if phase == "DONE":
                self._write_memory(task, workspace, state)
                self._write_state(state_path, state)
                break

            state["phase"] = "DONE"
            state["last_result"] = "unknown_phase"
            self._write_state(state_path, state)
            break

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
    ) -> tuple[ActionResponse, Optional[str]]:
        turn_messages = list(messages)
        if last_feedback:
            turn_messages.append(
                {"role": "user", "content": f"Last feedback:\n{last_feedback}"}
            )
        turn_messages.append({"role": "user", "content": prompt})
        llm_log.log({"request": turn_messages})
        response_text = ollama_chat(
            self.model,
            turn_messages,
            base_url=self.base_url,
            timeout=self.timeout,
        )
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
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        results: List[Dict[str, Any]] = []
        touched: List[str] = []
        for action in action_response.actions:
            try:
                placeholder_error = self._validate_action_inputs(action)
                if placeholder_error:
                    raise ValueError(placeholder_error)
                if action.tool == "write_file":
                    result = fs_tools.write_file(
                        workspace, action.path, action.content
                    )
                    touched.append(action.path)
                elif action.tool == "read_file":
                    result = fs_tools.read_file(workspace, action.path)
                elif action.tool == "list_dir":
                    result = fs_tools.list_dir(workspace, action.path)
                elif action.tool == "search_in_files":
                    result = fs_tools.search_in_files(
                        workspace, action.path, action.query
                    )
                elif action.tool == "run_cmd":
                    result = cmd_tools.run_cmd(
                        action.cmd, action.cwd, root=workspace
                    )
                else:
                    result = {"error": f"Unknown tool {action.tool}"}
            except Exception as exc:
                result = {"error": str(exc)}
            payload = {"tool": action.tool, "input": action.model_dump(), "result": result}
            actions_log.log(payload)
            if self.verbose:
                self._print_verbose(
                    f"Tool {action.tool}",
                    json.dumps(payload, indent=2, ensure_ascii=True),
                )
            results.append(result)
        return results, touched

    def _has_errors(self, results: List[Dict[str, Any]]) -> bool:
        for result in results:
            if isinstance(result, dict) and "error" in result:
                return True
        return False

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

    def _write_state(self, path: Path, state: Dict[str, Any]) -> None:
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def _write_memory(self, task: Task, workspace: Path, state: Dict[str, Any]) -> None:
        lesson = (
            f"Task {task.task_id} completed with result {state.get('last_result')}.\n"
            f"Files touched: {', '.join(state.get('files_touched', []))}\n"
            f"Workspace: {workspace}\n"
        )
        metadata = {
            "type": "lesson",
            "tags": ["ghost", "task"],
            "confidence": 0.5,
            "created": date.today().isoformat(),
        }
        self.memory.write_lesson(task.task_id, lesson, metadata)

    def _validate_action_inputs(self, action: Any) -> Optional[str]:
        def is_placeholder(value: Optional[str]) -> bool:
            if value is None:
                return True
            stripped = value.strip()
            return stripped == "" or stripped == "..."

        if action.tool == "write_file":
            if is_placeholder(action.path):
                return "write_file path is missing or placeholder"
            lower_path = action.path.replace("\\", "/").lower()
            if lower_path.endswith("pytest.ini"):
                if not action.content.lstrip().lower().startswith("[pytest]"):
                    return "pytest.ini must start with [pytest]"
            if "/test_" in lower_path or lower_path.startswith("test_"):
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
        if action.tool == "list_dir":
            if is_placeholder(action.path):
                return "list_dir path is missing or placeholder"
        if action.tool == "search_in_files":
            if is_placeholder(action.path) or is_placeholder(action.query):
                return "search_in_files path/query is missing or placeholder"
        if action.tool == "run_cmd":
            if is_placeholder(action.cmd) or is_placeholder(action.cwd):
                return "run_cmd cmd/cwd is missing or placeholder"
        return None

    def _print_verbose(self, label: str, message: str, limit: int = 4000) -> None:
        trimmed = message
        if len(trimmed) > limit:
            trimmed = trimmed[:limit] + "\n...[truncated]"
        print(f"[ghost] {label}:\n{trimmed}\n")
