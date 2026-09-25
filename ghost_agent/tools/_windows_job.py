"""Trusted launcher: join a kill-on-close job BEFORE starting user code.

Invoked as an absolute script with Python -I. This controls process lifetime,
not filesystem/network access. The non-inheritable job handle lives until this
launcher exits (including when its supervising process terminates it).
"""
import ctypes
from ctypes import wintypes as w
from contextlib import contextmanager
import os
import subprocess
import sys
from threading import Thread


@contextmanager
def parent_handle():
    """Pass an exact process handle, not a PID that could be reused."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    handle = kernel.OpenProcess(0x00100000, True, os.getpid())  # SYNCHRONIZE only
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield handle
    finally:
        kernel.CloseHandle(handle)


def watch_parent(handle):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    kernel.WaitForSingleObject.restype = w.DWORD
    if kernel.WaitForSingleObject(handle, 0) != 258:  # WAIT_TIMEOUT means alive
        raise RuntimeError("Supervisor is unavailable")

    def wait():
        # Signalled parent or invalid handle both fail closed. Exiting closes
        # the job handle, terminating this launcher's generated-code children.
        kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
        os._exit(125)

    Thread(target=wait, name="ghost-supervisor-watch", daemon=True).start()


def join_job():
    class Basic(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                    ("flags", w.DWORD), ("min_ws", ctypes.c_size_t),
                    ("max_ws", ctypes.c_size_t), ("active", w.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", w.DWORD),
                    ("scheduling", w.DWORD)]

    class Limits(ctypes.Structure):
        _fields_ = [("basic", Basic), ("io", ctypes.c_ulonglong * 6),
                    ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                    ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    kernel.SetInformationJobObject.restype = w.BOOL
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    kernel.AssignProcessToJobObject.restype = w.BOOL
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = Limits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway.
    if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


if __name__ == "__main__":
    try:
        job = join_job()
        watch_parent(int(sys.argv[1]))
        result = subprocess.run(sys.argv[2:], shell=False)
    except Exception as exc:
        print(f"Ghost process launcher refused: {exc}", file=sys.stderr)
        sys.exit(125)
    sys.exit(result.returncode)
