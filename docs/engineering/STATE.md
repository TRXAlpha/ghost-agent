# Ghost Engineering State

## Repository
- Branch: master
- HEAD: a90ff65 — oj15: repair self-repair evidence verification (S08 trust boundary)
- Working tree: CLEAN. OJ15-Recovery subsystem COMMITTED and ACCEPTED.

## Accepted Subsystem Report: OJ15-Recovery
- Owner-visible behavior: self-repair proposals verify ONLY through a real witnessed
  subprocess evaluator run; hand-entered/forged/stale-replay evidence cannot promote;
  apply/rollback bound to real promotion journal transactions.
- Verification evidence (2026-09-27, Python 3.14.2, .venv): focused 39 passed (post-commit);
  full suite 841 passed, 4 skipped, 2 pre-existing env failures, 0 collection errors.
- Reviewer verdicts: Reviewer A PASS (0 findings), Reviewer B PASS (0 findings).
- Resolved findings: OJ15-Recovery-A-1/2 (forgery via source=evaluator+digest), 
  OJ15-Recovery-B-1 (stale-nonce replay against mutated bytes) — all closed with
  regression tests.
- Commit: a90ff65
- Next dependency-ready task: TBD (see roadmap revalidation below)

## Active Task
**OJ09-FeedbackLoop: Close the proactive opportunity feedback loop (production wiring).**

Task contract: docs/engineering/tasks/oj09-feedback-loop.md

Confirmed defect (historical gap #2): `ProactiveEngine.record_feedback()` is only
called from tests/test_proactive.py; no production caller. Finished proactive jobs
leave their linked opportunities in `queued`/`running` indefinitely.

Chosen approach: `_execute_queued_outcome` (cli.py) is the single shared worker
handler (used by _cmd_worker and telegram worker loop). Add a feedback-closing helper
in proactive.py that maps a job's real terminal result to the opportunity outcome
(completed/tests_passed, rejected/tests_failed|error) and calls the existing
record_feedback idempotently; add ProactiveEngine.reconcile() for restart
reconciliation of opportunities whose job reached a terminal state while the loop
was not watching. No new executor/loop; no authority change.

Dependency-ready: YES (no external credentials/hardware required).

Task contract: docs/engineering/tasks/oj15-recovery.md

### Confirmed baseline failure
`python -m pytest tests/test_self_repair.py tests/test_self_repair_oj15.py -q`
→ ImportError: cannot import name 'SelfRepairManager' from 'ghost_agent.self_repair'
(SelfRepairManager class was removed by the unfinished patch, but cli.py,
control_plane.py, and both test files still import/construct it.) This propagated
to 11 collection errors across the suite.

### Resolved (implementation complete)
- SelfRepairManager restored as a real class; SelfRepairProposal is a pure dataclass.
- protected_roots = {root/.ghost, root/ghost_agent}; scope guard is path-based.
- verify_from_evaluator is the ONLY trusted evaluator path; hashes BEFORE and AFTER
  the evaluator run; binds the PRE-run digest; rejects if candidate mutated during eval.
- Trust boundary: verify_from_evaluator mints a random run_nonce per witnessed run and
  records the PRE-run digest it witnessed as IMMUTABLE ground truth until the next
  witnessed run. record_verification only counts evidence carrying that stored nonce AND
  a run_digest that equals BOTH the stored witnessed digest AND the current candidate
  digest. A fresh proposal (empty nonce) can never verify; forged source="evaluator"
  evidence, and stale-nonce replay against mutated bytes, are both rejected.
  (Fixes: empty-nonce forgery P2 + stale-nonce replay P2 — see reviews/OJ15-Recovery.*)
- _dir_digest excludes ephemeral trees because real pytest runs write cache dirs, in
  LOCKSTEP with PromotionManager.preview's set {.git, .ghost, .venv, __pycache__,
  .pytest_cache} (no .mypy_cache divergence) so digest and snapshot agree.
- record_verification only classifies as verified nonce-stamped evaluator evidence whose
  run_digest matches the CURRENT candidate digest; hand-entered/forged evidence rejected.
- mark_applied/mark_rollback bound to a REAL promotion journal record
  (PromotionManager.list()); verified status for apply, rolled_back for rollback.
- CLI verify uses verify_from_evaluator with _run_bounded_evaluator; sanitized env
  (PATH/SYSTEMROOT only) so host secrets are not inherited; explicit NOT-isolated note.
- Tests migrated + strengthened: real subprocess evaluators; mutation-during-eval
  rejected; mutation-after-verification invalidates evidence; hand-entered + forged
  (incl. empty-nonce) evidence rejected; promotion seeded via PromotionService for
  apply/rollback binding.

### Verification results (2026-09-27, Python 3.14.2, .venv)
- Focused: `pytest tests/test_self_repair.py tests/test_self_repair_oj15.py` = 39 passed.
- Full: `pytest tests/` = 841 passed, 4 skipped, 2 failed, 0 collection errors.
- The 2 failures are PRE-EXISTING environment failures in test_openjarvis_authority.py
  ("OpenJarvis needs Python 3.11-3.13", we run 3.14) — unrelated to self-repair and
  present before this task (openjarvis_bridge/runtime.py untouched).
- Baseline BEFORE fix: 11 collection errors, 0 pass.

### OJ09 — COMPLETE ✓
**Commit:** `3960869` ("OJ09: wire reconcile into watch(), close feedback on terminal early-returns, fix premature verdict")
- `ProactiveEngine.watch()` calls `reconcile()` via `_reconcile_safe()` before each tick
- `OpportunityStore.list_all()` returns all opps (not just last 100) for restart reconciliation
- `_execute_queued_outcome` early-returns (emergency-stop, route-mismatch, post-research stop) now mark the job terminal and call `_close_proactive_feedback`
- `feedback_outcome_for_job()` only yields verdicts for genuinely terminal jobs
- Regression tests added: `test_emergency_stop_closes_opportunity`, `test_reconcile_with_stale_opportunities`
- All 42 proactive/cli tests pass
**Status:** DONE — move to OJ12 (Scheduler Migration, dependency-ready: NO — credentials blocked)
**Next:** OJ12 requires credentials; identify dependency-ready task first
- Implementation done (in working tree, uncommitted) + new tests/test_proactive_feedback.py.
- Changes: ghost_agent/proactive.py (close_opportunity_feedback, feedback_outcome_for_job,
  _feedback_evidence, ProactiveEngine.reconcile), ghost_agent/cli.py
  (_close_proactive_feedback wired into both success and exception paths of
  _execute_queued_outcome).
- Focused: 34 passed (12 new + existing proactive/cli_outcomes).
- Full suite: 853 passed, 4 skipped, 2 FAILED (both pre-existing Python 3.14 env
  failures in test_openjarvis_authority.py — unrelated).
- Regression test verified: removing the wiring makes
  test_worker_completes_proactive_opportunity_when_tests_pass fail
  (opportunity stays 'queued' instead of 'completed').
- Next action: dispatch independent reviewers (correctness + architecture).

## MVP Smoke Test Finding (2026-09-27) — memory retrieval poisoned runs — FIXED
**Commit:** `1d6918d` ("memory: IDF-weighted retrieval with relevance threshold")
- Root cause: `_keyword_retrieve` scored by raw token overlap; generic metadata
  tokens ('task', 'test', 'iteration') made unrelated failed lessons match any
  coding query. Retrieved lessons injected the FULL goal text of unrelated past
  tasks (LRU cache, fizzbuzz); the small local model implemented the wrong goal.
- Fix: IDF-weighted scoring, stop/common-token blocklist, distinctiveness
  threshold (score > 1.0). Generic queries now return no lessons.
- Regression tests: tests/test_memory_retrieval_relevance.py (3 tests).
- Full suite: 854 passed, 4 skipped.
- Verified by run 3 (outcome_20260927_164623): model then worked on the CORRECT
  goal (hello.py/add written correctly). Remaining failure is small-model quality:
  it writes unittest-style tests, which Ghost's pytest-only guard rejects, so it
  hits iteration_limit. Next lever: strengthen the system prompt's test-style
  instruction or relax the guard for simple tasks.

## Verification Commands
- `python -m pytest tests/test_self_repair.py tests/test_self_repair_oj15.py -v`
- `python -m pytest tests/ -q` (full suite; 2 pre-existing env failures expected in
  test_openjarvis_authority.py due to Python 3.14 vs OpenJarvis 3.11-3.13 requirement)
- `python -c "from ghost_agent.self_repair import SelfRepairManager; print('OK')"`
- `python -c "from ghost_agent.control_plane import GhostSDK; print('OK')"`

## Environment
- Windows, Python 3.x (venv at .venv; current interpreter is 3.14.2)
- No external dependencies beyond pytest for the self-repair tests
