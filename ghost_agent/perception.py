"""Opt-in, read-only local perception adapters normalized into Ghost events."""
from __future__ import annotations

import hashlib
import os
import subprocess
import time
from pathlib import Path
from typing import Iterable

from .runtime import Event, EventBus, Observation, WorldModel


class FilesystemPerception:
    """Observe explicitly supplied workspace paths; this is not a background watcher."""

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root).resolve()
        self.events = EventBus(self.root)
        self.world = WorldModel(self.root)

    def observe(self, paths: Iterable[Path]) -> list[Observation]:
        observations: list[Observation] = []
        for raw in paths:
            path = Path(raw).resolve()
            if not path.is_relative_to(self.root):
                raise ValueError("Filesystem observation escapes the project scope")
            relative = path.relative_to(self.root).as_posix()
            state = {"exists": path.exists(), "path": relative}
            if path.is_file():
                stat = path.stat()
                state.update({"size": stat.st_size, "modified_at": stat.st_mtime,
                              "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            elif path.is_dir():
                state["entries"] = sum(1 for _ in path.iterdir())
            observation = Observation(f"file.{relative}", "file", state, "filesystem_adapter",
                                      observed_at=time.time(), last_verified=time.time())
            self.world.observe(observation)
            self.events.emit(Event("filesystem.observed", {"entity_id": observation.entity_id,
                                                            "exists": state["exists"]}, source="filesystem_adapter"))
            observations.append(observation)
        return observations


def _windows_process_alive(pid: int) -> bool:
    """Query a process handle without delivering a signal or requesting control."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    # SYNCHRONIZE permits only waiting/querying the termination state here.
    # A zero timeout reads that state immediately; it does not signal the process.
    handle = kernel32.OpenProcess(0x00100000, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER: no process with this positive PID
            raise ProcessLookupError(f"Process {pid} does not exist")
        raise ctypes.WinError(error)
    try:
        status = kernel32.WaitForSingleObject(handle, 0)
        if status == 0:  # WAIT_OBJECT_0: process has terminated
            return False
        if status == 0x102:  # WAIT_TIMEOUT: process is still running
            return True
        raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32.CloseHandle(handle)


class ProcessPerception:
    """Read-only process liveness observation; no process control capability."""

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root)
        self.events = EventBus(self.root)
        self.world = WorldModel(self.root)

    def observe(self, pid: int) -> Observation:
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise ValueError("Process id must be a positive integer")
        if os.name == "nt" and pid > 0xFFFFFFFF:
            raise ValueError("Process id exceeds the Windows DWORD range")
        error = ""
        try:
            if os.name == "nt":
                # On Windows, os.kill(pid, 0) sends CTRL_C_EVENT. It is not
                # the harmless POSIX liveness probe and can interrupt our host.
                alive = _windows_process_alive(pid)
            else:
                os.kill(pid, 0)
                alive = True
        except ProcessLookupError:
            alive = False
        except OSError as exc:
            # Access denial or a failed query does not prove that a process died.
            alive = None
            error = str(exc)
        state = {"pid": pid, "alive": alive}
        if error:
            state["error"] = error
        observation = Observation(f"process.{pid}", "process", state,
                                  "process_adapter", confidence=0.0 if alive is None else 1.0)
        self.world.observe(observation)
        self.events.emit(Event("process.observed", {"pid": pid, "alive": alive}, source="process_adapter"))
        return observation


class GitPerception:
    """Read-only Git working-tree observation; never changes repository state."""

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root).resolve()
        self.events = EventBus(self.root)
        self.world = WorldModel(self.root)

    def observe(self) -> Observation:
        try:
            result = subprocess.run(["git", "status", "--porcelain=v1", "--branch"], cwd=self.root,
                                    capture_output=True, text=True, timeout=10, check=False)
            lines = result.stdout.splitlines()
            available = result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            lines, available = [], False
        branch = lines[0][3:] if available and lines and lines[0].startswith("## ") else ""
        changes = lines[1:] if available else []
        observation = Observation("git.working_tree", "git", {
            "available": available, "branch": branch, "changed_entries": changes, "dirty": bool(changes),
        }, "git_adapter", confidence=1.0 if available else 0.5)
        self.world.observe(observation)
        self.events.emit(Event("git.observed", {"available": available, "dirty": bool(changes)}, source="git_adapter"))
        return observation
