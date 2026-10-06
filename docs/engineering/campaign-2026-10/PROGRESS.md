# Campaign 2026-10 progress log

Orchestrator: kanban task t_71d6c6b0. One row per task; only the orchestrator edits this file.

| Task | Wave | Status | Candidate branch | Reviews | Merge commit | Notes |
|---|---|---|---|---|---|---|
| T01 | 1 | merged | hermes-subagent/subagent-sa-0-83a36a63 | A: PASS | 1a19da1 | review-pass; 1001 passed, 1 deselected post-merge |
| T02 | 1 | merged | hermes-subagent/subagent-sa-1-6c48bc01 | A: PASS | cad28d0 | full suite no-deselect: 1002 passed in 107.80s |
| T03 | 1 | merged | hermes-subagent/subagent-sa-2-140704d8 | A: PASS | 081dd6e | 1003 passed post-merge |
| T04 | 1 | merged | hermes-subagent/subagent-sa-3-f0457260 | A: PASS | 382dd62 | 1004 passed post-merge |
| T05 | 1 | merged | hermes-subagent/subagent-sa-4-87f92f6e | A: PASS | f4e21a1 | docs-only; 1004 passed post-merge |
| T06 | 3 | merged | hermes-subagent/subagent-sa-0-d7cf2ca7 | A: PASS, B: PASS (2xP3 ordering -> repair ed832d8) | (merge) | empty-workspace mode; 1022 passed post-merge |
| T07 | 2 | merged | hermes-subagent/subagent-sa-0-6e94caa1 | A: FAIL(P1) -> repair ac3217f -> PASS | (merge) | budgets 10/600/32; full suite 1012 passed post-merge |
| T08 | 2 | merged | hermes-subagent/subagent-sa-1-baa5dbd1 | A: PASS (2xP3) | 3e4ae9b | recovered+completed by root; model turns only; 1016 passed post-merge |
| T09 | 1 | merged | hermes-subagent/subagent-sa-5-2f2d08cc | A: PASS | bb26c51 | 1019 passed post-merge |
| T10 | 4 | merged | hermes-subagent/subagent-sa-0-0ff132c4 | A: PASS | 73c29d5 | diagnostics written to state.json on iteration-limit hit; 1008 passed post-merge |
| T11 | 5 | pending | | | | |
| T12 | 4 | merged | hermes-subagent/subagent-sa-1-892021d5 | A: PASS, B: PASS (2xP3 stale-base) | e5492bd | daemon rebuilt on OvernightRunner; 1019 passed post-merge |
| T13 | 1 | merged | hermes-subagent/subagent-sa-6-56fce78c | A: PASS | f086130 | 1012 passed post-merge |
| T14 | 2 | merged | hermes-subagent/subagent-sa-2-c71c4fb6 | A: PASS (1xP3) | fcb1cf3 | notifications.py deleted; 1010 passed post-merge |
| T15 | 3 | merged | hermes-subagent/subagent-sa-1-94feacaa | A: PASS (2xP3, docs owned by T19) | 51c14b2 | memory manager+long_term deleted; 1012 passed post-merge |
| T16 | 4 | merged | hermes-subagent/subagent-sa-2-730081ce | A: FAIL(P2 stale-base → rebase) | 770a00f | dead proactive extensions+resource_manager deleted; 1008 passed post-merge |
| T17 | 6 | pending | | | | |
| T18 | 7 | pending | | | | |
| T19 | 8 | pending | | | | |

## Baseline (2026-10-06, master a602677)

```
C:/Users/chris/Documents/Ghost/ghost_agent/.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --deselect tests/test_overnight.py::TestRun::test_max_cycles_stops
995 passed, 1 deselected in 109.65s (0:01:49)
```

Setup: campaign docs committed as daa2e83; PROGRESS.md as 223576a.
