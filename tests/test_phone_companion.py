"""Ghost Companion (Android) adapter and quiet-window DND, with a fake phone endpoint."""
import json
from datetime import datetime

from ghost_agent import heartbeat as hb, owner, phone, situation

NOW = datetime(2026, 11, 23, 9, 10).timestamp()  # a Monday, inside the 09:05-09:49 window


class FakePhone:
    def __init__(self, events):
        self.events, self.calls = events, []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path.startswith("/events"):
            since = int(path.split("since=")[1].split("&")[0])
            rows = [e for e in self.events if e["seq"] > since]
            return {"events": rows, "last_seq": max([e["seq"] for e in self.events] or [0])}
        return {"ok": True}


def ev(seq, kind, at, **data):
    return {"seq": seq, "at": int(at * 1000), "kind": kind, "data": data}


def test_pull_is_incremental_and_derives_activity_and_place(tmp_path):
    (tmp_path / ".ghost/owner").mkdir(parents=True)
    (tmp_path / ".ghost/owner/home.json").write_text(json.dumps({"lat": 44.4268, "lon": 26.1025}))
    fake = FakePhone([ev(1, "unlock", NOW - 300), ev(2, "app", NOW - 200, pkg="org.telegram"),
                      ev(3, "screen_on", NOW - 100), ev(4, "location", NOW - 60, lat=44.4269, lon=26.1026, acc=8)])
    read = phone.adapter(tmp_path, http=fake)
    fields = read(NOW)
    assert fields["phone_activity"]["value"] == {"screen": "on", "app": "org.telegram", "last_unlock_min_ago": 5}
    assert fields["place"]["value"] == "home" and fields["place"]["source"] == "phone_gps"
    read(NOW)
    assert fake.calls[-1][1].startswith("/events?since=4")  # cursor advanced
    record = situation.rebuild(tmp_path, {"phone": read}, now=NOW)
    assert "44.42" not in json.dumps(record)  # coordinates never enter the situation record


def test_far_or_stale_fix_is_not_home(tmp_path):
    (tmp_path / ".ghost/owner").mkdir(parents=True)
    (tmp_path / ".ghost/owner/home.json").write_text(json.dumps({"lat": 44.4268, "lon": 26.1025}))
    far = phone.adapter(tmp_path, http=FakePhone([ev(1, "location", NOW - 60, lat=44.45, lon=26.1)]))(NOW)
    assert far["place"]["value"] == "away"
    stale_root = tmp_path / "s"
    (stale_root / ".ghost/owner").mkdir(parents=True)
    (stale_root / ".ghost/owner/home.json").write_text(json.dumps({"lat": 44.4268, "lon": 26.1025}))
    stale = phone.adapter(stale_root, http=FakePhone([ev(1, "location", NOW - 3600, lat=44.4268, lon=26.1025)]))(NOW)
    assert "place" not in stale


def test_quiet_window_dnd_needs_grant_and_toggles_once(tmp_path):
    owner.ensure_owner_files(tmp_path)
    owner.edit_owner_file(tmp_path, "USER.md", "# USER\n\n## Quiet windows\n- Monday 09:05-09:49\n")
    fake = FakePhone([])
    assert phone.sync_quiet_dnd(tmp_path, NOW, http=fake) == "off" and not fake.calls
    hb.set_grant(tmp_path, "care.phone_actions", True)
    assert phone.sync_quiet_dnd(tmp_path, NOW, http=fake) == "on"
    assert ("POST", "/dnd", {"on": True}) in fake.calls and ("POST", "/ringer", {"mode": "silent"}) in fake.calls
    assert phone.sync_quiet_dnd(tmp_path, NOW + 60, http=fake) == "unchanged"
    assert phone.sync_quiet_dnd(tmp_path, NOW + 45 * 60, http=fake) == "cleared"
    assert ("POST", "/ringer", {"mode": "normal"}) in fake.calls


def test_unreachable_phone_retries_later(tmp_path):
    owner.ensure_owner_files(tmp_path)
    owner.edit_owner_file(tmp_path, "USER.md", "# USER\n\n## Quiet windows\n- Monday 09:05-09:49\n")
    hb.set_grant(tmp_path, "care.phone_actions", True)

    def down(*a, **k):
        raise ConnectionError("phone asleep")
    assert phone.sync_quiet_dnd(tmp_path, NOW, http=down).startswith("unreachable")
    assert not (tmp_path / ".ghost/phone/dnd.json").exists()
