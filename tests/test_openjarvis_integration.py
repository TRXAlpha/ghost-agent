"""Real pinned OpenJarvis classes; scripted localhost HTTP is not a live-model eval."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("openjarvis")

from ghost_agent.openjarvis_bridge import assistant
from ghost_agent.openjarvis_bridge.runtime import EngineSettings, IntegrationError, chat
from ghost_agent.runtime import EmergencyStop


@pytest.fixture
def endpoint(monkeypatch):
    calls = []
    replies = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append({"path": self.path, "body": payload})
            reply = replies.pop(0) if replies else {"message": {"content": "hello"}, "eval_count": 2}
            if payload.get("stream"):
                body = (json.dumps({"message": {"content": "hel"}}) + "\n" +
                        json.dumps({"message": {"content": "lo"}, "done": True, "eval_count": 2}) + "\n").encode()
            else:
                body = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("GHOST_OPENJARVIS_URL", url)
    monkeypatch.setenv("GHOST_OPENJARVIS_ENGINE", "ollama")
    monkeypatch.delenv("GHOST_ALLOW_REMOTE_INFERENCE", raising=False)
    try:
        yield url, calls, replies
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_real_engine_protocol_and_tokens(endpoint):
    _, calls, _ = endpoint
    assert chat("tiny", [{"role": "user", "content": "hi"}], options={"num_predict": 32}) == "hello"
    assert calls[0]["path"] == "/api/chat"
    assert calls[0]["body"]["options"]["num_predict"] == 32
    assert not calls[0]["body"].get("tools")


def test_streaming_uses_upstream_and_delivers_callback_once(endpoint):
    seen = []
    assert "".join(chat("tiny", [{"role": "user", "content": "hi"}], stream=True, on_token=seen.append)) == "hello"
    assert seen == ["hel", "lo"]


def test_stream_early_close_releases_upstream_clients(endpoint, monkeypatch):
    import openjarvis.engine.ollama as upstream
    original = upstream.OllamaEngine
    engines = []
    def tracked(*args, **kwargs):
        engine = original(*args, **kwargs)
        engines.append(engine)
        return engine
    monkeypatch.setattr(upstream, "OllamaEngine", tracked)
    stream = chat("tiny", [{"role": "user", "content": "hi"}], stream=True)
    assert next(stream) == "hel"
    stream.close()
    assert engines[0]._client.is_closed
    assert engines[0]._async_client is None


def test_connection_failure_has_no_provider_fallback(monkeypatch):
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setenv("GHOST_OPENJARVIS_ENGINE", "ollama")
    monkeypatch.setenv("GHOST_OPENJARVIS_URL", f"http://127.0.0.1:{port}")
    from openjarvis.engine._base import EngineConnectionError
    with pytest.raises(EngineConnectionError):
        chat("tiny", [{"role": "user", "content": "hi"}], timeout=0.2)


def test_json_mode_is_not_lost_when_stream_requested(endpoint):
    _, calls, _ = endpoint
    assert list(chat("tiny", [{"role": "user", "content": "hi"}], stream=True, response_format="json")) == ["hello"]
    assert calls[0]["body"]["format"] == "json"
    assert calls[0]["body"]["stream"] is False


def test_unimplemented_schema_enforcement_is_honest(endpoint):
    with pytest.raises(IntegrationError, match="schema enforcement"):
        chat("tiny", [{"role": "user", "content": "hi"}], response_format={"type": "object"})
    assert not endpoint[1]


def test_real_openai_compatible_engine(endpoint, monkeypatch):
    _, calls, replies = endpoint
    monkeypatch.setenv("GHOST_OPENJARVIS_ENGINE", "openai-compat")
    replies.append({"choices": [{"message": {"content": "compatible"}, "finish_reason": "stop"}], "usage": {"total_tokens": 2}})
    assert chat("arbitrary-model", [{"role": "user", "content": "hi"}]) == "compatible"
    assert calls[0]["path"] == "/v1/chat/completions"
    assert calls[0]["body"]["model"] == "arbitrary-model"


@pytest.mark.parametrize("options", [{"num_predict": 0}, {"num_predict": True}, {"temperature": float("nan")}, {"seed": 42}])
def test_invalid_options_never_contact_endpoint(endpoint, options):
    with pytest.raises(IntegrationError):
        chat("tiny", [{"role": "user", "content": "hi"}], options=options)
    assert not endpoint[1]


def test_native_tool_response_never_executes(endpoint):
    endpoint[2].append({"message": {"tool_calls": [{"function": {"name": "shell_exec", "arguments": {"command": "bad"}}}]}})
    with pytest.raises(IntegrationError, match="nothing was executed"):
        chat("tiny", [{"role": "user", "content": "hi"}])


def test_selected_backend_reaches_upstream_from_ghost_import(endpoint, monkeypatch):
    from ghost_agent import orchestrator, cli
    monkeypatch.setenv("GHOST_INFERENCE_BACKEND", "openjarvis")
    for call in [orchestrator.ollama_chat, cli.ollama_chat]:
        assert call("tiny", [{"role": "user", "content": "hi"}]) == "hello"
    assert len(endpoint[1]) == 2


def test_actual_agent_denies_unregistered_tool_and_continues(endpoint, tmp_path):
    url, calls, replies = endpoint
    replies.extend([
        {"message": {"tool_calls": [{"function": {"name": "shell_exec", "arguments": {"command": "should never run"}}}]}},
        {"message": {"content": "I cannot execute that."}},
    ])
    result = assistant.ask(tmp_path, "Try a tool", "tiny", settings=EngineSettings(endpoint=url))
    assert result["status"] == "completed"
    assert result["tools"] == [{"name": "shell_exec", "success": False}]
    assert len(calls) == 2
    assert not calls[0]["body"].get("tools")
    assert "not approved" in calls[1]["body"]["messages"][-1]["content"]


def test_actual_agent_respects_stop_before_inference(endpoint, tmp_path):
    EmergencyStop(tmp_path).engage("test")
    with pytest.raises(IntegrationError, match="Emergency stop"):
        assistant.ask(tmp_path, "Hi", "tiny")
    assert not endpoint[1]


def test_upstream_sessions_survive_restart(endpoint, tmp_path):
    first = assistant.ask(tmp_path, "My lesson is Rust", "tiny")
    second = assistant.ask(tmp_path, "What was my lesson?", "tiny")
    assert first["session_id"] == second["session_id"]
    assert any(message["content"] == "My lesson is Rust" for message in endpoint[1][-1]["body"]["messages"])
    receipts = list((tmp_path / ".ghost/openjarvis/receipts").glob("*.json"))
    assert len(receipts) == 2
    assert all("My lesson" not in path.read_text() for path in receipts)


def test_native_memory_correction_delete_and_project_isolation(tmp_path):
    pytest.importorskip("openjarvis_rust")
    from ghost_agent.openjarvis_bridge.memory import ProjectMemory

    lesson = tmp_path / "lesson.md"
    lesson.write_text("Rust ownership prevents accidental aliasing.", encoding="utf-8")
    with ProjectMemory(tmp_path) as memory:
        first = memory.index("lesson.md")
        assert memory.search("ownership")[0]["source"] == "lesson.md"
        assert memory.search("ownership")[0]["metadata"]["source_sha256"] == first["sha256"]
        lesson.write_text("Lifetimes describe borrowing relationships.", encoding="utf-8")
        memory.index("lesson.md")
        assert not memory.search("ownership")
        assert memory.search("Lifetimes")
        with ProjectMemory(tmp_path / "other") as other:
            assert not other.search("Lifetimes")
        memory.forget("lesson.md")
        assert not memory.search("Lifetimes")
        assert lesson.exists()


def test_memory_refuses_private_paths_and_secrets(tmp_path):
    pytest.importorskip("openjarvis_rust")
    from ghost_agent.openjarvis_bridge.memory import ProjectMemory

    with ProjectMemory(tmp_path) as memory:
        for path in ["../outside.md", ".ghost/private.json", "workspaces/private.md", ".env"]:
            with pytest.raises(IntegrationError):
                memory.index(path)
        (tmp_path / "secret.md").write_text("sk-" + "x" * 30)
        with pytest.raises(IntegrationError, match="secret"):
            memory.index("secret.md")


def test_real_agent_calls_real_retrieval(endpoint, tmp_path):
    pytest.importorskip("openjarvis_rust")
    from ghost_agent.openjarvis_bridge.memory import ProjectMemory

    (tmp_path / "school.md").write_text("Borrowing uses references in Rust.")
    with ProjectMemory(tmp_path) as memory:
        memory.index("school.md")
    endpoint[2].extend([
        {"message": {"tool_calls": [{"function": {"name": "lesson_search", "arguments": {"query": "Borrowing"}}}]}},
        {"message": {"content": "Borrowing uses references [school.md, chunk 0]."}},
    ])
    result = assistant.ask(tmp_path, "Explain borrowing", "tiny", memory=True)
    assert result["tools"] == [{"name": "lesson_search", "success": True}]
    assert "school.md" in endpoint[1][-1]["body"]["messages"][-1]["content"]


def test_agent_budget_counts_hidden_continuation_calls(tmp_path):
    class Engine:
        def generate(self, *args, **kwargs):
            return {"content": "partial", "finish_reason": "length"}
    bounded = assistant.BoundedEngine(Engine(), tmp_path, 1)
    bounded.generate([])
    with pytest.raises(IntegrationError, match="budget"):
        bounded.generate([])


def test_real_ghost_build_test_repair_loop_uses_upstream_http(endpoint, tmp_path, monkeypatch):
    """Scripted MODEL decisions, real Ghost loop/files/test subprocesses/upstream HTTP."""
    from ghost_agent.orchestrator import Orchestrator, Task

    monkeypatch.setenv("GHOST_INFERENCE_BACKEND", "openjarvis")
    batches = [
        [{"tool": "write_file", "path": "plan.md", "content": "Implement add and a behavioral test."}],
        [{"tool": "write_file", "path": "calculator.py", "content": "def add(a, b):\n    return a - b\n"},
         {"tool": "write_file", "path": "test_calculator.py", "content": "from calculator import add\ndef test_add():\n    assert add(2, 3) == 5\n"}],
        [{"tool": "write_file", "path": "calculator.py", "content": "def add(a, b):\n    return a + b\n"}],
    ]
    endpoint[2].extend({"message": {"content": json.dumps({"actions": actions})}} for actions in batches)
    task = Task("upstream_repair", "Calculator", "Build a calculator with tests", {"max_iterations": 8}, {})
    artifacts = tmp_path / "outputs" / "run"
    Orchestrator(tmp_path, model="scripted", base_url=endpoint[0]).run_task_with_roots(task, tmp_path, artifacts)
    state = json.loads((artifacts / "state.json").read_text())
    assert state["last_result"] == "tests_passed"
    assert len(endpoint[1]) == 3
    assert any("AssertionError" in msg["content"] for msg in endpoint[1][2]["body"]["messages"])
    assert (artifacts / "sandbox/calculator.py").read_text() == "def add(a, b):\n    return a + b\n"
    assert not (tmp_path / "calculator.py").exists()


def test_proactive_mvp_uses_real_openjarvis_engine(endpoint, tmp_path, monkeypatch):
    from ghost_agent.proactive import ProactiveEngine
    monkeypatch.setenv("GHOST_INFERENCE_BACKEND", "openjarvis")
    suggestion = {
        "title": "Rust practice prototype",
        "goal": "Build a tiny Rust ownership exercise in the sandbox and test it.",
        "rationale": "The supplied learning context supports a small exercise.",
        "kind": "learning", "confidence": 0.8, "requires_user_input": False,
    }
    endpoint[2].append({"message": {"content": json.dumps(suggestion)}})
    (tmp_path / "rust.md").write_text("I want to learn Rust ownership.", encoding="utf-8")
    engine = ProactiveEngine(tmp_path, "tiny", context_paths=["rust.md"])
    result = engine.tick(auto_queue=False)
    assert result["status"] == "proposed"
    assert result["opportunity"]["kind"] == "learning"
    assert endpoint[1][0]["path"] == "/api/chat"
    assert "Rust ownership" in endpoint[1][0]["body"]["messages"][-1]["content"]
