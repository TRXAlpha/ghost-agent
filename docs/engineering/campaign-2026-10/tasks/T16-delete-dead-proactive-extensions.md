# T16: Delete dead proactive extensions and `resource_manager.py`

Size: S · Wave 4 (after T03, T06, T07, T15) · Files: `ghost_agent/proactive.py`,
delete `ghost_agent/resource_manager.py`, edit `tests/test_ai_os_subsystems.py`

## Why (decided)

At the bottom of `ghost_agent/proactive.py`, after the
`# AI-OS extensions: LearningTracker + ResourceGate + ProactiveOS` banner
(~line 608 at a602677), there are three classes: `LearningTracker`,
`ResourceGate` and `ProactiveLoop`.

- **None has a production caller.** The only users are
  `tests/test_ai_os_subsystems.py` (`TestLearningTracker`, `TestResourceGate`).
- `ProactiveLoop` is a third proactive loop alongside `ProactiveEngine.watch`
  and `OvernightRunner`. AGENTS.md forbids competing loops.
- `ResourceGate` is the only user of `resource_manager.ResourceManager`, which
  is otherwise unused. Real admission today is the command budget plus idle and
  emergency-stop gating.

## Steps

1. Prove there are no callers:
   ```bash
   git grep -n "LearningTracker\|ResourceGate\|ProactiveLoop\|resource_manager\|ResourceManager" -- ghost_agent tests
   ```
   Expected matches: only in `proactive.py` (the definitions),
   `resource_manager.py` itself, `nexus.py` (a docstring mention, line ~11) and
   `tests/test_ai_os_subsystems.py`. If anything else matches, stop and report.
2. In `ghost_agent/proactive.py`, delete everything from the
   `# ====...` banner line that precedes
   `# AI-OS extensions: LearningTracker + ResourceGate + ProactiveOS`
   to the end of the file. Check that the file still ends with the end of
   `ProactiveEngine` (the `_reconcile_safe` method) followed by a single newline.
3. After deleting, check whether any import at the top of `proactive.py` became
   unused (for example `CapabilityImprover`). For each import, run
   `git grep -n "<name>" ghost_agent/proactive.py`. Remove an import **only if**
   its name no longer appears anywhere else in the file, and list each removal.
   (`from .autonomy_improver import CapabilityImprover` may be used elsewhere in
   the file. Check; do not assume.)
4. Delete `ghost_agent/resource_manager.py`.
5. Do **not** edit `nexus.py` (T17 deletes it).
6. In `tests/test_ai_os_subsystems.py`, remove the `ResourceManager` import and
   the classes `TestResourceManager`, `TestLearningTracker` and
   `TestResourceGate`. Remove any import of `LearningTracker`, `ResourceGate`
   or `ProactiveLoop` from `ghost_agent.proactive` inside those classes or at
   the top.

## Must NOT

- Change `ProactiveEngine`, `OpportunityStore`, `queue_sandbox_outcome`,
  `idle_seconds` or anything above the banner.
- Delete `.ghost/proactive_learning.json` or `.ghost/resource_state.json` on
  disk, if present.

## Verification

```bash
"$PY" -c "import ghost_agent.cli, ghost_agent.proactive"
"$PY" -m pytest -q -p no:cacheprovider tests/test_ai_os_subsystems.py tests/test_proactive.py tests/test_proactive_feedback.py tests/test_overnight.py
"$PY" -m pytest -q -p no:cacheprovider
```

## Acceptance criteria

- AC1: The three classes and `resource_manager.py` are gone, with no remaining
  references outside `nexus.py`'s docstring.
- AC2: Only the three named test classes were removed.
- AC3: Every removed import is listed with grep evidence that it was unused.
- AC4: The full suite passes.
