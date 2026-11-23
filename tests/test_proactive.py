from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from ghost_agent.proactive import (
    Opportunity,
    OpportunityStore,
    ProactiveEngine,
    ProactiveError,
    idle_seconds,
    parse_proposal,
    similar,
)
from ghost_agent.autonomy import OutcomeStore
from ghost_agent.runtime import Scheduler


def proposal(goal="Research how to turn my Rust lessons into a small practice game.", **overrides):
    value = {"title": "Rust practice experiment", "goal": goal,
             "rationale": "The project context mentions learning Rust.", "kind": "learning",
             "confidence": 0.9, "requires_user_input": False}
    value.update(overrides)
    return json.dumps(value)


def test_parse_and_rejects_model_authority_fields():
    parsed = parse_proposal(proposal())
    assert parsed["kind"] == "learning"
    with pytest.raises(ProactiveError):
        parse_proposal(proposal(grant_permission=True))
    with pytest.raises(ProactiveError):
        parse_proposal(proposal(confidence=2))
    with pytest.raises(ProactiveError):
        parse_proposal(proposal(kind="shell"))


def test_tick_queues_one_durable_sandbox_outcome(tmp_path: Path):
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *args, **kwargs: proposal())
    result = engine.tick()
    assert result["status"] == "queued"
    assert len(OutcomeStore(tmp_path).list()) == 1
    assert Scheduler(tmp_path).list()[0].status == "queued"
    job = OutcomeStore(tmp_path).list()[0]
    assert job.acceptance_contract["proactive"] is True
    assert job.acceptance_contract["promotion_requires_approval"] is True
    assert (tmp_path / ".ghost/events.jsonl").read_text()


def test_tick_is_deduplicated_and_second_tick_does_not_add_work(tmp_path: Path):
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *args, **kwargs: proposal())
    assert engine.tick()["status"] == "queued"
    assert engine.tick()["status"] == "duplicate"
    assert len(OutcomeStore(tmp_path).list()) == 1


def test_missing_input_and_unsafe_goal_are_not_queued(tmp_path: Path):
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *args, **kwargs: proposal(
        goal="Diagnose whether my symptoms mean I am sick.", kind="research"))
    result = engine.tick()
    assert result["status"] == "needs_input"
    assert not Scheduler(tmp_path).list()

    engine = ProactiveEngine(tmp_path / "other", "tiny", chat_fn=lambda *args, **kwargs: proposal(
        goal="Research whether a Huawei Health Kit connector is feasible.", kind="integration",
        requires_user_input=True))
    result = engine.tick()
    assert result["status"] == "needs_input"
    assert not Scheduler(tmp_path / "other").list()


def test_no_queue_option_only_records_proposal(tmp_path: Path):
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *args, **kwargs: proposal())
    result = engine.tick(auto_queue=False)
    assert result["status"] == "proposed"
    assert not Scheduler(tmp_path).list()


def test_idle_gate_defers_when_state_is_unknown_or_owner_active(tmp_path: Path):
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *args, **kwargs: proposal(), idle_fn=lambda: None)
    assert engine.tick(require_idle=True)["reason"] == "idle_state_unavailable"
    engine = ProactiveEngine(tmp_path / "active", "tiny", chat_fn=lambda *args, **kwargs: proposal(), idle_fn=lambda: 4)
    assert engine.tick(require_idle=True, idle_after=10)["reason"] == "owner_active"


def test_emergency_stop_blocks_before_model(tmp_path: Path):
    from ghost_agent.runtime import EmergencyStop
    EmergencyStop(tmp_path).engage("test")
    called = []
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *args, **kwargs: called.append(1))
    assert engine.tick()["status"] == "stopped"
    assert not called


def test_dismiss_is_persistent(tmp_path: Path):
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *args, **kwargs: proposal())
    result = engine.tick(auto_queue=False)
    opportunity_id = result["opportunity"]["opportunity_id"]
    assert engine.dismiss(opportunity_id).status == "dismissed"
    assert OpportunityStore(tmp_path).list()[0].status == "dismissed"


def test_explicit_context_file_reaches_planner_and_is_scoped(tmp_path: Path):
    notes = tmp_path / "desksentinel.md"
    notes.write_text("DeskSentinel is an ESP32 pan tilt prototype with two servos.", encoding="utf-8")
    seen = []

    def planner(model, messages, **kwargs):
        seen.append(json.loads(messages[-1]["content"]))
        return proposal(goal="Research a safe DeskSentinel simulator and write a sandbox prototype.", kind="integration")

    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=planner, context_paths=["desksentinel.md"])
    result = engine.tick(auto_queue=False)
    assert result["status"] == "proposed"
    assert seen[0]["provided_context"][0]["excerpt"].startswith("DeskSentinel")
    with pytest.raises(ProactiveError):
        ProactiveEngine(tmp_path, "tiny", chat_fn=planner, context_paths=["../outside.md"]).context()


def test_watch_runs_existing_worker_callback_after_queue(tmp_path: Path, monkeypatch, permissive_policy):
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *args, **kwargs: proposal(), idle_fn=lambda: 1000)
    results = []
    def stop_after_sleep(_interval):
        raise KeyboardInterrupt
    monkeypatch.setattr("ghost_agent.proactive.time.sleep", stop_after_sleep)
    with pytest.raises(KeyboardInterrupt):
        engine.watch(interval=15, idle_after=10,
                     run_worker=lambda: (results.append("worker") or
                                         SimpleNamespace(status="completed", job_id="job", detail="ok")))
    assert results == ["worker"]


# -- OJ09: rejected-idea memory, feedback/stop conditions, bounded backlog -----

def test_dismissed_idea_is_suppressed_and_not_reproposed(tmp_path: Path):
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *a, **k: proposal())
    result = engine.tick(auto_queue=False)
    oid = result["opportunity"]["opportunity_id"]
    assert engine.dismiss(oid).status == "dismissed"
    # identical goal must not reappear as a new proposal
    again = engine.tick(auto_queue=False)
    assert again["status"] == "suppressed"
    assert again["opportunity"]["status"] == "dismissed"


def test_completed_idea_is_suppressed(tmp_path: Path):
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *a, **k: proposal())
    result = engine.tick(auto_queue=False)
    oid = result["opportunity"]["opportunity_id"]
    # simulate a user decision: mark completed via direct feedback helper
    from ghost_agent.proactive import Opportunity
    op = engine.store.find_any(oid)
    op.status = "queued"
    engine.store.save(op)
    engine.record_feedback(oid, "tests_passed")
    assert engine.store.find_any(oid).status == "completed"
    again = engine.tick(auto_queue=False)
    assert again["status"] == "suppressed"


def test_rejected_idea_is_suppressed(tmp_path: Path):
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *a, **k: proposal())
    result = engine.tick(auto_queue=False)
    oid = result["opportunity"]["opportunity_id"]
    op = engine.store.find_any(oid)
    op.status = "queued"
    engine.store.save(op)
    engine.record_feedback(oid, "tests_failed", evidence="flaky due to timing")
    assert engine.store.find_any(oid).status == "rejected"
    again = engine.tick(auto_queue=False)
    assert again["status"] == "suppressed"


def test_needs_input_idea_is_not_suppressed(tmp_path: Path):
    # requires_user_input is a legitimate defer, not a rejection — it may re-propose
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *a, **k: proposal(
        goal="Explain why the system is slow (report only).", requires_user_input=True))
    first = engine.tick(auto_queue=False)
    assert first["status"] == "needs_input"
    # a later tick can propose it again (status is no longer suppressed)
    op = engine.store.find_any(first["opportunity"]["opportunity_id"])
    assert op.status == "needs_input"
    assert engine.store.find_suppressed(op.opportunity_id) is None


def test_backlog_is_bounded(tmp_path: Path):
    # queue three distinct high-confidence proposals, then a fourth different one;
    # the backlog of non-queued proposals must stay <= max_proposed.
    engine = ProactiveEngine(tmp_path, "tiny")
    goals = [f"Research topic {i} and build a sandbox prototype." for i in range(4)]
    def planner(model, messages, **kwargs):
        # infer topic from the last queue maybe; simplest: return distinct goals
        idx = planner._idx
        planner._idx += 1
        return proposal(goal=goals[idx % len(goals)])
    planner._idx = 0
    engine.chat = planner
    statuses = [engine.tick(auto_queue=False)["status"] for _ in range(4)]
    assert all(s == "proposed" for s in statuses)
    # after capping, at most 3 remain proposed
    remaining = [o for o in engine.store.list(100) if o.status == "proposed"]
    assert len(remaining) <= 3
    # the evicted one is DISMISSED (rejected-idea memory), not deleted
    dismissed = [o for o in engine.store.list(100) if o.status == "dismissed"]
    assert len(dismissed) >= 1


def test_record_feedback_requires_queued_or_running(tmp_path: Path):
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *a, **k: proposal())
    oid = engine.tick(auto_queue=False)["opportunity"]["opportunity_id"]
    # proposed -> invalid for feedback
    with pytest.raises(ProactiveError, match="Cannot record feedback"):
        engine.record_feedback(oid, "tests_passed")
    # put it in a queued state, then an invalid outcome is rejected
    op = engine.store.find_any(oid)
    op.status = "queued"
    engine.store.save(op)
    with pytest.raises(ProactiveError, match="outcome"):
        engine.record_feedback(oid, "not-a-real-outcome")


def test_queued_job_budget_values(tmp_path: Path):
    from ghost_agent.proactive import queue_sandbox_outcome
    job = queue_sandbox_outcome(tmp_path, "build a small calculator", "scripted-model")
    assert job.iterations == 30
    assert job.acceptance_contract["command_budget_seconds"] == 1800
    assert job.acceptance_contract["command_budget_calls"] == 96
    scheduler = Scheduler(tmp_path)
    scheduled = scheduler.list()[0]
    assert scheduled.budget["iterations"] == 30


def test_opportunity_store_methods_defined_once():
    import inspect
    import ghost_agent.proactive as module
    src = inspect.getsource(module)
    find_any_count = src.count("    def find_any(")
    find_suppressed_count = src.count("    def find_suppressed(")
    assert find_any_count == 1, f"find_any defined {find_any_count} times (expected 1)"
    assert find_suppressed_count == 1, f"find_suppressed defined {find_suppressed_count} times (expected 1)"


# --- TD1: structured opportunity records and evidence ---

def test_old_opportunities_json_loads_with_unknown_and_missing_keys(tmp_path: Path):
    store = OpportunityStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    legacy = [{"opportunity_id": "opp_legacy", "title": "Old", "goal": "Do a thing",
               "rationale": "Because", "kind": "research", "confidence": 0.5,
               "requires_user_input": False, "from_the_future": {"x": 1}}]
    store.path.write_text(json.dumps(legacy), encoding="utf-8")
    loaded = store.list_all()
    assert loaded[0].opportunity_id == "opp_legacy"
    assert loaded[0].trigger == "tick"          # missing key -> default
    assert loaded[0].evidence_items == []
    store.save(loaded[0])                        # still saveable


def test_parse_proposal_drops_invalid_alternatives_and_risks():
    parsed = parse_proposal(proposal(
        alternatives=["valid alternative", 42, "x" * 300, "", "third", "fourth"],
        risks="not a list"))
    assert parsed["alternatives"] == ["valid alternative", "third", "fourth"]  # invalid dropped, cap 3
    assert parsed["risks"] == []  # non-list dropped entirely


def test_parse_proposal_rejects_unknown_fields():
    with pytest.raises(ProactiveError):
        parse_proposal(proposal(goal_ids=["opp_x"], evidence=[{"source": "s", "excerpt": "e"}]))


def test_tick_record_has_deterministic_evidence_goal_ids_trigger(tmp_path: Path, monkeypatch):
    context_file = tmp_path / "notes.md"
    context_file.write_text("Rust practice notes: build a small practice game.\n" * 10,
                            encoding="utf-8")
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *a, **k: proposal(),
                             context_paths=["notes.md"])
    result = engine.tick(auto_queue=False)
    record = result["opportunity"]
    assert record["trigger"] == "tick"
    assert record["evidence_items"][0]["source"] == "notes.md"
    assert len(record["evidence_items"][0]["excerpt"]) <= 300
    assert record["review_at"] is not None
    assert record["attempts"] == 0
    # model-supplied alternatives/risks flow through; goal_ids stays empty here
    assert record["goal_ids"] == []


def test_tick_matches_active_goal_tokens_into_goal_ids_and_evidence(tmp_path: Path):
    from ghost_agent.kernel import GoalStore
    goals = GoalStore(tmp_path)
    goal = goals.create("Rust practice game plan", kind="focus")
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *a, **k: proposal())
    record = engine.tick(auto_queue=False)["opportunity"]
    assert record["goal_ids"] == [goal.goal_id]
    assert any(item["source"] == "active_goal" and item["excerpt"] == goal.goal_id
               for item in record["evidence_items"])


# --- TD2: stale-goal and deadline triggers ---

def test_stale_goal_appears_in_context_and_sets_stale_trigger(tmp_path: Path):
    from ghost_agent.kernel import GoalStore
    store = GoalStore(tmp_path)
    expired = store.create("Old report draft", kind="focus",
                           deadline_at=time.time() - 60)
    fresh = store.create("Fresh plan", kind="focus", deadline_at=time.time() + 3600)
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *a, **k: proposal())
    record = engine.tick(auto_queue=False)["opportunity"]
    assert record["trigger"] == "stale_goal"
    # mark_stale persisted the lifecycle change during tick's context() call
    assert store.get(expired.goal_id).status == "stale"
    assert store.get(fresh.goal_id).status == "active"
    context = engine.context()
    assert context.get("stale_goals", []) == []
    assert [entry["goal_id"] for entry in context["upcoming_deadlines"]] == [fresh.goal_id]


def test_mark_stale_throttled_to_once_per_six_hours(tmp_path: Path):
    from ghost_agent.kernel import GoalStore
    store = GoalStore(tmp_path)
    first = store.create("Old report draft", kind="focus", deadline_at=1.0)
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *a, **k: proposal())
    engine.context()  # first run: marks stale, writes state
    state_path = tmp_path / ".ghost" / "proactive_state.json"
    assert "mark_stale_at" in json.loads(state_path.read_text(encoding="utf-8"))
    # a goal that expires after the throttled run is NOT flipped while throttled
    late = store.create("Later report draft", kind="focus", deadline_at=2.0)
    engine.context()
    assert store.get(late.goal_id).status == "active"
    # after the 6 h window the throttle expires and late goal is marked
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["mark_stale_at"] -= 6 * 3600 + 1
    state_path.write_text(json.dumps(state), encoding="utf-8")
    engine.context()
    assert store.get(late.goal_id).status == "stale"


def test_no_goals_yields_empty_stale_and_deadline_lists(tmp_path: Path):
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *a, **k: proposal())
    context = engine.context()
    assert context.get("stale_goals", []) == []
    assert context.get("upcoming_deadlines", []) == []
    # key presence drives TD1's trigger, so absent when empty
    assert "stale_goals" not in context
    assert "upcoming_deadlines" not in context


# --- TD3: similar(), reworded rejection memory, less of this ---

def test_similar_jaccard_cases():
    from ghost_agent.proactive import similar
    # tokenization: punctuation/case are normalized to word tokens
    assert similar("Hello, World!", "hello world") == 1.0
    # tokens shorter than 3 characters are ignored
    assert similar("a an the", "zz qq") == 0.0
    # tokens of length >= 3 are counted
    assert similar("rust compiler", "rust compiler") == 1.0
    # stopwords are removed before comparison
    assert similar("the cat and the dog", "cat dog") == 1.0
    # disjoint vocabularies -> 0.0
    assert similar("rust compiler backend", "garden watering schedule") == 0.0
    # identical -> 1.0
    assert similar("turn my Rust lessons into a practice game",
                   "turn my Rust lessons into a practice game") == 1.0
    # empty inputs -> 0.0
    assert similar("", "anything here") == 0.0
    assert similar("anything", "") == 0.0
    assert similar("", "") == 0.0


def _rejected_opportunity(tmp_path: Path, goal: str):
    """Queue a proposal then fail it so it becomes a rejected memory."""
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *a, **k: proposal(goal=goal))
    result = engine.tick(auto_queue=False)
    oid = result["opportunity"]["opportunity_id"]
    op = engine.store.find_any(oid)
    op.status = "queued"
    engine.store.save(op)
    engine.record_feedback(oid, "tests_failed", evidence="did not work")
    assert engine.store.find_any(oid).status == "rejected"
    return engine, oid


def test_reworded_similar_rejected_idea_is_suppressed_and_not_queued(tmp_path: Path):
    original = "Research how to turn my Rust lessons into a small practice game."
    engine, oid = _rejected_opportunity(tmp_path, original)
    # same meaning, different wording: must still cross the 0.6 threshold
    reworded = "Turn my Rust lessons into a small practice game to learn faster."
    assert similar(original, reworded) >= 0.6
    engine.chat = lambda *a, **k: proposal(goal=reworded)
    result = engine.tick()
    assert result["status"] == "suppressed"
    record = result["opportunity"]
    assert record["status"] == "rejected"
    assert record["rejection_reason"].startswith(f"similar to {oid} (")
    assert not Scheduler(tmp_path).list()


def test_unrelated_idea_is_not_suppressed_by_rejection_memory(tmp_path: Path):
    original = "Research how to turn my Rust lessons into a small practice game."
    engine, _ = _rejected_opportunity(tmp_path, original)
    unrelated = "Investigate whether a Huawei Health Kit connector is feasible."
    assert similar(original, unrelated) < 0.6
    engine.chat = lambda *a, **k: proposal(goal=unrelated, kind="integration")
    result = engine.tick()
    # an unrelated idea is queued as normal, no suppression
    assert result["status"] == "queued"


def test_old_rejections_do_not_suppress_similar_new_ideas(tmp_path: Path):
    original = "Research how to turn my Rust lessons into a small practice game."
    engine, oid = _rejected_opportunity(tmp_path, original)
    # age the rejection past the 30-day memory window
    old = engine.store.find_any(oid)
    old.created_at = time.time() - 31 * 86400
    engine.store.save(old)
    engine.chat = lambda *a, **k: proposal(
        goal="Turn my Rust lessons into a small practice game to learn faster.")
    result = engine.tick(auto_queue=False)
    assert result["status"] == "proposed"
    assert result["opportunity"]["status"] == "proposed"


def test_mark_less_like_this_marks_and_dismisses_proposed_only(tmp_path: Path):
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *a, **k: proposal())
    # still proposed: marked and dismissed
    proposed = engine.tick(auto_queue=False)["opportunity"]["opportunity_id"]
    marked = engine.mark_less_like_this(proposed)
    assert marked.less_like_this is True
    assert marked.status == "dismissed"
    assert engine.store.find_any(proposed).less_like_this is True
    # queued: marked but NOT dismissed
    second = ProactiveEngine(tmp_path, "tiny",
                             chat_fn=lambda *a, **k: proposal(goal="Build a tiny calculator app."))
    queued_id = second.tick()["opportunity"]["opportunity_id"]
    marked_queued = second.mark_less_like_this(queued_id)
    assert marked_queued.less_like_this is True
    assert marked_queued.status == "queued"
    # missing id raises
    with pytest.raises(ProactiveError, match="not found"):
        second.mark_less_like_this("does_not_exist")


# --- TD4: one clarifying question for needs_input ---

def test_parse_proposal_question_dropped_when_invalid_or_overlong():
    assert parse_proposal(proposal(question="Should I target a weekly or daily schedule?"))["question"] == \
        "Should I target a weekly or daily schedule?"
    assert parse_proposal(proposal(question="   "))["question"] == ""      # blank dropped
    assert parse_proposal(proposal(question=42))["question"] == ""          # non-text dropped
    assert parse_proposal(proposal(question="x" * 241))["question"] == ""   # overlong dropped
    assert parse_proposal(proposal(question="x" * 240))["question"] == "x" * 240


def test_needs_input_with_question_notifies_once_across_ticks(tmp_path: Path):
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *a, **k: proposal(
        goal="Explain why the system is slow (report only).",
        requires_user_input=True,
        question="Which workload felt slow, and when?"))
    first = engine.tick(auto_queue=False)
    assert first["status"] == "needs_input"
    assert first["opportunity"]["question"] == "Which workload felt slow, and when?"
    second = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *a, **k: proposal(
        goal="Explain why the system is slow (report only).",
        requires_user_input=True,
        question="Which workload felt slow, and when?")).tick(auto_queue=False)
    assert second["status"] == "needs_input"  # needs_input is not suppressed
    from ghost_agent.runtime import AttentionManager
    notes = AttentionManager(tmp_path).pending()
    assert len(notes) == 1  # deduplicated by notification_id across both ticks
    assert notes[0].notification_id == f"opp-question-{first['opportunity']['opportunity_id']}"
    assert notes[0].priority == "normal"
    assert notes[0].source == "proactive"
    assert notes[0].message == (
        f"Ghost needs input: Which workload felt slow, and when? "
        f"(opportunity {first['opportunity']['opportunity_id']})")


def test_needs_input_without_question_sends_no_notification(tmp_path: Path):
    engine = ProactiveEngine(tmp_path, "tiny", chat_fn=lambda *a, **k: proposal(
        goal="Diagnose whether my symptoms mean I am sick.", kind="research"))
    result = engine.tick()
    assert result["status"] == "needs_input"
    from ghost_agent.runtime import AttentionManager
    assert AttentionManager(tmp_path).pending() == []
