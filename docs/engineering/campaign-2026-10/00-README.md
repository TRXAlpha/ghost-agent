# Campaign 2026-10: Make Ghost actually do what its docs say

Written 2026-10-06 from a direct audit of commit `a602677` (master).
Audience: the owner and the cheap/small coding agents doing the work.

## Why this campaign exists

An audit found:

1. **Ghost almost never completes a job.** In `.ghost/outcomes/`, 2 of 18 real
   jobs passed, and every goal was a toy ("write add(a, b)"). The main cause is
   structural, not the model: every job copies the **entire Ghost repository**
   into its sandbox, so the "suite" gate runs Ghost's own ~1,000 tests
   (including one that hangs forever). Proactive jobs also get only 4
   iterations and 120 command-seconds. They could not succeed.
2. **The working tree's `ghost_agent/cli.py` fails to import**
   (`IndentationError` at line 1786), so every `ghost` command is broken.
3. **The "full-suite stall" in STATE.md is a test bug, not the environment.**
   `tests/test_overnight.py::TestRun::test_max_cycles_stops` loops forever.
4. **Several "AI-OS" modules are test-only scaffolding with no production
   caller**: `capability_manager`, `notifications`, `memory/manager`,
   `memory/long_term`, `resource_manager`, and the `LearningTracker`,
   `ResourceGate` and `ProactiveLoop` classes in `proactive.py`.
   `capability_manager` even writes to `.ghost/capabilities.json`, the same
   file the real gap registry uses.
5. **`nexus` is a kernel with nothing registered**, and `ghost nexus health`
   is just `ghost doctor`.
6. **`SelfImprover` never produces a change.** It fills a fixed sentence
   template and runs pytest on the live repo.
7. **`ghost daemon` duplicates `OvernightRunner`'s loop** (a competing loop,
   which AGENTS.md forbids), has never been run, and treats unknown idle state
   as idle.
8. **The OJ08 review-repair commit `7c1a1f0` was never merged into master.**

Every task below was decided by the root reviewer. **Workers do not make
design decisions.** If a task seems to need one, the worker stops and reports.

## Files in this folder

| File | Purpose |
|---|---|
| `00-README.md` | This file: overview, order, how to dispatch |
| `01-WORKER-RULES.md` | Rules every worker follows. Always give it to the worker. |
| `02-REVIEWER-CHECKLIST.md` | What a reviewer checks before a task is accepted |
| `03-FACTS.md` | Verified facts: interpreters, commands, traps, file map |
| `04-REPORT-TEMPLATE.md` | Exact format of the worker's final report |
| `tasks/Txx-*.md` | One file per task, self-contained |

## Task list

| ID | Title | Size | Depends on |
|---|---|---|---|
| T01 | Fix broken `cli.py` (indentation + `learn attempts` loop) | XS | none |
| T02 | Fix the infinite-loop overnight test; add hang diagnostics | XS | none |
| T03 | Remove duplicated `OpportunityStore` methods | XS | none |
| T04 | Integrate the unmerged OJ08 repair commit `7c1a1f0` | S | none |
| T05 | Correct false claims in `STATE.md` | XS | none |
| T06 | Empty-workspace mode for greenfield jobs | M | T08, T09 (file order only) |
| T07 | Realistic budgets for proactive/queued jobs | S | T03 (file order only) |
| T08 | Count only model turns against the iteration limit | S | T09 (file order only) |
| T09 | Reject placeholder file content from the model | S | none |
| T10 | Record why a job hit the iteration limit | S | T06 |
| T11 | `ghost bench`: fixed benchmark of non-toy goals | M | T06, T07, T08, T09 |
| T12 | Rebuild `ghost daemon` on `OvernightRunner` | M | T01 |
| T13 | Delete `capability_manager.py` | XS | none |
| T14 | Delete `notifications.py` | XS | T13 (file order only) |
| T15 | Delete `memory/manager.py` and `memory/long_term.py` | XS | T14 (file order only) |
| T16 | Delete dead proactive extensions and `resource_manager.py` | S | T03, T07, T06, T15 (file order only) |
| T17 | Remove the `nexus` kernel | S | T16, T12, T11 (file order only) |
| T18 | Make `SelfImprover` produce real sandboxed candidates | M | T06, T07, T16, T17 |
| T19 | Update README/STATE with live-vs-placeholder truth | S | everything else |

"File order only" means the tasks share a file, so they must not run at the
same time. It is not a logical dependency.

## Dispatch waves (one writer per file at a time)

Run every task in a wave in parallel, each in its own git worktree. Integrate
the whole wave into master before starting the next.

| Wave | Tasks | Files touched (owner per task) |
|---|---|---|
| 1 | T01, T02, T03, T04, T05, T09, T13 | T01 `cli.py` · T02 `tests/test_overnight.py`, `pyproject.toml` · T03 `proactive.py` · T04 `models/chat.py`, `tests/test_bridge_retry.py` · T05 `STATE.md` · T09 `orchestrator.py` · T13 `capability_manager.py`, `tests/test_ai_os_subsystems.py` |
| 2 | T07, T08, T14 | T07 `proactive.py`, `cli.py` · T08 `orchestrator.py` · T14 `notifications.py`, `tests/test_ai_os_subsystems.py` |
| 3 | T06, T15 | T06 `orchestrator.py`, `autonomy.py`, `proactive.py`, `cli.py`, `autonomy_improver.py` · T15 `memory/*`, `tests/test_ai_os_subsystems.py` |
| 4 | T10, T12, T16 | T10 `orchestrator.py` · T12 new `daemon.py`, `cli.py` · T16 `proactive.py`, `resource_manager.py`, `tests/test_ai_os_subsystems.py` |
| 5 | T11 | new `benchmark.py`, `cli.py` |
| 6 | T17 | `nexus.py`, `learning_engine.py`, `context_synthesizer.py`, `cli.py`, `tests/test_ai_os_subsystems.py` |
| 7 | T18 | `self_improver.py`, `proactive.py`, `cli.py`, `tests/test_ai_os_subsystems.py` |
| 8 | T19 | `README.md`, `docs/engineering/STATE.md` |

If you only have one worker, run the tasks in ID order inside each wave.

## How to dispatch a worker

1. Create a worktree from current master:
   ```bash
   git worktree add ../ghost_T06 -b campaign/T06 master
   ```
2. Give the worker exactly this prompt (replace `T06` and the file name):

   ```text
   You are a coding worker on the Ghost repository at <worktree path>.
   Read these files completely before doing anything else, in this order:
     1. docs/engineering/campaign-2026-10/01-WORKER-RULES.md
     2. docs/engineering/campaign-2026-10/03-FACTS.md
     3. docs/engineering/campaign-2026-10/tasks/T06-empty-workspace-mode.md
   Do only task T06. Follow every step in order. Do not make design decisions:
   if the task does not tell you what to do, stop and report the question.
   Finish with a report in the exact format of
   docs/engineering/campaign-2026-10/04-REPORT-TEMPLATE.md.
   ```
3. When the worker reports, give the diff and report to a reviewer with
   `02-REVIEWER-CHECKLIST.md` and the same task file. For important tasks
   (T06, T08, T12, T18), use two reviewers who do not see each other's reports.
4. Accept only when the reviewer says PASS **and** you have re-run the task's
   "Verification" commands yourself on the merged result.
5. Merge into master, delete the worktree, and record the commit in
   `docs/engineering/STATE.md` under "Campaign 2026-10".

## Definition of done for the whole campaign

- `python -m ghost_agent.cli --help` works, and the full suite finishes
  without hanging and without failures.
- No module in `ghost_agent/` is imported only by tests (except helpers that a
  task explicitly keeps).
- `ghost bench run --model <installed model>` produces a real pass-rate report
  on the fixed goal set. The owner runs this live; workers may not fake it.
- A proactive job on a greenfield goal runs in an empty sandbox, and its suite
  gate does not run Ghost's own tests.
- `ghost daemon run --max-cycles 1` runs one real cycle through
  `OvernightRunner` and writes `.ghost/daemon_status.json`.
- `ghost self-improve propose` queues real sandbox work, and `decide` reads
  real verification results.
- README and STATE.md describe only what exists.
