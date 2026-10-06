# T13: Delete `capability_manager.py` (duplicate capability lifecycle)

Size: XS · Wave 1 · Files: delete `ghost_agent/capability_manager.py`; edit
`tests/test_ai_os_subsystems.py`

## Why this is a deletion, not a wiring job (decided)

- `ghost_agent/capability_acquisition.py` is the real capability lifecycle (OJ10).
  It is used by `ghost capability` and by gap feedback.
- `capability_manager.py` re-implements the same R08 state machine, and has
  **no production caller** (only `tests/test_ai_os_subsystems.py` imports it).
- It writes `.ghost/capabilities.json`, the **same file** as the gap registry
  `autonomy.CapabilityRegistry`. If it were ever wired in, it would corrupt the
  gap queue.
- AGENTS.md forbids competing authority systems.

## Shared deletion procedure (T13–T17 use the same steps)

1. Prove there is no production caller:
   ```bash
   git grep -n "capability_manager\|CapabilityManager" -- ghost_agent
   ```
   Expected: matches only inside `ghost_agent/capability_manager.py` itself. If
   anything else matches, stop and report.
2. Check the docs for references:
   ```bash
   git grep -n "capability_manager\|CapabilityManager" -- "*.md"
   ```
   Do **not** edit the docs (T19 and T05 own them). List the matches in your
   report.
3. Delete the module:
   ```bash
   git rm --cached -q ghost_agent/capability_manager.py
   rm ghost_agent/capability_manager.py
   ```
   (Or simply delete the file; it must not exist afterwards.)
4. In `tests/test_ai_os_subsystems.py`:
   - remove the line `from ghost_agent.capability_manager import CapabilityManager`;
   - remove the whole `class TestCapabilityManager:` block (from its `class`
     line up to the next section comment or `class` line), and the
     `# ── CapabilityManager ──...` section comment above it, if there is one;
   - update the module docstring's list of subsystems to drop
     `capability_manager`.
5. Make sure nothing else in the test file used `CapabilityManager`.

## Tests to add

None. Removing dead code needs no new tests. The remaining tests must pass.

## Must NOT

- Touch `capability_acquisition.py`, `autonomy.py` or any `.ghost` data.
- Delete other test classes.

## Verification

```bash
"$PY" -c "import ghost_agent.cli"
"$PY" -m pytest -q -p no:cacheprovider tests/test_ai_os_subsystems.py tests/test_capability_acquisition.py tests/test_capability_wiring.py
"$PY" -m pytest -q -p no:cacheprovider --deselect tests/test_overnight.py::TestRun::test_max_cycles_stops
```

## Acceptance criteria

- AC1: `ghost_agent/capability_manager.py` no longer exists, and nothing
  imports it.
- AC2: Only the `TestCapabilityManager` tests were removed. Report the count
  before and after.
- AC3: The full suite passes.
