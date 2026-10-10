"""Durable event cursors and a failed-event queue over `.ghost/events.jsonl`.

Each cursor tracks a byte offset into the append-only event log so consumers
can read only new events and resume after restart, without a competing
control plane: the cursor is plain state under `.ghost/events/cursors/`.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import fields
from pathlib import Path

from .runtime import Event
from .utils.storage import atomic_json, atomic_write

_NAME_RE = re.compile(r"^[a-z0-9_-]{1,40}$")
_FAILED_LIMIT = 1000


class EventCursor:
    def __init__(self, project_root: Path, name: str) -> None:
        if not _NAME_RE.fullmatch(name):
            raise ValueError("invalid cursor name")
        self.root = Path(project_root)
        self.name = name
        self.cursor_path = self.root / ".ghost" / "events" / "cursors" / f"{name}.json"
        self.events_path = self.root / ".ghost" / "events.jsonl"
        self.failed_path = self.root / ".ghost" / "events" / "failed.jsonl"

    def _stored_offset(self) -> int:
        if not self.cursor_path.exists():
            return 0
        try:
            data = json.loads(self.cursor_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return 0
        offset = data.get("offset", 0)
        return offset if isinstance(offset, int) and offset > 0 else 0

    def read_new(self, max_events: int = 100) -> list[tuple[int, Event]]:
        offset = self._stored_offset()
        if not self.events_path.exists():
            return []
        known = {f.name for f in fields(Event)}
        results: list[tuple[int, Event]] = []
        failed: list[str] = []
        with self.events_path.open("rb") as stream:
            size = stream.seek(0, 2)
            if offset > size:
                # Log was replaced (e.g. rotation); start over from the beginning.
                offset = 0
            stream.seek(offset)
            pos = offset
            while len(results) < max_events:
                line = stream.readline()
                if not line:
                    break
                if not line.endswith(b"\n"):
                    # Incomplete trailing write: leave it for the next read.
                    break
                # Buffered stream tell() reports buffer position, not consumed
                # bytes, so track offsets by line length.
                end_offset = pos + len(line)
                pos = end_offset
                text = line.decode("utf-8", errors="replace").strip()
                try:
                    raw = json.loads(text)
                    data = {key: value for key, value in raw.items() if key in known}
                    if "schema_version" not in data:
                        data["schema_version"] = 1
                    event = Event(**data)
                except (json.JSONDecodeError, TypeError) as exc:
                    failed.append(json.dumps({
                        "offset": offset, "line": text[:2000],
                        "error": str(exc), "at": time.time(),
                    }))
                else:
                    results.append((end_offset, event))
                offset = end_offset
        if failed:
            self._append_failed(failed)
        return results

    def _append_failed(self, lines: list[str]) -> None:
        self.failed_path.parent.mkdir(parents=True, exist_ok=True)
        existing: list[str] = []
        if self.failed_path.exists():
            existing = self.failed_path.read_text(encoding="utf-8").splitlines()
        combined = existing + lines
        if len(combined) > _FAILED_LIMIT:
            combined = combined[-_FAILED_LIMIT:]
            atomic_write(self.failed_path, ("\n".join(combined) + "\n").encode("utf-8"))
        else:
            with self.failed_path.open("a", encoding="utf-8") as stream:
                for line in lines:
                    stream.write(line + "\n")

    def commit(self, offset: int) -> None:
        self.cursor_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(self.cursor_path, {"offset": offset, "updated_at": time.time()})
