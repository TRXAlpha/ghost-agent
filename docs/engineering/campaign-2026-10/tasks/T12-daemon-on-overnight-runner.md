# T12: Rebuild `ghost daemon` on `OvernightRunner`

Size: M · Wave 4 (after T01) · Important: two reviewers
Files: new `ghost_agent/daemon.py`, `ghost_agent/cli.py`, new
`tests/test_daemon.py`

## Objective

`_cmd_daemon` in `cli.py` (~line 3344) re-implements its own loop (proactive
tick → worker → trace-learning → capability improver) instead of using
`OvernightRunner`, the existing idle-aware loop. AGENTS.md forbids competing
loops. It also has these bugs:

- **Unknown idle state is treated as idle.** `_idle()` returns False when
  `idle_seconds()` is None, so the worker runs while the owner may be present.
  `OvernightRunner._user_active` correctly treats unknown as active.
- **The capability improver runs every cycle, even while the owner is active.**
  It can make model calls.
- **No `--model` flag**, so it always uses the configured default.
- **No quiet hours and no digest**, which OvernightRunner already provides.
- It imports `LearningEngine` without using it.
- It has never run and has no tests.

Rebuild it as a thin composition: each daemon cycle is **one**
`OvernightRunner.run_cycle()`, plus two Ghost learning hooks that run only when
the owner is away.

## Design (decided)

New module `ghost_agent/daemon.py`:

```python
class GhostDaemon:
    """Always-on composition of the existing loops; owns no scheduling of its own."""

    def __init__(self, root: Path, model: str, *, interval: float = 300.0,
                 idle_after: float = 900.0, trace_every: int = 10,
                 runner: Optional[OvernightRunner] = None,
                 trace_fn: Optional[Callable[[], dict]] = None,
                 improver_fn: Optional[Callable[[], dict]] = None,
                 sleep_fn: Callable[[float], None] = time.sleep) -> None: ...
    def run_cycle(self) -> dict: ...
    def run(self, *, max_cycles: Optional[int] = None) -> int: ...
```

- `runner` defaults to `OvernightRunner(root, model, interval=interval, idle_after=idle_after)`.
- `trace_fn` defaults to a function that calls
  `run_trace_learning(root, min_count=2)` and returns
  `{"scanned": ..., "signatures": ..., "actions": ...}`, exactly as the old
  `_trace_learn` did (copy that logic).
- `improver_fn` defaults to `lambda: CapabilityImprover(root, model).tick()`.
- Validation (raise `ValueError` with a clear message): `model` must be a
  non-blank string; `trace_every` must be an int ≥ 1; `interval > 0`.
- `run_cycle()`:
  1. `entry = self.runner.run_cycle()`
  2. `record = {"cycle": <daemon cycle count>, "ts": time.time(), "overnight": json.loads(entry.to_jsonl())}`
  3. `owner_away = not entry.user_active and not entry.emergency_stop`
  4. Trace learning: if `owner_away` and `cycle % trace_every == 0`, set
     `record["trace_learning"] = self.trace_fn()`. Wrap the call in
     `try/except Exception as exc`, and on failure set
     `{"status": "error", "error": str(exc)[:200]}`. Otherwise set
     `"not_due"` (cycle not a multiple) or `"deferred"` (owner not away).
  5. Capability improver: if `owner_away` **and** `not entry.quiet_hours`, set
     `record["capability"] = self.improver_fn()` (same try/except). Otherwise
     set `"deferred"`.
  6. Write `record` to `.ghost/daemon_status.json` with `atomic_json`.
  7. Return `record`.
- `run(max_cycles)`: loop while `not EmergencyStop(root).active()`. Stop when
  `max_cycles` is reached. The daemon keeps its **own** counter,
  `self._cycles`, incremented in `run_cycle` before step 2, so the loop never
  depends on a mocked runner (see T02 for what goes wrong otherwise). Call
  `self.sleep_fn(self.interval)` between cycles, but not after the last one.
  When finished, write `{"stopped": True, "total_cycles": n, "ts": time.time()}`
  to the status file and return `n`.
- Daemon cycle numbering is the daemon's own counter. The OvernightRunner's
  cycle number stays inside `record["overnight"]["cycle"]`.

## Steps

1. Read `ghost_agent/overnight.py` (all of it) and the current `_cmd_daemon`.
2. Create `ghost_agent/daemon.py` as designed. Import `CapabilityImprover` and
   `run_trace_learning` **inside** the default factory functions (lazy imports,
   as `OvernightRunner` does), to avoid import cycles.
3. In `cli.py`, in the daemon parser block (after T01, around line 1777), add to
   `daemon_run`:
   ```python
   daemon_run.add_argument("--model", default=None, help="Model for proactive ticks and capability assessment")
   ```
4. Replace the **body** of `_cmd_daemon` with:
   - `status` and `stop` branches, kept exactly as they are today;
   - the run branch:
     ```python
     model = _resolve_model(getattr(args, "model", None))
     if not model:
         print("A model is required: pass --model or configure one.")
         return
     from .daemon import GhostDaemon
     daemon = GhostDaemon(Path.cwd(), model, interval=args.interval,
                          idle_after=args.idle_after_seconds, trace_every=args.trace_every)
     cycles = daemon.run(max_cycles=args.max_cycles)
     print(f"AI-OS daemon stopped after {cycles} cycles.")
     ```
   Remove the old inner functions and loop, and the unused imports
   (`LearningEngine`, `ProactiveEngine`, `OutcomeWorker`, and so on) from
   `_cmd_daemon`.
5. Update the `daemon` parser help text to:
   `"Always-on loop: overnight proactive+worker cycle plus trace-learning and capability improvement while the owner is away"`.
6. Run `"$PY" -m py_compile ghost_agent/cli.py ghost_agent/daemon.py`.

## Tests to add: `tests/test_daemon.py`

Use a fake runner object with a `run_cycle()` method that returns
`OvernightCycleResult(cycle=..., ts="t", user_active=..., quiet_hours=..., emergency_stop=...)`
from a scripted list. Count the `trace_fn` and `improver_fn` calls. Pass
`sleep_fn=lambda s: None`.

1. `test_cycle_writes_status_file`: one cycle → `.ghost/daemon_status.json` has
   `"cycle": 1` and an `"overnight"` dict.
2. `test_owner_active_defers_learning_hooks`: user_active=True,
   trace_every=1 → no `trace_fn`/`improver_fn` calls; both fields are
   `"deferred"`.
3. `test_owner_away_runs_hooks`: user_active=False, quiet_hours=False,
   trace_every=1 → each called once.
4. `test_quiet_hours_defers_improver_only`: user_active=False,
   quiet_hours=True, trace_every=1 → trace called, improver not called.
5. `test_trace_every_cadence`: trace_every=3, 6 cycles, owner away → trace
   called exactly twice.
6. `test_hook_exception_recorded_not_raised`: trace_fn raises
   `RuntimeError("boom")` → record has `{"status": "error"}` containing
   `"boom"`, and the next cycle still runs.
7. `test_run_stops_at_max_cycles_with_counting_runner`: the fake runner does
   **not** track cycles. `run(max_cycles=3)` returns 3, and the runner was
   called 3 times. Add a hard guard in the fake (raise after 10 calls), so a
   regression fails instead of hanging.
8. `test_run_stops_on_emergency_stop`: engage `EmergencyStop(tmp_path)` before
   `run()` → returns 0, and the runner is not called. The status file has
   `"stopped": true`.
9. `test_unknown_idle_is_treated_as_active`: build a **real**
   `OvernightRunner(tmp_path, "m", proactive_tick=..., worker_fn=...)` with a
   proactive fake and a worker fake that both record calls. Monkeypatch
   `ghost_agent.proactive.idle_seconds` to return `None`. Run one daemon cycle.
   Assert that the worker fake and the improver were **not** called.
10. `test_invalid_arguments`: blank model, `trace_every=0` and `interval=0`
    raise `ValueError`.
11. `test_cli_daemon_requires_model`: monkeypatch `ghost_agent.cli._resolve_model`
    to return `""`, call `_cmd_daemon(argparse.Namespace(daemon_action="run", model=None, interval=1, idle_after_seconds=1, trace_every=1, max_cycles=1))`
    with `monkeypatch.chdir(tmp_path)`, and assert the "A model is required"
    message.

## Must NOT

- Change `overnight.py`.
- Add a second scheduler, queue or worker.
- Let any hook run while the owner is active or emergency stop is engaged.

## Verification

```bash
"$PY" -m py_compile ghost_agent/cli.py ghost_agent/daemon.py
"$PY" -m pytest -q -p no:cacheprovider tests/test_daemon.py tests/test_overnight.py tests/test_trace_learning.py tests/test_autonomy_improver.py
"$PY" -m pytest -q -p no:cacheprovider
"$PY" -m ghost_agent.cli daemon run --help
```

## Acceptance criteria

- AC1: The daemon has no loop logic of its own besides counting, sleeping and
  stop checks. Each cycle is exactly one `OvernightRunner.run_cycle()`.
- AC2: The learning hooks run only when the owner is away (and the improver
  also only outside quiet hours). Unknown idle counts as active.
- AC3: `--model` is supported, and a missing model is refused clearly.
- AC4: A status file is written every cycle, and on stop.
- AC5: The full suite passes.

## Owner live step (not the worker)

In the real project directory, with Ollama running:

```bash
ghost daemon run --model gemma4:26b --max-cycles 1 --interval 1
ghost daemon status
```
