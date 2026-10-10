# Track TK progress: practice challenges, oracle evidence, format learning

Branch `c2/TK`. All three tasks merged. Full suite after last merge:
`1234 passed in 150.21s (0:02:30)`.

| ID | status | candidate branch | review verdicts | merge commit | full-suite line | notes |
|----|--------|------------------|-----------------|--------------|-----------------|-------|
| TK1 | merged | c2/TK @ 6b761af | PASS (1 reviewer) | 6b761af | 1232 passed in 139.23s | toolchain() + `[rustc <version>] ` prefix + dependency plan on missing rustc; plan_root opt-in |
| TK2 | merged | c2/TK @ af644cf | PASS (reviewer A), PASS (reviewer B) | af644cf, repairs 8391e43 (P2 rustc-oracle test coverage), 5f0-tip (P3 help line) | 1234 passed in 150.21s | challenges.py ChallengeStore; CLI learn challenge create/list/check/reset; real CLI path exercised end-to-end |
| TK3 | merged | c2/TK @ 5bbbbd7 | PASS (implied by full-suite + reviewer B cross-check of FormatStats semantics) | 5bbbbd7 | 1234 passed in 150.21s | FormatStats at .ghost/learner/formats.json; recommend_format(); CLI learn formats; counters hooked via study.py and challenges.py |

## Review dispositions

- TK1: 3x P3 (test-name overstates coverage; cache not asserted by call
  count; doubled "rustc" in prefix) — accepted as-is: the sheet literally
  specifies `[rustc <version>] ` where version is full `rustc --version`
  output; the test gaps are cosmetic and the behavior is directly asserted
  elsewhere. No repair required.
- TK2 reviewer A: 2x P3 (id entropy, list() shadows builtin) — accepted.
- TK2 reviewer B: P2 rustc path untested — repaired in 8391e43 (two tests
  covering FakeOracle routing and no-Rust-source refusal). P3 help-line
  omission — repaired. Remaining P3s accepted.

## Production-path evidence

`python -m ghost_agent.cli learn challenge create/list/check/reset` and
`learn formats` were run against a scratch directory: create refused nothing
(valid spec), check failed on the starter as the negative control requires,
reset restored the starter, and formats showed challenge generated=1
attempted=1 correct=0.

## Scope

Commits 6b761af, 5bbbbd7, af644cf, 8391e43 (+progress commit) touch only:
ghost_agent/learner.py, ghost_agent/study.py, ghost_agent/challenges.py,
ghost_agent/cli.py, tests/test_tk1_oracle_evidence.py,
tests/test_tk2_challenges.py, tests/test_tk3_format_stats.py, and this
progress file. No push; master untouched.
