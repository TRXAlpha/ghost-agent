# Track TO progress: owner-return yield, verification classes, policy at dispatch

Branch `c2/TO`. All tasks in tracks/TO.md are merged with reviewer PASS verdicts.

| ID | status | candidate branch / commits | review verdicts | merge commit on c2/TO | full-suite line | notes |
|----|--------|----------------------------|-----------------|----------------------|-----------------|-------|
| TO1 | merged | hermes-subagent/subagent-sa-0-585c0ce9 @ 858e261 | Reviewer A PASS, Reviewer B PASS (P3-only) | 858e261 | 1290 passed in 172.67s (post-TO1) | worker die-on-truncate once (batch 1), re-dispatched and completed; P3 findings (first-turn latency fallback, broad OSError catch) dispositioned, no repair required |
| TO2 | merged | 06fe40e + repair 6cde931 | Reviewer A PASS, Reviewer B PASS; repair verified | 06fe40e, repair 6cde931 | 1297 passed in 130.85s (post-repair) | repairs: declared gates also run when no test commands discovered; OutcomeStore seed ["unit"] instead of prose list |
| TO3 | merged | a47f940 | Reviewer A PASS, Reviewer B PASS (P3-only: double PolicyStore construction — cosmetic, disposed) | a47f940 | see post-integration line below | per-sheet tests: 4 passed |

Post-track integration (operator-directed):
- 02ae72d: merged master (f3e321c "explained pauses no longer stall the queue", Scheduler.mark paused_by) into c2/TO.
- 1d03c47: TO3 policy pause now passes `paused_by="policy"`; `queue_sandbox_outcome` yields only for the unattended sources `proactive` and `self_improver` (benchmark and capability_improver excluded per operator note).

Final full suite on c2/TO after last change:
`1403 passed in 102.49s (0:01:42)` (command: `$PY -m pytest -q -p no:cacheprovider` from C:/Users/chris/Documents/Ghost/ghost_c2_TO)

No blocked tasks. Denied commands: none. Nothing pushed; master untouched by this track (master merge was read-only inbound).
