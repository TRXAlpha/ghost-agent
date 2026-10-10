# Progress: Track TN (transparent, outcome-tuned opportunity ranking)

| ID | status | candidate branch | review verdicts | merge commit | full-suite line | notes |
|---|---|---|---|---|---|---|
| TN1 | merged | hermes-subagent/subagent-sa-0-484da4f3 (cda013d) | A: PASS; B: PASS | 391c2da | 1233 passed in 142.11s | P3 findings disposed (dead test helper; threshold-pop coupling) — no action |
| TN2 | merged | hermes-subagent/subagent-sa-0-734acc9c (4564d41) | PASS | d415d1c | 1237 passed (worker full suite); track suite after TN3: 1242 passed | tick passes kind_success_rate to score |
| TN3 | merged (root-implemented) | direct commit on c2/TN: 762955e, repair 287c0af | A: FAIL (P1 --json missing) -> repaired 287c0af -> re-review PASS; B: FAIL (same P1) -> same repair | 762955e + 287c0af | 1242 passed in 132.29s | First delegate worker died on output-token exhaustion (zero work); TN3 implemented in root context per fallback. P3s disposed: negative max_active clamps at usage (documented), list(100) tail window shared with reconcile (disposition only) |

