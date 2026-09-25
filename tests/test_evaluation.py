from pathlib import Path
import json

import pytest

from ghost_agent.evaluation import EvaluationCase, EvaluationSuite


def test_evaluation_only_rewards_verified_outcomes(tmp_path: Path) -> None:
    suite = EvaluationSuite(tmp_path)
    case = EvaluationCase("parser", "coding", "Implement parser", {"tests": "pass"})

    result = suite.run(case, "local", lambda _case, _model: {"verified_success": True, "detail": "tests passed"})

    assert result.verified_success is True
    assert suite.reputation.score("local", "coding") is not None


@pytest.mark.parametrize("verdict", [None, [], {}, {"verified_success": "false"},
    {"verified_success": 1}, {"verified_success": True, "verification_failure": True},
    {"verified_success": True, "verification_failure": "false"},
    {"verified_success": True, "repair_attempted": "no"}])
def test_malformed_or_contradictory_evaluation_cannot_reward_model(tmp_path, verdict):
    suite = EvaluationSuite(tmp_path)
    case = EvaluationCase("parser", "coding", "Implement parser", {"tests": "pass"})
    result = suite.run(case, "local", lambda *args: verdict)
    assert result.verified_success is False
    assert result.verification_failure is True
    record = json.loads(suite.reputation.path.read_text(encoding="utf-8"))["local|coding"]
    assert record["successes"] == 0 and record["verification_failures"] == 1
    assert json.loads(suite.path.read_text(encoding="utf-8"))["verified_success"] is False


def test_evaluation_records_original_contract_and_task_class(tmp_path):
    suite = EvaluationSuite(tmp_path)
    case = EvaluationCase("protocol", "protocol", "Select one read action", {"expected": ["read_file"]})
    def mutate(received, model):
        received.case_id = "changed"
        received.task_class = "coding"
        received.acceptance["expected"].append("write_file")
        return {"verified_success": True}
    result = suite.run(case, "local", mutate)
    assert result.case_id == "protocol" and result.task_class == "protocol"
    assert result.acceptance == {"expected": ["read_file"]}
    assert suite.reputation.score("local", "coding") is None
    record = json.loads(suite.path.read_text(encoding="utf-8"))
    assert record["goal"] == "Select one read action"
    assert record["acceptance"] == {"expected": ["read_file"]}
