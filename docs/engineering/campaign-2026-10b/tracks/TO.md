# Track TO: pause on owner return, verification classes, policy at dispatch (S06, R12, R07, R04)

Wave 2 track, created 2026-10-10 when TH finished. Audits: `audit/A1.md`
(R12: "no mid-job detection … running job is not paused when the owner
returns"), `audit/A6.md` (R07: check-class taxonomy), `audit/A4.md` (R04:
evaluate authority at dispatch). Branch `c2/TO`, worktree `C:/Users/chris/Documents/Ghost/ghost_c2_TO`.
Files this track owns: `ghost_agent/orchestrator.py`, `ghost_agent/worker.py`,
the `_execute_queued_outcome` function in `cli.py`, and tests.

---

## TO1: unattended jobs yield when the owner returns (review: 2)

**Goal:** S06 acceptance, "Returning to the PC reduces or pauses
resource-heavy work within a configured, measured deadline".

**Decided design:**

- A task constraint `yield_on_owner_return: bool` (default False). Set it to
  True in `queue_sandbox_outcome`-created jobs, i.e. for sources `proactive`,
  `capability_improver`, `self_improver` and `benchmark`: add the key to the
  acceptance contract there, and copy it through `make_task_payload`'s key
  tuple. **Exception:** the owner's own `ghost accomplish` and Telegram
  requests keep False. Note that proactive.py and autonomy.py are outside
  your list for everything except this **one key**; keep the change to that.
- In `Orchestrator._run_loop`, before each model turn (PLAN/IMPLEMENT/REPAIR),
  if the constraint is true, call `idle_seconds()` (from proactive.py). If it
  is not None and is less than `GHOST_OWNER_RETURN_IDLE_S` (env, default 120),
  end the run with `phase=DONE`, `last_result="yielded_to_owner"`, and record
  `state["yield"] = {"detected_at": now, "idle_s": value, "latency_s": now - <time of the previous turn end>}`.
  The yield latency is the measured deadline evidence. `None`
  (unknown/non-Windows) does **not** yield.
- `_execute_queued_outcome`: when the run ends with `yielded_to_owner`, set
  the job to `paused` with reason `yielded_to_owner` (not failed). It can be
  resumed with the existing `ghost jobs resume`, which uses the preserved
  sandbox/checkpoint.
- Add `GHOST_OWNER_RETURN_IDLE_S` to the README-style docstring of
  `_run_loop`.

**Tests** (patch `idle_seconds`): an unattended job yields at the next turn
when idle is 5 s, with state latency recorded; an owner job never yields; None
never yields; the worker maps a yield to paused; resume continues from the
checkpoint.

---

## TO2: declared verification classes

**Goal:** a task can declare required check classes beyond tests, and
silently skipping one isn't possible (R07: "project ecosystems choose
appropriate compile, type, unit, … smoke checks").

**Decided design:**

- Constraint `required_checks: list[str]` from the allowlist `unit` (default:
  discovered pytest/node tests, as today), `compile`
  (`python -m compileall -q .`), `smoke` (a task-supplied
  `smoke_cmd: str`, run through the existing allowlisted command runner).
  Validate them in `Task`; unknown classes → `ValueError`.
- In VERIFY, after the existing gates pass, run each additional declared
  class as its own gate (stage name = class). If `smoke` is declared without
  `smoke_cmd`, the gate **fails** with `"smoke check declared but no smoke_cmd given"`.
  A failure goes to REPAIR like the test gates.
- `ghost accomplish --check compile --check smoke --smoke-cmd "python app.py --selftest"`
  sets them. Add the flags in the accomplish parser and contract; this one
  parser change in cli.py is allowed.

**Tests:** compile catches a syntax error that tests didn't import; smoke
without a cmd fails; an unknown class raises; the defaults are unchanged.

---

## TO3: re-check policy when the worker claims a job

**Goal:** authority is evaluated at dispatch, not only at queue time. If the
owner turns `build` off after a proactive job was queued, that job must not
run (R04).

**Decided design:** in `_execute_queued_outcome`, before research or the
sandbox starts, if the job's acceptance contract `source` is one of
`proactive`, `capability_improver`, `self_improver` or `benchmark`, call
`PolicyStore(root).require("build", ...)` (and `"improve"` for
`self_improver`). On `PolicyError`, mark the job `paused`, with a reason
quoting the policy message, notify the owner once (`normal`, id
`policy-paused-<job_id>`), and return a WorkerResult with status `paused`.
The owner's own jobs (no source, or `telegram`) are unaffected.

**Tests:** a queued proactive job, then build turned off, then the worker →
paused and notified, with the sandbox not created; build back on, then resume
→ runs; an owner job is unaffected.
