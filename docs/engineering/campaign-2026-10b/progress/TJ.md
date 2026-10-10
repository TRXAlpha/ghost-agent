# Progress: Track TJ (feasibility records, reuse inventory, resource snapshot)

| ID | status | candidate branch | review verdicts | merge commit | full-suite line | notes |
|----|--------|------------------|-----------------|--------------|-----------------|-------|
| TJ1 | merged | hermes-subagent/subagent-sa-0-663a38d7 (db5030a) | A: PASS (P3 benign lock-window note), B: PASS (P3 report-format note, P3 non-reproducible one-off) | (see git log) | 1225 passed in 126.09s (after master sync) | Both reviewers verified tests fail without the change |
| TJ2 | pending | | | | | |
| TJ3 | pending | | | | | parallel-ok with TJ2 |

Notes:
- First post-TJ1 full suite failed 1 test (tests/test_tf1_tf4_huawei.py::test_purge_all_and_event_privacy) — pre-existing on the branch since divergence: master had the fix (3505342, TF review repair) but c2/TJ had not synced. Fixed by `git merge --no-edit master` before TJ2; not a TJ regression.
