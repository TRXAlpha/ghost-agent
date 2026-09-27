"""Memory store with RAG-backed retrieval.

Upgraded from simple keyword matching to structured lessons with
relevance scoring. Works with the CodeIndex for code-aware retrieval
and the KnowledgeGraph for decision-informed retrieval.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


class MemoryStore:
    """Persistent memory with structured lessons and relevance-based retrieval.

    Stores:
    - lessons: Structured outcomes from past tasks
    - facts: Codebase facts (config, patterns, conventions)
    - snippets: Reusable code patterns found in the project

    Retrieval uses TF-IDF-like scoring with optional context from
    the CodeIndex and KnowledgeGraph.
    """

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.base_dir / "index.json"
        self._ensure_folders()
        self._code_index = None
        self._knowledge_graph = None

    def _ensure_folders(self) -> None:
        for folder in ("facts", "lessons", "snippets", "projects"):
            (self.base_dir / folder).mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self.index_path.write_text("{}", encoding="utf-8")

    def set_code_index(self, code_index: Any) -> None:
        """Attach a CodeIndex for code-aware retrieval."""
        self._code_index = code_index

    def set_knowledge_graph(self, graph: Any) -> None:
        """Attach a KnowledgeGraph for decision-informed retrieval."""
        self._knowledge_graph = graph

    def retrieve(self, query: str, limit: int = 5,
                 use_code_context: bool = True,
                 use_knowledge: bool = True) -> List[str]:
        """Retrieve relevant memories for a query.

        Combines:
        1. Keyword-based retrieval from stored notes
        2. Code context from the CodeIndex (if available)
        3. Task suggestions from the KnowledgeGraph (if available)
        """
        results = []

        # 1. Keyword retrieval from memory notes
        keyword_results = self._keyword_retrieve(query, limit=limit)
        results.extend(keyword_results)

        # 2. Code-aware context
        if use_code_context and self._code_index is not None:
            code_context = self._code_index.build_context(query, max_tokens=1500)
            if code_context and "No relevant code" not in code_context:
                results.append(f"--- Codebase Context ---\n{code_context}")

        # 3. Knowledge graph suggestions
        if use_knowledge and self._knowledge_graph is not None:
            suggestions = self._knowledge_graph.get_task_suggestions(query)
            if suggestions.get("similar_tasks", 0) > 0:
                sug_text = (
                    f"--- Past Experience ---\n"
                    f"Similar tasks: {suggestions['similar_tasks']}\n"
                    f"Success rate: {suggestions.get('success_rate', 0):.0%}\n"
                )
                if suggestions.get("suggested_strategy"):
                    sug_text += f"Suggested strategy: {suggestions['suggested_strategy']}\n"
                if suggestions.get("common_files"):
                    sug_text += f"Commonly involved files: {', '.join(suggestions['common_files'])}\n"
                if suggestions.get("avg_iterations"):
                    sug_text += f"Average iterations: {suggestions['avg_iterations']}\n"
                results.append(sug_text)

            # Check for error pattern suggestions
            if self._knowledge_graph.error_patterns:
                error_suggestions = self._get_error_suggestions(query)
                if error_suggestions:
                    results.append(error_suggestions)

        return results

    # Tokens so common across lessons that they carry no discriminating
    # signal. Matching on these alone caused retrieval to inject unrelated
    # past-task goals into prompts (small models then imitated the wrong
    # goal), so they are excluded from scoring entirely.
    _STOP_TOKENS = frozenset({
        "the", "a", "an", "and", "or", "to", "for", "of", "in", "on", "with",
        "is", "are", "was", "be", "it", "this", "that", "as", "at", "by",
        "task", "tasks", "test", "tests", "create", "created", "file",
        "files", "write", "written", "run", "runs", "add", "added", "use",
        "using", "focused", "minimal", "keep", "must", "should", "goal",
        "result", "strategy", "iteration", "iterations", "plan", "notes",
        "none", "noted", "failed", "success", "work", "worked", "insight",
        "key", "touched", "structured", "lesson", "ghost", "limited",
    })

    def _keyword_retrieve(self, query: str, limit: int = 3) -> List[str]:
        """Keyword retrieval with IDF weighting and a relevance threshold.

        Raw token-overlap scoring ranked lessons by generic tokens shared by
        every lesson (metadata words such as "task" or "test"), so unrelated
        past goals were injected into new prompts. Score each indexed note by
        the sum of IDF weights of its shared tokens with the query, drop
        stop/common tokens, and return nothing when no note shares a
        sufficiently distinctive token. Empty results are correct behavior:
        injecting an unrelated lesson is worse than no memory at all.
        """
        index = self._load_index()
        total_docs = len({path for paths in index.values() for path in paths})
        if total_docs == 0 or not index:
            return []
        tokens = {token for token in self._tokenize(query) if len(token) >= 3}
        tokens -= self._STOP_TOKENS
        scores: Dict[str, float] = {}
        for token in tokens:
            paths = index.get(token)
            if not paths:
                continue
            idf = 1.0 + (total_docs / len(paths))
            for path in paths:
                scores[path] = scores.get(path, 0.0) + idf
        # Require at least one distinctive shared token (idf weight > 1 means
        # the token appears in fewer than all documents).
        ranked = sorted(
            ((path, score) for path, score in scores.items() if score > 1.0),
            key=lambda item: item[1], reverse=True,
        )
        note_results = []
        for path, _score in ranked[:limit]:
            note_path = self.base_dir / path
            if note_path.exists():
                note_results.append(note_path.read_text(encoding="utf-8"))
        return note_results

    def _get_error_suggestions(self, query: str) -> str:
        """Get error pattern suggestions from knowledge graph."""
        if not self._knowledge_graph:
            return ""
        kg = self._knowledge_graph
        error_types = ["syntax_error", "import_error", "test_failure",
                      "type_error", "attribute_error", "json_error"]
        suggestions = []
        for etype in error_types:
            if etype.replace("_", " ") in query.lower() or etype in query.lower():
                # Find matching error patterns
                for ep in kg.error_patterns:
                    if ep.error_type == etype and ep.success_rate > 0.5:
                        suggestions.append(
                            f"Past fix for {etype}: {ep.fix_applied} "
                            f"(worked {ep.success_rate:.0%} of {ep.count} times)"
                        )
        if suggestions:
            return "--- Error Pattern Suggestions ---\n" + "\n".join(suggestions[:3])
        return ""

    # ── Structured lesson storage ────────────────────────────────────────

    def write_lesson(self, task_id: str, content: str,
                     metadata: Dict[str, object],
                     structured: bool = False) -> Path:
        """Write a lesson, optionally with structured fields."""
        filename = f"{task_id}.md"
        path = self.base_dir / "lessons" / filename

        if structured:
            note = self._format_structured_note(metadata, content)
        else:
            note = self._format_note(metadata, content)

        path.write_text(note, encoding="utf-8")
        self._update_index(path, note)
        return path

    def write_structured_lesson(
        self,
        task_id: str,
        goal: str,
        result: str,
        strategy: str,
        what_worked: List[str],
        what_failed: List[str],
        files_touched: List[str],
        iterations: int,
        key_insight: str = "",
    ) -> Path:
        """Write a richly structured lesson for future retrieval."""
        content = f"""## Goal
{goal}

## Result
{result} ({"SUCCESS" if result in ("tests_passed", "no_test_cmd") else "FAILED"})

## Strategy
{strategy}

## What Worked
{chr(10).join(f"- {w}" for w in what_worked) if what_worked else "- (none noted)"}

## What Failed
{chr(10).join(f"- {f}" for f in what_failed) if what_failed else "- (none noted)"}

## Files Touched
{', '.join(files_touched) if files_touched else '(none)'}

## Iterations
{iterations}

## Key Insight
{key_insight or '(none)'}
"""
        metadata = {
            "type": "structured_lesson",
            "tags": ["ghost", "task", result],
            "confidence": 0.7,
            "created": time.strftime("%Y-%m-%d"),
            "task_id": task_id,
            "result": result,
            "strategy": strategy,
        }
        return self.write_lesson(task_id, content, metadata, structured=True)

    def _format_structured_note(self, metadata: Dict[str, object],
                                 content: str) -> str:
        """Format a note with structured metadata."""
        header = "---\n"
        for key, value in metadata.items():
            header += f"{key}: {json.dumps(value)}\n"
        header += "---\n\n"
        return header + content.strip() + "\n"

    def _format_note(self, metadata: Dict[str, object], content: str) -> str:
        header = "---\n"
        for key, value in metadata.items():
            header += f"{key}: {json.dumps(value)}\n"
        header += "---\n\n"
        return header + content.strip() + "\n"

    # ── Index management ─────────────────────────────────────────────────

    def _load_index(self) -> Dict[str, List[str]]:
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _update_index(self, path: Path, content: str) -> None:
        index = self._load_index()
        tokens = self._tokenize(content)
        rel_path = str(path.relative_to(self.base_dir))
        for token in tokens:
            items = index.setdefault(token, [])
            if rel_path not in items:
                items.append(rel_path)
        self.index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")

    def _tokenize(self, text: str) -> Iterable[str]:
        return [token for token in re.findall(r"[a-z0-9]+", text.lower()) if token]

    # ── Utility ──────────────────────────────────────────────────────────

    def list_lessons(self, limit: int = 10) -> List[Dict[str, Any]]:
        """List recent lessons with metadata."""
        lessons_dir = self.base_dir / "lessons"
        lessons = []
        for path in sorted(lessons_dir.glob("*.md"), reverse=True)[:limit]:
            content = path.read_text(encoding="utf-8")
            # Extract YAML frontmatter
            meta = {}
            if content.startswith("---"):
                end = content.find("---", 3)
                if end > 0:
                    frontmatter = content[3:end].strip()
                    for line in frontmatter.split("\n"):
                        if ":" in line:
                            key, _, value = line.partition(":")
                            try:
                                meta[key.strip()] = json.loads(value.strip())
                            except (json.JSONDecodeError, ValueError):
                                meta[key.strip()] = value.strip()
            meta["file"] = path.name
            meta["preview"] = content[:200].replace("\n", " ")
            lessons.append(meta)
        return lessons

    def stats(self) -> Dict[str, int]:
        """Return memory statistics."""
        lessons_dir = self.base_dir / "lessons"
        facts_dir = self.base_dir / "facts"
        snippets_dir = self.base_dir / "snippets"
        return {
            "lessons": len(list(lessons_dir.glob("*.md"))) if lessons_dir.exists() else 0,
            "facts": len(list(facts_dir.glob("*.md"))) if facts_dir.exists() else 0,
            "snippets": len(list(snippets_dir.glob("*.md"))) if snippets_dir.exists() else 0,
        }
