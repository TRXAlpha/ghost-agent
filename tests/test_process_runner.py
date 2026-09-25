import ctypes
import os
import subprocess
import sys
import time
import tracemalloc

import pytest

from ghost_agent.tools.cmd_tools import join_command, run_cmd


def command(code):
    return join_command(["python", "-c", code])


def test_large_output_is_bounded_while_captured(tmp_path):
    tracemalloc.start()
    try:
        result = run_cmd(command("import os; [os.write(1, b'x'*65536) for _ in range(160)]"),
                         ".", tmp_path, max_output=1000)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result == {"returncode": 0, "output": "x" * 1000 + "\n...[truncated]"}
    assert peak < 2 * 1024 * 1024


def test_both_output_streams_and_nonzero_status(tmp_path):
    result = run_cmd(command("import os; os.write(1,b'out'); os.write(2,b'err'); raise SystemExit(7)"),
                     ".", tmp_path)
    assert result == {"returncode": 7, "output": "outerr"}


@pytest.mark.parametrize("limit", [-1, True, 1048577])
def test_invalid_output_limit_does_not_execute(tmp_path, limit):
    with pytest.raises(ValueError):
        run_cmd(command("open('executed','w').close()"), ".", tmp_path, max_output=limit)
    assert not (tmp_path / "executed").exists()


@pytest.mark.parametrize("timeout", [0, True, float("nan"), float("inf")])
def test_invalid_timeout_does_not_execute(tmp_path, timeout):
    with pytest.raises(ValueError):
        run_cmd(command("open('executed','w').close()"), ".", tmp_path, timeout=timeout)
    assert not (tmp_path / "executed").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows job lifecycle integration test")
@pytest.mark.parametrize("timeout", [False, True])
def test_windows_descendant_is_terminated(tmp_path, timeout):
    code = ("import subprocess,sys,time; from pathlib import Path; "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)']); "
            "Path('child.pid').write_text(str(p.pid)); "
            + ("time.sleep(10)" if timeout else "time.sleep(0.1)"))
    if timeout:
        with pytest.raises(subprocess.TimeoutExpired):
            run_cmd(command(code), ".", tmp_path, timeout=1)
    else:
        assert run_cmd(command(code), ".", tmp_path)["returncode"] == 0
    pid = int((tmp_path / "child.pid").read_text())
    assert_windows_exited(pid)


def assert_windows_exited(pid):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.WaitForSingleObject.restype = ctypes.c_ulong
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if handle:
        try:
            assert kernel.WaitForSingleObject(handle, 2000) == 0
        finally:
            kernel.CloseHandle(handle)
    else:
        assert ctypes.get_last_error() == 87  # Process no longer exists.


@pytest.mark.skipif(os.name != "nt", reason="Windows supervisor crash integration test")
@pytest.mark.parametrize("hard_kill", [False, True])
def test_supervisor_crash_stops_launcher_and_command(tmp_path, hard_kill):
    child_code = ("import os,time; from pathlib import Path; "
                  "Path('ready').write_text(str(os.getppid())+','+str(os.getpid())); time.sleep(10)")
    script = (
        "import os,sys,time; from pathlib import Path; from threading import Thread; "
        "from ghost_agent.tools.cmd_tools import run_cmd,join_command; "
        "root=Path(sys.argv[1]); "
        f"cmd=join_command(['python','-c',{child_code!r}]); "
        "thread=Thread(target=lambda: run_cmd(cmd,'.',root)); thread.start()\n"
        "while not (root/'ready').exists(): time.sleep(0.01)\n"
        + ("thread.join()" if hard_kill else "os._exit(73)")
    )
    supervisor = subprocess.Popen([sys.executable, "-c", script, str(tmp_path)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 5
        while not (tmp_path / "ready").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (tmp_path / "ready").exists()
        if hard_kill:
            supervisor.kill()
        else:
            assert supervisor.wait(timeout=3) == 73
        supervisor.wait(timeout=3)
        launcher, child = map(int, (tmp_path / "ready").read_text().split(","))
        assert_windows_exited(launcher)
        assert_windows_exited(child)
    finally:
        if supervisor.poll() is None:
            supervisor.kill()
        supervisor.wait(timeout=3)


def test_unicode_and_zero_limit(tmp_path):
    assert run_cmd(command("print('é界',end='')"), ".", tmp_path, max_output=2)["output"] == "é界"
    assert run_cmd(command("print('x')"), ".", tmp_path, max_output=0)["output"] == "\n...[truncated]"


def test_emergency_stop_before_launch(tmp_path):
    with pytest.raises(RuntimeError, match="emergency stop"):
        run_cmd(command("open('executed','w').close()"), ".", tmp_path, should_stop=lambda: True)
    assert not (tmp_path / "executed").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher fail-closed test")
def test_invalid_supervisor_handle_refuses_user_code(tmp_path):
    from pathlib import Path
    from ghost_agent.tools import _windows_job
    result = subprocess.run([sys.executable, "-I", str(Path(_windows_job.__file__).resolve()),
                             "0", sys.executable, "-c", "open('executed','w').close()"],
                            cwd=tmp_path, capture_output=True, timeout=5)
    assert result.returncode == 125
    assert not (tmp_path / "executed").exists()


def test_emergency_stop_during_execution(tmp_path):
    start = time.monotonic()
    with pytest.raises(RuntimeError, match="emergency stop"):
        run_cmd(command("import time; time.sleep(10)"), ".", tmp_path,
                should_stop=lambda: time.monotonic() - start > 0.5)
    assert time.monotonic() - start < 3
