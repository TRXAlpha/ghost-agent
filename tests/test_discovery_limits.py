import os
from types import SimpleNamespace

import pytest

from ghost_agent.test_discovery import discover_test_files
from ghost_agent.orchestrator import Orchestrator, Task


def test_nested_discovery_is_sorted_and_excludes_dependencies(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "z_test.py").write_text("")
    (tmp_path / "a.test.cjs").write_text("")
    (tmp_path / "test_notes.md").write_text("")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "hidden.test.js").write_text("")
    assert discover_test_files(tmp_path) == ["a.test.cjs", "tests/z_test.py"]


def test_budget_counts_ignored_files_too(tmp_path):
    (tmp_path / "test_a.py").write_text("")
    (tmp_path / "notes.txt").write_text("")
    with pytest.raises(ValueError, match="budget exceeded"):
        discover_test_files(tmp_path, max_entries=1)
    assert discover_test_files(tmp_path, max_entries=2) == ["test_a.py"]


def test_unreadable_subtree_does_not_produce_partial_verifier(tmp_path, monkeypatch):
    import ghost_agent.test_discovery as discovery
    (tmp_path / "test_a.py").write_text("")
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    scandir = os.scandir

    def denied(path):
        if path == hidden:
            raise PermissionError("fixture denies subtree")
        return scandir(path)

    monkeypatch.setattr(discovery.os, "scandir", denied)
    with pytest.raises(PermissionError):
        discover_test_files(tmp_path)
    assert Orchestrator._verification_command(Task("t", "title", "goal", {}, {}), tmp_path) is None


def test_reparse_entry_is_not_traversed(tmp_path, monkeypatch):
    import ghost_agent.test_discovery as discovery
    import stat

    class Entries:
        def __enter__(self):
            return iter([SimpleNamespace(name="linked", path=str(tmp_path / "linked"),
                stat=lambda **kwargs: SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400))])
        def __exit__(self, *args):
            pass

    calls = []
    def scan(path):
        calls.append(path)
        return Entries()
    monkeypatch.setattr(discovery.os, "scandir", scan)
    assert discover_test_files(tmp_path) == []
    assert calls == [tmp_path]


@pytest.mark.parametrize("limit", [0, -1, True])
def test_invalid_entry_limit(tmp_path, limit):
    with pytest.raises(ValueError):
        discover_test_files(tmp_path, limit)
