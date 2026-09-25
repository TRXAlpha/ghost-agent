import json

import pytest

from ghost_agent import protocol_benchmark as benchmark


@pytest.mark.parametrize("case", benchmark.CASES + benchmark.VALIDATION_CASES, ids=lambda case: case["id"])
def test_fixed_oracle_accepts_expected_actions(case):
    verdict = benchmark.check_response(json.dumps({"actions": case["actions"]}), case["actions"])
    assert verdict["exact_actions"] and verdict["strict_json"]


def test_parseable_wrong_action_is_not_a_success():
    result = benchmark.check_response('{"actions":[{"tool":"run_cmd","cmd":"pytest"}]}', [])
    assert result["parseable"] and not result["exact_actions"]


def test_parser_repair_does_not_count_as_strict_compliance():
    result = benchmark.check_response('```json\n{"actions": []}\n```', [])
    assert result["exact_actions"] and not result["strict_json"]


def test_repeatable_runner_records_outputs_without_executing_actions(tmp_path, monkeypatch):
    monkeypatch.setattr(benchmark, "fetch_models", lambda url: [{"name": "test", "digest": "test-digest"}])
    def chat(**kwargs):
        case = next(case for case in benchmark.CASES if case["prompt"] == kwargs["messages"][1]["content"])
        return json.dumps({"actions": case["actions"]})
    path = benchmark.run_benchmark(tmp_path, model="test", chat=chat)
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["summary"] == {"total": 8, "exact_actions": 8, "parseable": 8, "strict_json": 8}
    assert report["status"] == "completed"
    assert not (tmp_path / "answer.txt").exists()
    assert not (tmp_path / ".ghost").exists()


def test_transport_failure_never_becomes_success(tmp_path, monkeypatch):
    monkeypatch.setattr(benchmark, "fetch_models", lambda url: [{"name": "test"}])
    def fail(**kwargs):
        raise TimeoutError("server timed out")
    path = benchmark.run_benchmark(tmp_path, model="test", repetitions=1, chat=fail)
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["summary"]["exact_actions"] == 0
    assert all("transport_error" in result for result in report["results"])


@pytest.mark.parametrize("style", ["production", "compact"])
def test_validation_uses_selected_prompt_and_frozen_oracles(tmp_path, monkeypatch, style):
    monkeypatch.setattr(benchmark, "fetch_models", lambda url: [{"name": "test"}])
    expected_prompt = benchmark.SYSTEM_PROMPT if style == "production" else benchmark.COMPACT_PROMPT
    def chat(**kwargs):
        assert kwargs["messages"][0]["content"] == expected_prompt
        case = next(case for case in benchmark.VALIDATION_CASES
                    if case["prompt"] == kwargs["messages"][1]["content"])
        return json.dumps({"actions": case["actions"]})
    path = benchmark.run_benchmark(tmp_path, model="test", repetitions=1, chat=chat,
                                   prompt_style=style, dataset="validation")
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["summary"]["exact_actions"] == 6
    assert report["cases"] == benchmark.VALIDATION_CASES
    assert report["system_prompt"] == expected_prompt
    assert report["prompt_style"] == style
    assert report["dataset"] == "validation"


@pytest.mark.parametrize("settings", [{"prompt_style": "unknown"}, {"dataset": "unknown"}, {"output_format": "unknown"}])
def test_unknown_comparison_configuration_is_rejected(tmp_path, settings):
    with pytest.raises(ValueError, match="Unknown"):
        benchmark.run_benchmark(tmp_path, **settings)


@pytest.mark.parametrize("mode", ["unconstrained", "json", "schema"])
def test_output_constraint_is_sent_and_recorded(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(benchmark, "fetch_models", lambda url: [{"name": "test"}])
    expected = (benchmark.ActionResponse.model_json_schema() if mode == "schema"
                else "json" if mode == "json" else None)
    def chat(**kwargs):
        assert kwargs["response_format"] == expected
        return '{"actions":[]}'
    path = benchmark.run_benchmark(tmp_path, model="test", repetitions=1, chat=chat, output_format=mode)
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["output_format"] == mode
    assert report["format"] == expected
