from pathlib import Path

from ghost_agent.models.routing import ModelReputationStore, ModelRouter


def test_model_router_prefers_measured_verified_success(tmp_path: Path) -> None:
    reputation = ModelReputationStore(tmp_path)
    reputation.record("small", "coding", False, verification_failure=True)
    reputation.record("large", "coding", True)
    router = ModelRouter(reputation)

    assert router.choose("coding", ["small", "large"], "small") == "large"


def test_model_router_uses_fallback_without_evidence(tmp_path: Path) -> None:
    router = ModelRouter(ModelReputationStore(tmp_path))

    assert router.choose("research", ["candidate"], "fallback") == "fallback"
