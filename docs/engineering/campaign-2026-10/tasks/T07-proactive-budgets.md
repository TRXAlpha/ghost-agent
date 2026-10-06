# T07: Realistic budgets for proactive and queued jobs

Size: S · Wave 2 (after T03) · Files: `ghost_agent/proactive.py`,
`ghost_agent/cli.py`, tests in `tests/test_proactive.py` and
`tests/test_scheduler_recurring.py`

## Objective

Proactive, Telegram and capability-improver jobs are created with
`job.iterations = 4`, `command_budget_seconds = 120` and
`command_budget_calls = 16` (`queue_sandbox_outcome`, `proactive.py`
~lines 250–262). With planning and verification each costing an iteration
(fixed separately in T08), 4 iterations leave room for about one real attempt.
`ghost jobs add` defaults to `--iterations 3`.

Raise them to the values decided below. They are still bounded.

## Decided values

| Setting | Old | New |
|---|---|---|
| `job.iterations` in `queue_sandbox_outcome` | 4 | 10 |
| `ScheduledJob(... budget={"iterations": ...})` in `queue_sandbox_outcome` | 4 | 10 |
| `command_budget_seconds` in `queue_sandbox_outcome` | 120 | 600 |
| `command_budget_calls` in `queue_sandbox_outcome` | 16 | 32 |
| `ghost jobs add --iterations` default (`cli.py` ~line 1686) | 3 | 10 |

Put the four `queue_sandbox_outcome` values in module-level constants at the top
of `proactive.py`, after the imports:

```python
# Bounded per-job allowance for queued sandbox work (proactive, Telegram,
# capability improver). Raised from 4 iterations / 120 s after real jobs
# could not finish within it (audit 2026-10-06).
QUEUED_JOB_ITERATIONS = 10
QUEUED_JOB_COMMAND_SECONDS = 600
QUEUED_JOB_COMMAND_CALLS = 32
```

Use the constants in `queue_sandbox_outcome` (the iterations constant is used
twice).

## Steps

1. Read `queue_sandbox_outcome` and confirm the four old values. If they differ,
   stop and report.
2. Add the constants and use them as described.
3. In `cli.py`, change only the `default=3` on the `jobs` parser's
   `--iterations` argument to `default=10`. Update its help text if it mentions
   3.
4. Search the tests for assertions on the old values:
   ```bash
   git grep -n "== 4\b\|== 120\|== 16\b\|iterations.*3" tests/
   ```
   If an existing test asserts an old value **specifically for these
   settings**, update only that number to the new value, and list each change
   in your report. Do not change any other assertion.
5. Run `"$PY" -m py_compile ghost_agent/cli.py ghost_agent/proactive.py`.

## Tests to add

In `tests/test_proactive.py`:

- `test_queued_job_budget_values`: call
  `queue_sandbox_outcome(tmp_path, "build a small calculator", "scripted-model")`.
  Assert `job.iterations == 10` and that the contract has
  `command_budget_seconds == 600` and `command_budget_calls == 32`. Also load the
  scheduler (`Scheduler(tmp_path)`; read `runtime.py` for how to list jobs) and
  assert that the scheduled job's `budget["iterations"] == 10`.

In `tests/test_scheduler_recurring.py` (or wherever `jobs add` is tested; find
it with `git grep -n "jobs.*add" tests/`):

- `test_jobs_add_default_iterations_is_10`: build the parser the way existing
  tests do, or call `main()` with patched `sys.argv` if that is the existing
  pattern. Assert the default is 10. If no existing test drives the parser,
  write the test by parsing
  `["jobs", "add", "<goal>"]` through the same parser-construction code the
  existing tests use. If none exists, stop and report.

## Must NOT

- Change `ghost accomplish`'s default (`--iterations 12`) or its command budgets.
- Remove any bound (all values stay finite and validated by existing code).

## Verification

```bash
"$PY" -m pytest -q -p no:cacheprovider tests/test_proactive.py tests/test_proactive_feedback.py tests/test_scheduler_recurring.py tests/test_telegram_readiness.py tests/test_autonomy_improver.py
"$PY" -m pytest -q -p no:cacheprovider --deselect tests/test_overnight.py::TestRun::test_max_cycles_stops
```

(If T02 is already merged, drop the `--deselect`.)

## Acceptance criteria

- AC1: Queued jobs get 10 iterations, 600 s and 32 calls via named constants.
- AC2: `jobs add` defaults to 10 iterations.
- AC3: Every changed existing assertion is listed in the report, with the
  reason.
- AC4: The full suite passes.
