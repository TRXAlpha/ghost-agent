"""Life layer LL0-LL4: owner facts, owner files, situation, heartbeat, care grants."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ghost_agent import heartbeat as hb
from ghost_agent import owner, situation
from ghost_agent.context_manager import ValidatedMemoryStore
from ghost_agent.control_plane import GhostSDK
from ghost_agent.daemon import GhostDaemon
from ghost_agent.proactive import OpportunityStore, ProactiveEngine
from ghost_agent.runtime import AttentionManager, EmergencyStop, EventBus

NOON = 1_795_000_000.0  # fixed epoch; hour checked per test where it matters


def _noon(now: float = NOON) -> float:
    """Shift a timestamp to 12:00 local time so quiet hours never interfere."""
    from datetime import datetime
    day = datetime.fromtimestamp(now).replace(hour=12, minute=0, second=0, microsecond=0)
    return day.timestamp()


# -- LL0 owner facts -------------------------------------------------------------

@pytest.mark.parametrize("text,kind", [
    ("I got a new Samsung watch.", "device"),
    ("I'm joining HackMIT on 2026-12-05.", "deadline"),
    ("I want to learn Rust", "goal"),
    ("Timing FTC auto paths by hand is so tedious", "complaint"),
    ("Nice weather today", "note"),
])
def test_parse_statement_kinds(text, kind):
    assert owner.parse_statement(text)["kind"] == kind


def test_health_wording_is_tagged_never_diagnosed():
    assert owner.parse_statement("I slept badly")["sensitivity"] == "health"
    assert owner.parse_statement("I will still go")["sensitivity"] == "personal"


def test_tell_stores_validated_fact_and_event(tmp_path):
    result = owner.tell(tmp_path, "I got a new Samsung watch", source="cli")
    records = ValidatedMemoryStore(tmp_path)._load()
    assert records[0]["memory_id"] == result["memory_id"]
    assert records[0]["validation"] == "validated" and records[0]["subject"] == "owner.device"
    events = EventBus(tmp_path).recent(5, kind="owner.fact")
    assert events[-1].payload["memory_id"] == result["memory_id"]
    assert (tmp_path / ".ghost/owner/SOUL.md").exists()


def test_tell_goal_creates_goal_with_deadline(tmp_path):
    from ghost_agent.kernel import GoalStore
    result = owner.tell(tmp_path, "I'm joining HackMIT on 2026-12-05")
    goal = GoalStore(tmp_path).get(result["goal_id"])
    assert goal is not None and goal.deadline_at == result["deadline_at"] and goal.kind == "owner"


def test_tell_refuses_secrets_and_bad_source(tmp_path):
    with pytest.raises(owner.OwnerError):
        owner.tell(tmp_path, "my token is 123456789:" + "A" * 30)
    with pytest.raises(owner.OwnerError):
        owner.tell(tmp_path, "hello", source="web")
    assert not (tmp_path / ".ghost/validated-memory.json").exists()


def test_health_facts_stay_out_of_owner_context(tmp_path):
    owner.tell(tmp_path, "I slept badly")
    owner.tell(tmp_path, "I got a new Samsung watch")
    facts = owner.owner_context(tmp_path)["facts"]
    assert [f["kind"] for f in facts] == ["device"]
    assert len(owner.owner_facts(tmp_path, include_health=True)) == 2


def test_owner_fact_drives_one_proactive_tick(tmp_path):
    owner.tell(tmp_path, "I got a new Samsung watch")
    seen = []

    def planner(model, messages, **kwargs):
        seen.append(json.loads(messages[1]["content"]))
        return json.dumps({"title": "Reuse Health Connect", "goal": "Check whether HealthHub already "
                           "reads Samsung Health through Health Connect", "rationale": "owner fact",
                           "kind": "integration", "confidence": 0.5, "requires_user_input": False})

    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=planner)
    first = engine.tick(auto_queue=False)
    assert seen[0]["new_owner_facts"][0]["subject"] == "Samsung watch"
    assert "HealthHub" in seen[0]["owner"]["user"]
    assert first["opportunity"]["trigger"] == "owner_request"
    assert first["opportunity"]["evidence_items"][0]["source"].startswith("owner_fact:")
    engine.tick(auto_queue=False)
    assert "new_owner_facts" not in seen[1]  # consumed exactly once
    # ghost why on the fact links the opportunity
    fact_id = first["opportunity"]["evidence_items"][0]["source"].split(":", 1)[1]
    detail = GhostSDK(tmp_path).explain(fact_id)
    assert detail["opportunities"] == [first["opportunity"]["opportunity_id"]]


def test_fact_behind_long_event_history_is_seen(tmp_path):
    from ghost_agent.runtime import Event
    bus = EventBus(tmp_path)
    for i in range(700):
        bus.emit(Event("noise", {"i": i}))
    owner.tell(tmp_path, "I got a new Samsung watch")
    seen = []
    ProactiveEngine(tmp_path, "tiny", chat_fn=lambda m, msgs, **k: seen.append(
        json.loads(msgs[1]["content"])) or json.dumps({
            "title": "t", "goal": "research the watch", "rationale": "r", "kind": "research",
            "confidence": 0.5, "requires_user_input": False})).tick(auto_queue=False)
    assert seen[0]["new_owner_facts"]


def test_deferred_tick_does_not_consume_fact(tmp_path):
    owner.tell(tmp_path, "I got a new Samsung watch")
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *a, **k: "not json")
    with pytest.raises(Exception):
        engine.tick(auto_queue=False)
    seen = []
    ok = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda m, msgs, **k: seen.append(
        json.loads(msgs[1]["content"])) or json.dumps({
            "title": "t", "goal": "research the watch", "rationale": "r", "kind": "research",
            "confidence": 0.5, "requires_user_input": False}))
    ok.tick(auto_queue=False)
    assert seen[0]["new_owner_facts"]


# -- LL1 owner files --------------------------------------------------------------

def test_owner_files_versioned_edit(tmp_path):
    files = owner.read_owner_files(tmp_path)
    assert "Ghost" in files["SOUL.md"]
    owner.edit_owner_file(tmp_path, "USER.md", "# USER\nnew routine\n")
    assert owner.read_owner_files(tmp_path)["USER.md"].startswith("# USER\nnew")
    history = owner.owner_file_history(tmp_path, "USER.md")
    assert len(history) == 1
    assert "Tools I already run" in (tmp_path / ".ghost/owner/history" / history[0]).read_text()
    with pytest.raises(owner.OwnerError):
        owner.edit_owner_file(tmp_path, "OTHER.md", "x")
    with pytest.raises(owner.OwnerError):
        owner.edit_owner_file(tmp_path, "USER.md", "sk-" + "a" * 30)


# -- LL3/LL3b situation -------------------------------------------------------------

def _aw_fetch(state="not-afk", app="Code.exe", title="drive.py - ftc-gm - Visual Studio Code"):
    def fetch(url):
        if url.endswith("/buckets/"):
            return {"aw-watcher-window_pc": {}, "aw-watcher-afk_pc": {}}
        if "afk" in url:
            return [{"data": {"status": state}}]
        return [{"data": {"app": app, "title": title}}]
    return fetch


def test_situation_fuses_activitywatch_and_guesses_task(tmp_path):
    record = situation.rebuild(tmp_path, {"activitywatch": situation.activitywatch_adapter(_aw_fetch())},
                               now=NOON)
    fields = record["fields"]
    assert fields["current_project"]["value"] == "ftc-gm"
    assert fields["task_guess"]["value"] == "ftc"
    assert fields["place"]["value"] == "unknown" and fields["health"]["value"] == "unknown"
    for name in situation.FIELDS:
        assert {"value", "confidence", "source"} <= set(fields[name])


def test_situation_afk_and_gaming(tmp_path):
    afk = situation.rebuild(tmp_path, {"aw": situation.activitywatch_adapter(_aw_fetch(state="afk"))}, now=NOON)
    assert afk["fields"]["task_guess"]["value"] == "idle"
    game = situation.rebuild(tmp_path, {"aw": situation.activitywatch_adapter(
        _aw_fetch(app="factorio.exe", title="Factorio"))}, now=NOON)
    assert game["fields"]["task_guess"]["value"] == "gaming"


def test_stale_fields_decay_to_unknown(tmp_path):
    situation.rebuild(tmp_path, {"aw": situation.activitywatch_adapter(_aw_fetch())}, now=NOON)
    fresh = situation.load(tmp_path, now=NOON + 60)
    stale = situation.load(tmp_path, now=NOON + 3600)
    assert fresh["fields"]["pc_activity"]["value"]["state"] == "active"
    assert stale["fields"]["pc_activity"]["value"] == "unknown"
    assert stale["fields"]["pc_activity"]["stale_value"]["state"] == "active"


def test_dead_source_is_unknown_not_fatal(tmp_path):
    def broken(now):
        raise ConnectionError("down")
    record = situation.rebuild(tmp_path, {"aw": broken}, now=NOON)
    assert record["sources"]["aw"].startswith("unavailable")
    assert record["fields"]["pc_activity"]["value"] == "unknown"


def test_pushed_place_and_change_event(tmp_path):
    situation.rebuild(tmp_path, {"pushed": situation.pushed_adapter(tmp_path)})
    situation.push_observation(tmp_path, "place", "home", confidence=0.9, source="ghostpi.adguard", ttl=900)
    record = situation.rebuild(tmp_path, {"pushed": situation.pushed_adapter(tmp_path)})
    assert record["fields"]["place"]["value"] == "home"
    assert EventBus(tmp_path).recent(5, kind="situation.changed")
    with pytest.raises(ValueError):
        situation.push_observation(tmp_path, "task_guess", "x", confidence=1, source="x", ttl=10)


def test_git_adapter_window(tmp_path):
    adapter = situation.git_adapter([tmp_path / "ghost_agent"], run=lambda cmd: str(int(NOON - 600)))
    assert adapter(NOON)["current_project"]["value"] == "ghost_agent"
    assert adapter(NOON + 3 * 3600) == {}


def test_project_from_title():
    assert situation.project_from_title("Code.exe", "a.py - ghost_agent - Visual Studio Code") == "ghost_agent"
    assert situation.project_from_title("chrome.exe", "Inbox - Gmail") == ""


# -- LL4 heartbeat ------------------------------------------------------------------

def _beat(tmp_path, action, *, sent=None, **kw):
    def send(root, text):
        (sent if sent is not None else []).append(text)
        return {"status": "sent"}
    return hb.Heartbeat(tmp_path, "tiny", chat_fn=lambda *a, **k: json.dumps(action),
                        send_fn=send, adapters={}, **kw)


def test_heartbeat_none_and_note(tmp_path):
    assert _beat(tmp_path, {"action": "none"}).beat(_noon())["status"] == "done"
    note = _beat(tmp_path, {"action": "note", "text": "owner works late on FTC"}).beat(_noon())
    assert note["status"] == "done" and note["memory_id"].startswith("note_")


def test_message_needs_grant_and_cap_holds_across_restart(tmp_path):
    sent = []
    action = {"action": "message_owner", "text": "HackMIT registration closes tomorrow", "reason": "deadline"}
    refused = _beat(tmp_path, action, sent=sent).beat(_noon())
    assert refused["status"] == "refused" and "grant" in refused["refusal"] and not sent
    hb.set_grant(tmp_path, "care.message_owner", True)
    t = _noon()
    for i in range(3):
        assert _beat(tmp_path, action, sent=sent).beat(t + i)["status"] == "done"
    fourth = _beat(tmp_path, action, sent=sent).beat(t + 10)  # new instance = restart
    assert fourth["status"] == "refused" and "cap" in fourth["refusal"]
    assert len(sent) == 3
    assert _beat(tmp_path, action, sent=sent).beat(t + 25 * 3600)["status"] == "done"


def test_quiet_hours_hold_message_as_notification(tmp_path):
    from datetime import datetime
    hb.set_grant(tmp_path, "care.message_owner", True)
    night = datetime.fromtimestamp(NOON).replace(hour=23).timestamp()
    sent = []
    result = _beat(tmp_path, {"action": "message_owner", "text": "hi"}, sent=sent).beat(night)
    assert result["status"] == "deferred" and not sent
    assert any(n.source == "heartbeat" for n in AttentionManager(tmp_path).pending())


@pytest.mark.parametrize("raw", [
    json.dumps({"action": "send_email", "text": "x"}),
    json.dumps({"action": "message_owner", "text": "You might have the flu"}),
    json.dumps({"action": "message_owner", "text": ""}),
    "not json",
])
def test_invalid_actions_refused(tmp_path, raw):
    hb.set_grant(tmp_path, "care.message_owner", True)
    sent = []
    beat = hb.Heartbeat(tmp_path, "tiny", chat_fn=lambda *a, **k: raw, adapters={},
                        send_fn=lambda r, t: sent.append(t))
    assert beat.beat(_noon())["status"] == "refused" and not sent


def test_heartbeat_emergency_stop_and_room_and_proposal(tmp_path):
    EmergencyStop(tmp_path).engage("test")
    called = []
    beat = hb.Heartbeat(tmp_path, "tiny", chat_fn=lambda *a, **k: called.append(1), adapters={})
    assert beat.beat(_noon())["status"] == "stopped" and not called
    EmergencyStop(tmp_path).clear()
    hb.set_grant(tmp_path, "care.post_room", True)
    room = _beat(tmp_path, {"action": "post_room", "text": "owner quiet"}).beat(_noon())
    assert room["status"] == "refused" and "room" in room["refusal"]
    prop = _beat(tmp_path, {"action": "propose_opportunity", "text": "Build an FTC auto-path timing analyzer",
                            "reason": "owner complained"}).beat(_noon())
    stored = OpportunityStore(tmp_path).find_any(prop["opportunity_id"])
    assert stored.status == "proposed" and stored.trigger == "heartbeat" and not stored.job_id


def test_heartbeat_due_and_why(tmp_path):
    beat = _beat(tmp_path, {"action": "none", "reason": "nothing new"})
    assert beat.due(NOON)
    receipt = beat.beat(NOON)
    assert not beat.due(NOON + 60) and beat.due(NOON + 1800)
    detail = GhostSDK(tmp_path).explain(receipt["receipt_id"])
    assert detail["heartbeat_receipt"]["reason"] == "nothing new"


def test_health_value_never_reaches_heartbeat_model(tmp_path):
    situation.push_observation(tmp_path, "health", {"resting_hr": 71}, confidence=0.9,
                               source="healthhub", ttl=3600)
    prompts = []
    beat = hb.Heartbeat(tmp_path, "tiny", adapters={"pushed": situation.pushed_adapter(tmp_path)},
                        chat_fn=lambda m, msgs, **k: prompts.append(msgs[1]["content"]) or '{"action":"none"}')
    beat.beat()
    assert "resting_hr" not in prompts[0] and "withheld" in prompts[0]


def test_corrupt_grants_fail_closed(tmp_path):
    hb.set_grant(tmp_path, "care.message_owner", True)
    (tmp_path / ".ghost/owner/grants.json").write_text("{broken")
    assert hb.load_grants(tmp_path)["care.message_owner"]["enabled"] is False


def test_daemon_runs_heartbeat_without_idle(tmp_path):
    from ghost_agent.overnight import OvernightCycleResult

    class Runner:
        def run_cycle(self):
            return OvernightCycleResult(cycle=1, ts="t", user_active=True, quiet_hours=False,
                                        emergency_stop=False)
    calls = []
    daemon = GhostDaemon(tmp_path, "m", runner=Runner(), trace_fn=lambda: {}, improver_fn=lambda: {},
                         improve_sync_fn=lambda: [], sleep_fn=lambda s: None,
                         heartbeat_fn=lambda: calls.append(1) or {"status": "done"})
    record = daemon.run_cycle()
    assert calls and record["heartbeat"]["status"] == "done"  # owner active, still ran
    default = GhostDaemon(tmp_path, "m", runner=Runner(), trace_fn=lambda: {}, improver_fn=lambda: {},
                          improve_sync_fn=lambda: [], sleep_fn=lambda s: None)
    assert default.run_cycle()["heartbeat"]["status"] == "no_owner_files"


def test_unaddressed_fact_is_not_tagged_and_stays_pending(tmp_path):
    owner.tell(tmp_path, "I got a new Samsung watch")
    seen = []
    off_topic = lambda m, msgs, **k: seen.append(json.loads(msgs[1]["content"])) or json.dumps({
        "title": "Fix iteration limits", "goal": "Diagnose iteration_limit failures in hello.py jobs",
        "rationale": "r", "kind": "coding", "confidence": 0.5, "requires_user_input": False})
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=off_topic)
    first = engine.tick(auto_queue=False)
    assert first["opportunity"]["trigger"] == "tick"
    assert not any(e["source"].startswith("owner_fact:") for e in first["opportunity"]["evidence_items"])
    engine.tick(auto_queue=False)
    engine.tick(auto_queue=False)
    engine.tick(auto_queue=False)
    assert [bool(s.get("new_owner_facts")) for s in seen] == [True, True, True, False]  # released after 3
