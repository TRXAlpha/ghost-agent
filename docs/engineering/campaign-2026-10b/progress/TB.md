# Track TB progress

| ID | status | candidate branch | review verdicts | merge commit | full-suite line | notes |
|----|--------|------------------|-----------------|--------------|-----------------|-------|
| TB1 | merged | c2/TB (candidate committed in place) | A: PASS after disposing one invalid P2 (reviewer claimed sheet says build defaults off; sheet line 20 says build: True); B: PASS (incl. negative-control revert) | 3979164 | `1048 passed in 178.17s (0:02:58)` | P3s (unused imports in daemon/overnight, double load() in set) disposed as cosmetic |
| TB2 | merged | c2/TB (candidate committed in place) | A: PASS (single P3, disposed cosmetic) | fc3d4f3 | `1049 passed in 189.12s (0:03:09)` | also fixed 2 pre-existing NameError crashes in control_plane.py (shutil import, undefined `now` in integration_health) |
| TB3 | merged | c2/TB (candidate committed in place) | A: PASS (P3s only: dead code in tests/_jobs helper, private _load coupling; disposed as cosmetic/within-spec) | ad6c486 | `1054 passed in 139.85s (0:02:19)` | redaction verified live by reviewer (sk- pattern -> [REDACTED:openai_key]) |
| TB4 | merged | c2/TB (candidate committed in place) | A: FAIL round 1 (P2: CLI unknown-job LookupError traceback) -> repaired d1c82bb -> re-review PASS | 81053e8 + d1c82bb | `1062 passed in 131.89s (0:02:11)` | P3s: module-level apply_job_action instead of GhostSDK.job_action (functionally equivalent, disposed); unknown action 404 (reasonable) |
| TB5 | merged | c2/TB (candidate committed in place) | A: PASS (P3s: observe() plain write_text, engage() no lock — disposed cosmetic, pre-existing pattern); B: PASS (P3s: engage-hold ordering cosmetic, last-wins latency display; both with negative controls) | 1e36aad | `1068 passed in 138.64s (0:02:18)` | all 5 tasks merged; no blocked tasks |

Note: implementation done in root track context (GLM-5.3-Flash implementation subagents die on token exhaustion); reviews delegated to independent read-only subagents as required.
