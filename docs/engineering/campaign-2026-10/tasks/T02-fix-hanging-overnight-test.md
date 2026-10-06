# T02: Fix the infinite-loop overnight test; add hang diagnostics

Size: XS · Wave 1 · Files: `tests/test_overnight.py`, `pyproject.toml`

## Objective

`tests/test_overnight.py::TestRun::test_max_cycles_stops` hangs forever, so the
full suite never finishes. STATE.md wrongly blames the terminal environment.

**Root cause (verified):** `OvernightRunner.run()` (`ghost_agent/overnight.py`
~line 208) stops when `self._cycles >= max_cycles`. Only the real `run_cycle()`
increments `self._cycles`. The test replaces `run_cycle` with a `MagicMock`
side effect that increments a local `call_count` instead, so `_cycles` stays 0
and the loop never ends.

The production code is correct. **Fix the test, not `overnight.py`.**

## Steps

1. Read `ghost_agent/overnight.py` lines 87–222 and confirm that `run_cycle`
   increments `self._cycles` (search for `self._cycles += 1`). If it does not,
   stop and report.
2. In `tests/test_overnight.py`, change `counted_cycle` inside
   `test_max_cycles_stops` so it also advances the runner's counter, the way
   the real `run_cycle` does:
   ```python
           def counted_cycle(*, now=None):
               call_count["n"] += 1
               r._cycles += 1
               return MagicMock(emergency_stop=False, user_active=False)
   ```
3. Add a hard safety bound to the same test so that a regression fails instead
   of hanging. Before the `with patch.object(...)` block, add:
   ```python
           def bounded_cycle(*, now=None):
               if call_count["n"] >= 10:
                   raise AssertionError("run() did not stop at max_cycles")
               return counted_cycle(now=now)
   ```
   Then use `side_effect=bounded_cycle` instead of `side_effect=counted_cycle`.
4. Keep the existing assertion `assert cycles == 3`, and add
   `assert call_count["n"] == 3` after it.
5. In `pyproject.toml`, under the existing `[tool.pytest.ini_options]` section,
   add one line after `testpaths = ["tests"]`:
   ```toml
   faulthandler_timeout = 300
   ```
   This makes pytest dump all thread stack traces if a single test runs longer
   than 300 seconds, so future hangs are diagnosable. It is a built-in pytest
   option, not a new dependency.

## Tests to add

None beyond the fix. The fixed test is the test.

## Must NOT

- Change `ghost_agent/overnight.py`.
- Skip, xfail or delete the test.
- Add `pytest-timeout` or any other dependency.

## Verification

```bash
"$PY" -m pytest -q -p no:cacheprovider tests/test_overnight.py
"$PY" -m pytest -q -p no:cacheprovider
```

The second command is the **full suite with no deselect**. It must finish (about
2 minutes) with 0 failures. Record the exact pass count.

## Acceptance criteria

- AC1: `tests/test_overnight.py` finishes in under 10 seconds with every test
  passing.
- AC2: The full suite finishes without any deselect.
- AC3: If `run()` stopped honouring `max_cycles`, the test would fail with an
  `AssertionError` instead of hanging (argue this from the code in your report).
- AC4: `pyproject.toml` has `faulthandler_timeout = 300` and nothing else new.
