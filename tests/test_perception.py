import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from ghost_agent.perception import FilesystemPerception, GitPerception, ProcessPerception


def test_filesystem_perception_is_scoped_and_provenanced(tmp_path: Path) -> None:
    path = tmp_path / "data.txt"
    path.write_text("hello", encoding="utf-8")
    adapter = FilesystemPerception(tmp_path)

    observed = adapter.observe([path])[0]

    assert observed.state["sha256"]
    assert adapter.world.get("file.data.txt").state["size"] == 5
    with pytest.raises(ValueError):
        adapter.observe([tmp_path.parent / "outside.txt"])


def test_process_perception_records_current_process(tmp_path: Path, monkeypatch) -> None:
    if os.name == "nt":
        def forbidden_signal(*args):
            pytest.fail("Windows process observation must never send a console signal")

        monkeypatch.setattr(os, "kill", forbidden_signal)
    observed = ProcessPerception(tmp_path).observe(os.getpid())
    assert observed.state["alive"] is True


def test_process_observation_does_not_stop_child_and_detects_exit(tmp_path: Path) -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read(); sys.exit(259)"],
        stdin=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        adapter = ProcessPerception(tmp_path)
        assert adapter.observe(child.pid).state["alive"] is True
        assert child.poll() is None
    finally:
        child.stdin.close()
        child.wait(timeout=10)
    # 259 is Windows' STILL_ACTIVE sentinel when using GetExitCodeProcess;
    # checking the handle's termination state must still report this child dead.
    assert adapter.observe(child.pid).state["alive"] is False


@pytest.mark.parametrize("pid", [0, -1, True, 1.5])
def test_process_observation_rejects_invalid_pid(tmp_path: Path, pid) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        ProcessPerception(tmp_path).observe(pid)


@pytest.mark.skipif(os.name != "nt", reason="Windows process API")
def test_windows_process_observation_rejects_pid_truncation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="DWORD"):
        ProcessPerception(tmp_path).observe(2**32 + os.getpid())


@pytest.mark.skipif(os.name != "nt", reason="Windows process API")
@pytest.mark.parametrize("error, expected_alive", [(87, False), (5, None)])
def test_windows_unavailable_process_is_not_misreported(tmp_path: Path, monkeypatch, error, expected_alive) -> None:
    import ctypes

    api = Mock()
    api.OpenProcess.return_value = None
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: api)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: error)

    adapter = ProcessPerception(tmp_path)
    observed = adapter.observe(123)

    assert observed.state["alive"] is expected_alive
    assert adapter.world.get("process.123").state["alive"] is expected_alive
    api.OpenProcess.assert_called_once_with(0x00100000, False, 123)
    api.WaitForSingleObject.assert_not_called()
    api.CloseHandle.assert_not_called()
    if expected_alive is None:
        assert observed.confidence == 0.0
        assert observed.state["error"]


@pytest.mark.skipif(os.name != "nt", reason="Windows process API")
def test_windows_failed_query_closes_handle_and_records_unknown(tmp_path: Path, monkeypatch) -> None:
    import ctypes

    api = Mock()
    api.OpenProcess.return_value = 12345
    api.WaitForSingleObject.return_value = 0xFFFFFFFF
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: api)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 6)

    observed = ProcessPerception(tmp_path).observe(123)

    assert observed.state["alive"] is None
    api.WaitForSingleObject.assert_called_once_with(12345, 0)
    api.CloseHandle.assert_called_once_with(12345)


def test_git_perception_reports_unavailable_or_readonly_state(tmp_path: Path) -> None:
    observed = GitPerception(tmp_path).observe()

    assert "available" in observed.state
    assert observed.entity_id == "git.working_tree"
