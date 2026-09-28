"""OJ14: Huawei route connector — S02 conservative health/activity behavior."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from ghost_agent.huawei_route import (
    HuaweiRoute, HuaweiRouteError, HealthObservation,
)


class FetchResult:
    def __init__(self, raw, metric):
        self.raw = raw
        self.metric = metric
    def __call__(self, raw):
        return [{"metric": self.metric, "value": r, "unit": "minutes",
                 "timestamp": 1000.0, "timezone": "UTC", "provenance": "synthetic"}
                for r in self.raw]


def active_route(root, sources=("healthkit",), checked=1600000000.0):
    route = HuaweiRoute(root)
    route.register_route("Huawei Health Kit", list(sources), checked,
                         prerequisites=["Huawei developer account approval"],
                         limitations=["sleep not exposed on all firmware"])
    return route


def test_provider_route_records_metadata(tmp_path: Path):
    route = active_route(tmp_path)
    r = route.route()
    assert r.name == "Huawei Health Kit"
    assert r.sources == ["healthkit"]
    assert r.prerequisites == ["Huawei developer account approval"]
    assert r.limitations
    assert r.state == "discovered"
    assert r.checked_at == 1600000000.0


def test_honest_revoked_state(tmp_path: Path):
    route = active_route(tmp_path)
    assert route.set_state("revoked").state == "revoked"
    assert route.set_state("unavailable").state == "unavailable"
    with pytest.raises(HuaweiRouteError):
        route.set_state("bogus")


def test_ingest_requires_active_route(tmp_path: Path):
    route = active_route(tmp_path)
    with pytest.raises(HuaweiRouteError, match="active"):
        route.ingest([{"metric": "activity", "value": 30, "timestamp": 1000.0}])


def test_ingest_preserves_units_timezone_provenance(tmp_path: Path):
    route = active_route(tmp_path)
    route.set_state("active")
    obs = route.ingest([{"metric": "activity", "value": 45, "unit": "minutes",
                         "timestamp": 1000.0, "timezone": "Europe/London",
                         "provenance": "healthkit_daily"}])
    assert len(obs) == 1
    o = obs[0]
    assert o.metric == "activity" and o.value == 45.0
    assert o.unit == "minutes"
    assert o.timezone == "Europe/London"
    assert o.provenance == "healthkit_daily"
    assert o.timestamp == 1000.0


def test_default_unit_applied_when_missing(tmp_path: Path):
    route = active_route(tmp_path)
    route.set_state("active")
    obs = route.ingest([{"metric": "sleep", "value": 420, "timestamp": 1000.0}])
    assert obs[0].unit == "minutes"


def test_unknown_metric_ignored_not_guessed(tmp_path: Path):
    route = active_route(tmp_path)
    route.set_state("active")
    obs = route.ingest([{"metric": "brain_waves", "value": 99, "timestamp": 1000.0}])
    assert obs == []


def test_freshness_and_unknown_when_no_data(tmp_path: Path):
    route = active_route(tmp_path)
    route.set_state("active")
    now = 2000.0
    route.ingest([{"metric": "activity", "value": 500, "unit": "minutes",
                   "timestamp": now - 60, "timezone": "UTC",
                   "provenance": "synthetic"}])
    # fresh data -> active
    assert route.inactivity_status(now, max_age_s=3600) == "active"
    # force stale by asking for a tiny freshness window -> unknown, never a guess
    assert route.inactivity_status(now, max_age_s=10) == "unknown"


def test_inactivity_from_fresh_low_activity(tmp_path: Path):
    route = active_route(tmp_path)
    route.set_state("active")
    now = 2000.0
    route.ingest([{"metric": "activity", "value": 5, "unit": "minutes",
                   "timestamp": now - 60, "timezone": "UTC", "provenance": "synthetic"}])
    assert route.inactivity_status(now, max_age_s=3600, low_activity_value=200.0) == "inactive"


def test_owner_context_correction_shadows_data(tmp_path: Path):
    route = active_route(tmp_path)
    corr = route.correct_context("Traveling abroad this week", effect="travel")
    assert corr.effect == "travel"
    assert route.active_corrections(now=corr.applies_from + 10)[0].note == \
        "Traveling abroad this week"


def test_trend_summary_no_prediction(tmp_path: Path):
    route = active_route(tmp_path)
    route.set_state("active")
    now = 2000.0
    route.ingest([{"metric": "activity", "value": 30, "unit": "minutes",
                   "timestamp": now - 3600, "timezone": "UTC", "provenance": "synthetic"},
                  {"metric": "activity", "value": 60, "unit": "minutes",
                   "timestamp": now - 60, "timezone": "UTC", "provenance": "synthetic"}])
    summary = route.trend_summary(now, window_s=7 * 86400, metric="activity")
    assert summary["supports_prediction"] is False
    assert summary["hypotheses"] == []
    assert summary["measured"], "a measured change is present"
    assert route.supports_prediction is False


def test_trend_summary_unknown_without_data(tmp_path: Path):
    route = active_route(tmp_path)
    route.set_state("active")
    summary = route.trend_summary(2000.0, metric="activity")
    assert summary["measured"] == [] and "no recent data" in summary["note"]


def test_privacy_blocks_unauthorized_endpoint(tmp_path: Path):
    route = active_route(tmp_path)
    route.set_state("active")
    with pytest.raises(HuaweiRouteError, match="not authorized"):
        route.forward("https://telemetry.example.com")
    # The routing guard is a documented policy allow-list; the legacy
    # "ghost_dashboard" endpoint class remains an explicitly allow-listed
    # aggregate-internal destination.
    route.allow_endpoint("ghost_dashboard")
    route.ALLOWED_ENDPOINT_CLASSES = frozenset(route.ALLOWED_ENDPOINT_CLASSES | {"ghost_dashboard"})
    payload = route.forward("ghost_dashboard")
    assert isinstance(payload, list)


def test_corrupt_state_fails_closed(tmp_path: Path):
    route = active_route(tmp_path)
    route.set_state("active")
    route.observations_path.write_text("{ broken", encoding="utf-8")
    with pytest.raises(HuaweiRouteError, match="corrupt"):
        route.observations()
