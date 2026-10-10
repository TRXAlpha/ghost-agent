# Track TA progress

| ID | status | candidate branch | review verdicts | merge commit | full-suite line | notes |
|----|--------|------------------|-----------------|--------------|-----------------|-------|
| TA1 | merged | hermes-subagent/subagent-sa-0-6bf8543d (candidate 80649aa, repair a75c06c) | Reviewer A: FAIL→P2 fixed; Reviewer B: PASS with P2; repair fixed P2 | 509530a | 1043 passed in 184.69s (0:03:04) | P2: job.recurrence.skipped missing correlation_id; repaired, re-verified via full suite |
| TA2 | merged | hermes-subagent/subagent-sa-0-b6ac6c89 (e6929b7) | Reviewer: PASS (P3s only: failed-record re-append on repeated tail before first commit — spec-consistent; noted, no action) | merge into c2/TA | 1049 passed in 148.32s (0:02:28) | reviewer exercised CLI live end-to-end |
| TA3 | merged | hermes-subagent/subagent-sa-0-035c79fd (d7be2d4) | Reviewer A: PASS; Reviewer B: PASS (P3s only, disposed as-is: int-accepted lease validation superset, private-API test, redundant clear) | merge into c2/TA | 1055 passed in 153.37s (0:02:33) | both reviewers ran revert-scratch: tests fail without change |
| TA4 | merged | hermes-subagent/subagent-sa-0-a0fe6bf9 (562f9a9) | Reviewer: PASS (P3s only, disposed as-is: unused public json_load helper, stamp returns all versions) | merge into c2/TA | 1061 passed in 142.05s (0:02:22) | reviewer verified CLI wiring and dry-run no-write |
| TA5 | | | | | | |
