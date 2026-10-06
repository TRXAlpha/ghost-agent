# T15: Delete `memory/manager.py` and `memory/long_term.py` (competing memory authority)

Size: XS · Wave 3 (after T14; same test file) · Files: delete
`ghost_agent/memory/manager.py` and `ghost_agent/memory/long_term.py`; edit
`tests/test_ai_os_subsystems.py`

## Why this is a deletion (decided)

- The real memory authority is `context_manager.ValidatedMemoryStore`, which has
  validated records, retract/purge and the health-data privacy guard (OJ05/S02).
  Retrieval memory for coding is `memory/store.MemoryStore`.
- `memory/long_term.py` (facts, preferences, goals) and `memory/manager.py`
  create a **second** personal-memory store under `.ghost/memory/`, **without**
  the retract/purge or privacy guard. Wiring them in would let deleted or
  health data persist. AGENTS.md forbids competing memory authorities.
- Neither has a production caller. `memory/__init__.py` exports only
  `MemoryStore`.

## Steps

Follow the "Shared deletion procedure" in
`tasks/T13-delete-capability-manager.md`, with these substitutions:

- grep pattern: `"memory.manager\|memory.long_term\|memory_for\|MemoryManager\|LongTermMemory\|EpisodicMemory\|PersonalGoal"`.
- modules to delete: `ghost_agent/memory/manager.py` and
  `ghost_agent/memory/long_term.py`. Do **not** touch `memory/store.py` or
  `memory/__init__.py`. Check that `__init__.py` does not import the deleted
  modules (at a602677 it does not).
- in the test file: remove both imports and the `class TestLongTermMemory:` and
  `class TestMemoryManager:` blocks.

## Must NOT

- Touch `context_manager.py`, `memory/store.py` or `.ghost/memory/` on disk.

## Verification

```bash
"$PY" -c "import ghost_agent.cli"
"$PY" -m pytest -q -p no:cacheprovider tests/test_ai_os_subsystems.py tests/test_context_manager.py tests/test_health_privacy.py tests/test_memory_dependencies.py
"$PY" -m pytest -q -p no:cacheprovider --deselect tests/test_overnight.py::TestRun::test_max_cycles_stops
```

## Acceptance criteria

- AC1: Both modules are gone, and nothing imports them.
- AC2: Only the two test classes were removed.
- AC3: The full suite passes.
