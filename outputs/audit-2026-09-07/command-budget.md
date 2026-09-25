# Durable command allowance

Exhaustion follow-up: the coding loop checks persisted allowance before each
phase. Exhaustion terminates with command_budget_exhausted; corrupt/changed
records terminate with command_budget_invalid. A scripted workflow verifies
that a one-call allowance dispatches one command and does not request further
model repair turns. Successful final verification can still complete when it
uses the last permitted command; the budget check does not override DONE.

Verification: **592 tests passed** in 25.25 seconds; compileall passed.
Exhaustion follow-up: **593 passed** in 24.45 seconds; compileall passed.

Orchestrated outcome commands, including verification and repair commands, now
share a persisted call/time budget in the outcome artifacts. Defaults are 32
attempts and 300 seconds; validated task constraints can configure limits up to
1000 calls and one day. Existing records must match configured limits on resume.

Admission locks the budget, reserves a call and up to 30 seconds before execution,
and persists the reservation atomically. Completion refunds unused reserved time;
an abrupt process exit leaves a conservative charged reservation. A concurrent
admission is refused. Corrupt or exhausted state prevents command execution.
Tests cover reservation/refund, persisted calls, changed limits, corruption,
concurrency, and orchestrator command admission. A real subprocess exits through
`os._exit(73)` inside a reserved slot; the reservation remains charged, the OS
releases its lock, and subsequent admission refuses the exhausted allowance.

Limits: this bounds command attempts and allocated execution time only. Cleanup
latency, inference, research, GPU/CPU/RAM and total outcome wall time are not
included. It does not isolate host files or stop hostile writes to budget files.
Legacy interrupted outcomes without a budget record begin with a new allowance.
Standalone diagnostic tool calls outside an outcome retain their per-command
timeout. Budget exhaustion is a failed tool result, not verified completion.
