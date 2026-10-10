import json

from ghost_agent import orchestrator as module
from ghost_agent.orchestrator import SYSTEM_PROMPT


def test_default_max_output_tokens_raised_to_4096(tmp_path):
    agent = module.Orchestrator(tmp_path)
    assert agent.max_output_tokens == 4096
    assert agent.max_output_tokens <= 8192


def test_system_prompt_has_new_rules_and_no_placeholder_content():
    assert '"thought" is at most two short sentences. Never put code in "thought".' in SYSTEM_PROMPT
    assert "- Write at most ONE file per response; you will get more turns." in SYSTEM_PROMPT
    assert "- Use web_search only when the task needs information that is not in the workspace or the task description." in SYSTEM_PROMPT
    assert "ALWAYS use web_search" not in SYSTEM_PROMPT
    assert "FIRST use web_search" not in SYSTEM_PROMPT
    assert "# updated content" not in SYSTEM_PROMPT


def _truncate_runner(monkeypatch, response_text):
    value = object.__new__(module.Orchestrator)
    value.model = "scripted"
    value.base_url = None
    value.timeout = 1
    value.tui = None
    value.verbose = False
    value.max_input_chars = 24_000
    value.max_output_tokens = 4096
    monkeypatch.setattr(module, "ollama_chat",
                        lambda *args, **kwargs: response_text)

    class Log:
        records = []

        def log(self, record):
            self.records.append(record)

    return value, Log()


def test_truncated_response_reports_cutoff(monkeypatch):
    truncated = ('{"thought": "write it", "actions": [{"tool": "write_file",'
                 ' "path": "app.py", "content": "def add(a')
    value, log = _truncate_runner(monkeypatch, truncated)
    response, error = value._model_turn([], "Implement", log, "")
    assert error == ("Your response was cut off because it was too long. "
                     "Keep thought to one sentence and write only ONE file per response.")
    assert response.thought == "truncated"


def test_complete_json_response_reports_normal_parse_error(monkeypatch):
    value, log = _truncate_runner(monkeypatch, "{not json at all}")
    response, error = value._model_turn([], "Implement", log, "")
    assert "Could not parse a valid action response" in error
    assert response.thought == "invalid_json"


def test_failed_plan_goes_to_implement(tmp_path, monkeypatch):
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps({
        "id": "planfail", "title": "Feature",
        "goal": "Implement with tests", "context": {},
        "constraints": {"iteration_limit": 5}}))

    calls = {"n": 0}

    def model(_model, messages, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            # Plan turn: invalid JSON (parse failure)
            return "this is not json"
        return json.dumps({
            "thought": "implement",
            "actions": [
                {"tool": "write_file", "path": "app.py",
                 "content": "answer = 42\n"},
                {"tool": "write_file", "path": "test_app.py",
                 "content": "from app import answer\ndef test_answer():\n"
                            "    assert answer == 42\n"},
            ]})

    monkeypatch.setattr(module, "ollama_chat", model)
    agent = module.Orchestrator(tmp_path, model="scripted")
    agent.run_task(task_path)
    state = json.loads(
        (tmp_path / "workspaces" / "planfail" / "state.json").read_text())
    assert state["last_result"] == "tests_passed"
    assert state["turns"]["IMPLEMENT"] >= 1
    # plan feedback became the repair-prompt context; last_feedback after a
    # successful run reflects the final VERIFY results instead.
    assert state["last_result"] == "tests_passed"
