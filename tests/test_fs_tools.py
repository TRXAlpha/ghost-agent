from pathlib import Path

import pytest

from ghost_agent.tools.fs_tools import FsError, safe_path


def test_safe_path_allows_relative(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    result = safe_path(root, "notes.txt")
    assert result.is_relative_to(root)


def test_safe_path_blocks_escape(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    with pytest.raises(FsError):
        safe_path(root, "../outside.txt")
