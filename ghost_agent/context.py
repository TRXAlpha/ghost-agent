"""RAG Context Engine — AST parsing, TF-IDF index, search, and context building.

Parses Python files using the AST module to extract functions, classes, and imports.
Builds a TF-IDF index for retrieval. Injects focused context into LLM prompts.
"""
from __future__ import annotations

import ast
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


# ── Data classes ────────────────────────────────────────────────────────────


@dataclass
class FunctionInfo:
    name: str
    args: List[str]
    docstring: Optional[str] = None


@dataclass
class ClassInfo:
    name: str
    methods: List[FunctionInfo] = field(default_factory=list)
    docstring: Optional[str] = None


@dataclass
class ImportInfo:
    module: str
    names: List[str] = field(default_factory=list)
    is_from: bool = False


@dataclass
class FileSummary:
    rel_path: str
    line_count: int
    functions: List[FunctionInfo] = field(default_factory=list)
    classes: List[ClassInfo] = field(default_factory=list)
    imports: List[ImportInfo] = field(default_factory=list)
    top_level_docstring: Optional[str] = None
    tokens: Set[str] = field(default_factory=set)


@dataclass
class SearchResult:
    file_path: str
    score: float
    match_type: str
    snippet: str


# ── Tokenization ────────────────────────────────────────────────────────────


_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "in", "on", "at", "to", "of", "for",
    "and", "or", "but", "not", "with", "as", "by", "if", "it",
    "this", "that", "from", "be", "are", "was", "were", "has", "have",
    "do", "does", "did", "will", "would", "could", "should", "may",
    "might", "can", "shall", "no", "so", "up", "out", "all", "its",
    "my", "me", "we", "he", "she", "they", "them", "his", "her",
})


def _tokenize(text: str) -> Set[str]:
    """Tokenize text into lowercase words, filtering stop words and short tokens."""
    tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", text.lower())
    return {t for t in tokens if len(t) > 2 and t not in _STOP_WORDS}


# ── AST parsing ─────────────────────────────────────────────────────────────


def _extract_docstring(node: ast.AST) -> Optional[str]:
    """Extract docstring from an AST node."""
    return ast.get_docstring(node)


def _extract_args(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> List[str]:
    """Extract argument names from a function definition."""
    args = []
    for arg in func_node.args.args:
        args.append(arg.arg)
    if func_node.args.vararg:
        args.append(f"*{func_node.args.vararg.arg}")
    if func_node.args.kwarg:
        args.append(f"**{func_node.args.kwarg.arg}")
    return args


def parse_file(path: Path, root: Path) -> Optional[FileSummary]:
    """Parse a Python file using AST. Returns None on syntax errors."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return None

    rel = str(path.relative_to(root)) if root in path.parents else path.name
    line_count = len(source.splitlines())

    functions: List[FunctionInfo] = []
    classes: List[ClassInfo] = []
    imports: List[ImportInfo] = []

    top_docstring = _extract_docstring(tree)

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(FunctionInfo(
                name=node.name,
                args=_extract_args(node),
                docstring=_extract_docstring(node),
            ))
        elif isinstance(node, ast.ClassDef):
            methods = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(FunctionInfo(
                        name=item.name,
                        args=_extract_args(item),
                        docstring=_extract_docstring(item),
                    ))
            classes.append(ClassInfo(
                name=node.name,
                methods=methods,
                docstring=_extract_docstring(node),
            ))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(ImportInfo(
                    module=alias.name,
                    names=[alias.asname or alias.name],
                ))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = [alias.name for alias in node.names]
            imports.append(ImportInfo(
                module=module,
                names=names,
                is_from=True,
            ))

    tokens = _tokenize(source)

    return FileSummary(
        rel_path=rel,
        line_count=line_count,
        functions=functions,
        classes=classes,
        imports=imports,
        top_level_docstring=top_docstring,
        tokens=tokens,
    )


# ── Code Index ──────────────────────────────────────────────────────────────


class CodeIndex:
    """AST-based code index with TF-IDF search."""

    def __init__(self) -> None:
        self.summaries: Dict[str, FileSummary] = {}
        self._idf: Dict[str, float] = {}
        self._tf: Dict[str, Dict[str, float]] = {}

    _DEFAULT_EXCLUDE = {".venv", "__pycache__", ".git", "node_modules", ".ghost", "memories", "workspaces"}

    def build(self, root: Path, exclude_dirs: Optional[Set[str]] = None) -> None:
        """Build the index by walking the directory tree."""
        exclude = exclude_dirs if exclude_dirs is not None else self._DEFAULT_EXCLUDE
        self.summaries = {}
        root = Path(root).resolve()

        for path in root.rglob("*.py"):
            # Check if any parent dir is excluded
            rel_to_root = path.relative_to(root)
            parts = rel_to_root.parts
            if any(p in exclude for p in parts):
                continue

            summary = parse_file(path, root)
            if summary is not None:
                self.summaries[summary.rel_path] = summary

        self._build_tfidf()

    def _build_tfidf(self) -> None:
        """Build TF-IDF vectors from token sets."""
        n_docs = len(self.summaries)
        if n_docs == 0:
            return

        # Document frequency
        doc_freq: Counter[str] = Counter()
        for summary in self.summaries.values():
            for token in summary.tokens:
                doc_freq[token] += 1

        # IDF
        self._idf = {}
        for token, freq in doc_freq.items():
            self._idf[token] = math.log((n_docs + 1) / (freq + 1)) + 1

        # TF (normalized by doc length)
        self._tf = {}
        for path, summary in self.summaries.items():
            if not summary.tokens:
                self._tf[path] = {}
                continue
            tf_raw: Counter[str] = Counter()
            for token in summary.tokens:
                tf_raw[token] += 1
            max_tf = max(tf_raw.values()) if tf_raw else 1
            self._tf[path] = {t: c / max_tf for t, c in tf_raw.items()}

    def search(self, query: str, limit: int = 10) -> List[SearchResult]:
        """Search the index using TF-IDF similarity."""
        query_tokens = _tokenize(query)
        if not query_tokens or not self._tf:
            return []

        scores: List[tuple[str, float, str]] = []

        for path, tf in self._tf.items():
            score = 0.0
            matched_tokens = []
            for qt in query_tokens:
                if qt in tf and qt in self._idf:
                    score += tf[qt] * self._idf[qt]
                    matched_tokens.append(qt)

            if score > 0:
                # Determine match type
                summary = self.summaries.get(path)
                match_type = "content"
                if summary:
                    # Check if query matches function/class names
                    all_names = (
                        [f.name for f in summary.functions]
                        + [c.name for c in summary.classes]
                    )
                    if any(qt in name.lower() for name in all_names for qt in query_tokens):
                        match_type = "name"
                    elif summary.top_level_docstring and any(
                        qt in summary.top_level_docstring.lower() for qt in query_tokens
                    ):
                        match_type = "docstring"

                # Build snippet
                snippet = self._build_snippet(path, matched_tokens)

                scores.append((path, score, match_type, snippet))

        scores.sort(key=lambda x: x[1], reverse=True)

        results = []
        for path, score, match_type, snippet in scores[:limit]:
            results.append(SearchResult(
                file_path=path,
                score=score,
                match_type=match_type,
                snippet=snippet,
            ))
        return results

    def _build_snippet(self, path: str, tokens: List[str]) -> str:
        """Build a short snippet showing matched context."""
        summary = self.summaries.get(path)
        if not summary:
            return ""

        lines = []
        if summary.top_level_docstring:
            lines.append(summary.top_level_docstring.split("\n")[0])
        for func in summary.functions[:3]:
            doc = func.docstring.split("\n")[0] if func.docstring else ""
            lines.append(f"def {func.name}({', '.join(func.args[:3])}): {doc}")
        for cls in summary.classes[:2]:
            doc = cls.docstring.split("\n")[0] if cls.docstring else ""
            lines.append(f"class {cls.name}: {doc}")
            for method in cls.methods[:3]:
                lines.append(f"  def {method.name}(...)")
        return "\n".join(lines[:6])

    def get_class(self, name: str) -> Optional[ClassInfo]:
        """Look up a class by name across all indexed files."""
        for summary in self.summaries.values():
            for cls in summary.classes:
                if cls.name == name:
                    return cls
        return None

    def get_function(self, name: str) -> Optional[FunctionInfo]:
        """Look up a function by name across all indexed files."""
        for summary in self.summaries.values():
            for func in summary.functions:
                if func.name == name:
                    return func
        return None

    def stats(self) -> Dict[str, Any]:
        """Get index statistics."""
        total_lines = sum(s.line_count for s in self.summaries.values())
        total_functions = sum(len(s.functions) for s in self.summaries.values())
        total_classes = sum(len(s.classes) for s in self.summaries.values())
        all_tokens: Set[str] = set()
        for s in self.summaries.values():
            all_tokens.update(s.tokens)
        return {
            "files": len(self.summaries),
            "total_lines": total_lines,
            "functions": total_functions,
            "classes": total_classes,
            "unique_tokens": len(all_tokens),
        }

    def build_context(self, query: str, max_tokens: int = 2000) -> str:
        """Build a focused context string for the LLM from search results."""
        results = self.search(query, limit=5)
        if not results:
            return "No relevant code found."

        parts = []
        total_chars = 0
        char_budget = max_tokens * 4  # rough estimate: 1 token ≈ 4 chars

        for result in results:
            summary = self.summaries.get(result.file_path)
            if not summary:
                continue

            header = f"--- {result.file_path} (match: {result.match_type}, score: {result.score:.2f}) ---"
            if total_chars + len(header) > char_budget:
                break

            parts.append(header)
            total_chars += len(header)

            # Add the snippet
            if result.snippet:
                parts.append(result.snippet)
                total_chars += len(result.snippet)

            # Add key function/class signatures
            sigs = []
            for func in summary.functions:
                sigs.append(f"def {func.name}({', '.join(func.args)})")
            for cls in summary.classes:
                methods = ", ".join(m.name for m in cls.methods[:5])
                sigs.append(f"class {cls.name}({methods})")

            if sigs:
                sig_text = "\n".join(sigs)
                if total_chars + len(sig_text) > char_budget:
                    break
                parts.append(sig_text)
                total_chars += len(sig_text)

            parts.append("")
            total_chars += 1

        return "\n".join(parts)
