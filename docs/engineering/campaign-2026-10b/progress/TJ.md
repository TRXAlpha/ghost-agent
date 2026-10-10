# Progress: Track TJ (feasibility records, reuse inventory, resource snapshot)

| ID | status | candidate branch | review verdicts | merge commit | full-suite line | notes |
|----|--------|------------------|-----------------|--------------|-----------------|-------|
| TJ1 | merged | hermes-subagent/subagent-sa-0-663a38d7 (db5030a) | A: PASS (P3 benign lock-window note), B: PASS (P3 report-format note, P3 non-reproducible one-off) | a02ee57 | 1225 passed in 126.09s (after master sync) | Both reviewers verified tests fail without the change |
| TJ2 | merged | c2/TJ2b (a82e7b9) | A: PASS, B: PASS (P3 edge cases only, dispositioned) | adb5d43 | 1240 passed in 139.47s | Worker blocked on missing list APIs; orchestrator resolved design (manifest glob + require_upstream/pairing gate); no production caller of reuse_inventory exists (git grep verified) |
| TJ3 | merged | hermes-subagent/subagent-sa-1-db9bb254 (31d009b) | A: PASS, B: PASS (P3 host-dependent ctypes test, P3 POSIX ram_total_bytes addition — dispositioned) | 0bd6d76 | 1233 passed in 143.59s | Scheduler._fits_budget confirmed refusing budgets whose key is absent from available; not edited. Live doctor: "resources: ram 12.9/33.5 GB free, gpu 1.4/4.3 GB free" |

Notes:
- First post-TJ1 full suite failed 1 test (tests/test_tf1_tf4_huawei.py::test_purge_all_and_event_privacy) — pre-existing on the branch since divergence: master had the fix (3505342, TF review repair) but c2/TJ had not synced. Fixed by `git merge --no-edit master` before TJ2; not a TJ regression.
