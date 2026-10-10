# Track TE: notifications, focus, owner controls, integration integrity (R09, R08, S01)

Audit: `audit/A7.md`. Branch `c2/TE`, worktree `C:/Users/chris/Documents/Ghost/ghost_c2_TE`.
Files this track owns: `ghost_agent/runtime.py` (`Notification` and
`AttentionManager` classes **only**), new `ghost_agent/focus.py`,
`ghost_agent/extensions.py`, `ghost_agent/capability_acquisition.py`,
`ghost_agent/control_plane.py` (TE4 one method), `cli.py` (new blocks only),
and tests.

---

## TE1: `ghost notifications` with quiet-hours delivery (review: 2)

**Goal:** the owner can actually read and acknowledge notifications, and
quiet hours hold non-urgent ones. Today no CLI lists them; they only appear
in the control-plane snapshot.

**Decided design:**

- Quiet hours source: the existing notification preferences in
  control_plane.py (`notification_preferences()`; read what keys it stores).
  If they hold quiet hours, use them; otherwise use none (deliver all).
- New CLI, as a parser block after `check-in`: `ghost notifications list`
  shows `AttentionManager.deliverable(quiet_hours=<prefs or None>)`;
  `ghost notifications list --all` shows all pending, with a `held: quiet hours`
  / `held: focus` / `held: cooldown` marker for undeliverable items;
  `ghost notifications ack <id>` calls the existing `acknowledge`.
- The control-plane snapshot key `notifications` switches to
  `deliverable(...)`, and adds `"held_notifications": <count>`.

**Tests:** list during quiet hours hides normal and shows urgent (patch the
hour); `--all` marks held items; ack works; snapshot counts held items.

---

## TE2: notification cooldown and batching

**Goal:** a burst of notifications from one source is batched instead of
spamming (R09).

**Decided design:** in `AttentionManager.deliverable`, after the quiet-hour
filter: per `source`, if more than 3 deliverable `normal` items exist, return
the newest one with `message` replaced by
`f"{n} updates from {source}; latest: {message}"` (one synthetic Notification
copy, **not persisted**; the originals stay pending and individually
ackable via `--all`). `important`/`urgent` items are never batched. The
constant is `BATCH_THRESHOLD = 3`.

**Tests:** 5 normal items from one source → 1 batched item; mixed priorities
keep important ones separate; the originals remain in `pending()`.

---

## TE3: focus mode (review: 2)

**Goal:** a user-controlled focus session with purpose, duration, configured
interventions and immediate override (R09).

**Decided design:** new module `ghost_agent/focus.py`:

```python
@dataclass
class FocusSession:
    purpose: str; started_at: float; ends_at: float
    interventions: list[str]  # allowed: "hold_notifications"
    stopped_at: Optional[float] = None
class FocusStore:
    def __init__(self, root: Path)
    def start(self, purpose: str, minutes: int, interventions: list[str]) -> FocusSession  # minutes 1..600; purpose 1..200 chars; refuse if one is active
    def active(self, now: float | None = None) -> Optional[FocusSession]
    def stop(self) -> Optional[FocusSession]   # immediate override; idempotent
```

- The file is `.ghost/focus.json`, written with `atomic_json`. A corrupt file
  means no active session, and `start` raises until the file is fixed (don't
  silently overwrite).
- The only intervention implemented is `hold_notifications`: while active,
  `AttentionManager.deliverable` holds `normal`/`silent` items (marker
  `held: focus`); `important`/`urgent` still go through. Do **not** implement
  app blocking or any OS action. Unknown interventions are refused.
- CLI: `ghost focus start --purpose TEXT --minutes N [--hold-notifications]`,
  `ghost focus status`, `ghost focus stop`.
- `ghost notifications list --all` shows `held: focus`.

**Tests:** start/status/stop; expiry by time (patch time); hold works and
urgent still passes; a double start is refused; a corrupt file is handled.

---

## TE4: "why did you do this?"

**Goal:** the owner can ask why any job or opportunity exists (R09 functional
"why").

**Decided design:** add the GhostSDK method `explain(item_id: str) -> dict`
that gathers, for a job id (outcome job) or an opportunity id:
the opportunity record (if linked: `acceptance_contract.opportunity_id` or a
direct id), the goal (`goal_id`), the intent (IntentStore, by goal id), the
last 20 events whose payload mentions the id, and the outcome status/result.
The CLI is `ghost why <id>`, printing a short human summary followed by
`--json` detail. An unknown id prints `"No job or opportunity with id <id>"`.

**Tests:** a proactive-queued job fixture explains with its opportunity
evidence; an unknown id; JSON mode.

---

## TE5: extension integrity at load (parallel-ok with TE4)

**Goal:** an enabled extension whose files changed after enablement is
detected and disabled (R08).

**Decided design:** extensions.py already hashes files at enable time
(audit A7: ~line 216). Add `ExtensionManager.verify_integrity() -> list[dict]`,
which recomputes the hashes for every `approved`/`enabled` extension. On a
mismatch it sets the status to `tampered`, records
`SecurityMonitor.record_event("extension.tampered", {...})`, and returns the
list. Call it from: `ghost mcp` / `ghost extension` list commands (find the
existing ones), and `ghost doctor` (add an `extensions` key: `ok` or
`N tampered`). A `tampered` extension can't be used until it is re-tested and
re-approved through the existing flow.

**Tests:** modifying a file after enable → tampered on verify and in doctor; an
unchanged extension stays ok.

---

## TE6: capability `degraded` and `operationally_verified` states

**Goal:** the capability lifecycle can express live health (R08).

**Decided design:** in capability_acquisition.py's transition table, add
`active → degraded`, `degraded → active`, `degraded → disabled`, and a boolean
`operationally_verified` on the record. It is set only through a new method
`mark_operationally_verified(cap_id, evidence: str)`, which requires status
`active` and non-empty evidence. The CLI goes under the existing
`ghost capability` group: `degrade <id> --reason`, `recover <id>`,
`verify-live <id> --evidence TEXT`.

**Tests:** the transitions are allowed and refused per table; verify-live
requires active plus evidence; old records load.
