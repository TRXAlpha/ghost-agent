from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable

from .process_runner import run_bounded


class CmdError(ValueError):
    pass


DEFAULT_ALLOWLIST = {"python", "python.exe", "pytest", "git", "ruff", "node", "node.exe"}
BLOCKLIST_TOKENS = {"sudo", "rm -rf", "curl", "wget"}
COMMAND_ENV_KEYS = {"PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL"}


def _split_cmd(cmd: str) -> list[str]:
    if not cmd.strip() or any(char in cmd for char in "\x00\r\n"):
        raise CmdError("Expected one nonempty command, without control characters")
    if os.name != "nt":
        try:
            return shlex.split(cmd)
        except ValueError as exc:
            raise CmdError("Invalid command quoting") from exc
    # shlex(posix=False) retains quotes and is not a Windows argv parser.
    import ctypes
    from ctypes import wintypes

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    parse = shell32.CommandLineToArgvW
    parse.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    parse.restype = ctypes.POINTER(wintypes.LPWSTR)
    free = kernel32.LocalFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = ctypes.c_void_p
    count = ctypes.c_int()
    argv = parse(cmd.strip(), ctypes.byref(count))
    if not argv:
        raise CmdError("Cannot parse Windows command")
    try:
        return list(argv[:count.value])
    finally:
        free(argv)


def join_command(args: Iterable[str]) -> str:
    """Serialize arguments using the same platform convention as the parser."""
    parts = list(args)
    return subprocess.list2cmdline(parts) if os.name == "nt" else shlex.join(parts)


def _trusted_command(parts: list[str], root: Path) -> list[str]:
    requested = Path(parts[0])
    name = requested.name.lower()
    if name in {"python", "python.exe", "pytest", "ruff"}:
        executable = sys.executable
        prefix = [] if name.startswith("python") else ["-m", name]
    else:
        executable = shutil.which(name)
        if not executable:
            raise CmdError(f"Executable not installed: {name}")
        prefix = []
        if Path(executable).resolve().is_relative_to(Path(root).resolve()):
            raise CmdError("Cannot execute a workspace-provided executable")
    # An allowlisted basename must not authorize an arbitrary executable path.
    if requested.name != parts[0] and (
        not requested.is_absolute() or requested.resolve() != Path(executable).resolve()
    ):
        raise CmdError("Executable path does not match the trusted tool")
    if Path(executable).suffix.lower() in {".bat", ".cmd"}:
        raise CmdError("Batch scripts require a shell and are not allowed")
    return [executable, *prefix, *parts[1:]]


def _ensure_within_root(root: Path, cwd: str) -> Path:
    root = Path(root).resolve()
    path = Path(cwd)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if not path.is_relative_to(root):
        raise CmdError(f"CWD escapes workspace: {cwd}")
    return path


def run_cmd(
    cmd: str,
    cwd: str,
    root: Path,
    allowlist: Iterable[str] | None = None,
    timeout: int = 30,
    max_output: int = 4000,
    should_stop: Callable[[], bool] | None = None,
) -> dict:
    """Run one allowlisted tool without a shell.

    This is dispatch hardening, NOT OS containment: generated Python/tests
    still have the host user's access. Do not treat cwd as a security sandbox.
    """
    allow = {name.lower() for name in (DEFAULT_ALLOWLIST if allowlist is None else allowlist)}
    parts = _split_cmd(cmd)
    exe = Path(parts[0]).name.lower()
    cmd_lower = cmd.lower()

    if any(token in cmd_lower for token in BLOCKLIST_TOKENS):
        raise CmdError("Command contains blocked token")
    if exe not in allow:
        raise CmdError(f"Command not allowed: {exe}")
    if any(part in {"&", "&&", "|", "||", ";", "<", ">", ">>"} for part in parts):
        raise CmdError("Shell operators are not allowed; submit separate actions")
    if exe in {"python", "python.exe"} and parts[1:3] == ["-m", "pip"]:
        raise CmdError("Package installation requires a separate approved capability")

    safe_cwd = _ensure_within_root(root, cwd)
    if exe in {"node", "node.exe"}:
        if len(parts) < 3 or parts[1] != "--test":
            raise CmdError("Node capability currently requires --test and explicit test files")
        for value in parts[2:]:
            if value.startswith("-"):
                raise CmdError("Additional Node flags require a separate capability")
            path = _ensure_within_root(root, str(safe_cwd / value))
            if not path.is_file() or path.suffix not in {".js", ".cjs", ".mjs"}:
                raise CmdError("Node tests must be existing JavaScript files within the workspace")

    command = _trusted_command(parts, root)
    # Tool processes do not inherit provider tokens or user Python/Git hooks.
    # This reduces accidental exposure; host files remain accessible until an
    # OS-level execution backend provides real isolation.
    environment = {key: value for key, value in os.environ.items() if key.upper() in COMMAND_ENV_KEYS}
    environment.update(PYTHONNOUSERSITE="1", PYTHONIOENCODING="utf-8",
                       GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    return run_bounded(
        command,
        cwd=str(safe_cwd),
        env=environment,
        timeout=timeout,
        max_output=max_output,
        should_stop=should_stop,
    )
