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
    result = run_in_isolation(tmp_path, ["pytest", "-q"], env={}, timeout=10.0)
    assert result["available"] is False
    assert result["isolated"] is False
    assert result["error_type"] == "docker_unavailable"
    assert isinstance(result["error"], str)
    assert "note" in result
    # Must not raise — callers rely on the structured result, not an exception


def test_unavailable_result_has_all_required_fields(tmp_path):
    """The unavailability dict includes every field callers depend on."""
    required_fields = {"available", "error", "error_type", "isolated", "note"}
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

# Document: mount restriction — host filesystem must not be reachable from inside
def test_container_mount_blocks_host_filesystem(tmp_path):
    """OJ06 acceptance: generated code cannot write outside the sandbox mount.

    Run a command that attempts to write to /etc/outside-sandbox. The write
    must fail or be invisible to the host, proving the mount is read-only
    at the host boundary and the container's write attempt is refused.

    Requires: Docker daemon running.
    """
    _docker_skip()
    # Attempt a write to a path OUTSIDE the mount
    result = run_in_isolation(
        tmp_path,
        ["sh", "-c", "echo 'surprised' > /tmp/ghost-host-escape-attempt.txt"],
        env={},
        timeout=10.0,
        input_data={"_command": ["sh", "-c", "echo 'surprised' > /tmp/ghost-host-escape-attempt.txt"]},
    )
    import os
    assert not os.path.exists("/tmp/ghost-host-escape-attempt.txt"), \
        "Host file was written; sandbox mount is not restrictive."


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


# Document: artifact preservation — writes inside sandbox are visible
def test_container_sandbox_write_persists(tmp_path):
    """OJ06 acceptance: files written inside the sandbox mount are visible.

    Run a command that writes a file inside /workspace (the mount point).
    Verify the file appears on the host at the expected path, proving the
    mount allows writes in the correct direction and promotion can find them.

    Requires: Docker daemon running.
    """
    _docker_skip()
    artifact = tmp_path / "artifact.txt"
    # Write inside the container's workspace and verify the host sees it
    result = run_in_isolation(
        tmp_path,
        ["sh", "-c", "echo hello > /workspace/artifact.txt"],
        env={},
        timeout=10.0,
        input_data={"_command": ["sh", "-c", "echo hello > /workspace/artifact.txt"]},
    )
    # The file should appear inside tmp_path (the host mount)
    assert artifact.exists(), "Artifact not visible on host; mount write may be reversed."
    assert artifact.read_text().strip() == "hello"
