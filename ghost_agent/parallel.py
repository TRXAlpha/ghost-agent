"""Parallel sub-agent system for ghost-agent.

Decomposes complex tasks into independent sub-tasks that can be
executed concurrently by separate orchestrator instances. Results
are merged when all sub-agents complete.

This is what lets Ghost do heavy research: break a complex task
like "add auth to the API with tests and docs" into independent
sub-tasks that run in parallel.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class SubTask:
    """A sub-task to be executed by a parallel agent."""
    id: str
    goal: str
    depends_on: List[str] = field(default_factory=list)  # sub-task IDs this depends on
    files_to_touch: List[str] = field(default_factory=list)
    context: str = ""  # additional context for this sub-task
    result: Optional[str] = None
    files_changed: List[str] = field(default_factory=list)
    iterations: int = 0
    success: bool = False
    error: Optional[str] = None


@dataclass
class Decomposition:
    """A decomposed plan with sub-tasks and their dependency graph."""
    original_goal: str
    sub_tasks: List[SubTask]
    merge_strategy: str = "sequential"  # "sequential", "parallel", "hybrid"


class TaskDecomposer:
    """Decomposes complex goals into independent sub-tasks.

    Uses heuristics and past knowledge to split tasks into pieces
    that can run in parallel (no dependencies between them).
    """

    @staticmethod
    def decompose(goal: str, codebase_context: str = "",
                  knowledge_suggestions: Optional[Dict[str, Any]] = None) -> Decomposition:
        """Decompose a complex goal into sub-tasks.

        Returns a Decomposition with sub-tasks ordered by dependency.
        Independent sub-tasks can be run in parallel.
        """
        goal_lower = goal.lower()

        # Detect if this is actually a complex multi-part task
        parts = TaskDecomposer._extract_parts(goal)

        if len(parts) <= 1:
            # Single task, no decomposition needed
            return Decomposition(
                original_goal=goal,
                sub_tasks=[SubTask(id="main", goal=goal)],
                merge_strategy="sequential",
            )

        # Create sub-tasks from extracted parts
        sub_tasks = []
        for i, part in enumerate(parts):
            st = SubTask(
                id=f"sub_{i:03d}",
                goal=part,
            )
            sub_tasks.append(st)

        # Analyze dependencies between sub-tasks
        TaskDecomposer._resolve_dependencies(sub_tasks)

        # Determine merge strategy
        has_deps = any(st.depends_on for st in sub_tasks)
        if has_deps:
            merge_strategy = "hybrid"
        else:
            merge_strategy = "parallel"

        return Decomposition(
            original_goal=goal,
            sub_tasks=sub_tasks,
            merge_strategy=merge_strategy,
        )

    @staticmethod
    def _extract_parts(goal: str) -> List[str]:
        """Extract independent parts from a complex goal.

        Heuristics:
        - Split on "and", "then", numbered lists
        - Split on semicolons
        - Look for multiple action verbs
        """
        parts = []

        # Check for numbered lists: "1. do X\n2. do Y"
        numbered = re.findall(r"(?:^|\n)\s*\d+[\.\)]\s*(.+?)(?=\n\s*\d+[\.\)]|\Z)", goal, re.DOTALL)
        if len(numbered) >= 2:
            return [p.strip() for p in numbered if p.strip()]

        # Split on semicolons (at least 2 parts needed)
        if ";" in goal:
            parts = [p.strip() for p in goal.split(";") if p.strip()]
            if len(parts) >= 2:
                return parts

        # Split on " and " with action verbs
        conjunctions = re.split(r"\s+and\s+", goal, flags=re.IGNORECASE)
        if len(conjunctions) >= 2:
            # Only split if each part has a verb
            action_verbs = {"add", "create", "implement", "fix", "update",
                          "write", "build", "refactor", "test", "add", "remove",
                          "delete", "change", "modify", "improve", "optimize",
                          "document", "setup", "configure", "migrate"}
            verb_parts = []
            for part in conjunctions:
                words = set(part.lower().split())
                if words & action_verbs:
                    verb_parts.append(part.strip())
            if len(verb_parts) >= 2:
                return verb_parts

        # Single task
        return [goal]

    @staticmethod
    def _resolve_dependencies(sub_tasks: List[SubTask]) -> None:
        """Analyze sub-tasks and add dependency edges.

        Rules:
        - "test" tasks depend on "implement" tasks
        - "document" tasks depend on implementation tasks
        - "fix" tasks depend on "test" tasks that found the bug
        """
        task_types = {}
        for st in sub_tasks:
            goal_lower = st.goal.lower()
            if any(w in goal_lower for w in ("test", "spec", "coverage")):
                task_types[st.id] = "test"
            elif any(w in goal_lower for w in ("document", "readme", "comment")):
                task_types[st.id] = "document"
            elif any(w in goal_lower for w in ("implement", "create", "add", "build", "write")):
                task_types[st.id] = "implement"
            elif any(w in goal_lower for w in ("fix", "bug", "error")):
                task_types[st.id] = "fix"
            else:
                task_types[st.id] = "other"

        # Test tasks depend on implement tasks
        for st in sub_tasks:
            if task_types.get(st.id) == "test":
                for other in sub_tasks:
                    if task_types.get(other.id) == "implement" and other.id != st.id:
                        st.depends_on.append(other.id)

        # Document tasks depend on implement tasks
        for st in sub_tasks:
            if task_types.get(st.id) == "document":
                for other in sub_tasks:
                    if task_types.get(other.id) == "implement" and other.id != st.id:
                        st.depends_on.append(other.id)


class ParallelRunner:
    """Runs sub-tasks in parallel using ThreadPoolExecutor.

    Each sub-task gets its own workspace directory and runs through
    the orchestrator independently. Results are collected and merged.
    """

    def __init__(self, orchestrator_factory, max_workers: int = 3) -> None:
        """
        Args:
            orchestrator_factory: Callable that creates an Orchestrator instance
                                 for a given workspace path.
            max_workers: Maximum parallel sub-agents.
        """
        self.orchestrator_factory = orchestrator_factory
        self.max_workers = max_workers

    def run_decomposition(self, decomposition: Decomposition,
                         base_workspace: Path,
                         knowledge_graph: Optional[Any] = None) -> Dict[str, SubTask]:
        """Execute a decomposition, running independent tasks in parallel.

        Returns a dict of sub_task_id -> completed SubTask with results.
        """
        completed: Dict[str, SubTask] = {}
        pending = list(decomposition.sub_tasks)
        max_rounds = 10  # safety limit

        for _ in range(max_rounds):
            if not pending:
                break

            # Find tasks whose dependencies are all met
            ready = []
            still_pending = []
            for st in pending:
                deps_met = all(d in completed for d in st.depends_on)
                if deps_met:
                    ready.append(st)
                else:
                    still_pending.append(st)

            if not ready:
                # Deadlock or all remaining depend on failed tasks
                for st in still_pending:
                    st.error = "Dependency not met"
                    st.success = False
                    completed[st.id] = st
                break

            # Run ready tasks in parallel
            if len(ready) == 1:
                result = self._run_single(ready[0], base_workspace, knowledge_graph)
                completed[result.id] = result
            else:
                with ThreadPoolExecutor(max_workers=min(self.max_workers, len(ready))) as executor:
                    futures = {
                        executor.submit(
                            self._run_single, st, base_workspace, knowledge_graph
                        ): st.id
                        for st in ready
                    }
                    for future in as_completed(futures):
                        result = future.result()
                        completed[result.id] = result

            pending = still_pending

        return completed

    def _run_single(self, sub_task: SubTask, base_workspace: Path,
                    knowledge_graph: Optional[Any] = None) -> SubTask:
        """Run a single sub-task through the orchestrator."""
        workspace = base_workspace / f"_parallel_{sub_task.id}"
        workspace.mkdir(parents=True, exist_ok=True)

        # Write sub-task JSON
        task_data = {
            "id": f"parallel_{sub_task.id}_{uuid.uuid4().hex[:8]}",
            "title": sub_task.goal[:50],
            "goal": sub_task.goal,
            "constraints": {
                "iteration_limit": 6,
                "test_cmd": "pytest -q",
            },
            "context": {
                "parallel_subtask": True,
                "parent_files": sub_task.files_to_touch,
                "context_info": sub_task.context,
            },
        }
        task_path = workspace / "task.json"
        task_path.write_text(json.dumps(task_data, indent=2), encoding="utf-8")

        try:
            orchestrator = self.orchestrator_factory(workspace)
            orchestrator.run_task(task_path)

            # Read back results
            state_path = workspace / "state.json"
            if state_path.exists():
                state = json.loads(state_path.read_text(encoding="utf-8"))
                sub_task.result = state.get("last_result", "unknown")
                sub_task.files_changed = state.get("files_touched", [])
                sub_task.iterations = state.get("iteration", 0)
                sub_task.success = state.get("last_result") in (
                    "tests_passed", "no_test_cmd", "plan_ok"
                )
            else:
                sub_task.result = "no_state"
                sub_task.success = False

        except Exception as exc:
            sub_task.error = str(exc)
            sub_task.success = False
            sub_task.result = "exception"

        return sub_task

    @staticmethod
    def format_results(completed: Dict[str, SubTask]) -> str:
        """Format parallel execution results as a summary."""
        lines = ["Parallel Execution Results:\n"]

        total = len(completed)
        successes = sum(1 for st in completed.values() if st.success)
        lines.append(f"  {successes}/{total} sub-tasks succeeded\n")

        for st_id, st in completed.items():
            status = "[OK]" if st.success else "[FAIL]"
            files = len(st.files_changed)
            lines.append(
                f"  {status} {st_id}: {st.goal[:60]}\n"
                f"         result={st.result}, files={files}, iters={st.iterations}"
            )
            if st.error:
                lines.append(f"         error: {st.error[:80]}")

        return "\n".join(lines)
