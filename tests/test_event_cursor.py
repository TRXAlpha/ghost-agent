"""Tests for event cursors and the failed-event queue."""
from __future__ import annotations

import json

from ghost_agent.event_cursor import EventCursor
from ghost_agent.runtime import Event, EventBus


def _emit(root, n: int, kind: str = "tick") -> None:
    bus = EventBus(root)
    for i in range(n):
        bus.emit(Event(kind=kind, payload={"i": i}))


def test_resume_after_commit_returns_only_new_events(tmp_path):
    _emit(tmp_path, 3)
    cursor = EventCursor(tmp_path, "test")
    first = cursor.read_new()
    assert len(first) == 3
    cursor.commit(first[-1][0])
    _emit(tmp_path, 2, kind="tick2")
    resumed = cursor.read_new()
    assert len(resumed) == 2
    assert resumed[0][1].kind == "tick2"


def test_incomplete_trailing_line_not_consumed(tmp_path):
    _emit(tmp_path, 1)
    with (tmp_path / ".ghost" / "events.jsonl").open("ab") as f:
        f.write(b'{"kind": "partial", "paylo')
    cursor = EventCursor(tmp_path, "test")
    pairs = cursor.read_new()
    assert len(pairs) == 1
    assert pairs[0][1].kind == "tick"
    # The partial line must still be there on the next read once completed.
    with (tmp_path / ".ghost" / "events.jsonl").open("ab") as f:
        f.write(b'ad": {}, "source": "x"}\n')
    resumed = EventCursor(tmp_path, "test")
    resumed.commit(pairs[0][0])  # position after the first complete line
    new = resumed.read_new()
    assert [e.kind for _, e in new] == ["partial"]


def test_garbage_line_goes_to_failed_queue(tmp_path):
    path = tmp_path / ".ghost" / "events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not json\n")
    bus = EventBus(tmp_path)
    bus.emit(Event(kind="ok", payload={}))
    cursor = EventCursor(tmp_path, "test")
    pairs = cursor.read_new()
    assert [e.kind for _, e in pairs] == ["ok"]
    failed = tmp_path / ".ghost" / "events" / "failed.jsonl"
    records = [json.loads(line) for line in failed.read_text().splitlines()]
    assert records[0]["offset"] == 0 and "not json" in records[0]["line"]
    # Cursor still advanced past the garbage line once committed.
    cursor.commit(pairs[0][0])
    assert cursor.read_new() == []


def test_failed_queue_capped_at_1000(tmp_path):
    path = tmp_path / ".ghost" / "events.jsonl"
    path.parent.mkdir(parents=True)
    lines = []
    for i in range(1200):
        line = json.dumps({"bad": i}) + "\n"
        if "bad" not in {f.name for f in Event.__dataclass_fields__.values()}:
            lines.append(line.encode())
    path.write_bytes(b"".join(lines))
    cursor = EventCursor(tmp_path, "test")
    cursor.read_new()
    failed_lines = (tmp_path / ".ghost" / "events" / "failed.jsonl").read_text().splitlines()
    assert len(failed_lines) == 1000


def test_bad_cursor_name_raises(tmp_path):
    for bad in ["", "UPPER", "has space", "x" * 41, "bad!name"]:
        try:
            EventCursor(tmp_path, bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")


def test_offset_larger_than_file_resets(tmp_path):
    _emit(tmp_path, 2)
    cursor = EventCursor(tmp_path, "test")
    cursor.commit(10_000_000)
    pairs = cursor.read_new()
    assert len(pairs) == 2
