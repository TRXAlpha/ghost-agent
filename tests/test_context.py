"""Tests for ghost_agent.context — the RAG engine."""
from pathlib import Path

from ghost_agent.context import (
    CodeIndex,
    FunctionInfo,
    ClassInfo,
    ImportInfo,
    FileSummary,
    parse_file,
    _tokenize,
)


def test_parse_python_file(tmp_path: Path) -> None:
    """Parse a simple Python file."""
    code = '''"""Module docstring."""

import os
from pathlib import Path

def greet(name: str) -> str:
    """Greet someone."""
    return f"Hello, {name}!"

class Calculator:
    """A simple calculator."""
    
    def add(self, a: int, b: int) -> int:
        return a + b
    
    def subtract(self, a: int, b: int) -> int:
        return a - b
'''
    fpath = tmp_path / "test_module.py"
    fpath.write_text(code)
    
    summary = parse_file(fpath, tmp_path)
    assert summary is not None
    assert summary.rel_path == "test_module.py"
    assert summary.line_count > 0
    assert len(summary.functions) == 1
    assert summary.functions[0].name == "greet"
    assert "name" in summary.functions[0].args
    assert summary.functions[0].docstring == "Greet someone."
    assert len(summary.classes) == 1
    assert summary.classes[0].name == "Calculator"
    assert len(summary.classes[0].methods) == 2
    assert summary.classes[0].methods[0].name == "add"
    assert summary.top_level_docstring == "Module docstring."
    assert len(summary.imports) == 2


def test_parse_syntax_error(tmp_path: Path) -> None:
    """Files with syntax errors return None."""
    fpath = tmp_path / "bad.py"
    fpath.write_text("def foo(\n  broken")
    summary = parse_file(fpath, tmp_path)
    assert summary is None


def test_code_index_build(tmp_path: Path) -> None:
    """Build an index over multiple files."""
    # Create files
    (tmp_path / "a.py").write_text("def helper(): pass")
    (tmp_path / "b.py").write_text("class Foo: pass")
    (tmp_path / "c.txt").write_text("not python")
    
    index = CodeIndex()
    index.build(tmp_path)
    
    assert len(index.summaries) == 2  # Only .py files
    assert "a.py" in index.summaries
    assert "b.py" in index.summaries
    assert "c.txt" not in index.summaries


def test_code_index_search(tmp_path: Path) -> None:
    """Search the index for relevant files."""
    (tmp_path / "auth.py").write_text('''
def authenticate(user: str, password: str) -> bool:
    """Authenticate a user."""
    return user == "admin"

def login(username: str) -> None:
    """Login handler."""
    pass
''')
    (tmp_path / "utils.py").write_text('''
def format_date(date) -> str:
    return str(date)
''')
    
    index = CodeIndex()
    index.build(tmp_path)
    
    results = index.search("authentication login")
    assert len(results) > 0
    assert results[0].file_path == "auth.py"


def test_code_index_get_class(tmp_path: Path) -> None:
    """Look up a class by name."""
    (tmp_path / "models.py").write_text('''
class User:
    def __init__(self, name: str):
        self.name = name
    
    def greet(self) -> str:
        return f"Hello, {self.name}"
''')
    
    index = CodeIndex()
    index.build(tmp_path)
    
    cls = index.get_class("User")
    assert cls is not None
    assert cls.name == "User"
    assert len(cls.methods) == 2


def test_code_index_get_function(tmp_path: Path) -> None:
    """Look up a function by name."""
    (tmp_path / "helpers.py").write_text('''
def compute_score(data: list) -> float:
    """Compute a score from data."""
    return sum(data) / len(data)
''')
    
    index = CodeIndex()
    index.build(tmp_path)
    
    func = index.get_function("compute_score")
    assert func is not None
    assert func.name == "compute_score"
    assert func.docstring == "Compute a score from data."


def test_code_index_stats(tmp_path: Path) -> None:
    """Get index statistics."""
    (tmp_path / "a.py").write_text("def foo(): pass\ndef bar(): pass")
    (tmp_path / "b.py").write_text("class X: pass")
    
    index = CodeIndex()
    index.build(tmp_path)
    
    stats = index.stats()
    assert stats["files"] == 2
    assert stats["functions"] == 2
    assert stats["classes"] == 1
    assert stats["total_lines"] > 0


def test_code_index_build_context(tmp_path: Path) -> None:
    """Build a context string for the LLM."""
    (tmp_path / "auth.py").write_text('''
def authenticate(user: str, password: str) -> bool:
    """Authenticate a user."""
    return True
''')
    
    index = CodeIndex()
    index.build(tmp_path)
    
    context = index.build_context("authenticate user", max_tokens=1000)
    assert "auth.py" in context
    assert "authenticate" in context


def test_tokenize() -> None:
    """Basic tokenization."""
    tokens = _tokenize("Hello World! This is a test_function.")
    assert "hello" in tokens
    assert "world" in tokens
    assert "test_function" in tokens
    assert "is" not in tokens  # too short


def test_code_index_exclude_dirs(tmp_path: Path) -> None:
    """Exclude directories from indexing."""
    (tmp_path / "main.py").write_text("x = 1")
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "lib.py").write_text("y = 2")
    pycache = tmp_path / "__pycache__"
    pycache.mkdir()
    (pycache / "cached.py").write_text("z = 3")
    
    index = CodeIndex()
    index.build(tmp_path)
    
    assert len(index.summaries) == 1
    assert "main.py" in index.summaries
