from pathlib import Path

from ghost_agent.models.routing import ModelOrchestrator, ModelReputationStore, TaskClassifier


def test_classifier_identifies_research_and_complexity() -> None:
    task_class, complexity = TaskClassifier().classify(
        "Research literature evidence and citations for microbial iron cycling carbon export mechanisms"
    )
    assert task_class == "research"
    assert complexity in {"medium", "high"}


def test_orchestrator_uses_measured_reputation(tmp_path: Path) -> None:
    reputation = ModelReputationStore(tmp_path)
    reputation.record("reliable", "coding", True, context_chars=1000)
    reputation.record("unreliable", "coding", False, verification_failure=True)

    route = ModelOrchestrator(reputation).route("Implement a parser", ["unreliable", "reliable"], "unreliable")

    assert route["primary"] == "reliable"
    assert route["verifier"] in {"reliable", "unreliable"}
    assert route["planner"] in {"reliable", "unreliable"}
    assert route["critic"] in {"reliable", "unreliable"}
