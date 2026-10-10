# Track TD progress

| ID | status | candidate branch | review verdicts | merge commit | full-suite line | notes |
|---|---|---|---|---|---|---|
| TD1 | merged | hermes-subagent/subagent-sa-0-eaa6f95f (c3790d2) | A: PASS, B: PASS (P3s only: stale_goal arm inert until TD2; evidence_items naming deviation accepted by orchestrator) | a06f018 | 1043 passed in 166.25s (0:02:46) | evidence field named evidence_items (existing str evidence kept) |
| TD2 | merged | hermes-subagent/subagent-sa-0-61a76443 (f6800b4) | A: PASS, B: PASS (P3s only: unguarded throttle state RW; predicate duplication; accepted orchestrator decisions 1-3) | 5d44ac3 | 1046 passed in 144.65s (0:02:24) | stale_goals/upcoming_deadlines keys omitted when empty; 6h throttle in .ghost/proactive_state.json |
| TD3 | merged | hermes-subagent/subagent-sa-0-2517dbbf + repair hermes-subagent/subagent-sa-0-f936329a (6fe69d7, 668a4fe) | initial A: FAIL P1 (tests missing), B: FAIL; repair 668a4fe added 5 tests, dropped unused import; re-review: PASS (P3s pre-disposed) | 1059715 | 1051 passed in 159.42s (0:02:39) | CLI less in proactive group (accepted); suppressed proposals become future suppressors (matches sheet) |
| TD4 | merged | hermes-subagent/subagent-sa-0-20504123 (b3d3122) | A: PASS, B: PASS (P3s: overlong question dropped not truncated — accepted; reworded-question dedup edge noted) | 13db5dd | 1054 passed in 145.33s (0:02:25) | dedup by notification_id opp-question-<id> reuses AttentionManager |
| TD5 | merged | hermes-subagent/subagent-sa-0-20504123 (94ac0a0) | A: PASS, B: PASS (P3s: except Exception conflates corrupt-budget with exhausted — fail-safe; until-idle-ends path uncapped per sheet scope) | 9535874 | 1057 passed in 153.55s (0:02:33) | --max-inference-calls default 48 on overnight run + daemon run; daily file .ghost/inference/<date>.json |
| TD6 | merged | hermes-subagent/subagent-sa-0-20504123 (354e9a9) | A: PASS, B: PASS (P3s: empty-outcomes wording "- No completed jobs." accepted; 24h window hardcoded — accepted) | 3f846fa | 1060 passed in 123.28s (0:02:03) | Outcomes reuses MorningDigest collect/render; Coverage gap >3x interval; zero-cycle sentence |

## Final state

All 6 tasks in tracks/TD.md merged on c2/TD. Final full-suite line after last merge (3f846fa):
`1060 passed in 123.28s (0:02:03)`
