# T08: Count only model work against the iteration limit

Size: S · Wave 2 (after T09) · Important: two reviewers
Files: `ghost_agent/orchestrator.py`, tests

## Objective

In `Orchestrator._run_loop` (~lines 411–742), every phase does
`state["iteration"] += 1`, including:

- **PLAN**, which runs exactly once per run (only `INGEST` moves to `PLAN`);
- **VERIFY**, which makes no model call (it only runs tests).

Worse, the limit check at the top of the loop fires **before** a pending
VERIFY. When the last REPAIR reaches the limit, its fix is never tested, and
the job ends `iteration_limit` even if the fix was correct.

With the default limit of 8, the sequence
PLAN, IMPLEMENT, VERIFY, REPAIR, VERIFY, REPAIR, VERIFY, REPAIR gives only 4
real attempts, and the last one is never verified.

## Decided behaviour

1. `state["iteration"]` counts **IMPLEMENT and REPAIR turns only**.
2. A **successful PLAN** does not increment. A **failed PLAN** (it goes to
   REPAIR) also does not increment; the REPAIR that follows will.
3. **VERIFY never increments.**
4. The iteration-limit check is skipped while `state["phase"] == "VERIFY"`, so
   the last attempt is always verified. VERIFY then goes to DONE
   (`tests_passed`) or REPAIR, and the limit check fires on the next loop.

**Termination proof (include it in your report):** PLAN runs at most once per
run (only INGEST leads to it; resume never sets PLAN except from a stored
checkpoint, which also runs it once). Every VERIFY is followed by DONE or a
REPAIR. Every REPAIR and IMPLEMENT increments. So the number of model turns is
at most `1 + iteration_limit`, and the number of VERIFY runs is at most
`iteration_limit + 1`. Emergency stop and command budget checks are unchanged.

## Steps

1. Read the whole `_run_loop`. Confirm that `state["phase"] = "PLAN"` is set in
   exactly one place (the INGEST branch):
   ```bash
   git grep -n "\"PLAN\"" ghost_agent/orchestrator.py
   ```
   Expected: the INGEST branch, the `if phase == "PLAN":` check, the
   `_tui_phase("PLAN", ...)` call, and the phase set in `_prepare_resume`'s
   validation. If any other line **sets** the phase to PLAN, stop and report.
2. Change the limit check (~line 510) from
   ```python
   if state["phase"] != "DONE" and state["iteration"] >= iteration_limit:
   ```
   to
   ```python
   # VERIFY makes no model call; always verify the final attempt before stopping.
   if state["phase"] not in ("DONE", "VERIFY") and state["iteration"] >= iteration_limit:
   ```
3. In the `if phase == "PLAN":` branch, delete the line `state["iteration"] += 1`.
4. In the `if phase == "VERIFY":` branch, delete the line
   `state["iteration"] += 1`.
5. Leave the IMPLEMENT and REPAIR increments exactly as they are.
6. Do not change `_prepare_resume`.

## Existing tests that may change

Some tests assert iteration numbers. `tests/test_resume_validation.py` line ~40
asserts `state["iteration"] == 2`. Run the whole suite. For **each** failing
test:

- If it fails only because an asserted iteration count or a phase sequence now
  matches the new counting rule, update that one number or list, and record
  `old -> new` with a one-line reason in your report.
- If it fails for any other reason, stop and report. Do not "fix" it.

## Tests to add: `tests/test_iteration_counting.py`

Use the scripted-model pattern from `03-FACTS.md` §6, with
`Orchestrator.run_task`.

1. `test_plan_and_verify_do_not_consume_iterations`: limit 1, and the scripted
   model writes a correct `app.py` and `test_app.py` in IMPLEMENT. Expect
   `last_result == "tests_passed"` (under the old code this was
   `iteration_limit`: PLAN used the only iteration).
2. `test_final_repair_is_verified`: limit 2. IMPLEMENT writes a failing test;
   REPAIR writes a fix. Expect `tests_passed`, with `state["iteration"] == 2`.
3. `test_iteration_limit_still_enforced`: limit 3, and the model always writes a
   failing implementation. Expect `last_result == "iteration_limit"` and
   `state["iteration"] == 3`. Count the scripted model calls with a counter in
   the fake, and assert the count is `<= 4` (1 plan + 3).
4. `test_plan_failure_does_not_loop`: the model returns invalid JSON for the
   plan. Assert that the run terminates (bound the fake's call count; raise
   `AssertionError` after 20 calls) with a non-`tests_passed` result.

## Must NOT

- Change emergency-stop, command-budget or required-file checks.
- Change the default iteration limits (T07 handles queued-job budgets).

## Verification

```bash
"$PY" -m pytest -q -p no:cacheprovider tests/test_iteration_counting.py tests/test_resume_validation.py tests/test_deliverable_contracts.py tests/test_node_verification.py tests/test_command_budget.py tests/test_autonomy.py
"$PY" -m pytest -q -p no:cacheprovider
```

## Acceptance criteria

- AC1: Only IMPLEMENT and REPAIR increment the iteration counter.
- AC2: The final attempt before the limit is always verified (test 2).
- AC3: The limit is still enforced (test 3), and every run terminates (test 4).
- AC4: Every changed existing assertion is listed with old -> new and a reason.
- AC5: The full suite passes.
