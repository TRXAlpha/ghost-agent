# Ghost Engineering State

## Repository
- Branch: master
- HEAD: cb6d282 — ledger: record OJ13/OJ16 blockers and completed OJ09-OJ12, OJ14, OJ15
- Uncommitted changes: ghost_agent/cli.py, ghost_agent/self_repair.py, tests/test_self_repair.py
- Untracked: docs/engineering/ (this STATE.md + task documents)

## Active Task
**OJ15-Recovery: Repair self-repair evidence verification patch** — IMPLEMENTATION COMPLETE, IN REVIEW

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

### Current phase: ACCEPTED — READY TO COMMIT
- Round-4 independent review: Reviewer A VERDICT=PASS (0 findings), Reviewer B VERDICT=PASS
  (0 findings). All 3 prior P2 findings independently verified closed.
  See docs/engineering/reviews/OJ15-Recovery-loop.md.
- Candidate frozen = current uncommitted working tree on base cb6d282.
- Next action: commit accepted subsystem (only task files + docs/engineering/), then update
  this STATE.md with the commit ID and select the next dependency-ready subsystem.

## Verification Commands
- `python -m pytest tests/test_self_repair.py tests/test_self_repair_oj15.py -v`
- `python -m pytest tests/ -q` (full suite; 2 pre-existing env failures expected in
  test_openjarvis_authority.py due to Python 3.14 vs OpenJarvis 3.11-3.13 requirement)
- `python -c "from ghost_agent.self_repair import SelfRepairManager; print('OK')"`
- `python -c "from ghost_agent.control_plane import GhostSDK; print('OK')"`

## Environment
- Windows, Python 3.x (venv at .venv; current interpreter is 3.14.2)
- No external dependencies beyond pytest for the self-repair tests
