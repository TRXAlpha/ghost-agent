from __future__ import annotations

from pathlib import Path
from typing import List


class FsError(ValueError):
    pass


def safe_path(root: Path, path_str: str) -> Path:
    root = Path(root).resolve()
    raw = Path(path_str)
    if raw.is_absolute():
        candidate = raw
    else:
        candidate = root / raw
    candidate = candidate.resolve()
    if not candidate.is_relative_to(root):
        raise FsError(f"Path escapes workspace: {path_str}")
    return candidate


def read_file(root: Path, path_str: str) -> str:
    path = safe_path(root, path_str)
    return path.read_text(encoding="utf-8", errors="replace")


def write_file(root: Path, path_str: str, content: str) -> str:
    path = safe_path(root, path_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"wrote {path}"


def list_dir(root: Path, path_str: str) -> List[str]:
    path = safe_path(root, path_str)
    if not path.exists():
        raise FsError(f"Path not found: {path}")
    entries = []
    for entry in sorted(path.iterdir()):
        suffix = "/" if entry.is_dir() else ""
        entries.append(f"{entry.name}{suffix}")
    return entries


def search_in_files(root: Path, path_str: str, query: str) -> List[dict]:
    base = safe_path(root, path_str)
    if not base.exists():
        raise FsError(f"Path not found: {base}")

    matches = []
    for file_path in base.rglob("*"):
        if not file_path.is_file():
            continue
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for idx, line in enumerate(text.splitlines(), start=1):
            if query in line:
                matches.append(
                    {
                        "path": str(file_path.relative_to(root)),
                        "line": idx,
                        "text": line.strip(),
                    }
                )
    return matches
