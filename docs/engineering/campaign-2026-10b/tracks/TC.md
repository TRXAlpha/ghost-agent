# Track TC: isolated verification, evidence identity, self-improvement loop (R07, R13, S08)

Audit: `audit/A6.md`. Branch `c2/TC`, worktree `C:/Users/chris/Documents/Ghost/ghost_c2_TC`.
Files this track owns: `ghost_agent/self_repair.py`, `ghost_agent/self_improver.py`,
`ghost_agent/promotion.py`, `ghost_agent/evaluation.py`, the
`_run_bounded_evaluator` and `_cmd_self_repair` functions in `cli.py`, the
`self-repair` parser block, `ghost_agent/daemon.py` (TC4: one hook only), and
tests.

---

## TC1: container-isolated self-repair verification (review: 2)

**Goal:** `ghost self-repair verify` runs the evaluator inside the existing
Docker isolation when it is available, and records honestly which runner was
used (R07).

**Decided design:**

- Add `--isolation {auto,container,host}` (default `auto`) to the
  `self-repair` parser.
- In `_cmd_self_repair`'s verify branch, the evaluator callback becomes:
  - **container**, or **auto** when available: call
    `openjarvis_bridge.isolated_execution.run_in_isolation(candidate, shlex.split(args.command, posix=False), timeout=<same deadline as the host path>)`.
    Map its result to the evaluator dict the manager expects (`returncode`,
    `output`, …; read `_run_bounded_evaluator`'s return shape and match it), and
    add `"runner": "container"`.
  - If `run_in_isolation` returns `available: False` (docker_unavailable or
    openjarvis_unavailable): under **container**, refuse with
    `Self-repair refused: container isolation unavailable (<error_type>: <error>)`
    and record nothing. Under **auto**, fall back to `_run_bounded_evaluator`
    and add
    `"runner": "host", "isolation_unavailable": "<error_type>"`.
  - **host:** `_run_bounded_evaluator` as today, plus `"runner": "host"`.
- `SelfRepairManager` must persist these keys in the stored evidence. Check
  `_is_evaluator_evidence` / `_run_verified_locked` and allow the extra keys
  without weakening any existing check.

**Tests** (monkeypatch `run_in_isolation` and `docker_available`; never call
Docker): auto with unavailable isolation → host run, and evidence has
`runner == "host"` plus the reason; container with unavailable isolation →
refused, no evidence; container available (patched to return a successful
result) → evidence `runner == "container"`; host → host.

---

## TC2: environment identity in verification evidence

**Goal:** evidence states which environment produced it, so "same bytes,
different environment" is detectable (R07, R13).

**Decided design:** add a helper
`environment_identity(candidate: Path, command: str) -> dict` in
`self_repair.py`. It returns:
`{"python": sys.version.split()[0], "executable": sys.executable, "platform": platform.platform(), "evaluator_command": command, "supervisor_rev": <git rev-parse HEAD of the supervisor repo via subprocess, or "unknown">, "dependency_digests": {relpath: sha256 for each existing file among pyproject.toml, requirements*.txt, *.lock, package.json, package-lock.json in the candidate root (not recursive)}}`.
Call it in the verification path, and store it under an `environment` key in
the evidence. A later verification whose `dependency_digests` differ from the
stored ones counts as a different environment. Do **not** change pass/fail
semantics; just record it.

**Tests:** the identity contains the keys; dependency digests change when
pyproject.toml changes; the evidence stores the environment.

---

## TC3: repeated-failure backoff for self-improvement (parallel-ok with TC2)

**Goal:** a failure signature whose candidates keep failing stops being
re-proposed (R13 "repeated failed attempts stop or defer").

**Decided design:** in `SelfImprover.propose` (self_improver.py), before
creating a candidate for a signature key, count the candidates with the same
`signature_key` whose status is `failed`, created within the last 7 days. If
there are **3 or more**, skip the signature, and record
`store.record_decision(<last failed candidate id>, "rejected", "deferred: 3 failures in 7 days")`
once (don't duplicate the decision if it already exists). Use constants
`MAX_FAILURES_PER_SIGNATURE = 3`, `FAILURE_WINDOW_SECONDS = 7 * 86400`.

**Tests:** 3 failed candidates → the signature is skipped and the next one is
chosen; 2 failed → still proposed; failures older than 7 days don't count; the
decision isn't duplicated.

---

## TC4: daemon drives SelfImprover.sync

**Goal:** finished self-improvement jobs are handed to self-repair without a
manual `sync` (R13).

**Decided design:** in `daemon.py`, `GhostDaemon.run_cycle`, add a third hook
`improve_sync` next to the existing trace-learning hook. It runs on every cycle
when the owner is away (same `owner_away` condition, no quiet-hours
restriction: it makes no model calls). Its default `improve_sync_fn` is
`lambda: [c.candidate_id for c in SelfImprover(root).sync()]`, injectable like
the other hooks, with errors captured into the record like the others. The
record key is `"improve_sync"`.

**Tests:** extend `tests/test_daemon.py`: the hook is called when away, is not
called when the owner is active, and its errors are recorded, not raised.

---

## TC5: update policy and observed promotion (review: 2)

**Goal:** promotion goes through an explicit, owner-set update policy and an
observation window before it counts as operationally verified (S08, R13).

**Decided design:**

- New file `.ghost/update_policy.json`:
  `{"version": 1, "auto_activate": false, "observation_window_seconds": 86400, "protected_paths": ["ghost_agent/policy.py", "ghost_agent/approvals.py", "ghost_agent/self_repair.py", "ghost_agent/promotion.py", "ghost_agent/evaluation.py", "ghost_agent/security.py", "ghost_agent/kernel.py"]}`.
  The defaults are used when it's missing.
- In `PromotionService`, before applying a self-repair promotion: if any
  changed file matches `protected_paths`, refuse with
  `"protected path requires the owner-controlled update process: <path>"`. This
  is R13's "core authority changes use a separately controlled path". Owner
  manual `ghost promote` of non-self-repair work is unaffected. Find how
  promotion knows the source; if it can't distinguish, apply the check only on
  the `request_promotion` path of `SelfRepairManager`.
- After a promotion is applied, record `observe_until = applied_at + window`.
  `ghost promote list` shows `observing` until the window passes with no
  rollback, then `operationally_verified`. Compute it on read; no background
  job is needed.

**Tests:** a protected path is refused; a normal path is allowed; the status
moves from observing to operationally_verified based on time (patch time).
