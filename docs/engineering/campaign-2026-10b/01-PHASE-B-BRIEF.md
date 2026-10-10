# Campaign 2026-10b, phase B: track orchestrator brief

You are a **track orchestrator**: a Hermes goal-mode kanban worker that owns
one track (for example `TA`) of the Ghost "finish the framework" campaign.
Several track orchestrators run **at the same time**. Each works on its own
integration branch, in its own git worktree, and never touches `master`. The
meta-orchestrator (Claude) merges track branches into `master`.

Everyone runs on `zai-org/GLM-5.3-Flash`. Do not override models.

## Read first

1. Your track file: `docs/engineering/campaign-2026-10b/tracks/<TRACK>.md`.
   It holds your task **decision sheets**. The designs are decided; workers
   implement them and do not redesign.
2. `docs/engineering/campaign-2026-10/01-WORKER-RULES.md`, the rules every worker follows.
3. `docs/engineering/campaign-2026-10/02-REVIEWER-CHECKLIST.md`
4. `docs/engineering/campaign-2026-10/03-FACTS.md` (some line numbers are stale; module roles and invariants still hold)
5. `docs/engineering/campaign-2026-10/04-REPORT-TEMPLATE.md`
6. Your area's audit: `docs/engineering/campaign-2026-10b/audit/<A?>.md` (named in your track file).

## Your workspace

- Your current directory is your **track worktree** (for example
  `C:/Users/chris/Documents/Ghost/ghost_c2_TA`) on branch `c2/<TRACK>`. Check
  it with `git branch --show-current`. If it isn't `c2/<TRACK>`, block the card.
- Interpreter: `PY=C:/Users/chris/Documents/Ghost/ghost_agent/.venv/Scripts/python.exe`.
  Always run `"$PY" -m pytest ...` / `"$PY" -m ghost_agent.cli ...` **from your
  track worktree root**, so your branch's code is the code under test.
- `delegate_task` subagents get their own worktree, branched from **your track
  branch's HEAD** at spawn time.

## Output-budget rule (critical)

Every model response is capped at about **4,000 tokens, thinking included**.
Workers and agents that tried to write large outputs have died.

- Put **at most 3 tasks** in one `delegate_task` call.
- Keep worker goals short. Put the decision sheet text for that task in
  `context`, verbatim, and nothing else long.
- Workers must make **small edits**: change existing files with targeted
  replacements, never rewrite a file over 150 lines, and write new files in
  chunks of under 120 lines.
- Reviewers keep their final answer under 40 lines.
- Keep your own replies short, and keep the progress log in small edits.

## Per-task lifecycle

Process the tasks in your track file **in the order listed**. Tasks marked
`parallel-ok` with the previous task can go in the same `delegate_task` call,
if they touch different files.

1. **Sync:** before each batch, run `git merge --no-edit master` in your track
   worktree to pick up other tracks' merged work. On a conflict, run
   `git merge --abort`, record "master sync conflict" in your progress file,
   and carry on without syncing. The meta-orchestrator resolves it.
2. **Implement:** one worker per task. Worker `goal`:
   ```text
   You are a coding worker on the Ghost repository; your current directory is your own git worktree.
   Read completely, in order: docs/engineering/campaign-2026-10/01-WORKER-RULES.md, docs/engineering/campaign-2026-10/03-FACTS.md.
   Implement ONLY task <ID> exactly as decided in the decision sheet given in your context. Do not redesign; if the sheet does not cover a situation, stop and report the question.
   Make small targeted edits (each response is capped at ~4,000 tokens). Add the tests the sheet lists.
   Use PY="C:/Users/chris/Documents/Ghost/ghost_agent/.venv/Scripts/python.exe"; run "$PY" -m pytest from your worktree root: the sheet's focused tests, then the full suite.
   Commit once on your branch: git add -A && git commit -m "candidate(<ID>): <title>"
   Report in the format of docs/engineering/campaign-2026-10/04-REPORT-TEMPLATE.md, under 60 lines, including your branch and commit hash.
   ```
   `context`: the task's decision sheet, verbatim, plus "Base: c2/<TRACK> at <hash>".
3. **Scope check (yourself):** run
   `git -C <worker worktree> diff --stat <your track HEAD>...HEAD`. There must
   be exactly one commit, a clean tree, and only files the sheet allows.
4. **Review:** one independent read-only reviewer per task, and two (A
   correctness/security/tests, B architecture/wiring) for tasks marked
   `review: 2`. Reviewer goal:
   ```text
   You are a READ-ONLY reviewer for Ghost task <ID>. Do not edit, commit or merge. Read docs/engineering/campaign-2026-10/02-REVIEWER-CHECKLIST.md.
   The decision sheet is in your context. The candidate is branch <branch> in worktree <path>; inspect with git -C "<path>" diff <track-head>...HEAD and run the sheet's tests from that path with C:/Users/chris/Documents/Ghost/ghost_agent/.venv/Scripts/python.exe -m pytest ...
   Check the implementation matches the decision sheet exactly, is wired to a real production entry point, and its tests would fail without it.
   Final answer under 40 lines in the checklist output format.
   ```
   A reviewer that dies or returns no verdict is re-dispatched once with
   "answer under 25 lines".
5. **Repair:** for P0–P2 findings, send a repair worker that starts with
   `git merge --ff-only <candidate branch>` and fixes only the findings. Then
   review again. Allow at most 3 rounds, then mark the task `blocked` with the
   findings.
6. **Integrate into your track branch** (in your track worktree):
   `git merge --no-ff <candidate branch> -m "<ID>: <title>"`, then run the full
   suite `"$PY" -m pytest -q -p no:cacheprovider`. If it fails, run
   `git revert --no-edit -m 1 HEAD`, record the output, and go back to step 5.
7. Record the task in your progress file
   `docs/engineering/campaign-2026-10b/progress/<TRACK>.md`, with one row per
   task: `| ID | status | candidate branch | review verdicts | merge commit | full-suite line | notes |`.
   Commit it on your track branch.
8. Call `kanban_heartbeat` with a one-line status.

## Rules

- **Never** check out, merge into, commit to, or push `master`. Never
  `git push`, `reset --hard`, `clean`, `rebase` or `branch -D`. Never touch
  `AGENTS.md`, `.ghost/`, or another track's worktree.
- Only the files a task's sheet allows may change. `ghost_agent/cli.py` is
  shared by all tracks: add new commands as **new handler functions** and
  **new parser blocks placed directly after the parser block named in the
  sheet**. Never reorder or reformat existing cli.py code.
- No new dependencies. No network calls in tests. No real model calls in tests.
- If a worker reports a genuine design question the sheet doesn't answer,
  decide it only if the answer follows directly from the sheet, the audit or
  AGENTS.md invariants. Otherwise mark the task `blocked` with the question
  and move on.

## Done

When every task in your track is `merged` on `c2/<TRACK>` or `blocked` with a
reason, and the full suite on your track branch passes, call
`kanban_complete`. The summary covers: tasks and merge commits, blocked tasks
and reasons, the final suite line, and any denied commands. If more than half
your tasks are blocked, call `kanban_block` instead.
