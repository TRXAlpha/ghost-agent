# Track TL progress

| ID | status | candidate branch | review verdicts | merge commit | full-suite line | notes |
|----|--------|------------------|-----------------|--------------|-----------------|-------|
| TL1 | merged | hermes-subagent/subagent-sa-0-77370bc5 | A: PASS, B: PASS (after repair; P2 lock finding fixed in repair 2cc16de) | b670ab7 | 1213 passed, 1 deselected in 127.96s | P2 usage-ledger lock + P3 fail-closed ledger fixed by repair worker; 2 pre-existing test_real_agent_* failures are environment-only (main venv Python 3.14), unrelated |
| TL2 | merged | hermes-subagent/subagent-sa-0-1bdaa1db | A: PASS, B: PASS (P3s only: glob-order test fragility, processing-receipt notification semantics — disposed as no-action) | 60369f3 | 1311 passed, 5 skipped, 1 deselected in 172.35s (OpenJarvis venv) | reconcile at serve start after pairing check, before token check — reconcile needs no token |
