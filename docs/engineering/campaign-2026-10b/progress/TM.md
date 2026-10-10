# Track TM progress: operational controls in the loopback API and dashboard

| ID | status | candidate branch | review verdicts | merge commit | full-suite line | notes |
|---|---|---|---|---|---|---|
| TM1 | merged | hermes-subagent/subagent-sa-0-e3b43703 (cba76aa) | A: PASS, B: PASS (P3 only: payload-type check order, held-as-list vs count — disposed: 400 still returned, list preserves CLI parity) | merge of cba76aa on c2/TM | 1227 passed in 132.62s | all 10 owner routes, token gating per sheet, shared notifications_overview; full suite quoted from post-merge run |
| TM2 | merged | hermes-subagent/subagent-sa-0-0b07b63b (75c4464) | A: PASS, B: PASS (P3 only: scheduler_jobs duplicates jobs key — sheet-mandated, disposed accepted; prompt() for estop reason) | merge of 75c4464 on c2/TM | 1231 passed in 125.51s | token entry (sessionStorage, bearer), 6 control sections, textContent-only DOM, snapshot scheduler_jobs; negative control verified (3/4 tests fail at base) |
