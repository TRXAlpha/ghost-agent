"""Tests for TF1 (`ghost huawei` CLI) and TF4 (retention/purge)."""
import argparse
import json
import time

import pytest

from ghost_agent.cli import _cmd_huawei
from ghost_agent.huawei_route import HuaweiRoute, HuaweiRouteError


@pytest.fixture
def route(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return HuaweiRoute(tmp_path)


def _active_route(route):
    route.register_route("test", ["synthetic"], time.time())
    route.set_state("active")
    return route


def _ns(**kw):
    return argparse.Namespace(**{**{"huawei_action": None, "days": None,
                                    "purge_all": False, "window_seconds": 7 * 86400,
                                    "metric": "activity"}, **kw})


def test_status_honest_not_configured(route, capsys):
    _cmd_huawei(_ns(huawei_action="status"))
    data = json.loads(capsys.readouterr().out)
    assert data["state"] == "not_configured"
    assert "synthetic" in data["note"]


def test_register_route_set_state_ingest_trend(route, tmp_path, capsys):
    _cmd_huawei(_ns(huawei_action="register-route", name="t",
                    source=["synthetic"], prerequisite=[], limitation=[]))
    capsys.readouterr()
    _cmd_huawei(_ns(huawei_action="set-state", state="active"))
    capsys.readouterr()
    rows = [{"metric": "activity", "value": 300.0, "timestamp": time.time()}]
    data_file = tmp_path / "rows.json"
    data_file.write_text(json.dumps(rows))
    _cmd_huawei(_ns(huawei_action="ingest", file=str(data_file)))
    out = capsys.readouterr().out
    assert json.loads(out)["ingested"] == 1
    # Privacy: raw values must not appear in CLI output.
    assert "300" not in out
    _cmd_huawei(_ns(huawei_action="trend"))
    trend = json.loads(capsys.readouterr().out)
    assert trend["supports_prediction"] is False
    assert trend["measured"]


def test_correction_appears_in_context(route, capsys):
    _cmd_huawei(_ns(huawei_action="correct", note="unworn watch", effect="unworn"))
    data = json.loads(capsys.readouterr().out)
    assert data["note"] == "unworn watch"
    assert route.active_corrections()


def test_retention_set_and_show(route, capsys):
    _cmd_huawei(_ns(huawei_action="retention", days=30))
    assert json.loads(capsys.readouterr().out)["retention_days"] == 30
    assert route.retention_days() == 30
    with pytest.raises(HuaweiRouteError):
        route.set_retention_days(0)
    with pytest.raises(HuaweiRouteError):
        route.set_retention_days(4000)


def test_old_observations_purged_on_ingest(route):
    _active_route(route)
    old = [{"metric": "steps", "value": 10.0, "timestamp": time.time() - 200 * 86400}]
    route.ingest(old)
    assert route.retention_days() == 90  # default
    fresh = [{"metric": "activity", "value": 500.0, "timestamp": time.time()}]
    route.ingest(fresh)
    remaining = route.observations()
    assert [o.metric for o in remaining] == ["activity"]


def test_purge_all_and_event_privacy(route, capsys):
    _active_route(route)
    route.ingest([{"metric": "steps", "value": 42.0, "timestamp": time.time()}])
    _cmd_huawei(_ns(huawei_action="purge", purge_all=True))
    assert json.loads(capsys.readouterr().out) == {"removed": 1}
    assert route.observations() == []
    # The purge event must exist and must not contain observation values.
    events_path = route.root / ".ghost" / "events.jsonl"
    assert events_path.exists()
    payload = None
    for line in events_path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("kind") == "huawei.purge":
            payload = event
    assert payload is not None, "huawei.purge event must be recorded"
    body = {k: v for k, v in payload.items() if k not in ("kind", "created_at", "source")}
    assert set(body["payload"]) <= {"removed", "reason"}
    assert "42" not in json.dumps(body["payload"])
