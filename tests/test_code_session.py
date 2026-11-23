"""Read-write code session for the chat agent: grant, confinement, audit, caps, stop."""
import json
import threading
import time
from unittest.mock import MagicMock

import pytest

from ghost_agent import heartbeat as hb
from ghost_agent.openjarvis_bridge import session_tools as st
from ghost_agent.openjarvis_bridge import telegram as tg
from ghost_agent.runtime import EmergencyStop


def on(tmp_path, **kw):
    hb.set_grant(tmp_path, "session.code", True, **kw)


def test_off_by_default_and_every_refusal_is_audited(tmp_path):
    ok, text = st.execute(tmp_path, "write_file", {"path": "a.py", "content": "x=1"})
    assert not ok and "session.code is off" in text
    assert st.audit_rows(tmp_path)[-1]["ok"] is False
    assert not (st.session_root(tmp_path) / "a.py").exists()


def test_write_run_read_list_in_the_workspace(tmp_path):
    on(tmp_path)
    assert st.execute(tmp_path, "write_file", {"path": "hello.py", "content": "print(6*7)\n"})[0]
    ok, out = st.execute(tmp_path, "run", {"cmd": "python hello.py"})
    assert ok and "42" in out
    assert "print(6*7)" in st.execute(tmp_path, "read_file", {"path": "hello.py"})[1]
    assert "hello.py" in st.execute(tmp_path, "list_dir", {})[1]
    assert [r["tool"] for r in st.audit_rows(tmp_path)] == ["write_file", "run", "read_file", "list_dir"]


def test_escapes_secrets_shell_and_pip_are_refused(tmp_path):
    on(tmp_path)
    for tool, params in [("read_file", {"path": "../../../validated-memory.json"}),
                         ("write_file", {"path": r"..\..\evil.py", "content": "x"}),
                         ("write_file", {"path": ".env", "content": "A=1"}),
                         ("write_file", {"path": "k.txt", "content": "token 123456789:" + "A" * 30}),
                         ("run", {"cmd": "python -m pip install requests"}),
                         ("run", {"cmd": "python a.py && python b.py"}),
                         ("run", {"cmd": "powershell -c dir"}),
                         ("run", {"cmd": "python x.py", "cwd": "../.."})]:
        ok, text = st.execute(tmp_path, tool, params)
        assert not ok and text.startswith("Failed"), (tool, params, text)


def test_output_is_secret_redacted(tmp_path):
    on(tmp_path)
    st.execute(tmp_path, "write_file", {"path": "leak.py", "content": "print('123456789:' + 'A' * 30)\n"})
    ok, out = st.execute(tmp_path, "run", {"cmd": "python leak.py"})
    assert ok and "AAAAAAAAAA" not in out


def test_cap_and_emergency_stop(tmp_path):
    on(tmp_path, max_count=2)
    assert st.execute(tmp_path, "list_dir", {})[0] and st.execute(tmp_path, "list_dir", {})[0]
    assert "cap of 2" in st.execute(tmp_path, "list_dir", {})[1]
    hb.set_grant(tmp_path, "session.code", True, max_count=100)
    EmergencyStop(tmp_path).engage("t")
    assert "emergency stop" in st.execute(tmp_path, "list_dir", {})[1]
    assert not st.enabled(tmp_path)


def test_root_is_owner_set_and_must_exist(tmp_path):
    target = tmp_path / "proj"
    target.mkdir()
    assert st.set_session_root(tmp_path, str(target)) == target.resolve()
    assert st.session_root(tmp_path) == target.resolve()
    with pytest.raises(ValueError):
        st.set_session_root(tmp_path, str(tmp_path / "missing"))


def test_bridge_asks_for_a_session_only_when_granted(tmp_path):
    from ghost_agent.openjarvis_bridge.telegram import pair, TelegramBridge
    pair(tmp_path, "1234", "1234")
    channel = MagicMock()
    channel.send.return_value = True
    responder = MagicMock(return_value={"task_id": "t", "content": "ok"})
    bridge = TelegramBridge(tmp_path, channel, "tiny", responder=responder)

    class M:
        channel, sender, conversation_id = "telegram", "1234", "1234"
        def __init__(self, i, text): self.message_id, self.content = str(i), text
    bridge.handle(M(1, "run the tests"))
    assert "code_session" not in responder.call_args.kwargs
    on(tmp_path)
    bridge.handle(M(2, "run the tests again"))
    assert responder.call_args.kwargs["code_session"] is True


def test_stop_is_handled_while_a_long_message_runs(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from ghost_agent.openjarvis_bridge.telegram import pair, TelegramBridge
    pair(tmp_path, "5309400589", "5309400589")
    started, release, sent = threading.Event(), threading.Event(), []

    def slow(root, content, model, **kw):
        started.set()
        release.wait(10)
        return {"task_id": "t", "content": "late answer"}

    channel = tg.BotApiChannel("t", sender=lambda token, chat, text: sent.append(text) or {"ok": True})
    bridge = TelegramBridge(tmp_path, channel, "tiny", responder=slow)
    row = lambda i, text: json.dumps({"message_id": i, "chat_id": 5309400589, "from_id": 5309400589, "text": text})
    pool = ThreadPoolExecutor(max_workers=1)
    tg.relay_lines(tmp_path, bridge, [row(1, "build me a thing please")], pool)
    assert started.wait(5)
    t0 = time.time()
    tg.relay_lines(tmp_path, bridge, [row(2, "stop")], pool)  # must not wait for the slow answer
    assert time.time() - t0 < 2 and EmergencyStop(tmp_path).active()
    assert any("Emergency stop engaged" in s for s in sent)
    release.set()
    pool.shutdown(wait=True)
    assert "late answer" not in sent  # revoked before delivery


def test_long_replies_are_chunked_for_telegram():
    parts = []
    ch = tg.BotApiChannel("t", sender=lambda token, chat, text: parts.append(text) or {"ok": True})
    assert ch.send("1", "x" * 9000) and [len(p) for p in parts] == [3900, 3900, 1200]
