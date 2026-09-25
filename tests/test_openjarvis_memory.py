"""OJ05 slice: memory index + session history purging.

Source-index deletion previously left copied excerpts in session history.
forget_with_sessions() atomically clears the index and purges matching
assistant messages from the project's session store.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("openjarvis")

from ghost_agent.openjarvis_bridge import assistant
from ghost_agent.openjarvis_bridge.memory import ProjectMemory
from ghost_agent.openjarvis_bridge.runtime import EngineSettings


def test_forget_removes_source(tmp_path):
    lesson = tmp_path / "lesson.md"
    lesson.write_text("Rust uses borrowing and lifetimes.", encoding="utf-8")
    with ProjectMemory(tmp_path) as memory:
        memory.index("lesson.md")
        assert memory.search("borrowing")
        memory.forget("lesson.md")
        assert not memory.search("borrowing")
        assert lesson.exists()


def test_forget_with_sessions_removes_index_and_messages(tmp_path):
    pytest.importorskip("openjarvis_rust")
    lesson = tmp_path / "lesson.md"
    lesson.write_text("Ownership prevents aliasing bugs.", encoding="utf-8")
    state = tmp_path / ".ghost" / "openjarvis"
    state.mkdir(parents=True, exist_ok=True)

    # Index the lesson first so it exists in the store
    with ProjectMemory(tmp_path) as memory:
        memory.index("lesson.md")

    # Create a session store and save an assistant message citing the source
    from openjarvis.sessions.session import SessionStore
    store = SessionStore(state / "sessions.db")
    session = store.get_or_create("owner", channel="local", channel_user_id="owner")
    # Simulate an assistant response that cites the lesson
    store.save_message(session.session_id, "assistant",
                       "According to lesson.md, ownership prevents aliasing bugs.",
                       channel="local")
    store.close()

    # Forget with sessions — both index and session purge
    with ProjectMemory(tmp_path) as memory:
        result = memory.forget_with_sessions("lesson.md")

    assert result["forgotten_source"] == "lesson.md"
    assert result["session_messages_purged"] == 1
    assert "note" in result
    # Verify index is gone
    with ProjectMemory(tmp_path) as memory:
        assert not memory.search("aliasing")
    # Original file untouched
    assert lesson.exists()


def test_forget_with_sessions_missing_session_db_succeeds(tmp_path):
    pytest.importorskip("openjarvis_rust")
    lesson = tmp_path / "lesson.md"
    lesson.write_text("A short lesson.", encoding="utf-8")
    with ProjectMemory(tmp_path) as memory:
        memory.index("lesson.md")
        result = memory.forget_with_sessions("lesson.md")

    assert result["forgotten_source"] == "lesson.md"
    assert result["session_messages_purged"] == 0
    assert "session_purge_error" not in result
    assert lesson.exists()
    with ProjectMemory(tmp_path) as memory:
        assert not memory.search("lesson")


def test_forget_with_sessions_user_messages_preserved(tmp_path):
    pytest.importorskip("openjarvis_rust")
    lesson = tmp_path / "lesson.md"
    lesson.write_text("Some topic.", encoding="utf-8")
    state = tmp_path / ".ghost" / "openjarvis"
    state.mkdir(parents=True, exist_ok=True)

    with ProjectMemory(tmp_path) as memory:
        memory.index("lesson.md")

    from openjarvis.sessions.session import SessionStore
    store = SessionStore(state / "sessions.db")
    session = store.get_or_create("owner", channel="local", channel_user_id="owner")
    # User message referencing the lesson is NOT purged
    store.save_message(session.session_id, "user", "Tell me about lesson.md", channel="local")
    store.close()

    with ProjectMemory(tmp_path) as memory:
        result = memory.forget_with_sessions("lesson.md")

    assert result["session_messages_purged"] == 0
    with ProjectMemory(tmp_path) as memory:
        assert not memory.search("topic")


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
            reply = replies.pop(0) if replies else {"message": {"content": "ok"}, "eval_count": 1}
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


def test_native_memory_correction_delete_and_project_isolation(tmp_path):
    """Reuse existing test to confirm correction/delete/isolation still work."""
    pytest.importorskip("openjarvis_rust")
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