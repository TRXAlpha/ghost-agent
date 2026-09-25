"""Bounded local test discovery. An incomplete scan is not a usable test list."""
from __future__ import annotations

import os
from pathlib import Path
import stat


EXCLUDED = {"node_modules", ".venv", ".ghost", ".git", "__pycache__"}


def discover_test_files(root: Path, max_entries: int = 10000) -> list[str]:
    if type(max_entries) is not int or max_entries < 1:
        raise ValueError("Discovery entry budget must be a positive integer")
    root = Path(root)
    metadata = root.lstat()
    if (not stat.S_ISDIR(metadata.st_mode)
            or getattr(metadata, "st_file_attributes", 0) & 0x400):
        raise ValueError("Test discovery requires a real workspace directory")
    pending = [root]
    found = []
    scanned = 0
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                scanned += 1
                if scanned > max_entries:
                    raise ValueError("Test discovery budget exceeded; specify an acceptance command")
                metadata = entry.stat(follow_symlinks=False)
                if (stat.S_ISLNK(metadata.st_mode)
                        or getattr(metadata, "st_file_attributes", 0) & 0x400):
                    continue  # Includes Windows junctions and other reparse points.
                if stat.S_ISDIR(metadata.st_mode):
                    if entry.name not in EXCLUDED:
                        pending.append(Path(entry.path))
                elif stat.S_ISREG(metadata.st_mode):
                    name = entry.name
                    if ((name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py")))
                            or name.endswith((".test.js", ".test.cjs", ".test.mjs"))):
                        found.append(Path(entry.path).relative_to(root).as_posix())
    return sorted(found)
