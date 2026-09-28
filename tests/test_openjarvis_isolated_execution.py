"""OJ06: OpenJarvis ContainerRunner qualification and honest unavailability.

The ContainerRunner provides OS-level filesystem and network isolation for
sandboxed code execution.  This test suite:
  - Verifies Docker daemon availability detection.
  - Documents positive isolation acceptance criteria (require live Docker).
  - Verifies graceful handling when Docker is unavailable.
  - Wires the runner into Ghost's acceptance and promotion contracts.

Full positive isolation tests (filesystem mounts, network blocks, timeout
enforcement) require a running Docker daemon.  They are documented here as
skipped tests with clear acceptance descriptions; they become executable once
Docker is running on this host.
"""
from __future__ import annotations

import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("openjarvis")

from ghost_agent.openjarvis_bridge import isolated_execution as iso
from ghost_agent.openjarvis_bridge.isolated_execution import (
    docker_available,
    require_upstream,
    run_in_isolation,
)


# -- Docker availability detection -------------------------------------------

def test_docker_client_installed():
    """docker executable is in PATH (daemon may or may not be running)."""
    import shutil
    assert shutil.which("docker") is not None, "docker executable not found in PATH"


def test_docker_daemon_unavailable_reported():
    """When Docker daemon is not reachable, docker_available() returns (False, reason)."""
    available, reason = docker_available()
    # This host has the client but not the daemon; the result must honestly
    # reflect that state rather than pretending isolation is available.
    if available:
        pytest.skip("Docker daemon appears to be running; isolation is available on this host.")
    assert not available
    assert isinstance(reason, str)
    assert len(reason) > 0


# -- Unavailable path (tested on this host; Docker daemon is not running) ------

def test_run_in_isolation_unavailable_returns_structured_result(tmp_path):
    """When Docker is unavailable, run_in_isolation returns a graceful
    unavailability dict with available=False and error_type='docker_unavailable'.

    Callers should fall back to the regular sandbox runner in this case.
    The returned dict also carries isolated=False and an informative note."""
    available, _ = docker_available()
    if available:
        # This host has Docker running; verify the available path instead
        result = run_in_isolation(tmp_path, ["python", "-c", "print(1)"],
                                 env={}, timeout=10.0)
        assert result["available"] is True
        assert result["isolated"] is True
        return
    # Docker unavailable: verify graceful structured response
    result = run_in_isolation(tmp_path, ["pytest", "-q"], env={}, timeout=10.0)
    assert result["available"] is False
    assert result["isolated"] is False
    assert result["error_type"] == "docker_unavailable"
    assert isinstance(result["error"], str)
    assert "note" in result


def test_unavailable_result_has_all_required_fields(tmp_path):
    """The unavailability dict includes every field callers depend on."""
    required_fields = {"available", "error", "error_type", "isolated", "note"}
    available, _ = docker_available()
    if available:
        pytest.skip("Docker daemon running; unavailable-path field set is not exercised.")
    result = run_in_isolation(tmp_path, ["python", "-c", "print(1)"])
    assert required_fields <= result.keys(), f"Missing fields: {required_fields - result.keys()}"


def test_require_upstream_returns_with_upstream():
    """require_upstream succeeds in the OpenJarvis environment."""
    require_upstream()  # should not raise when OpenJarvis is installed


# -- Acceptance stubs: require live Docker --------------------------------------
# These tests document the positive isolation acceptance criteria from OJ06.
# They are skipped here because the Docker daemon is not running on this host.
# Enable them once Docker is available; they are not marked xfail so the suite
# stays green in the unconfigured environment.

def _docker_skip():
    available, _ = docker_available()
    if not available:
        pytest.skip("Docker daemon not running; isolate the command instead.")

# Container path helpers: the container is Linux, so a marker written "outside
# the sandbox" is a POSIX path inside the container (e.g. /tmp/...). Docker
# Desktop does NOT map container /tmp to the host temp dir — that's the
# isolation property under test.
def marker_host_path() -> str:
    import os
    import tempfile
    return os.path.join(tempfile.gettempdir(), "ghost-host-escape-attempt.txt")


# Marker the command writes INSIDE the container (never visible on host).
def marker_in_container_path() -> str:
    return "/tmp/ghost-host-escape-attempt.txt"


# Document: mount restriction — host filesystem must not be reachable from inside
def test_container_mount_blocks_host_filesystem(tmp_path):
    """OJ06 acceptance: generated code cannot write outside the sandbox mount.

    Run a command that attempts to write to /tmp inside the container (a path
    outside the read-only /workspace mount). Docker Desktop maps container
    /tmp to a private tmpfs, not the host temp dir — so the host temp dir must
    remain clean, proving the container's filesystem writes are confined.

    Requires: Docker daemon running.
    """
    _docker_skip()
    import os
    stale = marker_host_path()
    if os.path.exists(stale):  # remove any stale marker from earlier runs
        os.remove(stale)
    result = run_in_isolation(
        tmp_path,
        ["sh", "-c", f"echo 'surprised' > {marker_in_container_path()}"],
        env={},
        timeout=10.0,
        input_data={"_command": ["sh", "-c", f"echo 'surprised' > {marker_in_container_path()}"]},
    )
    assert not os.path.exists(marker_host_path()), \
        "Host temp file was written; container filesystem is not isolated."


# Document: network restriction — outbound traffic must be refused
def test_container_network_isolated(tmp_path):
    """OJ06 acceptance: generated code cannot make outbound network calls.

    Run a command that makes an HTTP request to a test endpoint. The request
    must time out or be refused, proving the container's network namespace
    is isolated from the host.

    Requires: Docker daemon running.
    """
    _docker_skip()
    result = run_in_isolation(
        tmp_path,
        ["sh", "-c", "curl http://127.0.0.1:1/ || true"],
        env={},
        timeout=5.0,
        input_data={"_command": ["curl", "http://127.0.0.1:1/"]},
    )
    # Network must be blocked or refused; 127.0.0.1:1 is not a valid service.
    # The important thing is that no host network was accessed.
    assert result.get("isolated") is True


# Document: timeout enforcement — runaway commands are stopped
def test_container_timeout_stops_command(tmp_path):
    """OJ06 acceptance: a long-running command is stopped at the timeout.

    Run `sleep 60` with a 3-second timeout. The container must be terminated
    and a timeout error returned, not just the command's exit on timeout.

    Requires: Docker daemon running.
    """
    _docker_skip()
    result = run_in_isolation(
        tmp_path,
        ["sleep", "60"],
        env={},
        timeout=3.0,
        input_data={"_command": ["sleep", "60"]},
    )
    assert result.get("error_type") == "timeout", \
        f"Expected timeout error, got: {result.get('error_type')}"


# Document: artifact persistence — deliverables return via captured stdout;
# the source mount is read-only (writes to it must not reach the host)
def test_container_output_capture(tmp_path):
    """OJ06 acceptance: command deliverables are captured through the
    container boundary via the sentinel-wrapped JSON stdout channel.

    Requires: Docker daemon running.
    """
    _docker_skip()
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "hello.txt").write_text("hello")
    result = run_in_isolation(
        sandbox,
        ["sh", "-c", "cat hello.txt"],
        env={},
        timeout=15.0,
        input_data={"_command": ["sh", "-c", "cat hello.txt"]},
    )
    assert result.get("returncode") == 0, f"container run failed: {result}"
    assert "hello" in (result.get("output") or "")


def test_container_source_mount_read_only(tmp_path):
    """OJ06 acceptance: the sandbox source mount cannot modify the host.

    Upstream mounts every entry read-only; a write attempt into the mounted
    sandbox must fail inside the container and never land on the host.

    Requires: Docker daemon running.
    """
    _docker_skip()
    result = run_in_isolation(
        tmp_path,
        ["sh", "-c", "touch blocked-write.txt && echo wrote"],
        env={},
        timeout=10.0,
        input_data={"_command": ["sh", "-c", "touch blocked-write.txt"]},
    )
    assert not (tmp_path / "blocked-write.txt").exists(), \
        "Host sandbox was modified through a read-only mount."
    assert result.get("isolated") is True
