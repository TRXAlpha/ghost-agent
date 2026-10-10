"""OJ03 slice: one enforced grant/authority seam for upstream tool dispatch.

The bridge ecosystem is default-deny. A capability runs only under an active,
revocable, project-scoped grant; corrupt authority state fails closed; and no
code path can mint a grant for a consequential upstream tool.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ghost_agent.openjarvis_bridge import assistant
from ghost_agent.openjarvis_bridge.authority import (
    AuthorityStateError,
    GrantStore,
    authorize_call,
    provision_lesson_search,
)
from ghost_agent.openjarvis_bridge.runtime import EngineSettings


# -- pure authority store -------------------------------------------------

def _grants_json(tmp_path):
    return tmp_path / ".ghost" / "openjarvis" / "grants.json"


def test_default_deny_with_no_grant(tmp_path):
    ok, reason = authorize_call(tmp_path, "owner", "lesson_search", {"query": "x"})
    assert not ok
    assert "no active grant" in reason.lower()


def test_provision_then_authorized(tmp_path):
    provision_lesson_search(tmp_path, "owner", "reason")
    ok, reason = authorize_call(tmp_path, "owner", "lesson_search", {"query": "x"})
    assert ok
    assert reason == "authorized"


def test_ensure_is_idempotent(tmp_path):
    store = GrantStore(tmp_path)
    first = store.ensure("owner", "lesson_search", str(tmp_path.resolve()), "r")
    second = store.ensure("owner", "lesson_search", str(tmp_path.resolve()), "r")
    assert first.grant_id == second.grant_id
    assert len(store.all()) == 1


def test_revocation_denies(tmp_path):
    grant = provision_lesson_search(tmp_path, "owner", "reason")
    GrantStore(tmp_path).revoke(grant.grant_id)
    ok, _ = authorize_call(tmp_path, "owner", "lesson_search", {"query": "x"})
    assert not ok


def test_expired_grant_denied(tmp_path):
    store = GrantStore(tmp_path)
    store.ensure("owner", "lesson_search", str(tmp_path.resolve()), "r", ttl=0.001)
    time.sleep(0.02)
    ok, _ = authorize_call(tmp_path, "owner", "lesson_search", {"query": "x"})
    assert not ok


def test_cross_project_refusal(tmp_path):
    project_a = tmp_path / "a"
    project_b = tmp_path / "b"
    provision_lesson_search(project_a, "owner", "reason")
    ok, _ = authorize_call(project_b, "owner", "lesson_search", {"query": "x"})
    assert not ok


def test_corrupt_state_fails_closed(tmp_path):
    _grants_json(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    _grants_json(tmp_path).write_text("{ not json", encoding="utf-8")
    ok, reason = authorize_call(tmp_path, "owner", "lesson_search", {"query": "x"})
    assert not ok
    assert "denied" in reason.lower()

    # a corrupt file is never treated as a valid empty/permissive store
    _grants_json(tmp_path).write_text("not a list", encoding="utf-8")
    with pytest.raises(AuthorityStateError):
        GrantStore(tmp_path)._load()


def test_refuses_to_provision_consequential_capability(tmp_path):
    store = GrantStore(tmp_path)
    with pytest.raises(ValueError, match="not provisionable"):
        store.ensure("owner", "shell_exec", str(tmp_path.resolve()), "r")


def test_param_shape_mismatch_denied(tmp_path):
    provision_lesson_search(tmp_path, "owner", "reason")
    ok, _ = authorize_call(tmp_path, "owner", "lesson_search", {"query": "x", "extra": 1})
    assert not ok


def test_wrong_principal_denied(tmp_path):
    provision_lesson_search(tmp_path, "owner", "reason")
    ok, _ = authorize_call(tmp_path, "unpaired-guess", "lesson_search", {"query": "x"})
    assert not ok


# -- real agent dispatch --------------------------------------------------

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


def test_real_agent_denies_lesson_search_without_grant(endpoint, tmp_path):
    url, calls, replies = endpoint
    replies.extend([
        {"message": {"tool_calls": [{"function": {"name": "lesson_search", "arguments": {"query": "x"}}}]}},
        {"message": {"content": "I cannot search without authorization."}},
    ])
    # memory is DISABLED, so no grant is provisioned; default-deny still applies
    result = assistant.ask(tmp_path, "Search for x", "tiny", memory=False, settings=EngineSettings(endpoint=url))
    assert result["status"] == "completed"
    assert result["tools"] == [{"name": "lesson_search", "success": False}]
    assert not calls[0]["body"].get("tools")


def test_real_agent_still_denies_consequential_tool(endpoint, tmp_path):
    url, calls, replies = endpoint
    replies.extend([
        {"message": {"tool_calls": [{"function": {"name": "shell_exec", "arguments": {"command": "bad"}}}]}},
        {"message": {"content": "I cannot execute that."}},
    ])
    result = assistant.ask(tmp_path, "Try a tool", "tiny", settings=EngineSettings(endpoint=url))
    assert result["status"] == "completed"
    assert result["tools"] == [{"name": "shell_exec", "success": False}]
    assert len(calls) == 2
    assert "not approved" in calls[1]["body"]["messages"][-1]["content"]


# -- structured grant fields (TL1) ----------------------------------------

def _write_grants(tmp_path, records):
    path = _grants_json(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records), encoding="utf-8")


def _legacy_grant(tmp_path) -> dict:
    return {
        "grant_id": "grant_legacy",
        "principal": "owner",
        "capability": "lesson_search",
        "scope": str(tmp_path.resolve()),
        "reason": "legacy",
        "status": "active",
        "expires_at": None,
        "created_at": 1.0,
        "updated_at": 1.0,
    }


def test_legacy_grant_without_new_fields_loads(tmp_path):
    _write_grants(tmp_path, [_legacy_grant(tmp_path)])
    grants = GrantStore(tmp_path).all()
    assert len(grants) == 1
    assert grants[0].side_effects == []
    assert grants[0].conditions == {}
    assert grants[0].limits == {}
    assert grants[0].provenance == "owner"


def test_unknown_side_effect_fails_closed(tmp_path):
    record = _legacy_grant(tmp_path)
    record["side_effects"] = ["launch_missiles"]
    _write_grants(tmp_path, [record])
    with pytest.raises(AuthorityStateError):
        GrantStore(tmp_path)._load()


def test_unknown_condition_key_fails_closed(tmp_path):
    record = _legacy_grant(tmp_path)
    record["conditions"] = {"must_wave_flag": True}
    _write_grants(tmp_path, [record])
    with pytest.raises(AuthorityStateError):
        GrantStore(tmp_path)._load()


def test_wrong_condition_type_fails_closed(tmp_path):
    record = _legacy_grant(tmp_path)
    record["conditions"] = {"max_per_day": "three"}
    _write_grants(tmp_path, [record])
    with pytest.raises(AuthorityStateError):
        GrantStore(tmp_path)._load()


def test_unknown_limit_key_fails_closed(tmp_path):
    record = _legacy_grant(tmp_path)
    record["limits"] = {"max_tons": 1}
    _write_grants(tmp_path, [record])
    with pytest.raises(AuthorityStateError):
        GrantStore(tmp_path)._load()


def test_invalid_provenance_fails_closed(tmp_path):
    record = _legacy_grant(tmp_path)
    record["provenance"] = "self_minted"
    _write_grants(tmp_path, [record])
    with pytest.raises(AuthorityStateError):
        GrantStore(tmp_path)._load()


def test_ensure_records_structured_fields(tmp_path):
    grant = GrantStore(tmp_path).ensure(
        "owner", "lesson_search", str(tmp_path.resolve()), "r",
        side_effects=["read"], conditions={"max_per_day": 2},
        limits={"max_calls": 10})
    assert grant.side_effects == ["read"]
    assert grant.conditions == {"max_per_day": 2}
    assert grant.limits == {"max_calls": 10}
    assert grant.provenance == "owner"
    with pytest.raises(ValueError, match="provenance"):
        GrantStore(tmp_path).ensure(
            "owner", "lesson_search", str(tmp_path.resolve()), "r",
            provenance="migration")
    with pytest.raises(ValueError, match="side_effects"):
        GrantStore(tmp_path).ensure(
            "owner", "lesson_search", str(tmp_path.resolve()), "r",
            side_effects=["spend_money"])


def test_max_per_day_denies_nth_call_and_resets_next_day(tmp_path, monkeypatch):
    store = GrantStore(tmp_path)
    store.ensure("owner", "lesson_search", str(tmp_path.resolve()), "r",
                 conditions={"max_per_day": 2})
    now = time.time()
    assert authorize_call(tmp_path, "owner", "lesson_search", {"query": "a"}, now=now)[0]
    assert authorize_call(tmp_path, "owner", "lesson_search", {"query": "b"}, now=now)[0]
    ok, reason = authorize_call(tmp_path, "owner", "lesson_search", {"query": "c"}, now=now)
    assert not ok
    assert "max_per_day" in reason
    next_day = now + 24 * 3600
    ok, _ = authorize_call(tmp_path, "owner", "lesson_search", {"query": "d"}, now=next_day)
    assert ok
