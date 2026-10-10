# Track TI progress: failure learning and benchmark regression tracking

| ID | status | candidate branch | review verdicts | merge commit | full-suite line | notes |
|----|--------|------------------|-----------------|--------------|-----------------|-------|
| TI1 | merged | hermes-subagent/subagent-sa-0-13410522 (1a88411) | A: PASS; B: PASS (review:2) | d638cb0 | 1172 passed, 1 deselected in 154.77s | P3 notes: rule-1 `truncations` key has no in-repo producer yet (follow-up candidate); normalized_error uses result code in diagnostic branch |
| TI2 | merged | hermes-subagent/subagent-sa-0-3131f33f (b4ab059) | PASS | c9327fe | see final line below | P3: newest-first is lexicographic on run dir name (same as existing latest_report); one usage-string line edited in cli.py (trivial) |
| TI3 | merged | hermes-subagent/subagent-sa-1-347e5081 (150a76b) | PASS | cda78b6 | see final line below | No production importer of ghost_agent.evaluation (grep evidence); docs matches for EvaluationSuite listed, docs not edited |

## Final suite (c2/TI at cda78b6)

Command: `"$PY" -m pytest -q -p no:cacheprovider --deselect tests/test_overnight.py::TestRun::test_max_cycles_stops`
Result: `1 failed, 1159 passed, 1 deselected in 134.34s`
The 1 failure is `tests/test_tf1_tf4_huawei.py::test_purge_all_and_event_privacy` — PRE-EXISTING and outside TI's ownership list: verified failing identically at base commit 5b2ca59 (before any TI change) and reproduced on untouched master. Assertion precedence bug in the test itself (line 95, conditional-expression binding). Flagged for the meta-orchestrator / TF track owner; not fixed here per ownership rules.

## Notes

- TI1 (2026-10-10): implemented per sheet (state.json `_state` attach, 4 signature rules in priority order, model/route actions for model types, capability-gap path limited to limit_tests_failed/limit_focused_tests_failed). Two independent reviewers, both VERDICT PASS, no P0-P2. Pre-existing deselected test: tests/test_overnight.py::TestRun::test_max_cycles_stops (campaign baseline).
- TI2 (2026-10-10): list_reports/compare in benchmark.py + bench history/compare CLI (new parser blocks after the report subparser; handlers following the existing pattern). Reviewer PASS, no P0-P2.
- TI3 (2026-10-10): deleted ghost_agent/evaluation.py, tests/test_evaluation.py, tests/test_evaluation_recovery.py after proving no production caller. Reviewer PASS, independently re-ran the import grep.
- P3 follow-up candidate: orchestrator.py does not write `truncations` into run/state.json, so TI1 rule 1 is dormant until a producer exists.
- No commits touched files outside the TI ownership list, AGENTS.md, or .ghost/. Nothing pushed; master untouched.
