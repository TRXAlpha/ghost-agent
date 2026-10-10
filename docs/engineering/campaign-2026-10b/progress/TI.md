# Track TI progress: failure learning and benchmark regression tracking

| ID | status | candidate branch | review verdicts | merge commit | full-suite line | notes |
|----|--------|------------------|-----------------|--------------|-----------------|-------|
| TI1 | merged | hermes-subagent/subagent-sa-0-13410522 (1a88411) | A: PASS; B: PASS (review:2) | d638cb0 | 1172 passed, 1 deselected in 154.77s | P3 notes: rule-1 `truncations` key has no in-repo producer yet (follow-up candidate); normalized_error uses result code in diagnostic branch |

## Notes

- TI1 (2026-10-10): implemented per sheet (state.json `_state` attach, 4 signature rules in priority order, model/route actions for model types, capability-gap path limited to limit_tests_failed/limit_focused_tests_failed). Two independent reviewers, both VERDICT PASS, no P0-P2. Pre-existing deselected test: tests/test_overnight.py::TestRun::test_max_cycles_stops (campaign baseline).
- P3 follow-up candidate: orchestrator.py does not write `truncations` into run/state.json, so TI1 rule 1 is dormant until a producer exists.
