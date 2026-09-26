# Task OJ09-FeedbackLoop: Close the proactive opportunity feedback loop

## Task ID
OJ09-FeedbackLoop

## OJ / scenario references
- OJ09 (proactive loop / opportunities), OJ11 (idle/proactive loop production wiring)
- Roadmap narrative: "opportunity -> durable work -> verified result -> feedback"
- Historical gap #2: `record_feedback()` was not called by the production worker;
  finished jobs could leave opportunities queued indefinitely.

## Owner-visible outcome
When a proactively-queued sandbox job finishes through the real production worker
(`OutcomeWorker.run_next(_execute_queued_outcome)`), the linked opportunity
transitions from `queued`/`running` to `completed` (tests passed) or `rejected`
(failed / error), recorded durably via the existing `ProactiveEngine.record_feedback`,
idempotently, and with restart reconciliation that closes any opportunity whose
linked job reached a terminal state while the loop was not watching.

## Entry points (production)
- `ghost_agent/cli.py::_execute_queued_outcome` — the single shared worker handler
  used by both `_cmd_worker` (cli.py:2663) and the Telegram worker loop
  (`openjarvis_bridge/telegram.py:161`).
- `ghost_agent/proactive.py::ProactiveEngine` / `queue_sandbox_outcome`.

## Existing implementation
- `ProactiveEngine.record_feedback(opportunity_id, outcome, evidence)` exists but is
  ONLY called from `tests/test_proactive.py` (lines 156, 169, 214, 220). No production
  caller. This is the defect: finished proactive jobs never close their opportunity.
- `queue_sandbox_outcome` records the link as
  `job.acceptance_contract["opportunity_id"]` and `"source"` (proactive | telegram).
- `_execute_queued_outcome` already determines `job.result` ("tests_passed" or other)
  and returns `WorkerResult(job.job_id, "completed"/"failed", job.result)`.

## Dependencies
- `OutcomeStore` (job.status / job.result / job.acceptance_contract)
- `Scheduler` (job terminal state completed|failed|cancelled — for reconciliation)
- `ProactiveEngine` / `OpportunityStore`
- `record_feedback` semantics are FIXED (do not weaken): only queued/running may
  receive feedback; outcome in {tests_passed, tests_failed, error}; maps to
  completed/rejected.

## Allowed changes
- `ghost_agent/proactive.py` (add a feedback-closing helper and a `reconcile()` method)
- `ghost_agent/cli.py` (`_execute_queued_outcome` only — call the helper)
- `tests/test_proactive.py` and a NEW `tests/test_proactive_feedback.py`
- Do NOT touch `openjarvis_bridge/telegram.py` (it reuses `_execute_queued_outcome`)

## Non-goals
- No new executor/scheduler/orchestration loop.
- No change to authority/promotion/verification semantics.
- No weakening of `record_feedback` validation (idempotency must be added at the
  caller/helper level, not by removing the queued/running guard).
- No telemetry or analytics additions.

## Interfaces and state ownership
- OpportunityStore owns demand-side opportunity state (single writer via
  `save`'s exclusive lock already).
- The feedback writer is Ghost itself (single-owner invariant). Evidence must come
  from the REAL job result, not fabricated.

## Acceptance criteria
1. **Production feedback closed**: executing the real `_execute_queued_outcome`
   handler via `OutcomeWorker.run_next` on a proactively-queued job durably moves the
   linked opportunity to `completed` (when `job.result == "tests_passed"`) or
   `rejected` (failed/error), with the real job result as evidence.
2. **Idempotent**: re-running the feedback path on an already-finalized opportunity
   (or re-running the worker on an already-terminal job) does NOT double-record,
   corrupt, or error destructively. `record_feedback`'s queued/running guard remains.
3. **Outcome mapping correct**: completed -> tests_passed; failed-with-`tests_failed`
   -> tests_failed; worker/other failure -> error.
4. **Restart reconciliation**: opportunities stuck in `queued`/`running` whose linked
   job is terminal (completed/failed/cancelled in the scheduler) are closed to
   completed/rejected on reconciliation.
5. **Non-proactive jobs safe**: jobs WITHOUT an `opportunity_id` (manual outcomes) are
   untouched by the feedback path (no crash, no state change).
6. **Tests** (non-mocked production path where feasible): a regression test that
   detects the ORIGINAL defect (no production caller closes the loop); a real
   `_execute_queued_outcome` integration test; a negative control; an idempotency test;
   a reconcile test.
7. Focused suite green; full suite has no NEW failures (only the 2 pre-existing
   Python 3.14 env failures).

## Definition of done
Focused tests pass; production path exercised via real worker; two independent
reviewer PASS (correctness + architecture); subsystem committed on `master`;
STATE.md + ledger updated.

## Verification commands
- `python -m pytest tests/test_proactive.py tests/test_proactive_feedback.py -q`
- `python -m pytest tests/test_cli_outcomes.py -q`
- `python -m pytest tests/ -q` (expect 2 pre-existing env failures only)

## Known limitations
- Live model/inference not required for this subsystem (deterministic worker results
  via a fake/recorded result through the real handler).
