# T01: Fix the broken `cli.py` (owner's uncommitted learn commands)

Size: XS · Wave 1 · Files: `ghost_agent/cli.py`, new `tests/test_cli_learn.py`

## Objective

The owner has an uncommitted edit to `ghost_agent/cli.py` in the main checkout.
It adds four `ghost learn` sub-commands (`next`, `progress`, `lessons`,
`attempts`), but it has two bugs:

1. **`IndentationError` at line 1786.** The `daemon` parser lines were
   over-indented, so `import ghost_agent.cli` fails and **every `ghost` command
   is broken**.
2. **`learn attempts` prints only one attempt**, because `return` is inside the
   `for` loop.

Bring the owner's edit into your worktree, fix both bugs, remove the
unimplemented `--watch` flag, and add tests.

## Steps

1. Apply the owner's diff, which was saved before the campaign started:
   ```bash
   git apply "C:/Users/chris/Documents/Ghost/owner-cli-uncommitted-2026-10-06.patch"
   git diff --stat
   ```
   Expected: only `ghost_agent/cli.py` changed, about 50 lines added. If
   `git apply` fails, stop and report.

2. Confirm the bug:
   ```bash
   "$PY" -m py_compile ghost_agent/cli.py
   ```
   Expected: `IndentationError: unexpected indent (... line 1786)`.

3. Fix the daemon parser block. Find this text (around lines 1782–1789):
   ```python
       daemon_run.add_argument("--trace-every", type=int, default=10,
                                   help="Run trace-learning every N cycles (default 10)")
           daemon_run.add_argument("--watch", action="store_true",
                                   help="Run daemon in watch mode (like proactive watch)")
           daemon_sub.add_parser("status", help="Show daemon health / last cycle summary")
           daemon_sub.add_parser("stop", help="Engage emergency stop to stop the daemon")
   ```
   Replace it with exactly this (the `--watch` flag is removed because
   `_cmd_daemon` never reads it; a flag that does nothing is a fake feature):
   ```python
       daemon_run.add_argument("--trace-every", type=int, default=10,
                               help="Run trace-learning every N cycles (default 10)")
       daemon_sub.add_parser("status", help="Show daemon health / last cycle summary")
       daemon_sub.add_parser("stop", help="Engage emergency stop to stop the daemon")
   ```

4. Fix the `attempts` branch in `_cmd_learn`. Find:
   ```python
       if action == "attempts":
           items = learner.attempts(args.concept_id, limit=getattr(args, "limit", 20))
           if not items:
               print("No attempts recorded.")
               return
           for a in items:
               outcome = "✓" if a.correct else "✗"
               print(f"  {outcome} [{a.concept_id}] at {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(a.at))}"
               f"{f' | {a.submission[:60]}' if a.submission else ''}")
               return
   ```
   Replace it with exactly:
   ```python
       if action == "attempts":
           items = learner.attempts(args.concept_id, limit=getattr(args, "limit", 20))
           if not items:
               print("No attempts recorded.")
               return
           for a in items:
               outcome = "correct" if a.correct else "wrong"
               when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(a.at))
               detail = f" | {a.submission[:60]}" if a.submission else ""
               print(f"  {outcome} [{a.concept_id}] at {when}{detail}")
           return
   ```
   (The ✓/✗ symbols are replaced by words because the Windows console code page
   can raise `UnicodeEncodeError` on them.)

5. Check that there are no other `✓`/`✗` characters in the lines the owner's
   patch added. Leave other existing occurrences in the file alone.

6. Compile and import:
   ```bash
   "$PY" -m py_compile ghost_agent/cli.py
   "$PY" -c "import ghost_agent.cli"
   "$PY" -m ghost_agent.cli learn --help
   "$PY" -m ghost_agent.cli daemon run --help
   ```
   All four must succeed. `learn --help` must list `next`, `progress`, `lessons`
   and `attempts`. `daemon run --help` must **not** list `--watch`.

## Tests to add: `tests/test_cli_learn.py`

Follow the pattern in `tests/test_cli_devices.py`: `monkeypatch.chdir(tmp_path)`,
call `_cmd_learn(argparse.Namespace(...))` directly, and read `capsys`. Use
`LearnerStore(tmp_path)` from `ghost_agent.learner` to set up data
(`register_concept(concept_id, name)` and
`record_attempt(concept_id, correct, ...)`; read the signature in
`ghost_agent/learner.py` ~line 177 first).

1. `test_learn_attempts_lists_every_attempt`: register one concept and record
   3 attempts. Run `learn_action="attempts"`, `concept_id=...`, `limit=20`.
   Assert that the output contains 3 lines that start with `"  correct"` or
   `"  wrong"`.
2. `test_learn_attempts_respects_limit`: 3 attempts, `limit=2`, assert
   exactly 2 attempt lines.
3. `test_learn_attempts_empty`: no attempts, assert `"No attempts recorded."`.
4. `test_learn_progress_reports_overall_mastery`: one concept, attempts
   correct+wrong, `learn_action="progress"`, assert `"Overall mastery: 50.0%"`
   is in the output.
5. `test_learn_next_when_nothing_due`: no concepts, `learn_action="next"`,
   assert `"Nothing due for review right now."`.
6. `test_cli_module_imports`: `import ghost_agent.cli` and assert
   `hasattr(ghost_agent.cli, "main")`. This guards against the indentation
   regression.

## Must NOT

- Change any `learn` behaviour other than the two fixes above.
- Touch `_cmd_daemon` (T12 rewrites it).
- Reformat other parts of `cli.py`.

## Verification

```bash
"$PY" -m py_compile ghost_agent/cli.py
"$PY" -m pytest -q -p no:cacheprovider tests/test_cli_learn.py tests/test_cli_devices.py tests/test_cli_outcomes.py
"$PY" -m pytest -q -p no:cacheprovider --deselect tests/test_overnight.py::TestRun::test_max_cycles_stops
```

## Acceptance criteria

- AC1: `import ghost_agent.cli` succeeds.
- AC2: `learn next/progress/lessons/attempts` exist, and `attempts` prints all
  attempts up to `--limit`.
- AC3: `daemon run` has no `--watch` flag.
- AC4: The new tests pass, and the full suite (minus the known hanging test)
  has no new failures.

## Root integration note (not for the worker)

The owner's uncommitted `cli.py` diff is preserved at
`C:/Users/chris/Documents/Ghost/owner-cli-uncommitted-2026-10-06.patch`, and
T01 incorporates it. The main checkout's `cli.py` was already restored to HEAD
on 2026-10-06 (verified: the patch reapplies cleanly), so the T01 merge needs
no cleanup. Never touch `AGENTS.md`; its uncommitted owner edit stays as is.
