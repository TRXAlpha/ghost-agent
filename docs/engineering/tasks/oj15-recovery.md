# Task OJ15-Recovery: Repair self-repair evidence verification patch

## Context
An unfinished, uncommitted patch attempted to strengthen self-repair verification with
evaluator execution and candidate evidence, but broke imports and merged manager methods
into the dataclass. We must restore a coherent application, keep the good strengthening,
and resolve the defects. See docs/engineering/STATE.md for the baseline failure and
confirmed defect list. This patch must be repaired, not discarded.

## Subsystem
Self-repair evidence verification (ghost_agent/self_repair.py, its CLI command in
ghost_agent/cli.py, and tests/test_self_repair.py + tests/test_self_repair_oj15.py).

## Base revision
HEAD cb6d282 with the uncommitted working-tree changes present (do not reset them).

## Allowed changes
- ghost_agent/self_repair.py
- ghost_agent/cli.py (only the self-repair command + _run_bounded_evaluator + its argparse)
- tests/test_self_repair.py
- tests/test_self_repair_oj15.py
Do NOT modify control_plane.py, promotion.py, or other modules.

## Required behavior / design (from STATE.md "Recovery design decisions")
1. Restore `SelfRepairManager` as a proper class (__init__ sets self.root, self.path
   = root/".ghost"/"self-repair.json", self.protected_roots, and creates the dir).
   `SelfRepairProposal` stays a PURE dataclass (no manager methods, no self.path use).
2. Keep: SelfRepairError, _dir_digest, protected-roots scope guard, _is_evaluator_evidence.
3. `protected_roots = {root/".ghost", root/"ghost_agent"}`. `_assert_candidate_scope` must
   reject a candidate equal to root, outside root, or inside a protected root, using path
   relationships (resolve + relative_to). Keep the old oj15 expectations: a sandbox under
   ".ghost" is rejected; a sandbox under "ghost_agent" is rejected.
4. `verify_from_evaluator(proposal_id, evaluator)` is the ONLY producer of trusted
   evaluator evidence. Flow:
   - resolve candidate; if missing -> error.
   - pre_digest = _dir_digest(candidate)
   - run evaluator(candidate) -> {returncode:int, output}
   - post_digest = _dir_digest(candidate)
   - if post_digest != pre_digest -> SelfRepairError("candidate changed during evaluation")
   - build evidence records: source="evaluator", run_digest=pre_digest, returncode, output.
   - persist via internal record path; status = verified iff all returncode==0 AND a real
     promotion snapshot exists; else rejected.
5. `record_verification(proposal_id, regression_results, benchmark_results)` stays public
   but never counts non-evaluator evidence as verification. Promotion eligibility requires
   evaluator-sourced evidence whose run_digest matches the CURRENT candidate digest and a
   nonempty promotion snapshot. Any hand-entered or digest-mismatched result -> rejected.
6. `mark_applied(proposal_id, promotion_id)` and
   `mark_rollback(proposal_id, promotion_id, reason)`:
   - apply only from verified; rollback only from applied.
   - Bind to a REAL promotion journal record: promotion_id must match an actual
     promotion directory under root/".ghost"/"promotions" (via PromotionManager.list()),
     with journal status "verified" for apply and "rolled_back" for rollback. Reject a
     bare nonempty string that does not correspond to a real promotion record.
   - Preserve previous behavior constraints (invalid transition -> error; rejection of
     non-verified apply; rejection of re-applying after rollback).
7. CLI:
   - keep manager = SelfRepairManager(Path.cwd()); propose prints proposal; verify uses
     verify_from_evaluator with a bounded evaluator; request-promotion unchanged.
   - `_run_bounded_evaluator(candidate, command)`: run `shlex.split(command)` with
     cwd=candidate, capture_output, text, timeout (default 60s, configurable via
     GHOST_EVALUATOR_TIMEOUT env). CRITICAL: sanitize the environment — build a minimal
     env (PATH + a small allowlist) instead of inheriting the full os.environ, so host
     credentials/secrets are NOT leaked into the candidate process. Handle missing
     command (FileNotFoundError) and TimeoutExpired. Return {returncode, output capped}.
     Add a comment stating this is an OPERATOR-triggered local evaluator, NOT
     containerized or host-isolated (do not claim containment).
8. Tests (tests/test_self_repair.py, tests/test_self_repair_oj15.py) must be updated to the
   new API. Migrate oj15 tests that used record_verification(...,[{"returncode":0}],...) to
   verify_from_evaluator with a real evaluator helper that actually runs a subprocess (e.g.
   a tiny inline python that succeeds/fails). Add/keep:
   - hand-entered evidence rejected;
   - candidate mutation during evaluation rejected (evaluator that edits the candidate);
   - candidate mutation after verification invalidates evidence;
   - corrupt records preserved;
   - real evaluator success -> verified -> promotion request works;
   - mark_applied/mark_rollback bound to a real promotion record (seed a promotion via
     PromotionService/PromotionManager in the tmp project), including rejection when the
     promotion_id does not correspond to a real record.
   Do NOT weaken: a compiling/returncode-0 fixture must be a real subprocess run, and
   promotion must still be refused without genuine evaluator verification.

## Dependencies / interfaces
- promotion.py: PromotionManager (list, snapshot) and PromotionService — read-only usage.
- control_plane.py imports SelfRepairManager from self_repair; keep the name.

## Non-goals
- Do NOT modify the openjarvis container runner or wire containerized execution here.
- Do NOT rewrite promotion.py.
- Do NOT add a separate evidence authority store.
- Do NOT change CLI actions other than self-repair.

## Acceptance (definition of done)
- `python -m pytest tests/test_self_repair.py tests/test_self_repair_oj15.py -v` passes.
- `python -c "from ghost_agent.self_repair import SelfRepairManager; print('OK')"` works.
- `python -c "from ghost_agent.control_plane import GhostSDK; print('OK')"` works.
- No NEW failures in `python -m pytest tests/ -q` beyond pre-existing baseline failures
  (record any baseline failures that also fail on a clean HEAD checkout for comparison).
- Verify_from_evaluator binds pre-run digest; mutation during/after eval invalidates.
- Hand-entered evidence cannot promote. mark_applied/mark_rollback need real promotion.

## Reporting format (worker)
Task ID / base / changed files / implemented behavior and callers / requirements met /
tests run + exact results / failures + corrections / known limitations / blockers /
ready for independent review.
