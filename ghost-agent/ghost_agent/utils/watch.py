from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable, List, Set


class FileWatcher:
    def __init__(self, root: Path, ignore_dirs: Iterable[str]) -> None:
        self.root = Path(root).resolve()
        self.ignore_dirs: Set[str] = set(ignore_dirs)
        self._snapshot: Dict[str, float] = self._scan()

    def poll(self, max_changes: int = 25) -> List[str]:
        current = self._scan()
        changes: List[str] = []

        for path, mtime in current.items():
            if path not in self._snapshot:
                changes.append(f"added: {path}")
            elif mtime > self._snapshot[path]:
                changes.append(f"modified: {path}")

        for path in self._snapshot:
            if path not in current:
                changes.append(f"deleted: {path}")

        self._snapshot = current
        return changes[:max_changes]

    def _scan(self) -> Dict[str, float]:
        snapshot: Dict[str, float] = {}
        for root, dirs, files in os.walk(self.root):
            dirs[:] = [
                d for d in dirs if d not in self.ignore_dirs and not d.startswith(".")
            ]
            for name in files:
                path = Path(root) / name
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                rel = str(path.relative_to(self.root))
                snapshot[rel] = mtime
        return snapshot
