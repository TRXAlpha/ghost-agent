# Track TB: owner authority, policy modes, control plane (R04, R14)

Audit: `audit/A4.md`. Branch `c2/TB`, worktree `C:/Users/chris/Documents/Ghost/ghost_c2_TB`.
Files this track owns: new `ghost_agent/policy.py`, `ghost_agent/control_plane.py`,
`ghost_agent/api_server.py`, `ghost_agent/dashboard.py`, the
`queue_sandbox_outcome` function in `proactive.py` (TB1 only: one guard),
`cli.py` (new blocks only), and tests.

---

## TB1: standing policy modes (review: 2)

**Goal:** the owner sets standing autonomy modes once. They are enforced at
the real hand-offs, and corrupt policy fails closed (R04).

**Decided design:** new module `ghost_agent/policy.py`:

```python
MODES = ("suggest", "build", "operate", "improve", "idle_work")
DEFAULTS = {"suggest": True, "build": True, "operate": False, "improve": False, "idle_work": False}
class PolicyError(ValueError): ...
class PolicyStore:
    def __init__(self, project_root: Path)
    def load(self) -> dict[str, bool]     # missing file -> DEFAULTS copy
    def set(self, mode: str, enabled: bool) -> dict[str, bool]
    def require(self, mode: str, action: str) -> None   # raises PolicyError(f"{action} needs '{mode}' mode; enable with: ghost policy set {mode} on")
```

- The file is `.ghost/policy.json`:
  `{"version": 1, "modes": {...}, "updated_at": float, "updated_by": "owner"}`,
  written with `atomic_json` under an `exclusive_lock`.
- **Fail closed:** if the file exists but is unreadable, isn't valid JSON, or
  has a non-bool mode value, `load()` returns `{"suggest": True}` plus all
  other modes False, and `set()` raises `PolicyError("policy file is corrupt; inspect .ghost/policy.json")`.
- Unknown mode names raise `PolicyError`.
- **Enforcement points** (exactly these):
  1. `proactive.queue_sandbox_outcome`: as the first check after argument
     validation, call `PolicyStore(root).require("build", f"queueing {source} sandbox work")`.
     For `source == "self_improver"`, also call `require("improve", ...)`.
     Convert `PolicyError` to `ProactiveError(str(exc))`, so existing callers'
     error handling works.
  2. `OvernightRunner.run` / `GhostDaemon.run` and `ProactiveEngine.watch`: at
     start, `require("idle_work", "unattended background work")`. The CLI
     handlers catch `PolicyError`, print the message, and return. A single
     `proactive tick` (manual) is **not** gated by idle_work.
     Note: `overnight.py`/`daemon.py` are also edited by TA5. Make only this
     one-line guard at the top of `run()`.
- CLI: a new `policy` command after the `approval` parser block (cli.py ~line 1699):
  `ghost policy show`, and `ghost policy set <mode> on|off`.

**Tests** (`tests/test_policy.py`): defaults when the file is missing;
set/load round trip; corrupt file → fail-closed load, and set raises;
`queue_sandbox_outcome` refuses with `ProactiveError` when build is off;
self_improver needs improve; overnight run refuses without idle_work (patch
`run_cycle` and assert it's never called); the CLI show/set output.

**Existing tests:** tests that call `queue_sandbox_outcome` or run the
overnight/daemon loops will now fail because the default `idle_work` is off.
Fix them **only** by creating a policy file in the test's tmp root, with a
small fixture in `tests/conftest.py` named `permissive_policy` (writes all
modes on). Apply it to exactly the failing tests. List each one in your
report.

---

## TB2: control-plane views

**Goal:** the existing GhostSDK views are reachable from the CLI (R14).

**Decided design:** `ghost control-plane` gains
`--view {snapshot,goals,opportunities,resources,integrations,notifications}`
(default `snapshot`), which maps to the existing GhostSDK methods. First find
their exact names in `control_plane.py`: goal_tree, opportunity_pipeline,
resource_usage, integration_health, notification_preferences. Print
`json.dumps(result, indent=2, default=str)`. If a method needs arguments,
use its defaults. If a named method doesn't exist, stop and report.

**Tests:** each view prints valid JSON in an empty tmp project.

---

## TB3: `ghost export` (parallel-ok with TB2)

**Goal:** a versioned, secret-free export of personal state for backup and
portability (R14).

**Decided design:** the `GhostSDK.export(include: set[str] | None = None) -> dict` method returns
`{"format": "ghost-export", "version": 1, "exported_at": iso, "sections": {...}}`,
with these sections, each read through the store's existing list/load method:
`goals`, `approvals`, `notifications`, `validated_memories`, `devices`,
`opportunities`, `scheduler_jobs`, `capability_gaps`, `policy`.
**Exclude:** anything from `secrets.py` stores, `.env` and credential
references, and redact with the existing `security.redact_secrets` on every
string value (recursively). The CLI is
`ghost export --out PATH [--only goals,devices,...]`. It refuses to overwrite
an existing file, and writes with `atomic_write`.

**Tests:** the export includes the sections; a planted secret-pattern string
in a goal title is redacted; overwrite is refused; `--only` filters; the output
carries `version == 1`.

---

## TB4: API job controls

**Goal:** the loopback API can pause, resume and cancel jobs through the same
code as `ghost jobs` (R14).

**Decided design:** in `api_server.py`, add
`POST /v1/jobs/<job_id>/pause|resume|cancel`. Factor the state-change logic
that `_cmd_jobs` uses for pause/resume/cancel into a function
`apply_job_action(root, job_id, action) -> dict` in `control_plane.py`
(GhostSDK method `job_action`). Make `_cmd_jobs` call it too, so the CLI and
the API share one path. The API keeps the existing auth, content-type and
Host checks. It returns 200 with the job dict, 404 for an unknown job, and 409
for a disallowed transition.

**Tests:** extend `tests/test_api_server.py` style: pause a queued job via the
API; a disallowed transition gives 409; unknown → 404; the CLI still works.

---

## TB5: emergency stop holds and reports (review: 2)

**Goal:** engaging emergency stop is visible and measured, and revocation
cancels work that depends on it (R04).

**Decided design:**

- `EmergencyStop.engage` already exists (runtime.py). In the CLI handler for
  `ghost emergency-stop engage`, after engaging, call a new
  `Scheduler.hold_all(reason)`, which sets every `queued` job to `paused` with
  event `job.paused_by_emergency_stop`. On `disengage`, jobs paused by it
  (record the marker `paused_by: "emergency_stop"` on the job dict) go back to
  `queued`; other paused jobs stay paused.
  This edits `Scheduler` in runtime.py: add **one new method** plus the
  `paused_by` field handling only. Coordinate by keeping the change local; TA
  also edits Scheduler.
- Latency: `engage` records `engaged_at`. `OvernightRunner.run_cycle` and the
  worker claim path each record their first observation of the stop as
  `SecurityMonitor.record_event("emergency_stop.observed", {"component": ..., "latency_s": str(round(now-engaged_at, 3))})` (detail values are strings`
  once per engagement (keep a marker field `observed_by: [..]` in the stop
  file). Check the `SecurityMonitor` API in security.py first.
- `ghost emergency-stop status` prints the engaged time and the observed
  components with latencies.

**Tests:** engage pauses queued jobs and disengage restores only those;
running jobs are untouched; observation is recorded once per component;
status output.
