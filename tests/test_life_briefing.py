"""Scenario 2: owner goal -> verified briefing, reminders, 3 ideas, build N, one report."""
from __future__ import annotations

import json
import time
from datetime import datetime

import pytest

from ghost_agent import briefing, heartbeat as hb, owner
from ghost_agent.autonomy import OutcomeStore
from ghost_agent.runtime import AttentionManager, EmergencyStop, EventBus

FUTURE = datetime.now().year + 1
PAGE = (f"HackFoo {FUTURE} runs on March 20, {FUTURE} at the main hall. "
        f"Registration closes on {FUTURE}-03-01 at midnight. "
        "Teams of up to four students may enter. All code must be written during the event. " * 3)


def search(query, n):
    return [{"title": "HackFoo", "url": "https://hackfoo.example/"}]


def fetch(url):
    return PAGE


def model(facts=None, ideas=None):
    """Scripted model: fact extraction calls get `facts`, idea calls get `ideas`."""
    def chat_fn(model_name, messages, **kwargs):
        if "Extract facts" in messages[0]["content"]:
            return json.dumps({"facts": facts if facts is not None else [
                {"field": "registration_deadline", "value": f"{FUTURE}-03-01",
                 "quote": f"Registration closes on {FUTURE}-03-01 at midnight."},
                {"field": "dates", "value": f"March 20, {FUTURE}", "quote": f"runs on March 20, {FUTURE}"},
                {"field": "rules", "value": "teams of up to four", "quote": "Teams of up to four students may enter."},
                {"field": "prizes", "value": "a car", "quote": "the winner gets a car"},  # not on the page
            ]})
        return json.dumps({"ideas": ideas if ideas is not None else [
            {"title": f"Idea {i}", "pitch": "p", "build_goal": f"Build a tested Python prototype number {i}"}
            for i in (1, 2, 3)]})
    return chat_fn


def test_goal_statement_queues_briefing(tmp_path):
    result = owner.tell(tmp_path, f"I'm joining HackFoo {FUTURE}")
    assert briefing.pending_briefings(tmp_path)[0]["goal_id"] == result["goal_id"]


def test_unquoted_facts_are_dropped():
    kept = briefing.verify_facts(model()(None, [{"content": "Extract facts"}]), PAGE, "u")
    assert {f["field"] for f in kept} == {"registration_deadline", "dates", "rules"}
    assert briefing.verify_facts("not json", PAGE, "u") == []


def test_parse_date_forms():
    assert briefing.parse_date("2027-03-01") == datetime(2027, 3, 1).timestamp()
    assert briefing.parse_date("March 20, 2027") == datetime(2027, 3, 20).timestamp()
    assert briefing.parse_date("20 March 2027") == datetime(2027, 3, 20).timestamp()
    assert briefing.parse_date("soon") is None


def test_full_flow_briefing_reminders_ideas_build_report(tmp_path):
    goal = owner.tell(tmp_path, f"I'm joining HackFoo {FUTURE}")["goal_id"]
    sent = []
    out = hb.life_tick(tmp_path, "tiny", send_fn=lambda r, t: sent.append(t) or {"status": "sent"},
                       adapters={}, search_fn=search, fetch_fn=fetch, chat_fn=model(), quiet_hours=(0, 0))
    brief = out["briefings"][0]
    assert brief["facts"] == 3 and brief["reminders"] == 4  # T-7d and T-1d for 2 verified dates
    record = briefing.load_briefing(tmp_path, goal)
    assert record["status"] == "briefed" and record["gaps"] == [] and record["sources"]
    # care.message_owner is off: notification only, never Telegram
    assert not sent and brief["delivery"]["delivery"]["status"] == "notification_only"
    note = next(n for n in AttentionManager(tmp_path).pending() if n.source == "heartbeat")
    assert "registration deadline" in note.message and "build N" in note.message and "3. Idea 3" in note.message
    # second tick does not re-brief
    assert hb.life_tick(tmp_path, "tiny", adapters={}, search_fn=search, fetch_fn=fetch,
                        chat_fn=model())["briefings"] == []
    # build 2 -> sandbox job through the existing worker queue
    queued = briefing.build(tmp_path, 2, "tiny")
    assert queued["status"] == "queued"
    job = OutcomeStore(tmp_path).load(queued["job_id"])
    assert job.acceptance_contract["source"] == "owner" and job.acceptance_contract["promotion_requires_approval"]
    assert briefing.build(tmp_path, 2, "tiny")["status"] == "already_queued"
    with pytest.raises(briefing.BriefingError):
        briefing.build(tmp_path, 4, "tiny")
    # report once, from the job's real terminal result
    assert briefing.report_finished(tmp_path, lambda t: {}) == []  # still queued
    job.status, job.result = "completed", "tests_passed"
    OutcomeStore(tmp_path).save(job)
    reports = []
    assert briefing.report_finished(tmp_path, lambda t: reports.append(t))[0]["result"] == "tests_passed"
    assert briefing.report_finished(tmp_path, lambda t: reports.append(t)) == []
    assert len(reports) == 1 and "Prototype 2 (Idea 2): tests_passed" in reports[0]


def test_reminders_fire_once_and_hold_in_quiet_hours(tmp_path):
    item = briefing.add_reminder(tmp_path, time.time() + 5, "HackFoo registration closes")
    assert briefing.add_reminder(tmp_path, time.time() - 5, "past") is None
    later = time.time() + 10
    night = datetime.fromtimestamp(later).replace(hour=23).timestamp()
    if night < later:
        night += 86400
    got = []
    hb.life_tick(tmp_path, "tiny", now=night, adapters={},
                 send_fn=lambda r, t: got.append(t), search_fn=search, fetch_fn=fetch, chat_fn=model())
    assert briefing.reminders(tmp_path)[0]["status"] == "pending"  # held overnight
    fired = briefing.fire_due(tmp_path, lambda t: got.append(t) or {"status": "x"}, now=later)
    assert fired[0]["reminder_id"] == item["reminder_id"] and briefing.fire_due(tmp_path, lambda t: {}, later) == []


def test_ideas_validation(tmp_path):
    owner.tell(tmp_path, f"I'm joining HackFoo {FUTURE}")
    goal = briefing.pending_briefings(tmp_path)[0]["goal_id"]
    bad = [{"title": "x", "pitch": "p", "build_goal": "deploy to production"}] * 3
    with pytest.raises(briefing.BriefingError):
        briefing.propose_ideas(tmp_path, goal, "tiny", chat_fn=model(ideas=bad))


def test_failed_research_retries_then_gives_up(tmp_path):
    owner.tell(tmp_path, f"I'm joining HackFoo {FUTURE}")
    def broken(*a, **k):
        raise ConnectionError("model down")
    for _ in range(3):
        briefing.run_pending(tmp_path, "tiny", lambda t: {}, search_fn=search, fetch_fn=fetch, chat_fn=broken)
    assert briefing.pending_briefings(tmp_path) == []


def test_prompted_messages_skip_cap_but_need_grant(tmp_path):
    sent = []
    hb.deliver_owner(tmp_path, "reply", why="t", send_fn=lambda r, t: sent.append(t) or {"status": "sent"})
    assert not sent
    hb.set_grant(tmp_path, "care.message_owner", True, max_count=1)
    for _ in range(3):
        hb.deliver_owner(tmp_path, "reply", why="t", send_fn=lambda r, t: sent.append(t) or {"status": "sent"})
    assert len(sent) == 3 and hb.used_in_window(tmp_path, "message_owner", 24, time.time()) == 0
    EmergencyStop(tmp_path).engage("t")
    assert hb.deliver_owner(tmp_path, "x", why="t")["status"] == "refused"


def test_salience_brings_heartbeat_forward(tmp_path):
    beat = hb.Heartbeat(tmp_path, "tiny", chat_fn=lambda *a, **k: '{"action":"none"}', adapters={})
    now = time.time()
    beat.beat(now)
    assert not beat.due(now + 400)
    owner.tell(tmp_path, "I got a new phone")
    assert beat.due(now + 400)       # salient event and >= 5 min since the last beat
    assert not beat.due(now + 100)   # minimum gap still holds
