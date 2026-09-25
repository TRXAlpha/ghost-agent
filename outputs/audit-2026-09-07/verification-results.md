# Verification result integrity

Checkpoint verification: **582 tests passed** in 21.97 seconds; compileall passed.

Gate-checkpoint follow-up: each attempted gate is saved as running before
execution, then passed/failed with its result after execution. Both command
and output use existing best-effort secret-pattern redaction. An interruption
test verifies that an earlier passed gate survives and an interrupted gate has
no fabricated result. A persisted running label is an interrupted attempt record,
not proof of a currently live process. Resume may rerun checks; this is not
exactly-once execution and tests still have host access.

Verification: **554 tests passed** in 18.71 seconds; compileall passed.

Source-only follow-up: **560 passed** in 17.90 seconds; compileall passed.
Removed implicit compileall-as-acceptance fallback. A scripted source-only run
now records verification_missing, never tests_passed. Focused selection accepts
both pytest filename conventions and rejects non-Python test-prefixed assets.
Explicit user test commands are still honored; their meaningfulness is not
independently established. Non-Python ecosystem verification remains unfinished.

Command success now requires an actual integer zero exit code, string output,
and no error field. Boolean false and floating-point zero no longer count as
successful exits. The verification adapter rejects missing, multiple, non-record,
or malformed results with an explicit failed gate. Execution timeout and stop
errors already enter this failed-result path through the tool dispatcher.

Focused-test and broad-suite failures now record the gate that actually failed;
the presence of an earlier successful focused gate no longer mislabels a later
suite failure. Regression tests cover malformed verdicts and both stage paths
through a scripted orchestrator run.

These are result-integrity checks, not independently generated acceptance tests.
Generated code and tests still share host permissions, and a real integer zero
does not prove that the test suite meaningfully covers the requested outcome.
