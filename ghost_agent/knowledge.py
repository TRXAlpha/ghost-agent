"""Knowledge graph for decision-making and self-improvement.

Tracks:
- File relationships (who imports whom, who calls whom)
- Task outcomes (what strategy worked for what type of task)
- Error patterns and their fixes
- Strategy effectiveness over time

The orchestrator queries this before every decision. Over time,
Ghost gets smarter about YOUR codebase.
"""
from __future__ import annotations

import json
import hashlib
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .utils.storage import atomic_write, exclusive_lock


# ── Node types ───────────────────────────────────────────────────────────────

@dataclass
class FileNode:
    path: str
    imports: List[str] = field(default_factory=list)
    imported_by: List[str] = field(default_factory=list)
    functions: List[str] = field(default_factory=list)
    classes: List[str] = field(default_factory=list)
    last_modified: float = 0.0
    modification_count: int = 0


@dataclass
class TaskRecord:
    task_id: str
    goal: str
    result: str
    strategy: str  # what approach was used
    files_touched: List[str]
    iterations: int
    parse_errors: int
    timestamp: float
    tags: List[str] = field(default_factory=list)
    success: bool = False


@dataclass
class ErrorPattern:
    error_type: str  # e.g. "syntax_error", "import_error", "test_failure"
    error_message: str
    fix_applied: str
    count: int = 1
    last_seen: float = 0.0
    success_rate: float = 0.0  # how often this fix worked


@dataclass
class StrategyEffectiveness:
    strategy: str
    task_type: str  # e.g. "refactor", "bug_fix", "new_feature", "test"
    success_count: int = 0
    failure_count: int = 0
    avg_iterations: float = 0.0
    last_used: float = 0.0


# ── Knowledge Graph ──────────────────────────────────────────────────────────

class KnowledgeGraph:
    """Persistent knowledge graph that tracks codebase relationships
    and learning from past runs.

    Stored as JSON in .ghost/knowledge.json. Loaded lazily, saved
    after every meaningful update.
    """

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.graph_path = self.base_dir / "knowledge.json"

        self.files: Dict[str, FileNode] = {}
        self.tasks: List[TaskRecord] = []
        self.error_patterns: List[ErrorPattern] = []
        self.strategies: Dict[str, StrategyEffectiveness] = {}
        self._loaded_hash = None
        self._scored_records = set()
        self._load()

    # ── Persistence ──────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            raw = self.graph_path.read_bytes()
        except FileNotFoundError:
            return
        try:
            data = json.loads(raw.decode("utf-8"))
            if (not isinstance(data, dict)
                    or set(data) - {"files", "tasks", "error_patterns", "strategies"}
                    or any(not isinstance(data.get(key, {}), dict) for key in ("files", "strategies"))
                    or any(not isinstance(data.get(key, []), list) for key in ("tasks", "error_patterns"))):
                raise ValueError("Invalid knowledge record containers")
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Knowledge storage is corrupt; preserve it for review") from exc
        self._loaded_hash = hashlib.sha256(raw).hexdigest()

        for path, fdata in data.get("files", {}).items():
            self.files[path] = FileNode(
                path=path,
                imports=fdata.get("imports", []),
                imported_by=fdata.get("imported_by", []),
                functions=fdata.get("functions", []),
                classes=fdata.get("classes", []),
                last_modified=fdata.get("last_modified", 0),
                modification_count=fdata.get("modification_count", 0),
            )

        for tdata in data.get("tasks", []):
            if not isinstance(tdata, dict) or "task_id" not in tdata:
                raise ValueError("Invalid knowledge task record")
            self.tasks.append(TaskRecord(
                task_id=tdata["task_id"],
                goal=tdata.get("goal", ""),
                result=tdata.get("result", ""),
                strategy=tdata.get("strategy", ""),
                files_touched=tdata.get("files_touched", []),
                iterations=tdata.get("iterations", 0),
                parse_errors=tdata.get("parse_errors", 0),
                timestamp=tdata.get("timestamp", 0),
                tags=tdata.get("tags", []),
                success=tdata.get("result") == "tests_passed",
            ))
            self._validate_task(self.tasks[-1])

        for edata in data.get("error_patterns", []):
            if not isinstance(edata, dict):
                raise ValueError("Invalid repair-pattern record")
            self.error_patterns.append(ErrorPattern(
                error_type=edata["error_type"],
                error_message=edata["error_message"],
                fix_applied=edata["fix_applied"],
                count=edata.get("count", 1),
                last_seen=edata.get("last_seen", 0),
                success_rate=edata.get("success_rate", 0),
            ))
            self._validate_error_pattern(self.error_patterns[-1])

        # Legacy aggregates may credit intermediate steps or penalize stops.
        # Rebuild in memory from retained outcome records, without writing on read.
        for record in self.tasks:
            self._update_strategy(record)

    def save(self) -> None:
        data = {
            "files": {},
            "tasks": [],
            "error_patterns": [],
            "strategies": {},
        }

        for path, fnode in self.files.items():
            data["files"][path] = {
                "imports": fnode.imports,
                "imported_by": fnode.imported_by,
                "functions": fnode.functions,
                "classes": fnode.classes,
                "last_modified": fnode.last_modified,
                "modification_count": fnode.modification_count,
            }

        for task in self.tasks:
            data["tasks"].append({
                "task_id": task.task_id,
                "goal": task.goal,
                "result": task.result,
                "strategy": task.strategy,
                "files_touched": task.files_touched,
                "iterations": task.iterations,
                "parse_errors": task.parse_errors,
                "timestamp": task.timestamp,
                "tags": task.tags,
                "success": task.success,
            })

        for ep in self.error_patterns:
            data["error_patterns"].append({
                "error_type": ep.error_type,
                "error_message": ep.error_message,
                "fix_applied": ep.fix_applied,
                "count": ep.count,
                "last_seen": ep.last_seen,
                "success_rate": ep.success_rate,
            })

        for skey, sval in self.strategies.items():
            data["strategies"][skey] = {
                "strategy": sval.strategy,
                "task_type": sval.task_type,
                "success_count": sval.success_count,
                "failure_count": sval.failure_count,
                "avg_iterations": sval.avg_iterations,
                "last_used": sval.last_used,
            }

        raw = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8")
        with exclusive_lock(self.graph_path.with_suffix(".lock")):
            try:
                current = hashlib.sha256(self.graph_path.read_bytes()).hexdigest()
            except FileNotFoundError:
                current = None
            if current != self._loaded_hash:
                raise ValueError("Knowledge storage changed since load; reload before writing")
            atomic_write(self.graph_path, raw)
            self._loaded_hash = hashlib.sha256(raw).hexdigest()

    # ── File graph operations ────────────────────────────────────────────

    def update_file(self, rel_path: str, functions: List[str],
                    classes: List[str], imports: List[str]) -> None:
        """Update file node in the graph."""
        now = time.time()
        if rel_path in self.files:
            node = self.files[rel_path]
            node.functions = functions
            node.classes = classes
            node.last_modified = now
            node.modification_count += 1
        else:
            self.files[rel_path] = FileNode(
                path=rel_path,
                functions=functions,
                classes=classes,
                last_modified=now,
                modification_count=1,
            )

        # Update import edges
        node = self.files[rel_path]
        old_imports = set(node.imports)
        new_imports = set(imports)
        node.imports = list(new_imports)

        # Update reverse edges
        for added in new_imports - old_imports:
            if added in self.files:
                if rel_path not in self.files[added].imported_by:
                    self.files[added].imported_by.append(rel_path)
        for removed in old_imports - new_imports:
            if removed in self.files:
                if rel_path in self.files[removed].imported_by:
                    self.files[removed].imported_by.remove(rel_path)

    def build_from_index(self, code_index: Any) -> None:
        """Build file graph from a CodeIndex instance."""
        from .context import CodeIndex
        assert isinstance(code_index, CodeIndex)
        for rel_path, summary in code_index.summaries.items():
            imports = []
            for imp in summary.imports:
                # Try to resolve to local file
                for other_path in code_index.summaries:
                    other_stem = Path(other_path).stem
                    if other_stem in imp.names or imp.module.endswith(other_stem):
                        imports.append(other_path)
            self.update_file(
                rel_path=rel_path,
                functions=[f.name for f in summary.functions],
                classes=[c.name for c in summary.classes],
                imports=imports,
            )
        self.save()

    def get_file_deps(self, rel_path: str) -> Dict[str, List[str]]:
        """Get dependencies for a file."""
        node = self.files.get(rel_path)
        if not node:
            return {"imports": [], "imported_by": []}
        return {"imports": node.imports, "imported_by": node.imported_by}

    def get_change_impact(self, rel_path: str) -> List[str]:
        """Given a file change, return all files that might be affected."""
        affected = set()
        queue = [rel_path]
        visited = set()
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            node = self.files.get(current)
            if node:
                for dep in node.imported_by:
                    affected.add(dep)
                    queue.append(dep)
        affected.discard(rel_path)
        return sorted(affected)

    def get_hot_files(self, top_n: int = 10) -> List[Tuple[str, int]]:
        """Return the most frequently modified files."""
        ranked = sorted(
            self.files.values(),
            key=lambda f: f.modification_count,
            reverse=True,
        )
        return [(f.path, f.modification_count) for f in ranked[:top_n]]

    # ── Task learning ────────────────────────────────────────────────────

    def record_task(self, task_id: str, goal: str, result: str,
                    strategy: str, files_touched: List[str],
                    iterations: int, parse_errors: int,
                    tags: Optional[List[str]] = None) -> None:
        """Record a completed task for learning."""
        success = result == "tests_passed"
        record = TaskRecord(
            task_id=task_id,
            goal=goal,
            result=result,
            strategy=strategy,
            files_touched=files_touched,
            iterations=iterations,
            parse_errors=parse_errors,
            timestamp=time.time(),
            tags=[] if tags is None else tags,
            success=success,
        )
        self._validate_task(record)
        if any(self._task_key(previous) == self._task_key(record) for previous in self.tasks):
            self.save()  # Also retries a previous failed save without adding credit.
            return
        self.tasks.append(record)
        self._update_strategy(record)
        self.save()

    @staticmethod
    def is_assessed_result(result: str) -> bool:
        return result in {"tests_passed", "tests_failed", "focused_tests_failed",
                          "iteration_limit", "implement_failed", "plan_missing",
                          "required_files_missing", "tests_missing", "verification_missing"}

    @staticmethod
    def _validate_task(record: TaskRecord) -> None:
        if (any(not isinstance(value, str) or not value.strip()
                for value in (record.task_id, record.goal, record.result, record.strategy))
                or any(type(value) is not int or value < 0 for value in (record.iterations, record.parse_errors))
                or type(record.timestamp) not in (int, float) or not 0 <= record.timestamp < float("inf")
                or any(not isinstance(value, list) or any(not isinstance(item, str) for item in value)
                       for value in (record.files_touched, record.tags))):
            raise ValueError("Invalid knowledge task record")

    @staticmethod
    def _task_key(record: TaskRecord) -> str:
        data = asdict(record)
        data.pop("timestamp")
        data.pop("success")
        return json.dumps(data, sort_keys=True)

    def _update_strategy(self, record: TaskRecord) -> None:
        if not self.is_assessed_result(record.result):
            return
        key = self._task_key(record)
        if key in self._scored_records:
            return
        self._scored_records.add(key)
        goal, strategy, iterations = record.goal, record.strategy, record.iterations
        success = record.result == "tests_passed"

        # Update strategy effectiveness
        task_type = self._classify_task(goal)
        skey = f"{strategy}:{task_type}"
        if skey not in self.strategies:
            self.strategies[skey] = StrategyEffectiveness(
                strategy=strategy, task_type=task_type
            )
        s = self.strategies[skey]
        if success:
            s.success_count += 1
        else:
            s.failure_count += 1
        total = s.success_count + s.failure_count
        s.avg_iterations = (
            (s.avg_iterations * (total - 1) + iterations) / total
            if total > 0 else iterations
        )
        s.last_used = record.timestamp

    def record_error(self, error_type: str, error_message: str,
                     fix_applied: str, worked: bool) -> None:
        """Record an error pattern and whether the fix worked."""
        if type(worked) is not bool:
            raise ValueError("Repair result must be an explicit boolean")
        if any(not isinstance(value, str) or not value.strip()
               for value in (error_type, error_message, fix_applied)):
            raise ValueError("Repair evidence requires error type, message, and attempted fix")
        # Check if we've seen this pattern before
        for ep in self.error_patterns:
            if (ep.error_type == error_type and ep.error_message == error_message[:200]
                    and ep.fix_applied == fix_applied):
                ep.count += 1
                ep.last_seen = time.time()
                if worked:
                    # Increase success rate
                    ep.success_rate = (
                        ep.success_rate * 0.8 + 0.2
                    )
                else:
                    ep.success_rate = ep.success_rate * 0.8
                self.save()
                return

        # New pattern
        self.error_patterns.append(ErrorPattern(
            error_type=error_type,
            error_message=error_message[:200],
            fix_applied=fix_applied,
            count=1,
            last_seen=time.time(),
            success_rate=1.0 if worked else 0.0,
        ))
        self.save()

    @staticmethod
    def _validate_error_pattern(pattern: ErrorPattern) -> None:
        if (any(not isinstance(value, str) or not value.strip()
                for value in (pattern.error_type, pattern.error_message, pattern.fix_applied))
                or type(pattern.count) is not int or pattern.count < 1
                or type(pattern.last_seen) not in (int, float) or not 0 <= pattern.last_seen < float("inf")
                or type(pattern.success_rate) not in (int, float) or not 0 <= pattern.success_rate <= 1):
            raise ValueError("Invalid repair-pattern record")

    def get_suggested_fix(self, error_type: str,
                          error_message: str) -> Optional[str]:
        """Given an error, suggest a fix based on past experience."""
        best_match = None
        best_score = 0.0

        for ep in self.error_patterns:
            if ep.error_type != error_type:
                continue
            # Fuzzy match on error message
            overlap = self._message_overlap(ep.error_message, error_message)
            score = overlap * ep.success_rate * min(ep.count, 5) / 5
            if score > best_score:
                best_score = score
                best_match = ep.fix_applied

        if best_score > 0.3:
            return best_match
        return None

    def get_task_suggestions(self, goal: str) -> Dict[str, Any]:
        """Suggest strategy based on past similar tasks."""
        task_type = self._classify_task(goal)
        goal_lower = goal.lower()

        # Find relevant past tasks
        relevant = []
        seen = set()
        for task in self.tasks:
            if not self.is_assessed_result(task.result):
                continue
            key = self._task_key(task)
            if key in seen:
                continue
            seen.add(key)
            goal_words = set(goal_lower.split())
            past_words = set(task.goal.lower().split())
            overlap = len(goal_words & past_words)
            if overlap >= 2 or task_type == self._classify_task(task.goal):
                relevant.append(task)

        if not relevant:
            return {"task_type": task_type, "suggestion": "No past experience for this type."}

        # Find best strategy for this task type
        best_strategy = None
        best_rate = 0.0
        for skey, sval in self.strategies.items():
            if sval.task_type == task_type:
                total = sval.success_count + sval.failure_count
                if total >= 2:
                    rate = sval.success_count / total
                    if rate > best_rate:
                        best_rate = rate
                        best_strategy = sval.strategy

        # Find commonly touched files for this type
        file_freq: Dict[str, int] = {}
        for task in relevant:
            for f in task.files_touched:
                file_freq[f] = file_freq.get(f, 0) + 1
        common_files = sorted(file_freq.items(), key=lambda x: x[1], reverse=True)[:5]

        # Find average iterations
        avg_iters = sum(t.iterations for t in relevant) / len(relevant)

        return {
            "task_type": task_type,
            "similar_tasks": len(relevant),
            "success_rate": sum(1 for t in relevant if t.success) / len(relevant),
            "suggested_strategy": best_strategy,
            "strategy_confidence": best_rate,
            "common_files": [f for f, _ in common_files],
            "avg_iterations": round(avg_iters, 1),
        }

    # ── Strategy recommendations ─────────────────────────────────────────

    def get_strategy_report(self) -> str:
        """Generate a human-readable report of strategy effectiveness."""
        lines = ["Strategy Effectiveness Report:\n"]
        for skey, sval in sorted(self.strategies.items()):
            total = sval.success_count + sval.failure_count
            if total == 0:
                continue
            rate = sval.success_count / total * 100
            lines.append(
                f"  {sval.strategy:20s} | {sval.task_type:15s} | "
                f"{sval.success_count}/{total} ({rate:.0f}%) | "
                f"avg {sval.avg_iterations:.1f} iters"
            )

        if self.error_patterns:
            lines.append("\nTop Error Patterns:")
            top_errors = sorted(self.error_patterns, key=lambda e: e.count, reverse=True)[:10]
            for ep in top_errors:
                lines.append(
                    f"  [{ep.error_type:20s}] x{ep.count:3d} | "
                    f"fix success: {ep.success_rate:.0%} | "
                    f"{ep.fix_applied[:60]}"
                )

        hot = self.get_hot_files(5)
        if hot:
            lines.append("\nMost Modified Files:")
            for path, count in hot:
                lines.append(f"  {path}: {count} modifications")

        return "\n".join(lines)

    # ── Helpers ──────────────────────────────────────────────────────────

    def _classify_task(self, goal: str) -> str:
        """Classify a task goal into a category."""
        goal_lower = goal.lower()
        if any(w in goal_lower for w in ("fix", "bug", "error", "broken", "crash")):
            return "bug_fix"
        if any(w in goal_lower for w in ("document", "readme", "comment", "docs")):
            return "documentation"
        if any(w in goal_lower for w in ("refactor", "restructure", "reorganize", "clean")):
            return "refactor"
        if any(w in goal_lower for w in ("test", "spec", "coverage")):
            return "testing"
        if any(w in goal_lower for w in ("add", "create", "implement", "new", "feature")):
            return "new_feature"
        if any(w in goal_lower for w in ("update", "modify", "change", "edit")):
            return "modification"
        return "general"

    def _message_overlap(self, a: str, b: str) -> float:
        """Compute word-level overlap between two error messages."""
        words_a = set(a.lower().split())
        words_b = set(b.lower().split())
        if not words_a or not words_b:
            return 0.0
        intersection = words_a & words_b
        return len(intersection) / max(len(words_a), len(words_b))
