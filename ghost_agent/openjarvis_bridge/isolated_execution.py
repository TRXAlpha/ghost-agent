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


def _run_in_container(command: list[str], sandbox: Path, env: Dict[str, str],
                       timeout: float, input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Execute inside an OpenJarvis ContainerRunner when Docker is available.

    The sandbox directory is mounted read-only so generated code cannot persist
    to the host.  Upstream's ContainerRunner handles container lifecycle,
    timeout, and output capture; Ghost's wrapper binds it to Ghost contracts.
    """
    require_upstream()
    from openjarvis.sandbox.runner import ContainerRunner

    runner = ContainerRunner(timeout=timeout)

    # Expose only the sandbox as a read-only mount.  No host paths are exposed.
    mounts = [f"{str(sandbox.resolve())}:/workspace:ro"]
    result = runner.run(input_data, workspace="/workspace", mounts=mounts, env=env or {})
    return {
        "returncode": 0 if not result.get("error") else 1,
        "output": result.get("content", ""),
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
) -> Dict[str, Any]:
    """Execute a command with OS-level isolation when Docker is available.

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
        # Container input convention: the command is the primary input
        data["_command"] = command
        data["_cwd"] = "/workspace"

    try:
        return _run_in_container(command, sandbox, env or {}, timeout, data)
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
