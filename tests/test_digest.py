"""OJ11: morning digest of overnight outcome results."""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import pytest

from ghost_agent.digest import MorningDigest, _is_quiet


def at(hour, minute=0):
    """Return a Unix timestamp for today at `hour` local time."""
    now = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    return time.mktime(now.timetuple())


class FakeEmergencyStop:
    def __init__(self, active=False):
        self._active = active
    def active(self):
        return self._active


def make_digest(root, quiet_hours=(22, 8), digest_hour=8, hours_back=24.0,
                collect_fn=None):
    return MorningDigest(root, quiet_hours=quiet_hours, digest_hour=digest_hour,
                         hours_back=hours_back, collect_fn=collect_fn)


def test_init_validation():
    with pytest.raises(ValueError, match="quiet_hours"):
        MorningDigest(Path("/tmp"), quiet_hours=(25, 8))
    with pytest.raises(ValueError, match="digest_hour"):
        MorningDigest(Path("/tmp"), digest_hour=25)
    with pytest.raises(ValueError, match="hours_back"):
        MorningDigest(Path("/tmp"), hours_back=0)


def test_is_quiet():
    # quiet (22, 8): quiet when hour >= 22 OR hour < 8
    assert _is_quiet(22, (22, 8)) is True
    assert _is_quiet(23, (22, 8)) is True
    assert _is_quiet(7, (22, 8)) is True
    assert _is_quiet(8, (22, 8)) is False  # digest_hour=8 is exactly at boundary
    assert _is_quiet(14, (22, 8)) is False
    # normal quiet (0, 8): quiet when 0 <= h < 8
    assert _is_quiet(0, (0, 8)) is True
    assert _is_quiet(7, (0, 8)) is True
    assert _is_quiet(8, (0, 8)) is False


def test_collect_uses_custom_fn(tmp_path: Path):
    items_collected = []
    def collector(since):
        items_collected.append(since)
        return [{"goal": "test goal", "result": "tests_passed", "finished": time.time()}]

    dig = make_digest(tmp_path, collect_fn=collector)
    result = dig.collect(time.time() - 86400)

    assert len(items_collected) == 1
    assert result[0]["goal"] == "test goal"
    assert isinstance(items_collected[0], (int, float))  # since is a valid timestamp


def test_render():
    assert MorningDigest.render([]) == ""
    items = [
        {"goal": "fix the parser", "result": "tests_passed", "finished": 0},
        {"goal": "add logging", "result": "tests_passed", "finished": 0},
    ]
    out = MorningDigest.render(items)
    assert "Morning digest" in out
    assert "fix the parser" in out
    assert "add logging" in out
    assert "-> tests_passed" in out


def test_render_caps_at_max_items(tmp_path: Path):
    items = [{"goal": f"item {i}", "result": "ok", "finished": 0} for i in range(30)]
    out = MorningDigest.render(items)
    for i in range(20):
        assert f"item {i}" in out
    for i in range(20, 30):
        assert f"item {i}" not in out


def test_emit_skips_when_disabled(tmp_path: Path, monkeypatch):
    dig = make_digest(tmp_path)
    dig.disable()
    now = time.time()
    result = dig.emit(now=now)
    assert result is None


def test_emit_skips_during_quiet_hours(tmp_path: Path, monkeypatch):
    collected = []
    dig = make_digest(tmp_path, quiet_hours=(22, 8), digest_hour=9,
                       collect_fn=lambda s: (collected.append(s) or []))
    # 23:00 — inside quiet hours, digest_hour=9 not reached
    # use a time where tm_hour=23 to be clearly inside quiet (22,8)
    fixed_now = at(23)
    result = dig.emit(now=fixed_now)
    assert result is None
    # nothing was collected (emit was gated before collection)
    assert collected == []


def test_emit_skips_before_digest_hour(tmp_path: Path, monkeypatch):
    collected = []
    dig = make_digest(tmp_path, digest_hour=9,
                       collect_fn=lambda s: (collected.append(s) or []))
    # 07:00 — before digest hour, not in quiet
    fixed_now = at(7)
    result = dig.emit(now=fixed_now)
    assert result is None


def test_emit_skips_when_no_results(tmp_path: Path, monkeypatch):
    dig = make_digest(tmp_path, digest_hour=9, collect_fn=lambda s: [])
    # 09:00 — after digest_hour, not quiet, but no results
    fixed_now = at(9, 5)
    result = dig.emit(now=fixed_now)
    assert result is None  # no speculative work when none worthwhile


def test_emit_emits_once_per_day(tmp_path: Path, monkeypatch):
    dig = make_digest(tmp_path, digest_hour=9, collect_fn=lambda s: [
        {"goal": "item 1", "result": "tests_passed", "finished": time.time()}])

    fixed_now = at(9, 5)
    # Patch AttentionManager so we don't need the full system
    class FakeNote:
        notification_id = "note_fake"
        status = "pending"
    from ghost_agent import runtime as rt
    orig_notify = rt.AttentionManager.notify
    rt.AttentionManager.notify = lambda self, n: FakeNote()

    result1 = dig.emit(now=fixed_now)
    assert result1 is not None  # first emission
    result2 = dig.emit(now=fixed_now + 300)  # same day, 5 min later
    assert result2 is None  # already emitted today

    rt.AttentionManager.notify = orig_notify


def test_corrupt_state_fails_closed(tmp_path: Path):
    path = tmp_path / ".ghost" / "digest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ invalid", encoding="utf-8")
    dig = MorningDigest(tmp_path, digest_hour=9)
    with pytest.raises(ValueError, match="corrupt"):
        dig.emit()


def test_disable(tmp_path: Path):
    dig = MorningDigest(tmp_path, digest_hour=9)
    state = dig.disable()
    assert state.enabled is False