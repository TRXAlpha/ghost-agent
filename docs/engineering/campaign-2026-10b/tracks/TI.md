# Track TI: failure learning and benchmark regression tracking (R13, R05 reporting)

Wave 2 track, created 2026-10-10 when TC finished. Audits: `audit/A6.md`
(R13), `audit/A5.md`. Branch `c2/TI`, worktree `C:/Users/chris/Documents/Ghost/ghost_c2_TI`.
Files this track owns: `ghost_agent/trace_learning.py`, `ghost_agent/benchmark.py`,
`ghost_agent/evaluation.py` (TI3 deletes it), `tests/test_evaluation*.py`,
`cli.py` (`bench` and `trace-learning` blocks and new blocks only), and tests.

---

## TI1: failure signatures use the run diagnostics (review: 2)

**Goal:** trace-learning distinguishes *why* jobs fail (truncation, model
unavailable, missing deliverables, failing tests), instead of lumping them all
into `result_iteration_limit`. This is the T10 follow-up.

**Decided design:**

- In `TraceAnalyzer.analyze`, for each failed outcome, also read
  `<outcome dir>/run/state.json` when present (`load_outcomes` knows the
  outcome dir; extend it to attach the parsed state as `_state`, and tolerate
  missing or corrupt state by ignoring it).
- Signature rules, applied in this order; the first match wins:
  1. `state.get("truncations", 0) >= 2` → error_type `model_output_truncated`
  2. `state.get("last_result") == "model_unavailable"` or `state.get("model_failures", 0) >= 2` → `model_unavailable`
  3. `state.get("parse_errors", 0) >= 3` → `model_protocol_errors`
  4. `state.get("limit_reason")` set → `limit_<limit_reason>` (for example `limit_required_files_missing`)
  5. otherwise → today's behavior.
  The subsystem for rules 1–3 is `"model"`; for rule 4 it is `"orchestrator"`.
- `propose_from`: the new types `model_output_truncated`, `model_unavailable`
  and `model_protocol_errors` are proposed as **model/route actions** with the
  text `"consider a larger output budget, a different model, or file-block output"`.
  They are never capability gaps. `limit_*` types follow the existing
  capability-gap path only for `limit_tests_failed` / `limit_focused_tests_failed`.
- Keep `normalize_error_text` etc. unchanged.

**Tests:** fixtures with state.json for each rule produce the expected
error_type; a missing or corrupt state falls back; the model types never
create capability gaps.

---

## TI2: benchmark history and regression comparison

**Goal:** benchmark runs can be compared over time, so improvements and
regressions are visible (R13 "track both improvements and regressions",
R05 "report live task success by model").

**Decided design** (benchmark.py):

- `list_reports(project_root) -> list[dict]`: every
  `.ghost/bench/*/report.json`, newest first (skip unreadable ones with a
  warning entry `{"run_id", "error"}`).
- `compare(report_a, report_b) -> dict`: per goal_id, `{"a": {"tests_passed", "accepted"}, "b": {...}, "change": "improved"|"regressed"|"same"|"new"|"removed"}`.
  `accepted` decides the change (`tests_passed` breaks ties). The summary
  includes both runs' rates, their deltas, and the model names. Comparing
  runs of different models is allowed but flagged `"different_models": true`.
- CLI: `ghost bench history` (table: run_id, model, accepted_rate,
  ghost_pass_rate, goals) and `ghost bench compare <run_a> <run_b>`
  (`latest` and `previous` are accepted as aliases).

**Tests:** list with two fixture reports plus one corrupt one; each compare
change kind; the alias resolution; the different-models flag.

---

## TI3: retire the orphan EvaluationSuite (parallel-ok with TI2)

**Goal:** remove a test-only module that duplicates the protected-evaluation
role now filled by `ghost bench` (hidden tests outside the candidate). Same
rule as campaign 1, T13–T17.

**Decided design:** first prove there is no production caller:
`git grep -n "evaluation" ghost_agent | grep -v "^ghost_agent/evaluation.py"`
(matches inside unrelated words are fine; report what you saw). If anything
in `ghost_agent/` imports `ghost_agent.evaluation` / `.evaluation`, **stop and
report**. Otherwise delete `ghost_agent/evaluation.py`,
`tests/test_evaluation.py` and `tests/test_evaluation_recovery.py`, and grep
the docs (`README.md`, `docs/`) for `EvaluationSuite`, listing the matches in
your report without editing docs.

**Tests:** none new; the full suite passes.
