# T05: Correct false claims in `docs/engineering/STATE.md`

Size: XS · Wave 1 · Files: `docs/engineering/STATE.md` only

## Objective

STATE.md contains claims the 2026-10-06 audit disproved. Correct them without
deleting history. Add a dated correction section at the top, and mark the wrong
lines inline.

## Steps

1. Directly under the `# Ghost Engineering State` heading and the
   `## Repository` block, insert a new section with **this exact content**
   (copy it as written):

   ```markdown
   ## 2026-10-06 audit corrections (supersede claims below)

   Verified at a602677 by direct code reading and test runs:

   - **Full-suite "stall" was a test bug, not the environment.**
     `tests/test_overnight.py::TestRun::test_max_cycles_stops` mocks
     `run_cycle`, the only code that increments `_cycles`, so `run()` never
     stops. With that test deselected: 995 passed in ~2 min. Fix: campaign T02.
   - **Working-tree `ghost_agent/cli.py` did not import** (IndentationError at
     line 1786, from the uncommitted learn-command edit). Fix: campaign T01.
   - **"All AI-OS subsystems are now wired to production CLI entry points" is
     false.** `capability_manager`, `notifications`, `memory/manager`,
     `memory/long_term`, `resource_manager`, and `proactive.LearningTracker`,
     `ResourceGate` and `ProactiveLoop` have no production caller (tests only).
     `capability_manager` writes `.ghost/capabilities.json`, the gap registry's
     file. `Nexus` has no registered subsystems in production.
   - **`ghost daemon` has never run** (no `.ghost/daemon_status.json`). It
     re-implements the `OvernightRunner` loop instead of composing it, and it
     never uses LearningEngine or SelfImprover.
   - **SelfImprover does not produce changes**: it fills a fixed template and
     runs pytest on the live repo.
   - **Outcome reliability:** 2 of 18 real jobs passed, all on toy goals. Main
     cause: every job copies the whole Ghost repo into its sandbox, so the
     suite gate runs Ghost's own ~1,000 tests. Proactive jobs also get 4
     iterations and 120 s.
   - **OJ08 repair commit `7c1a1f0` was never merged** into master.

   Remediation plan: `docs/engineering/campaign-2026-10/00-README.md`.
   ```

2. In the existing text, append ` **(disproved 2026-10-06, see audit corrections)**`
   to the end of each of these lines. Search for each quoted fragment:
   - `All AI-OS subsystems are now wired to production CLI entry points.`
   - `Confirms full-suite runs stall in this Hermes terminal environment`
   - `full-suite single runs stall in this session's terminal environment`
3. Change the `## Repository` block's `Working tree:` line to:
   `- Working tree: DIRTY — AGENTS.md (owner, intentional) and ghost_agent/cli.py (owner's uncommitted learn commands; broken until T01)`

## Must NOT

- Delete any existing line.
- Edit any other file.
- Add claims that the text above does not contain.

## Verification

```bash
git diff --stat
git diff docs/engineering/STATE.md
```

Only STATE.md changed. The diff shows one inserted section, three appended
markers and one replaced `Working tree:` line.

## Acceptance criteria

- AC1: The corrections section is present verbatim.
- AC2: The three disproved lines are marked.
- AC3: No existing content was removed.
