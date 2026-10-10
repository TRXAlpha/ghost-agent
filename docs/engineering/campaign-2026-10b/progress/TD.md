# Track TD progress

| ID | status | candidate branch | review verdicts | merge commit | full-suite line | notes |
|---|---|---|---|---|---|---|
| TD1 | merged | hermes-subagent/subagent-sa-0-eaa6f95f (c3790d2) | A: PASS, B: PASS (P3s only: stale_goal arm inert until TD2; evidence_items naming deviation accepted by orchestrator) | a06f018 | 1043 passed in 166.25s (0:02:46) | evidence field named evidence_items (existing str evidence kept) |
| TD2 | merged | hermes-subagent/subagent-sa-0-61a76443 (f6800b4) | A: PASS, B: PASS (P3s only: unguarded throttle state RW; predicate duplication; accepted orchestrator decisions 1-3) | 5d44ac3 | 1046 passed in 144.65s (0:02:24) | stale_goals/upcoming_deadlines keys omitted when empty; 6h throttle in .ghost/proactive_state.json |
