# OJ15-Recovery review loop

Task: Repair self-repair evidence verification patch (base cb6d282).

## Round 1 (initial candidate)
- Reviewer A (correctness/security): VERDICT FAIL — P2: public record_verification trusted
  caller-asserted source=="evaluator" + caller-computable digest (no real run). P3: test gap
  (only source=="user" tested, not forged evaluator label). P3: promotion binding not
  content-bound.
- Reviewer B (architecture): VERDICT PASS — P3s: residual in-process forgery model; digest
  exclusion list divergence (`.mypy_cache`).

Resolutions: added nonce trust boundary; aligned EXCLUDED_DIGEST_PARTS to
PromotionManager.preview set; added forged-evaluator-label test. Re-review.

## Round 2 (nonce fix)
- Reviewer A: VERDICT FAIL — P2: empty-nonce forgery. On a FRESH proposal run_nonce==""
  (dataclass default); public record_verification resolved expected_nonce="" and accepted
  evidence with run_nonce=="" + computed digest => verified + promotion, NO real run.
  Reproduced. The new test only omitted the nonce key (None != ""), missing the empty-string
  attack.
- Reviewer B: (fresh session returned no text; PASS implied by no response but not relied on).

Resolution: `passed` now requires `nonce_ok = bool(expected_nonce)`; a fresh (empty-nonce)
proposal can never verify. Added test_forged_empty_nonce_on_fresh_proposal_cannot_verify.
Re-review.

## Round 3 (empty-nonce fix)
- Reviewer A: VERDICT FAIL — P2: stale-nonce replay. After one legit run, the plaintext
  reusable nonce is not bound to its digest; a caller can mutate the candidate, recompute
  public _dir_digest, replay the stored nonce against the NEW digest => verified + promotion
  for un-witnessed bytes. Reproduced. item["run_digest"] was being overwritten by the caller's
  recomputed digest on the public path.
- Reviewer B: (fresh session returned no text).

Resolution: item["run_digest"] is now IMMUTABLE ground truth of what a witnessed run saw
(only verify_from_evaluator rewrites it via _run_verified_locked). Evidence must match BOTH
the stored witnessed digest AND the current candidate digest AND the nonce. Public
record_verification never rewrites run_digest/run_nonce. Added
test_stale_nonce_replay_against_mutated_candidate_cannot_verify. Re-review (round 4 pending).

## Current candidate state (round 4)
- Focused 39 passed (including 3 new forgery/replay regression tests).
- Full 841 passed, 4 skipped, 2 pre-existing env failures (OpenJarvis Py3.11-3.13 gate).
- Immutability of witnessed digest is the key invariant; verify in round 4.
