"""Bounded output capture and process-lifetime control, not OS isolation."""
from __future__ import annotations

import os
from contextlib import nullcontext
from pathlib import Path
import signal
import subprocess
import sys
import time
from threading import Thread
from typing import Callable


def run_bounded(command: list[str], *, cwd: str, env: dict[str, str],
                timeout: float, max_output: int,
                should_stop: Callable[[], bool] | None = None) -> dict:
    if type(timeout) not in (int, float) or not 0 < timeout <= 86400:
        raise ValueError("Command timeout must be positive and at most one day")
    if type(max_output) is not int or not 0 <= max_output <= 1048576:
        raise ValueError("Output limit must be an integer from 0 to 1048576")
    if should_stop and should_stop():
        raise RuntimeError("Command cancelled by emergency stop")
    retained = bytearray()
    truncated = False
    errors = []
    # Four bytes per requested character preserves UTF-8 prefixes with bounded
    # memory. Excess bytes are drained and discarded, never spooled to disk.
    byte_limit = max_output * 4
    if os.name == "nt":
        from ._windows_job import parent_handle
    with parent_handle() if os.name == "nt" else nullcontext() as handle:
        launch = command
        startup = None
        if os.name == "nt":
            launch = [sys.executable, "-I", str(Path(__file__).with_name("_windows_job.py").resolve()),
                      str(handle), *command]
            startup = subprocess.STARTUPINFO()
            startup.lpAttributeList = {"handle_list": [handle]}
        proc = subprocess.Popen(launch, cwd=cwd, env=env, shell=False,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt", startupinfo=startup, close_fds=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)

    def drain():
        nonlocal truncated
        try:
            while chunk := proc.stdout.read1(65536):
                remaining = byte_limit - len(retained)
                retained.extend(chunk[:remaining])
                truncated |= len(chunk) > remaining
        except OSError as exc:
            errors.append(exc)

    reader = Thread(target=drain, name="ghost-command-output", daemon=True)
    reader.start()
    try:
        deadline = time.monotonic() + timeout
        while True:
            if should_stop and should_stop():
                raise RuntimeError("Command cancelled by emergency stop")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                code = proc.wait(timeout=min(0.1, remaining))
                break
            except subprocess.TimeoutExpired:
                pass
    finally:
        # Windows launcher exit closes its job; on POSIX kill remaining group
        # members even after normal parent exit. POSIX children can escape via
        # setsid; this is not a hostile-code containment mechanism.
        if os.name == "nt":
            if proc.poll() is None:
                proc.kill()
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.wait(timeout=5)
        reader.join(timeout=5)
        if not reader.is_alive():
            proc.stdout.close()
    if reader.is_alive():
        raise RuntimeError("Command output pipe remained open after process cleanup")
    if errors:
        raise RuntimeError("Command output capture failed") from errors[0]
    text = retained.decode("utf-8", errors="replace")
    truncated |= len(text) > max_output
    return {"returncode": code, "output": text[:max_output] + ("\n...[truncated]" if truncated else "")}
