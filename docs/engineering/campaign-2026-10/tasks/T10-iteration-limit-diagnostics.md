# T10: Record why a job hit the iteration limit

Size: S · Wave 4 (after T06, T08) · Files: `ghost_agent/orchestrator.py`,
new `tests/test_limit_diagnostics.py`

## Objective

When a job ends with `iteration_limit`, its `state.json` has
`last_result = "iteration_limit"` and loses what was actually going wrong
(tests failing? invalid JSON? the model never wrote a test?). Trace-learning
then groups all such jobs into one useless signature.

Record a small, structured diagnostic in `state.json` so people (and later
tooling) can see *why*.

## Decided state additions

All are written into the existing `state` dict, and therefore into `state.json`
through the existing `_write_state`.

- `state["turns"]`: a dict counting model turns per phase. Keys `"PLAN"`,
  `"IMPLEMENT"`, `"REPAIR"`; values are ints. Increment the matching key once
  each time a model turn is made in that branch, right after `_model_turn`
  returns.
- `state["parse_errors"]`: an int, incremented whenever `_model_turn` returns a
  non-None `parse_error`, in any branch.
- `state["results_history"]`: a list of the `last_result` value set at the end
  of each phase step (PLAN, IMPLEMENT, VERIFY, REPAIR), capped at the most
  recent 20 entries. Append after `state["last_result"]` is set in each branch.
- When the iteration limit fires, before `state["last_result"]` is overwritten,
  set
  `state["limit_reason"] = state.get("last_result") or "unknown"`
  (for example `"tests_failed"`, `"repair_failed"`, `"tests_missing"`).

Initialise missing keys with `state.setdefault(...)` at the start of
`_run_loop`, so resumed checkpoints from older versions still work. For example:
`state.setdefault("turns", {"PLAN": 0, "IMPLEMENT": 0, "REPAIR": 0})`.

## Steps

1. Read `_run_loop` in full, after T06 and T08 are merged.
2. Add the `setdefault` lines right before the `while True:` loop.
3. Add the increments and appends described above. Keep each addition to one or
   two lines. Write a small private helper
   `_note_result(state: Dict[str, Any]) -> None` that appends
   `state["last_result"]` to `results_history` and trims it to the last 20, and
   call it in each branch after `last_result` is set.
4. In the iteration-limit block, set `limit_reason` before
   `state["last_result"] = "iteration_limit"`.
5. Check that `_prepare_resume`'s validation does not reject the new keys (it
   validates specific keys only; confirm and say so in your report).

## Tests to add: `tests/test_limit_diagnostics.py`

Use the scripted-model pattern from `03-FACTS.md` §6.

1. `test_limit_reason_records_tests_failed`: the model always writes a failing
   test; limit 2. Assert `state["last_result"] == "iteration_limit"`,
   `state["limit_reason"] == "tests_failed"` (or `"focused_tests_failed"`,
   whichever the VERIFY gate produced; assert it is one of those two), and
   `state["turns"]["IMPLEMENT"] == 1`, `state["turns"]["REPAIR"] >= 1`.
2. `test_parse_errors_counted`: the model returns `"not json"` for every
   non-plan call; limit 2. Assert `state["parse_errors"] >= 2`.
3. `test_results_history_capped`: limit 15 with an always-failing model; assert
   `len(state["results_history"]) <= 20`.
4. `test_resume_old_checkpoint_without_new_keys`: write a `state.json` without
   the new keys, resume via the existing resume path (copy the setup from
   `tests/test_resume_validation.py`), and assert that the run completes and
   that the keys now exist.

## Must NOT

- Change any phase transition, counter rule or result value.
- Write any new file (only `state.json`, via existing writes).

## Verification

```bash
"$PY" -m pytest -q -p no:cacheprovider tests/test_limit_diagnostics.py tests/test_resume_validation.py tests/test_iteration_counting.py
"$PY" -m pytest -q -p no:cacheprovider
```

## Acceptance criteria

- AC1: `iteration_limit` jobs record `limit_reason`, `turns`, `parse_errors`
  and `results_history`.
- AC2: Old checkpoints still resume.
- AC3: The full suite passes.

## Follow-up (not this task)

Teaching `trace_learning.py` to use `limit_reason` in its signatures is a
separate task for the root to schedule later.
