# Track TC progress

| ID | status | candidate branch | review verdicts | merge commit | full-suite line | notes |
|---|---|---|---|---|---|---|
| TC1 | merged | hermes-subagent/subagent-sa-0-32240b3c @ c48f744 | A: PASS, B: PASS (P3s only: empty-command provenance in auto mode noted, disposed) | 28fae03 | 1042 passed in 180.20s (0:03:00) | --isolation {auto,container,host}; container refuse / auto host fallback with runner+isolation_unavailable persisted in evidence; docker never called in tests |
| TC2 | merged | hermes-subagent/subagent-sa-0-32ecd66b @ 28512bb | A: PASS (P3s only) | afa3f7b (TC2+TC3 merges) | 1049 passed in 159.53s (0:02:39) | environment_identity helper; environment key in evidence; no pass/fail change |
| TC3 | merged | hermes-subagent/subagent-sa-1-a3a8eb0b @ fc06aaa | A: PASS (P3 only: defer decision skipped if last-failed has any earlier decision; noted) | afa3f7b | 1049 passed in 159.53s (0:02:39) | MAX_FAILURES_PER_SIGNATURE=3, FAILURE_WINDOW=7d; dedupe via list_decisions check |
| TC4 | merged | hermes-subagent/subagent-sa-0-e8677de2 @ bae7a02 | A: PASS (P3s only) | e58a28f (TC4+TC5 merges) | 1060 passed in 132.93s (0:02:12) | improve_sync hook in daemon run_cycle; owner_away only, errors captured, key improve_sync |
| TC5 | merged | hermes-subagent/subagent-sa-1-e484690d @ fdcceb9 + repair subagent-sa-0-62ddf11e @ fb15ddc | A initial: FAIL (P2 missing malformed-policy tests, P3 path normalization); repair; re-review: PASS (both closed) | e58a28f | 1060 passed in 132.93s (0:02:12) | update_policy defaults in code; protected-path refusal on SelfRepairManager.request_promotion; observe_until computed on read |

