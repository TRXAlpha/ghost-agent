# Track TP progress: document ingest adapters, source preference, conflicting context

Branch c2/TP, base 3506122 (wave-2). Suite runner: `ghost_agent/.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider` from the track worktree. The known flaky `tests/test_overnight.py::TestRun::test_max_cycles_stops` was deselected, consistent with sibling tracks.

| ID | status | candidate branch | review verdicts | merge commit | full-suite line | notes |
|----|--------|------------------|-----------------|--------------|-----------------|-------|
| TP1 | merged | hermes-subagent/subagent-sa-0-62b53a42 @ 12d767f | Reviewer A: PASS; Reviewer B: PASS (review: 2 satisfied) | 2c511ea | 1300 passed, 1 deselected in 180.67s | Real pdftotext integration test ran on this host (extracted "Hello"). P3 dispositions: skipped stats implemented as new `skipped` field on KnowledgeBaseStats (additive, default empty); tool-crash-during-run (RuntimeError/OSError) returns 0 without a skipped entry — sheet only required the unavailable-tool case, accepted. |
| TP2 | merged | hermes-subagent/subagent-sa-1-c11506e1 @ 4d28dc8 | Reviewer: PASS (tests proven to fail on base: 2 failed before change) | 171821d | 1300 passed, 1 deselected in 180.67s | official_docs tier at 0.95, placed before all existing tiers incl. tertiary_or_social; consumers verified (web_tools internal, cli.py:753/767, research.py:76) — none closed-set. P3 note: `docs.` prefix rule means any such subdomain scores 0.95 — exactly as the sheet specifies. |
| TP3 | merged | hermes-subagent/subagent-sa-2-c2bd066b @ 4c02510 | Reviewer: PASS (tests proven to fail on base) | 00751fb | 1300 passed, 1 deselected in 180.67s | assemble() has no render surface (dict-only; consumed at cli.py:3368), so per sheet fallback the warning line is payload key `conflict_warnings`. Conflicts/conflict_warnings are first in the drop order and counted in omitted_for_budget. P3 dispositions: ContextSynthesizer ctor mkdirs state_dir (accepted; unwritable root breaks Ghost broadly anyway); conflicts with >2 item_ids expose only first two (matches sheet's {a,b,reason} shape). |

## Summary

- All 3 tasks in tracks/TP.md merged onto c2/TP with reviewer PASS verdicts; zero blocked tasks.
- Merge commits: TP1=2c511ea, TP2=171821d, TP3=00751fb.
- Final full-suite line after last merge (TP3):
  `1300 passed, 1 deselected in 180.67s`
- No commit on c2/TP touches files outside the track ownership list (knowledge_base.py, extractors.py, tools/web_tools.py, context_manager.py, tests). Nothing pushed; master untouched.
- Denied commands: none.
