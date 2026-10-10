from pathlib import Path
import json

import pytest

from ghost_agent.context_manager import ContextAssembler, MemoryRecord, ValidatedMemoryStore
from ghost_agent.kernel import GoalStore
from ghost_agent.runtime import Event, EventBus


def test_validated_memory_expires_and_can_be_retracted(tmp_path: Path) -> None:
    store = ValidatedMemoryStore(tmp_path)
    record = store.remember(MemoryRecord("m1", "semantic", "The service is running", "healthcheck", 0.9,
                                         validation="validated"))
    assert store.active("validated")[0].memory_id == record.memory_id

    store.invalidate("m1", "service stopped")
    assert not store.active()


def test_context_assembler_includes_current_goals_validated_memory_and_events(tmp_path: Path) -> None:
    goal = GoalStore(tmp_path).create("Build a bridge")
    ValidatedMemoryStore(tmp_path).remember(MemoryRecord(
        "m2", "semantic", "Bridge API requires a token", "research", 0.8, validation="validated"
    ))
    EventBus(tmp_path).emit(Event("job.queued", {"goal_id": goal.goal_id}))

    context = ContextAssembler(tmp_path).assemble(goal.goal_id)

    assert context["goal"]["goal_id"] == goal.goal_id
    assert context["validated_memories"][0]["memory_id"] == "m2"
    assert context["recent_events"][0]["kind"] == "job.queued"


def test_context_assembler_surfaces_contradictory_memories(tmp_path: Path) -> None:
    store = ValidatedMemoryStore(tmp_path)
    store.remember(MemoryRecord(
        "c1", "semantic", "Latency for service is 100 ms", "probe", 0.9, validation="validated"
    ))
    store.remember(MemoryRecord(
        "c2", "semantic", "Latency for service is 900 ms", "probe", 0.9, validation="validated"
    ))

    context = ContextAssembler(tmp_path).assemble()

    assert context["conflicts"] == [{
        "a": "c1", "b": "c2", "reason": "Conflicting values for 'latency for service': ['100', '900']"
    }]
    assert context["conflict_warnings"] == [
        "Conflicting records: c1 vs c2 (Conflicting values for 'latency for service': ['100', '900']);"
        " do not assume either is true."
    ]


def test_context_assembler_omits_conflicts_when_no_contradiction(tmp_path: Path) -> None:
    ValidatedMemoryStore(tmp_path).remember(MemoryRecord(
        "m3", "semantic", "Bridge API requires a token", "research", 0.8, validation="validated"
    ))

    context = ContextAssembler(tmp_path).assemble()

    assert "conflicts" not in context
    assert "conflict_warnings" not in context


def test_context_assembler_drops_conflicts_before_required_content(tmp_path: Path) -> None:
    store = ValidatedMemoryStore(tmp_path)
    store.remember(MemoryRecord(
        "c1", "semantic", "Latency for service is 100 ms", "probe", 0.9, validation="validated"
    ))
    store.remember(MemoryRecord(
        "c2", "semantic", "Latency for service is 900 ms", "probe", 0.9, validation="validated"
    ))
    goal = GoalStore(tmp_path).create("Build a bridge")
    context = ContextAssembler(tmp_path).assemble(goal.goal_id, max_chars=1100)

    assert not context["conflicts"]
    assert not context["conflict_warnings"]
    assert context["omitted_for_budget"]["conflicts"] == 1
    assert context["omitted_for_budget"]["conflict_warnings"] == 1
    assert context["goal"]["goal_id"] == goal.goal_id
    assert context["validated_memories"]


def test_memory_store_rejects_unknown_classes_and_context_includes_fresh_world(tmp_path: Path) -> None:
    store = ValidatedMemoryStore(tmp_path)
    with pytest.raises(ValueError, match="memory kind"):
        store.remember(MemoryRecord("invalid", "fiction", "no", "test", 0.5))

    from ghost_agent.runtime import Observation, WorldModel
    WorldModel(tmp_path).observe(Observation("service.api", "service", {"status": "up"}, "health"))
    context = ContextAssembler(tmp_path).assemble()
    assert context["world_state"][0]["entity_id"] == "service.api"


def test_context_budget_drops_whole_records_but_keeps_user_constraints(tmp_path):
    from ghost_agent.identity import IdentityStore, UserProfile
    IdentityStore(tmp_path).save(UserProfile(policies=["Ask before deployment"], prohibitions=["Never send messages"]))
    goal = GoalStore(tmp_path).create("Review project", constraints=["No file modifications"])
    store = ValidatedMemoryStore(tmp_path)
    store.remember(MemoryRecord("large", "semantic", "Evidence. " * 3000, "test", 1.0, validation="validated"))
    EventBus(tmp_path).emit(Event("oversized", {"text": "x" * 10000}))
    result = ContextAssembler(tmp_path).assemble(goal.goal_id, max_chars=1500)
    assert len(json.dumps(result, ensure_ascii=False)) <= 1500
    assert result["user"]["prohibitions"] == ["Never send messages"]
    assert result["goal"]["constraints"] == ["No file modifications"]
    assert result["omitted_for_budget"]["validated_memories"] == 1
    assert result["omitted_for_budget"]["recent_events"] == 1
    assert not result["validated_memories"]
    assert not result["active_goals"]  # Selected goal is not duplicated.


def test_oversized_required_profile_is_not_silently_truncated(tmp_path):
    from ghost_agent.identity import IdentityStore, UserProfile
    IdentityStore(tmp_path).save(UserProfile(prohibitions=["Do not " + "x" * 5000]))
    with pytest.raises(ValueError, match="complete user profile"):
        ContextAssembler(tmp_path).assemble(max_chars=1000)


def test_oversized_selected_goal_is_not_silently_truncated(tmp_path):
    goal = GoalStore(tmp_path).create("Task", constraints=["x" * 5000])
    with pytest.raises(ValueError, match="complete user profile and goal"):
        ContextAssembler(tmp_path).assemble(goal.goal_id, max_chars=1000)


@pytest.mark.parametrize("budget", [0, -1, True, 1.5, "8000"])
def test_invalid_context_budgets_are_rejected(tmp_path, budget):
    with pytest.raises(ValueError, match="max_chars"):
        ContextAssembler(tmp_path).assemble(max_chars=budget)


def test_requested_missing_goal_does_not_fall_back_to_unscoped_context(tmp_path):
    with pytest.raises(ValueError, match="goal is missing"):
        ContextAssembler(tmp_path).assemble("goal_missing")
