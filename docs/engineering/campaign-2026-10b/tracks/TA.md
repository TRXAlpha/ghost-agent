# Track TA: durable state, events, recovery (R01, R12)

Audit: `audit/A1.md`. Branch `c2/TA`, worktree `C:/Users/chris/Documents/Ghost/ghost_c2_TA`.
Files this track owns: `ghost_agent/runtime.py` (the `Event`, `EventBus`,
`ScheduledJob` and `Scheduler` classes only), `ghost_agent/worker.py`, new
`ghost_agent/migrations.py`, new `ghost_agent/event_cursor.py`, `cli.py` (new
blocks only), and tests.
Do **not** edit `AttentionManager`/`Notification` in runtime.py (track TE
owns them).

---

## TA1: event envelope (review: 2)

**Goal:** events gain the identity and tracing fields R01 requires, while old
JSONL lines stay readable.

**Decided design** (in `runtime.py`):

- Add these fields to `@dataclass Event`, after `created_at` and all with
  defaults:
  `event_id: str = field(default_factory=lambda: uuid4().hex)`,
  `ingested_at: Optional[float] = None`, `correlation_id: str = ""`,
  `causation_id: str = ""`, `sensitivity: str = "normal"`,
  `schema_version: int = 2`.
- Add the module constant `EVENT_SENSITIVITIES = ("normal", "personal", "health", "secret")`.
- `EventBus.emit`: if `event.sensitivity` is not in `EVENT_SENSITIVITIES`,
  raise `ValueError("unknown event sensitivity")`. Set
  `event.ingested_at = time.time()` if it is None. Then write as today.
- `EventBus.recent`: build each Event from **known fields only**, so lines with
  extra or missing keys load. Old lines get `schema_version=1` when the key is
  absent; set this explicitly when loading, so the default of 2 is not used
  for old lines.
- `correlation_id` at emit sites: everywhere `worker.py` and `Scheduler` emit an
  event whose payload contains `job_id`, pass `correlation_id=<job_id>`. Find
  them with `git grep -n "Event(" ghost_agent/worker.py ghost_agent/runtime.py`.
  Do not change other modules' emit calls.

**Tests** (`tests/test_event_envelope.py`):

- A new event gets a 32-hex `event_id` and an `ingested_at` that is not None.
- A legacy line `{"kind":"x","payload":{},"source":"s","created_at":1}` loads
  with `schema_version == 1`.
- A line with an unknown extra key still loads.
- A bad sensitivity raises `ValueError`.
- A worker-emitted event for a claimed job has `correlation_id == job_id`
  (reuse the setup in `tests/test_worker*.py` or `tests/test_autonomy_jobs.py`).

**Non-goals:** no change to the event file location or format beyond the
added keys.

---

## TA2: event cursors and failed-event queue

**Goal:** consumers can resume reading events from a durable position, and
unreadable lines go to a bounded failed queue (R01).

**Decided design:** new module `ghost_agent/event_cursor.py`:

```python
class EventCursor:
    def __init__(self, project_root: Path, name: str) -> None
    def read_new(self, max_events: int = 100) -> list[tuple[int, Event]]
    def commit(self, offset: int) -> None
```

- `name` must match `^[a-z0-9_-]{1,40}$`, otherwise `ValueError`.
- The cursor file is `.ghost/events/cursors/<name>.json`, holding
  `{"offset": int, "updated_at": float}` and written with `atomic_json`.
- `read_new` opens `.ghost/events.jsonl` in binary mode, seeks to the stored
  offset (0 if none), reads complete lines (an incomplete trailing line is
  ignored, not consumed), and returns `(end_offset_after_line, Event)` pairs.
  Parse lines like `EventBus.recent` (TA1's tolerant loader).
- An unparseable line is appended as
  `{"offset": o, "line": <first 2000 chars>, "error": str, "at": time}` to
  `.ghost/events/failed.jsonl`, and still counts as consumed. Keep at most
  1,000 lines in `failed.jsonl` (when over, rewrite it with the last 1,000
  using `atomic_write`).
- If the stored offset is larger than the file size (the file was replaced),
  restart from 0 and emit nothing special.
- CLI: a new `events` command, added after the `doctor` parser block:
  `ghost events tail --cursor NAME [--max N] [--no-commit]` prints one JSON
  line per event and commits the last offset unless `--no-commit`.
  `ghost events failed` prints the failed queue.

**Tests** (`tests/test_event_cursor.py`): resume after commit returns only new
events; an incomplete trailing line isn't consumed; a garbage line goes to
failed.jsonl and is skipped; the failed queue is capped at 1,000; a bad
cursor name raises; offset larger than the file resets.

---

## TA3: job leases and generation (review: 2)

**Goal:** a claimed job carries a lease and a generation, so a stale worker
can't overwrite a newer attempt, and expired leases are recovered visibly
(R01).

**Decided design:**

- Add these fields to `ScheduledJob`:
  `lease_expires_at: Optional[float] = None`, `generation: int = 0`.
  Validate them in `Scheduler._load` like the other fields: `generation` is an
  int ≥ 0; `lease_expires_at` is None or a float.
- When a claim sets the job to `running`: increment `generation` and set
  `lease_expires_at = now + lease_seconds`, where `lease_seconds` is
  `job.budget.get("lease_seconds", 21600)` (6 h) and must be an int from 60 to
  604800, otherwise use the default.
- `Scheduler.mark(...)` gets a new optional keyword `generation: int | None = None`.
  When it is given and differs from the stored generation, raise
  `ValueError("stale generation")` and change nothing. Existing callers that
  don't pass it behave as before. `worker.py` must pass the generation it
  claimed.
- When a job leaves `running`, clear `lease_expires_at` to None (same places
  `claimed_at` is cleared).
- Recovery: in `worker.py`, where the startup orphan flow pauses jobs left
  `running` by a dead worker, **also** pause any `running` job whose
  `lease_expires_at` is in the past, and emit `Event("job.lease_expired", {...job_id, generation})`
  using the same notification path the orphan flow uses. Do not auto-resume.

**Tests** (`tests/test_job_leases.py`): claim increments the generation and
sets the lease; mark with a stale generation raises and leaves the state
unchanged; mark without a generation still works (legacy); an expired lease
is paused at worker startup with the event; an invalid `lease_seconds` falls
back to 6 h; an old scheduler.json without the new keys loads.

---

## TA4: schema registry and `ghost migrate`

**Goal:** a durable-state version registry and a migration command with
backups. It fails closed when data is newer than the code (R01).

**Decided design:** new module `ghost_agent/migrations.py`:

- `CURRENT_VERSIONS = {"scheduler": 1, "notifications": 1, "events": 2, "opportunities": 1, "goals": 1}`.
- The registry file `.ghost/schema.json` holds `{"stores": {name: version}, "updated_at": float}`.
- `MIGRATIONS: dict[tuple[str, int], Callable[[Path], None]] = {}` is the
  (store, from_version) → step registry. It is empty for now; the framework
  only.
- `check(root) -> list[str]` returns problems: any store whose recorded
  version is greater than `CURRENT_VERSIONS` →
  `"<store> data version N is newer than this Ghost (M); refusing"`. A missing
  schema.json is not a problem (it means version 1 / current).
- `apply(root, dry_run: bool) -> list[str]`: for each store whose recorded
  version is lower than current, run the registered steps in order. **Before
  the first step** copy the store's file to
  `.ghost/backups/<UTC yyyymmddTHHMMSS>/<filename>`. Then write the new
  version into schema.json. A missing step for a gap raises `ValueError`.
  Dry run reports without writing.
- `stamp(root)` writes the current versions for stores whose files exist but
  aren't yet recorded.
- `ghost doctor` gains a `schema` entry: `ok` or the first problem (find where
  Doctor builds its result dict in runtime.py; add one key).
- CLI: `ghost migrate --check | --apply [--dry-run] | --stamp`, as a new
  parser block after `doctor`.

**Tests** (`tests/test_migrations.py`): a newer version is reported by check
and by doctor; apply with a registered fake step backs up the file first and
bumps the version; dry run writes nothing; a missing step raises; stamp
records the current versions.

---

## TA5: quiet-hours and digest-window flags (parallel-ok with TA4)

**Goal:** the owner can set quiet hours and the digest window for unattended
loops from the CLI (R12).

**Decided design:**

- Add `--quiet-hours START END` and `--digest-window START END` (two ints each,
  0–23, `nargs=2`, `type=int`) to the `overnight run` and `daemon run` parsers.
  Pass them as `quiet_hours=tuple(...)` and `digest_window=tuple(...)` to
  `OvernightRunner(...)`. For the daemon, add the same two keyword parameters
  to `GhostDaemon.__init__` and forward them to its default `OvernightRunner`.
- Validate with argparse choices or a check: values outside 0–23 → print
  `"hours must be 0-23"` and return.

**Tests:** parsing `daemon run --quiet-hours 22 7` builds a runner with
`quiet_hours == (22, 7)` (patch `GhostDaemon.run`/`OvernightRunner.run` to
capture it); out-of-range hours are refused.

**Files:** cli.py, daemon.py, tests.
