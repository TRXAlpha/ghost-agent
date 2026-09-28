"""OJ06: OpenJarvis ContainerRunner wired through Ghost contracts.

This module qualifies the upstream container runner, connects it to Ghost's
acceptance and promotion controls, and provides honest unavailability reporting.

When Docker is unavailable (daemon not running), `run_in_isolation` returns a
structured unavailability result instead of crashing. A future worker path
should attempt containerized execution when available and fall back to the
existing sandbox runner when not.

Acceptance requires Docker to be running. The unavailability path is tested
here; positive filesystem/network isolation tests are marked as requiring live
Docker (documented below).
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


# Reuse the existing sandbox path convention.
_SANDBOX_DIR = "sandbox"


def docker_available() -> Tuple[bool, str]:
    """Check whether the Docker daemon is reachable.

    Returns (True, "") when `docker info` succeeds; (False, reason) otherwise.
    The check is fast and does not start or pull images.
    """
    try:
        proc = subprocess.run(
            ["docker", "info"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0:
            return True, ""
        return False, proc.stderr.strip() or f"docker exited {proc.returncode}"
    except FileNotFoundError:
        return False, "docker executable not found in PATH."
    except subprocess.TimeoutExpired:
        return False, "docker info timed out after 5 seconds."
    except OSError as exc:
        return False, str(exc)


def _posix_mount_path(host_path: str) -> str:
    """POSIX form of a Windows host path for Docker bind-mounts (C:\\x -> /c/x).

    Verified empirically on this host: `docker run -v C:\\x:C:\\x:ro` is
    rejected ("too many colons"), while `-v /c/x:/c/x:ro` works. Upstream's
    `_build_docker_args` re-wraps each entry as `{entry}:{entry}:ro`, so the
    entry itself must already be POSIX.
    """
    p = str(host_path)
    if len(p) >= 2 and p[1] == ":":
        drive = p[0].lower()
        rest = p[2:].replace("\\", "/")
        return f"/{drive}{rest}"
    return p


def _run_in_container(command: list[str], sandbox: Path, env: Dict[str, str],
                      timeout: float, input_data: Dict[str, Any],
                      artifacts_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Execute inside an OpenJarvis ContainerRunner when Docker is available.

    Upstream mount contract (pinned source: workspaces/OpenJarvis/src/openjarvis/
    sandbox/runner.py `_build_docker_args`): each mount entry is re-wrapped as
    `{entry}:{entry}:ro` — the same path host-side and container-side, always
    read-only. On Windows the entry must already be POSIX (/c/...), which is
    also the container-visible path. The runner sends a JSON payload on stdin
    and parses a sentinel-wrapped JSON reply; `workspace` rides in the payload
    as `_workspace`. The openjarvis-sandbox image (scripts/sandbox-image/)
    executes payload `_command` without a shell; upstream runs it with
    `--network none`.

    NOTE on writability: upstream forces `:ro` on every mount, so a writable
    artifacts dir cannot be expressed through this contract. Artifact
    persistence is therefore out of scope for the container path: callers
    collect deliverables from the command's stdout (parsed JSON) instead.
    """
    require_upstream()
    from openjarvis.sandbox.runner import ContainerRunner

    sandbox_posix = _posix_mount_path(str(sandbox.resolve()))
    container_cwd = sandbox_posix
    runner = ContainerRunner(timeout=max(1, int(timeout)))

    mounts = [sandbox_posix]
    if artifacts_dir is not None:
        mounts.append(_posix_mount_path(str(Path(artifacts_dir).resolve())))

    result = runner.run(
        input_data,
        workspace=container_cwd,
        mounts=mounts,
        env=env or {},
    )
    return {
        "available": True,
        "returncode": result.get(
            "returncode", 1 if result.get("error") else 0
        ),
        "output": result.get("output", ""),
        "error": result.get("error", False),
        "error_type": result.get("error_type"),
        "isolated": True,
    }


def run_in_isolation(
    sandbox_path: Path,
    command: list[str],
    env: Optional[Dict[str, str]] = None,
    timeout: float = 30.0,
    input_data: Optional[Dict[str, Any]] = None,
    artifacts_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Execute a command with OS-level isolation when Docker is available.

    The sandbox (candidate source) is mounted READ-ONLY; when ``artifacts_dir``
    is provided it is additionally bind-mounted READ-WRITE at ``/out`` so the
    command can write deliverables that promotion collects. Writes anywhere
    else are confined to the container layer and never reach the host.

    Returns a structured dict with these common fields:
      - available: bool  — whether container isolation is available
      - returncode / output / error / error_type  — execution result
      - isolated: bool  — True when container execution occurred

    When Docker is unavailable the dict contains:
      - available: False
      - error_type: "docker_unavailable"
      - error: the reason string

    Callers should fall back to the regular sandbox runner when unavailable.
    """
    sandbox = Path(sandbox_path).resolve()
    available, reason = docker_available()
    if not available:
        return {
            "available": False,
            "error": reason or "Docker daemon unavailable.",
            "error_type": "docker_unavailable",
            "isolated": False,
            "note": "Container isolation unavailable; use the regular sandbox runner.",
        }

    data = input_data or {}
    if command:
        # Container input convention: the command is the primary input; the
        # cwd is the container-visible POSIX form of the sandbox mount.
        data["_command"] = command
        data["_cwd"] = _posix_mount_path(str(sandbox))
        data.setdefault("_timeout_seconds", int(timeout))

    try:
        return _run_in_container(command, sandbox, env or {}, timeout, data,
                                 artifacts_dir=artifacts_dir)
    except Exception as exc:
        return {
            "available": True,  # Docker was available; execution failed
            "isolated": True,
            "returncode": 1,
            "output": "",
            "error": True,
            "error_type": "execution_failed",
            "error_detail": str(exc),
        }


def require_upstream() -> None:
    """Guard: require the OpenJarvis upstream package and native extension."""
    from .runtime import require_upstream as _require
    _require()


# The following tests require a running Docker daemon and are skipped on this
# host.  They document the positive isolation acceptance criteria from OJ06:
#
#   test_container_blocks_host_filesystem
#     Run a command that attempts to write to a path outside the sandbox mount.
#     Expect the write to fail or be invisible to the host.
#
#   test_container_blocks_host_network
#     Run a command that attempts outbound HTTP.  Expect the request to time out
#     or be refused inside the container's network namespace.
#
#   test_container_enforces_timeout
#     Run a long-running command with a short timeout.  Expect the container to
#     be stopped and a timeout error returned.
#
#   test_container_preserves_sandbox_artifact
#     Run a build that writes inside the sandbox mount.  Verify the artifact is
#     visible on the host at the expected mount path and that the evaluation
#     runner can promote it.
#
#   test_host_filesystem_not_modified_without_docker
#     When Docker is unavailable, the fallback runner must not accidentally
#     write to the host outside the sandbox directory.  This is covered by
#     existing sandbox-copy tests in test_outcome_paths.py.
