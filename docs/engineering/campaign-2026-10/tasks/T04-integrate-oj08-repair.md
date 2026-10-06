# T04: Integrate the unmerged OJ08 repair commit `7c1a1f0`

Size: S · Wave 1 · Files: `ghost_agent/models/chat.py`, `tests/test_bridge_retry.py`

## Objective

OJ08 (bounded model retry) was merged into master as commit `773f1d5`'s
content. Reviewers then found problems, and the fix commit `7c1a1f0` on branch
`oj08-retry` was **never merged**. Its message:

> OJ08: address reviewer findings — reservation-required contract, real
> input_chars, no-retry local errors, guarded telemetry, explicit model arg

Bring that commit into your worktree, resolve any conflicts conservatively, and
prove it with tests.

**This task explicitly permits `git cherry-pick`**, which is otherwise
forbidden by the worker rules.

## Steps

1. Inspect the commit:
   ```bash
   git show --stat 7c1a1f0
   git show 7c1a1f0
   ```
   Expected: changes only `ghost_agent/models/chat.py` and
   `tests/test_bridge_retry.py`. If it touches anything else, stop and report.
2. Run the baseline:
   ```bash
   "$PY" -m pytest -q -p no:cacheprovider tests/test_bridge_retry.py tests/test_openjarvis_spend.py tests/test_model_routing.py
   ```
3. Apply it without committing:
   ```bash
   git cherry-pick --no-commit 7c1a1f0
   ```
4. **If there are conflicts:** resolve each hunk so that the final code
   contains every behaviour described in the commit message **and** every
   behaviour already in master. If a hunk cannot satisfy both, stop, run
   `git cherry-pick --abort`, and report the hunk.
5. Read the final `models/chat.py` diff against master and confirm these five
   behaviours from the commit message, each with a file:line reference in your
   report:
   - a. a spend reservation is required (the contract refuses to call without
     one where the commit says so);
   - b. `input_chars` is computed from the real messages;
   - c. local errors (for example `ValueError` or `IntegrationError`) are
     **not** retried;
   - d. telemetry/event emission failures cannot break the call;
   - e. the model argument is passed explicitly.
6. Find every caller of `chat(` in `ghost_agent/` (`git grep -n "chat("`) and
   check whether the commit changed the signature in a way a caller relies on.
   List any caller that would break, and stop if one would.

## Tests to add

The commit already adds tests to `tests/test_bridge_retry.py`. Do not add more
unless step 5 finds a behaviour with no test. In that case, add one focused
test per missing behaviour, using the fakes already in that file.

## Must NOT

- Commit anywhere except your own worktree branch (rule 7). Commit the
  resolved cherry-pick there as your single candidate commit.
- Change retry counts, backoff or which errors are retryable beyond what the
  commit does.

## Verification

```bash
"$PY" -m pytest -q -p no:cacheprovider tests/test_bridge_retry.py
"$PY" -m pytest -q -p no:cacheprovider --deselect tests/test_overnight.py::TestRun::test_max_cycles_stops
```

## Acceptance criteria

- AC1: All five behaviours (5a–5e) are present, with file:line evidence.
- AC2: `tests/test_bridge_retry.py` passes, including the commit's added tests.
- AC3: The full suite has no new failures.
- AC4: No caller of `chat()` is broken.
