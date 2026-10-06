# Worker report template

Copy this exactly and fill in every section. Write "none" where a section is
empty. Do not leave a section out.

```text
TASK: Txx — <title>
STATUS: candidate ready for review | blocked | partial

START POINT
  worktree: <path>
  git log --oneline -1: <output>
  git status --short (before work): <output>
  ghost_agent import path check: <output of the import check in 03-FACTS §1>

BASELINE (before any change)
  <command>
  <last 5 lines of output>

FILES CHANGED
  <path> — <one line: what changed>
  ...
  (paste `git diff --stat`)

STEPS
  Step 1: done | skipped (why) — <file:line evidence>
  Step 2: ...

TESTS ADDED
  <test file>::<test name> — what it proves
  Fails without the change? yes (shown) | yes (by reasoning) | no — explain

VERIFICATION (after change)
  <command>
  <last 5 lines of output>

ACCEPTANCE CRITERIA
  AC1: met | not met — evidence
  ...

LIVE / EXTERNAL CHECKS
  none performed | <exactly what was run, against what, with what result>

QUESTIONS / BLOCKERS
  <any situation the task did not cover; what you did NOT decide>

NOTICED BUT NOT TOUCHED
  <other bugs or oddities, with file:line>

LIMITATIONS
  <what this change does not do>
```
