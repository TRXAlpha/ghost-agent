# Orchestrator brief: run Campaign 2026-10 with Hermes subagents

You are the **root orchestrator** (AGENTS.md) for Campaign 2026-10 on the Ghost
repo at `C:\Users\chris\Documents\Ghost\ghost_agent` (branch `master`). You
run as a goal-mode kanban worker. You do **not** write product code yourself.
You dispatch subagents with `delegate_task`, judge their evidence, merge
accepted work into `master`, and keep a progress log.

Everyone, including you, runs on `zai-org/GLM-5.3-Flash`. Do not override
models.

## Read first (completely, in this order)

1. `docs/engineering/campaign-2026-10/00-README.md`: task list and **waves**
2. `docs/engineering/campaign-2026-10/01-WORKER-RULES.md`
3. `docs/engineering/campaign-2026-10/02-REVIEWER-CHECKLIST.md`
4. `docs/engineering/campaign-2026-10/03-FACTS.md`
5. `docs/engineering/campaign-2026-10/04-REPORT-TEMPLATE.md`

Read each task file only when you dispatch that task.

## Environment facts

- Interpreter for tests: `C:/Users/chris/Documents/Ghost/ghost_agent/.venv/Scripts/python.exe`
  (called `$PY` in the docs). Always use `"$PY" -m pytest ...` from the
  directory being tested.
- `delegation.worktree_isolation` is ON. Every subagent gets its own git
  worktree under `.worktrees/subagent-<id>`, on its own branch created from
  `master`'s **current HEAD at spawn time**. The `delegate_task` result has a
  `worktree` field: `path`, `branch`, `commits`, `dirty`.
- Up to 10 subagents run concurrently. A `delegate_task` call with
  `tasks=[...]` runs them in parallel and returns when all are done.
- Approvals are unattended. A command the safety reviewer denies is **not**
  retried with variations. Record it as a blocker.
- Forbidden for everyone: `git push`, `git reset --hard`, `git clean`,
  `git rebase`, `git branch -D`, editing or reverting `AGENTS.md` (the owner's
  uncommitted edit; leave it dirty), touching `.ghost/`, running state-changing
  `ghost` commands in the main checkout, and installing packages.

## Output-budget rule (learned on run 1)

Run 1 died because one `delegate_task` call carried 7 long reviewer prompts,
and the model ran out of output tokens while writing it. So:

- Put **at most 3 tasks** in one `delegate_task` call. Send more calls for
  larger waves.
- Keep each `goal` to the template text only. Put long material (reports,
  finding lists) in `context`, and cap it at about 40 lines. Summarise rather
  than paste.
- Keep your own replies short. Write detail to PROGRESS.md in small edits.

## Step 0: setup (once)

1. `git status --short` must show only `M AGENTS.md`,
   `?? docs/engineering/campaign-2026-10/` and possibly `M .gitignore` (Hermes
   adds `.worktrees/`). Anything else: block the card with the details.
2. Commit the campaign docs, so subagent worktrees contain them:
   ```bash
   git add docs/engineering/campaign-2026-10 .gitignore
   git commit -m "docs: campaign 2026-10 task packages and orchestration brief"
   ```
   Never `git add -A` in the main checkout: it would sweep in `AGENTS.md`.
3. Create `docs/engineering/campaign-2026-10/PROGRESS.md` with a table:
   `| Task | Wave | Status | Candidate branch | Reviews | Merge commit | Notes |`,
   one row per task (T01–T19), all `pending`. **Only you edit this file.**
   Do not edit `docs/engineering/STATE.md`; T05 and T19 own it. Commit it:
   `git add docs/engineering/campaign-2026-10/PROGRESS.md && git commit -m "campaign: progress log"`.
4. Baseline: in the main checkout run
   `"$PY" -m pytest -q -p no:cacheprovider --deselect tests/test_overnight.py::TestRun::test_max_cycles_stops`.
   Record the final line in PROGRESS.md notes (expected `995 passed, 1 deselected`).

## Per-wave procedure

Do waves 1 → 8 in order (`00-README.md`, "Dispatch waves"). **Never start a
wave until every task of the previous wave is merged** (or explicitly blocked
with no later task depending on it).

### A. Implement (parallel)

One `delegate_task` call, with one entry in `tasks` per task in the wave. Each
entry:

- `goal`:
  ```text
  You are a coding worker on the Ghost repository. Your current directory is your own git worktree.
  Read these files completely before anything else, in order:
    1. docs/engineering/campaign-2026-10/01-WORKER-RULES.md
    2. docs/engineering/campaign-2026-10/03-FACTS.md
    3. docs/engineering/campaign-2026-10/tasks/<TASK FILE>
  Do only task <Txx>. Follow every step in order. Do not make design decisions: if the task does not say what to do, stop and report the question.
  Use PY="C:/Users/chris/Documents/Ghost/ghost_agent/.venv/Scripts/python.exe" and always run "$PY" -m ... from your worktree root.
  When verified, commit once on your branch: git add -A && git commit -m "candidate(<Txx>): <title>"
  Finish with a report in the exact format of docs/engineering/campaign-2026-10/04-REPORT-TEMPLATE.md, including your branch name and commit hash.
  ```
- `context`: the task id, title, wave, and the names of any earlier merged
  tasks it builds on, with their merge commits.

### B. Check each result before review

For each returned task, read the report and the `worktree` field. Reject
immediately (go to D, Repair) if:

- `commits` is 0, or `dirty` is true;
- the report is missing sections, says STATUS `blocked` or `partial`, or
  claims results without command output;
- `git -C <worktree path> diff --stat master...HEAD` touches files outside the
  task's file list (plus the named new tests). Run this yourself.

If the worker reported a genuine **question** (the task didn't cover a
situation), decide it yourself **only if** the answer follows from the task
file, AGENTS.md or the facts. Write the decision in PROGRESS.md and send it
through Repair. If it needs an owner decision, mark the task `blocked` in
PROGRESS.md and continue with the tasks that don't depend on it.

### C. Review (parallel, independent)

For **every** task, dispatch reviewers in one `delegate_task` call. Use two
reviewers (A and B) for T06, T08, T11, T12 and T18, and one reviewer for all
other tasks. Reviewers of different tasks can share one call.

- `goal`:
  ```text
  You are a READ-ONLY reviewer for Ghost campaign task <Txx>. Do not edit, format, commit, merge or push anything.
  Read: docs/engineering/campaign-2026-10/02-REVIEWER-CHECKLIST.md, 01-WORKER-RULES.md, 03-FACTS.md, and tasks/<TASK FILE>.
  The candidate is branch <branch> in worktree <absolute worktree path>. Inspect it with:
    git -C "<path>" log --oneline master..HEAD
    git -C "<path>" diff master...HEAD
  Run the task's Verification commands with the current directory set to "<path>", using
    PY="C:/Users/chris/Documents/Ghost/ghost_agent/.venv/Scripts/python.exe" and "$PY" -m ...
  Focus: <A: correctness, security, tests, failure/negative cases | B: architecture, scope, production wiring, invariants in 03-FACTS section 7>.
  Output exactly the format at the end of 02-REVIEWER-CHECKLIST.md.
  ```
- `context`: the worker's full report.
- Do **not** give reviewer B reviewer A's findings.

A task passes review only if every reviewer says `VERDICT: PASS` with no P0,
P1 or P2 findings. Write P3 findings into PROGRESS.md notes, with your
disposition.

### D. Repair (when B or C fails)

Dispatch one repair worker per failed task (in parallel). `goal`: the same as
the implement goal, plus:

```text
This is a REPAIR of an existing candidate. First run, from your worktree root:
  git merge --ff-only <candidate branch>
Then fix ONLY these findings (and nothing else):
<numbered list of findings, with file:line and evidence>
Re-run the task's Verification commands, commit once ("repair(<Txx>): <summary>"), and report in the template format.
```

The repaired branch is the new candidate. Run C again with **fresh**
reviewers. Allow at most **3 repair rounds** per task. After that, mark it
`blocked` in PROGRESS.md with the outstanding findings, and continue with
independent work.

### E. Integrate (sequential, in the main checkout)

For each passed task, in task-id order within the wave:

1. `git status --short`: only the allowed owner/campaign files may be dirty.
2. `git merge --no-ff <candidate branch> -m "<Txx>: <task title>"`.
   If there is a merge conflict, run `git merge --abort`. Then dispatch a
   repair worker whose goal is to bring the candidate up to date: after
   `git merge --ff-only <candidate branch>`, run
   `git merge master`, resolve the conflicts conservatively (keep both sides'
   behaviour), re-run Verification, and commit. Then re-review it (C) and
   retry the merge.
3. In the main checkout, run that task's **Verification** commands, then the
   full suite: `"$PY" -m pytest -q -p no:cacheprovider`. Add
   `--deselect tests/test_overnight.py::TestRun::test_max_cycles_stops` until
   T02 is merged; after T02, run with **no** deselect. Abort a full-suite run
   that has printed nothing new for 10 minutes, and treat it as a failure.
4. If anything fails after the merge, run `git revert --no-edit -m 1 HEAD`,
   record the failing output in PROGRESS.md, and send the task back to D with
   that output as the finding.
5. On success, update the task's PROGRESS.md row (status `merged`, the merge
   commit hash, the review verdicts and the full-suite final line). Commit it:
   `git add docs/engineering/campaign-2026-10/PROGRESS.md && git commit -m "campaign: <Txx> merged"`.
6. Call `kanban_heartbeat` with a one-line status, for example
   `"wave 2: T07 merged, T08 in review"`.

Never merge a candidate that does not match what was reviewed. If you had to
change anything, it goes back through review.

## Special cases

- **T01:** the owner's patch is at
  `C:/Users/chris/Documents/Ghost/owner-cli-uncommitted-2026-10-06.patch`. The
  main checkout's `cli.py` is already at HEAD.
- **T04:** the worker is allowed to `git cherry-pick --no-commit 7c1a1f0`
  inside its worktree.
- **T05 and T19** edit `docs/engineering/STATE.md`. You never edit it.
- **Owner live steps** in T11, T12 and T18 are **not** yours. List them in the
  final report; do not run them.
- If a task's code no longer matches its file (an earlier task changed the
  quoted lines), tell the worker in `context` which merged task changed it, and
  that it must locate the code by function name. If the change in meaning is
  significant, block it for the owner instead.

## Stopping rules

- **Done:** every task is `merged` (or `blocked` with a recorded reason), and
  the final full suite on master passes with no deselect. Then call
  `kanban_complete`. Its summary must include: the merged tasks with commits;
  the blocked tasks with reasons; the final full-suite line; the owner live
  steps still to run; and any denied commands.
- **Block the card** (`kanban_block`) only if setup fails, master's full suite
  breaks and a revert doesn't fix it, or more than half of the remaining tasks
  are blocked.
- Call `kanban_heartbeat` at least every 30 minutes during long waits.
- Keep your own context small: store detail in PROGRESS.md, not in your
  messages. Never paste whole diffs or logs into a reply.
