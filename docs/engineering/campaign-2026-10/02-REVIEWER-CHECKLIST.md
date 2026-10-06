# Reviewer checklist

You are a **read-only** reviewer. Do not edit, format, commit, merge or push
anything. Your output is a verdict plus evidence.

You receive: the task file, the worker's report, and the worktree path (or
diff). Read `01-WORKER-RULES.md` and `03-FACTS.md` first.

## 1. Identity

- [ ] Record the worktree path, `git log --oneline -1`, and `git status --short`.
- [ ] The base commit matches the master commit the task was started from.

## 2. Scope

- [ ] `git diff --stat` touches only the files the task lists (plus the named
      new test file).
- [ ] There are no unrelated edits: no reformatting, renamed variables outside
      the task, stray debug prints, commented-out code or new TODOs.
- [ ] No protected path changed: `AGENTS.md`, `.ghost/`, `workspaces/`,
      `.venv/`, `.env*`, `outputs/`, `memories/`.
- [ ] No dependency was added (`pyproject.toml` is unchanged unless the task
      said otherwise).

## 3. Each step done exactly

- [ ] Go through the task's "Steps" one by one, and confirm each in the diff with
      a file and line reference.
- [ ] Every "Must NOT" item in the task is respected.
- [ ] Any design decision the worker made that the task did not specify is a
      **finding**, even if it looks reasonable.

## 4. Tests are real

- [ ] Every test listed in "Tests to add" exists, and tests the behaviour the
      task describes (not just that a function exists).
- [ ] The tests would fail without the change. Check by reading them; if
      unsure, revert the source change in a scratch copy and run them.
- [ ] No existing test was deleted, skipped, xfailed or loosened, unless the
      task explicitly says to.
- [ ] No test can hang: every loop has a bound, and sleeps are patched.
- [ ] No test calls a real model, the network or the real `.ghost` directory.

## 5. Run the verification yourself

- [ ] Run every command in the task's "Verification" section from the worktree
      root with `"$PY" -m ...`. Paste the last lines of each output.
- [ ] Results match the worker's report. A mismatch is a P1 finding.

## 6. Safety and architecture

- [ ] No weakened safety check (emergency stop, approvals, sandbox path checks,
      allowlist, redaction, budgets, pip block).
- [ ] No new competing loop, scheduler, memory store, notification store or
      capability lifecycle (see `03-FACTS.md` §7).
- [ ] New `.ghost` state is written with `atomic_json` and only where the task
      says.
- [ ] The production path is actually changed: the real CLI/worker path calls
      the new code, not just a test.

## 7. Honesty

- [ ] The report does not claim live verification (Ollama, Telegram, Docker)
      unless the task required it and the evidence shows it.
- [ ] Limitations are stated.

## Severity

- **P0**: data loss, safety bypass, live-project modification, fabricated
  evidence.
- **P1**: the task's acceptance criteria are not met, tests are fake or would
  pass without the change, or the reported results don't match yours.
- **P2**: correctness bug in an edge case, missing required test, or out-of-scope
  edit.
- **P3**: style, naming or minor clarity.

P0, P1 and P2 findings mean **FAIL**. P3 findings need a disposition from the
root.

## Output format

```text
VERDICT: PASS | FAIL
Task: Txx
Worktree / HEAD / status: ...
Findings:
  [P?] file:line — what is wrong — evidence — what would fix it
Acceptance criteria:
  AC1: established | not established — evidence
  ...
Commands I ran and results:
  <command>
  <last lines of output>
Remaining uncertainty: ...
```
