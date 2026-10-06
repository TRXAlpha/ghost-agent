# T17: Remove the `nexus` kernel

Size: S · Wave 6 (after T11, T12, T16) · Files: delete `ghost_agent/nexus.py`;
edit `ghost_agent/learning_engine.py`, `ghost_agent/context_synthesizer.py`,
`ghost_agent/cli.py` and `tests/test_ai_os_subsystems.py`

## Why (decided)

- `Nexus` is described as the "AI-OS kernel", but **no production code ever
  registers a subsystem**. `ghost nexus subsystems` always lists 0.
- `ghost nexus health` just calls `Doctor(root).check()`, so it duplicates
  `ghost doctor`.
- `ghost nexus register` prints "Subsystems auto-register via the Nexus
  Subsystem protocol at startup.", which is **false**.
- `ghost nexus state` reads `.ghost/nexus_state.json`, which nothing in
  production writes.
- A kernel that routes calls between subsystems would be a competing control
  plane. Ghost's control plane is `control_plane.GhostSDK` plus the CLI.

The only production code that imports from `nexus` is the `health_check()`
method of `LearningEngine` and of `ContextSynthesizer`, which exist only "for
NEXUS integration".

## Steps

1. Prove the callers:
   ```bash
   git grep -n "nexus\|Nexus\|health_check" -- ghost_agent tests
   ```
   Expected production matches: `cli.py` (parser lines ~1831–1840, the dispatch
   entry `"nexus": lambda: _cmd_nexus(args)`, and the `_cmd_nexus` function);
   `learning_engine.py` and `context_synthesizer.py` (their `health_check`
   methods); and `nexus.py` itself. Other `health_check` matches (Telegram,
   Huawei, kernel manifests) are unrelated and must stay. Report anything
   unexpected and stop.
2. `learning_engine.py`: delete only the `health_check` method (and the
   `# -- Health (for NEXUS integration) --` comment line above it, if that
   comment then heads only `initialize`/`shutdown`, change it to `# -- Lifecycle --`).
   Keep `initialize` and `shutdown`.
3. `context_synthesizer.py`: the same as step 2.
4. Check that nothing calls `.health_check()` on a `LearningEngine` or
   `ContextSynthesizer` (step 1's grep plus a look at `_cmd_learn` and
   `_cmd_context` in `cli.py`).
5. `cli.py`:
   - delete the `nexus` parser block (from `nexus_parser = sub.add_parser("nexus", ...)`
     through the `unregister` sub-parser's last `add_argument`);
   - delete the `"nexus": lambda: _cmd_nexus(args),` dispatch entry;
   - delete the whole `_cmd_nexus` function, including its section-banner
     comment, if it has its own banner;
   - run `"$PY" -m py_compile ghost_agent/cli.py`.
6. Delete `ghost_agent/nexus.py`.
7. `tests/test_ai_os_subsystems.py`: remove
   `from ghost_agent.nexus import Nexus, NexusConfig` and the `class TestNexus:`
   block. Update the module docstring.
8. If any test elsewhere calls `health_check()` on `LearningEngine` or
   `ContextSynthesizer`, stop and report. (At a602677 none do.)

## Must NOT

- Change `Doctor`, `ghost doctor` or any other command.
- Remove `initialize`/`shutdown` from the two engines.

## Verification

```bash
"$PY" -m py_compile ghost_agent/cli.py ghost_agent/learning_engine.py ghost_agent/context_synthesizer.py
"$PY" -c "import ghost_agent.cli"
"$PY" -m ghost_agent.cli learn --help
"$PY" -m ghost_agent.cli context --help
"$PY" -m pytest -q -p no:cacheprovider tests/test_ai_os_subsystems.py
"$PY" -m pytest -q -p no:cacheprovider
```

## Acceptance criteria

- AC1: `nexus.py` and `ghost nexus` are gone, and nothing references them.
- AC2: `learn` and `context` commands still work.
- AC3: Only `TestNexus` was removed from the tests.
- AC4: The full suite passes.
