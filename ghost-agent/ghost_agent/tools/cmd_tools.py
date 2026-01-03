from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Iterable


class CmdError(ValueError):
    pass


DEFAULT_ALLOWLIST = {"python", "python.exe", "pytest", "git", "pip", "ruff"}
BLOCKLIST_TOKENS = {"sudo", "rm -rf", "curl", "wget"}


def _split_cmd(cmd: str) -> list[str]:
    return shlex.split(cmd, posix=os.name != "nt")


def _extract_exe(cmd: str) -> str:
    parts = _split_cmd(cmd)
    if not parts:
        raise CmdError("Empty command")
    return Path(parts[0]).name


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
) -> dict:
    allow = set(allowlist or DEFAULT_ALLOWLIST)
    exe = _extract_exe(cmd)
    cmd_lower = cmd.lower()

    if any(token in cmd_lower for token in BLOCKLIST_TOKENS):
        raise CmdError("Command contains blocked token")
    if exe not in allow:
        raise CmdError(f"Command not allowed: {exe}")

    safe_cwd = _ensure_within_root(root, cwd)

    result = subprocess.run(
        cmd,
        cwd=str(safe_cwd),
        shell=os.name == "nt",
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    output = (result.stdout or "") + (result.stderr or "")
    if len(output) > max_output:
        output = output[:max_output] + "\n...[truncated]"
    return {"returncode": result.returncode, "output": output}
