from ghost_agent.intent import IntentStore


def test_intent_store_keeps_request_outcome_and_plan_separate(tmp_path) -> None:
    store = IntentStore(tmp_path)
    record = store.capture("Build it", "Build a sandboxed parser", "goal_1", "Which input format?")
    planned = store.set_plan(record.intent_id, ["inspect", "implement", "verify"])

    assert planned.user_request == "Build it"
    assert planned.inferred_outcome == "Build a sandboxed parser"
    assert planned.status == "planned"
