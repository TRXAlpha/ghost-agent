# Track TG progress: personal memory controls, document lifecycle, study loop

| ID | status | candidate branch | review verdicts | merge commit | full-suite line | notes |
|----|--------|------------------|-----------------|--------------|-----------------|-------|
| TG1 | merged | hermes-subagent/subagent-sa-0-210c3bba (candidate 241b21e) | A: PASS, B: PASS (P3 only) | 77f705c | `1046 passed in 187.54s (0:03:07)` | P3: retracted records exempt from retention purge (accepted as intended); export uses plain write_text; CLI list omits revisions/last_verified_at fields. |
| TG2 | merged | hermes-subagent/subagent-sa-0-4a59c6dc (candidate 2d81258) | A: PASS, B: PASS (P3 only) | 6c8ab3f | `1051 passed in 142.14s (0:02:22)` | P3: multi-word markdown headings capture only one word; delete of unknown source still emits event; dedup narrowed to per-source (per sheet). |
| TG3 | merged | hermes-subagent/subagent-sa-0-4cfa70dd (candidate 7252543) | A: PASS, B: PASS (P3 only) | b115fa6 | `1057 passed in 173.69s (0:02:53)` | P3: hint counter uses concept-level wrong attempts (sheet unspecified, disposed as acceptable); corrupt answer file raises JSONDecodeError instead of StudyError; count capped 1-10. |
| TG4 | merged | hermes-subagent/subagent-sa-0-d47e1709 (candidate fba8076) | PASS (single reviewer, P3 only) | 7bebe4a | `1064 passed in 164.78s (0:02:44)` | P3: local-midnight due_at semantics unspecified by sheet; disposed as acceptable. |

## Notes
- Master sync before TG1: already up to date (no other tracks merged yet).
- All commits touch only track-owned files (context_manager.py, cli.py, tests/).
