# Track TL: structured grants, uncertain external actions, device telemetry validation (R04, R01, S01, R11)

Wave 2 track, created 2026-10-10 when TF finished. Audits: `audit/A4.md`
(R04 grants), `audit/A1.md` (R01-d uncertain external actions), `audit/A7.md`
(S01), `audit/A8.md` (R11 telemetry). Branch `c2/TL`, worktree `C:/Users/chris/Documents/Ghost/ghost_c2_TL`.
Files this track owns: `ghost_agent/openjarvis_bridge/authority.py`,
`ghost_agent/openjarvis_bridge/telegram.py`, `ghost_agent/devices.py`,
new `ghost_agent/telemetry.py`, `cli.py` (new blocks and the
`openjarvis telegram` block only), and tests. No real Telegram or network
calls in tests; never send a real message.

---

## TL1: structured grant fields (review: 2)

**Goal:** a grant records the R04 fields (side effects, conditions, limits,
provenance), validated fail-closed.

**Decided design:**

- Add to `Grant` (defaults so old grants load):
  `side_effects: list[str] = field(default_factory=list)`
  (allowed values: `read`, `write_sandbox`, `write_live`, `send_message`,
  `network`, `device_control`, `spend`), `conditions: dict = field(default_factory=dict)`
  (allowed keys: `quiet_hours_only: bool`, `requires_owner_present: bool`,
  `max_per_day: int`), `limits: dict = field(default_factory=dict)`
  (allowed keys: `max_calls: int`, `max_seconds: int`),
  `provenance: str = "owner"` (`owner` | `migration`).
- `GrantStore._load` validation: unknown side effects, unknown condition or
  limit keys, wrong types, or a non-owner provenance on a new grant →
  `AuthorityStateError` (fail closed, like the existing checks).
- Grant creation APIs take these as optional keyword args.
- `authorize_call` (or the bridge's equivalent dispatch check; find it)
  enforces `max_per_day` by counting today's successful authorizations for
  that grant in a small ledger `.ghost/openjarvis/grant_usage.json`
  (`{grant_id: {"date": "YYYY-MM-DD", "count": n}}`). Over the limit → deny,
  with the reason. Other conditions are recorded but **not** enforced in this
  task (state that in the docstring).

**Tests:** legacy grants load; an invalid side effect or key fails closed;
max_per_day denies the N+1th call the same day and resets the next day
(patch the date); provenance is validated.

---

## TL2: uncertain delivery reconciliation for Telegram (review: 2)

**Goal:** a crash in the middle of a send yields an explicit *uncertain*
record that the owner is told about, never a silent loss or a duplicate
resend (R01 "preserve an explicit uncertain result", S01 "retries do not
repeat … consequential actions").

**Decided design:**

- New function `reconcile_receipts(root) -> list[dict]` in telegram.py. Scan
  `state_root(root)/"telegram-receipts"/*.json`. Any receipt whose status is
  `processing` or `sending` and has no `finished_at` becomes
  `status="uncertain"` with `reconciled_at=now`. Write it with `atomic_json`,
  and notify the owner once per receipt via
  `AttentionManager.notify(Notification("important", f"Telegram message {message_id} may or may not have been answered (Ghost stopped mid-send). Check the chat.", notification_id=f"tg-uncertain-{message_id}", source="telegram"))`.
- Call `reconcile_receipts` at the start of `serve(...)`, before polling
  starts.
- The handler must already skip messages whose receipt exists. Verify this
  in `handle()`. If an `uncertain` receipt's message is redelivered, it must
  **not** be processed again; extend the skip check to include `uncertain`.
- `ghost openjarvis telegram receipts [--status S]` lists the receipts.

**Tests:** a stuck `sending` receipt → uncertain, plus one notification even
across two reconciles; a delivered receipt is untouched; a redelivered
message with an uncertain receipt is not reprocessed (use the stub
channel/handler pattern from the existing telegram tests).

---

## TL3: Telegram transport reconnect/backoff test (parallel-ok with TL2)

**Goal:** prove that a transient channel failure is retried with backoff and
keeps receipts consistent (S01). This is evidence the audit found missing.

**Decided design:** read `serve`/the worker loop in telegram.py. If a
reconnect/backoff mechanism exists, write tests for it with a stub channel
that raises a transient error twice and then works. Assert that the backoff
delays (patch sleep) grow and that no message is processed twice. If **no**
backoff exists, implement a minimal one where the channel's poll/receive is
called: exponential 1, 2, 4 … capped at 60 s, reset after a success, and the
emergency stop is checked on every retry. Then test it. Keep it within
telegram.py.

**Tests:** as described; the emergency stop ends the retry loop.

---

## TL4: device telemetry validation (review: 2)

**Goal:** device data is validated and can never become instructions,
generalizing what HuaweiRoute does to all devices (R11, audit A8 gap 4).

**Decided design:** new module `ghost_agent/telemetry.py`:

```python
@dataclass(frozen=True)
class TelemetrySample: device_id: str; metric: str; value: float; unit: str; observed_at: float; received_at: float
class TelemetryError(ValueError): ...
def validate_sample(raw: dict, *, device: Device, now: float, max_skew_s: float = 300, max_age_s: float = 86400) -> TelemetrySample
```

- `metric` must match `^[a-z][a-z0-9_]{0,40}$` and be in the device's
  `verified_capabilities` or `capabilities` (declared). `value` must be a
  finite float. `unit` is from a fixed allowlist
  (`deg, deg_s, celsius, percent, count, steps, bpm, seconds, meters, lux, volts, none`).
- `observed_at` more than `max_skew_s` in the future → error (clock skew);
  older than `max_age_s` → error (stale).
- Any **string** field longer than 64 chars, or containing a newline →
  error (blocks prompt-injection payloads riding in telemetry).
- Extra keys are rejected.
- `TelemetryStore(root)` appends valid samples to
  `.ghost/telemetry/<device_id>.jsonl`, with `latest(device_id, metric)`.
  Each accepted sample updates the device's `last_seen` via
  `DeviceRegistry.update` (TF2 added it).
- CLI: `ghost device ingest <id> --file samples.json` validates each sample,
  prints accepted/rejected counts with reasons, and never echoes rejected
  string content.

**Tests:** each rejection reason; a valid sample is stored and updates
last_seen; an injection-like string is refused, and its content is not
printed.
