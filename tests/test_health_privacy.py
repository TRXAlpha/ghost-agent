"""OJ05/S02: Health-data privacy guard and memory deletion invalidation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ghost_agent.huawei_route import HuaweiRoute, HuaweiRouteError, HealthObservation
from ghost_agent.context_manager import MemoryRecord, ValidatedMemoryStore


# ── Fixtures / helpers ───────────────────────────────────────────────────────

def active_route(root, sources=("healthkit",), checked=1600000000.0):
    route = HuaweiRoute(root)
    route.register_route("Huawei Health Kit", list(sources), checked,
                         prerequisites=["Huawei developer account approval"],
                         limitations=["sleep not exposed on all firmware"])
    route.set_state("active")
    return route


def seed_observation(route: HuaweiRoute, metric: str = "steps", value: float = 8000.0,
                     timestamp: float = 1000.0) -> HealthObservation:
    route.set_state("active")
    obs = route.ingest([{"metric": metric, "value": value, "timestamp": timestamp,
                         "timezone": "UTC", "provenance": "synthetic"}])
    assert len(obs) == 1
    return obs[0]


def read_events(root: Path) -> list[dict]:
    events_path = root / ".ghost" / "privacy" / "events.jsonl"
    if not events_path.exists():
        return []
    return [json.loads(line) for line in
            events_path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── Health-data routing guard ────────────────────────────────────────────────

def test_guard_denies_raw_value_routes_by_default(tmp_path: Path) -> None:
    """Any path that would put raw health values into model context or
    external calls is denied by default — no allow_endpoint opt-in."""
    route = active_route(tmp_path)
    route.set_state("active")
    obs = seed_observation(route)

    # Raw value endpoints (model context and external) are denied by default
    with pytest.raises(HuaweiRouteError, match="not authorized"):
        route.forward("model_context")
    with pytest.raises(HuaweiRouteError, match="not authorized"):
        route.forward("https://external.example.com/ingest")
    with pytest.raises(HuaweiRouteError, match="not authorized"):
        route.forward("raw_observations")

    # Even explicit allow_endpoint cannot override the policy allow-list
    route.allow_endpoint("https://external.example.com")
    with pytest.raises(HuaweiRouteError, match="not authorized"):
        route.forward("https://external.example.com")


def test_allow_listed_aggregate_passes(tmp_path: Path) -> None:
    """Only documented aggregate policy classes are allowed."""
    route = active_route(tmp_path)
    route.set_state("active")
    seed_observation(route, metric="steps", value=8000.0)

    payload = route.forward("aggregate_daily_step_count")
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0]["metric"] == "steps"
    assert payload[0]["value"] == 8000.0  # aggregate policy class permits the export

    assert route.privacy_allowed("ghost_daily_aggregate") is True
    assert route.privacy_allowed("aggregate_daily_step_count") is True
    assert route.privacy_allowed("https://anything.example.com") is False


def test_event_log_written_without_raw_values(tmp_path: Path) -> None:
    """Routing decisions are logged to .ghost/privacy/events.jsonl without raw values."""
    route = active_route(tmp_path)
    route.set_state("active")
    seed_observation(route, metric="steps", value=12345.0)

    # Trigger both a deny and an allow decision
    with pytest.raises(HuaweiRouteError):
        route.forward("https://attacker.example.com")
    route.forward("aggregate_daily_step_count")
    route.privacy_allowed("raw_values_to_model")

    events = read_events(tmp_path)
    assert len(events) >= 3
    for event in events:
        # Only policy metadata: no raw health values, no observation payloads
        assert set(event.keys()) == {"ts", "route", "endpoint", "decision", "reason_code"}
        assert event["decision"] in {"allow", "deny"}
        assert event["reason_code"] in {"policy_explicit", "policy_default"}
        # No raw health values in the event payload
        serialized = json.dumps(event)
        assert "12345" not in serialized
        assert "steps" not in serialized

    decisions = {(e["route"], e["decision"]) for e in events}
    assert ("forward", "deny") in decisions
    assert ("forward", "allow") in decisions
    assert ("privacy_allowed", "deny") in decisions


# ── Memory retraction and purge (deletion invalidation) ─────────────────────

def _record(memory_id: str, content: str, depends_on=None, validation: str = "validated") -> MemoryRecord:
    return MemoryRecord(memory_id=memory_id, kind="semantic", content=content,
                        source="test", confidence=0.9,
                        validation=validation, depends_on=depends_on or [])


def test_retract_excludes_from_retrieval(tmp_path: Path) -> None:
    store = ValidatedMemoryStore(tmp_path)
    store.remember(_record("root", "owner is in Berlin", ))
    store.remember(_record("dep", "depends on the retracted fact",
                           depends_on=["root"]))
    assert [r.memory_id for r in store.active()] == ["root", "dep"]

    retracted = store.retract("root", "user requested deletion")
    assert retracted.validation == "retracted"
    assert retracted.invalidated_at is not None
    # Root and dependents excluded from retrieval
    assert [r.memory_id for r in store.active()] == []
    # Record remains on disk, auditable (validation is "retracted")
    reloaded = ValidatedMemoryStore(tmp_path)
    assert [r.memory_id for r in reloaded.active()] == []
    disk = json.loads(
        (tmp_path / ".ghost" / "validated-memory.json").read_text(encoding="utf-8"))
    assert len(disk) == 2
    for item in disk:
        assert item["validation"] == "retracted"


def test_purge_cascades_transitively(tmp_path: Path) -> None:
    """Purging a root memory removes every record that cites it, transitively."""
    store = ValidatedMemoryStore(tmp_path)
    store.remember(_record("root", "base fact"))
    store.remember(_record("child", "cites base", depends_on=["root"]))
    store.remember(_record("grandchild", "cites child", depends_on=["child"]))
    store.remember(_record("unrelated", "unrelated memory"))

    purged = store.purge("root", "GDPR erasure request")
    assert purged.memory_id == "root"

    ids = {r.memory_id for r in store.active()}
    assert ids == {"unrelated"}
    # Purged records are physically removed from the store file
    raw = json.loads((tmp_path / ".ghost" / "validated-memory.json").read_text(encoding="utf-8"))
    remaining = {item["memory_id"] for item in raw}
    assert remaining == {"unrelated"}
    assert {"root", "child", "grandchild"}.isdisjoint(ids)


def test_purged_id_stays_gone_after_reload(tmp_path: Path) -> None:
    store = ValidatedMemoryStore(tmp_path)
    store.remember(_record("root", "base fact"))
    store.remember(_record("child", "cites root", depends_on=["root"]))

    store.purge("root", "GDPR erasure request")
    # Fresh store instance (reload from disk)
    fresh = ValidatedMemoryStore(tmp_path)
    ids = {r.memory_id for r in fresh.active()}
    assert "root" not in ids and "child" not in ids
    raw = json.loads((tmp_path / ".ghost" / "validated-memory.json").read_text(encoding="utf-8"))
    assert [item["memory_id"] for item in raw] == []


def test_purge_requires_reason_and_existing_id(tmp_path: Path) -> None:
    store = ValidatedMemoryStore(tmp_path)
    store.remember(_record("a", "content a"))
    with pytest.raises(ValueError, match="reason"):
        store.purge("a", "")
    with pytest.raises(ValueError, match="not found"):
        store.purge("missing-id", "any reason")
    # The store still holds 'a'
    assert [r.memory_id for r in store.active()] == ["a"]


def test_retract_requires_reason(tmp_path: Path) -> None:
    store = ValidatedMemoryStore(tmp_path)
    store.remember(_record("a", "content a"))
    with pytest.raises(ValueError, match="reason"):
        store.retract("a", "   ")
