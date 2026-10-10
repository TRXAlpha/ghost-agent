# Track TG progress: personal memory controls, document lifecycle, study loop

| ID | status | candidate branch | review verdicts | merge commit | full-suite line | notes |
|----|--------|------------------|-----------------|--------------|-----------------|-------|
| TG1 | merged | hermes-subagent/subagent-sa-0-210c3bba (candidate 241b21e) | A: PASS, B: PASS (P3 only) | 77f705c | `1046 passed in 187.54s (0:03:07)` | P3: retracted records exempt from retention purge (accepted as intended); export uses plain write_text; CLI list omits revisions/last_verified_at fields. |

## Notes
- Master sync before TG1: already up to date (no other tracks merged yet).
- All commits touch only track-owned files (context_manager.py, cli.py, tests/).
