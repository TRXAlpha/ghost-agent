# Bounded command execution

Verification: full suite **535 passed** in 16.36 seconds; compileall passed.
Supervisor-watch follow-up: **538 passed** in 18.14 seconds; compileall passed,
including invalid supervisor-handle refusal before generated code executes.

The host command runner now drains combined stdout/stderr in fixed-size chunks,
retaining at most four UTF-8 bytes per requested output character (maximum
1 MiB characters), instead of collecting unlimited output before truncation.
There is no output spool file. Output ordering is now the merged pipe order.
Timeout/output limits are validated before launch.

On Windows a trusted absolute Python script launched with `-I` joins an unnamed
kill-on-close job before spawning the requested command. Assignment failure
refuses execution. The job handle is not inherited. Normal launcher exit or
timeout termination kills remaining job descendants. See Microsoft's
[job object documentation](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects).
On POSIX a new session/process group is killed on completion or timeout; children
that deliberately change sessions can escape that group.

The orchestrator supplies an emergency-stop check, polled every 100 ms while a
command runs. Stop and callback errors enter the same cleanup path as timeout.
This is polling plus OS cleanup latency, not instantaneous hard real-time stop.

Real Windows tests verify normal and timeout descendant termination with
non-mutating process handles, both output streams, nonzero exit codes, Unicode,
invalid limits, pre-launch/in-flight stop, and 10 MiB output with less than
2 MiB peak traced Python allocations. POSIX cleanup is not verified on this
Windows host.

Follow-up: Windows launchers inherit a SYNCHRONIZE-only handle to the exact
supervisor process through an explicit handle list. A watcher exits the launcher
when that process terminates or the wait fails, closing the kill-on-close job.
This avoids PID reuse and refuses missing/dead supervisor handles before starting
generated code. The supervisor closes its local handle copy after launch.
Tests exercise both `os._exit(73)` and forced supervisor termination, then verify
that the launcher and generated command have exited. The API mechanism uses
[Windows wait functions](https://learn.microsoft.com/en-us/windows/win32/sync/wait-functions).

Remaining boundaries: no filesystem/network isolation, CPU/RAM/GPU limits or
POSIX supervisor-crash watchdog. A hostile process with the
host user's rights is not contained by these controls. No external services
were launched, packages installed, or user artifacts removed by this change.
