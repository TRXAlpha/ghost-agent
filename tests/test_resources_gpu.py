"""TJ3: real RAM/GPU snapshot and doctor resources string."""
import subprocess

import pytest

from ghost_agent import runtime as rt
from ghost_agent.runtime import Doctor, ResourceMonitor


class FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture()
def no_gpu(monkeypatch):
    monkeypatch.setattr(rt.shutil, "which", lambda name: None)


def test_windows_ram_fields_via_ctypes(tmp_path, no_gpu):
    # On Windows os.sysconf does not exist, so the ctypes GlobalMemoryStatusEx
    # branch must supply the RAM keys with plausible values.
    snapshot = ResourceMonitor(tmp_path).available()
    assert "ram_bytes" in snapshot
    assert "ram_total_bytes" in snapshot
    assert snapshot["ram_bytes"] <= snapshot["ram_total_bytes"]
    assert snapshot["ram_total_bytes"] > 0


def test_gpu_two_gpus_summed(tmp_path, monkeypatch, no_gpu):
    monkeypatch.setattr(rt.shutil, "which", lambda name: "C:/fake/nvidia-smi.exe")
    monkeypatch.setattr(rt.subprocess, "run",
                        lambda *a, **k: FakeProc(stdout="4096, 512\n4096, 1024\n"))
    snapshot = ResourceMonitor(tmp_path).available()
    assert snapshot["gpu_vram_total_bytes"] == (4096 + 4096) * 1048576
    assert snapshot["gpu_vram_free_bytes"] == ((4096 - 512) + (4096 - 1024)) * 1048576


def test_gpu_missing_binary_keys_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(rt.shutil, "which", lambda name: None)
    snapshot = ResourceMonitor(tmp_path).available()
    assert "gpu_vram_total_bytes" not in snapshot
    assert "gpu_vram_free_bytes" not in snapshot


def test_gpu_timeout_keys_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(rt.shutil, "which", lambda name: "C:/fake/nvidia-smi.exe")

    def slow(*a, **k):
        raise subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=3.0)

    monkeypatch.setattr(rt.subprocess, "run", slow)
    snapshot = ResourceMonitor(tmp_path).available()
    assert "gpu_vram_total_bytes" not in snapshot
    assert "gpu_vram_free_bytes" not in snapshot


def test_gpu_bad_output_keys_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(rt.shutil, "which", lambda name: "C:/fake/nvidia-smi.exe")
    monkeypatch.setattr(rt.subprocess, "run", lambda *a, **k: FakeProc(returncode=1))
    snapshot = ResourceMonitor(tmp_path).available()
    assert "gpu_vram_free_bytes" not in snapshot


def test_doctor_resources_string_both_known(tmp_path, monkeypatch):
    monkeypatch.setattr(ResourceMonitor, "available", lambda self: {
        "ram_bytes": 5.1e9, "ram_total_bytes": 16.0e9,
        "gpu_vram_free_bytes": 3.6e9, "gpu_vram_total_bytes": 4.0e9})
    assert Doctor(tmp_path).check()["resources"] == \
        "ram 5.1/16.0 GB free, gpu 3.6/4.0 GB free"


def test_doctor_resources_string_unknown_parts(tmp_path, monkeypatch):
    monkeypatch.setattr(ResourceMonitor, "available", lambda self: {"cpu_cores": 8.0})
    assert Doctor(tmp_path).check()["resources"] == "ram unknown, gpu unknown"


def test_fits_budget_refuses_gpu_job_without_gpu():
    from ghost_agent.runtime import ScheduledJob, Scheduler

    job = ScheduledJob(job_id="j1", goal_id="g1",
                       budget={"gpu_vram_free_bytes": 6e9})
    assert not Scheduler._fits_budget(job, {"cpu_cores": 4, "disk_bytes": 1e9})
    assert Scheduler._fits_budget(
        job, {"gpu_vram_free_bytes": 8e9})
