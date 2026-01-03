from __future__ import annotations

from pathlib import Path

from .cmd_tools import run_cmd


def git_status(root: Path) -> dict:
    return run_cmd("git status -sb", cwd=str(root), root=root)


def git_diff(root: Path) -> dict:
    return run_cmd("git diff", cwd=str(root), root=root)
