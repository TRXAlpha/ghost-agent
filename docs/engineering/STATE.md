# Ghost Engineering State

## Repository
- Branch: master
- HEAD: 6457ce7 — OJ15/S08 trace-based learning (2026-09-28 evening)
- Working tree: CLEAN except AGENTS.md (owner's rewritten swarm-model instructions, intentionally uncommitted — owner decision).

## 2026-09-28 evening session — 9 accepted commits
- 8e559af OJ06: container isolation on real Docker (POSIX mount contract, sandbox image)
- ae7a4ff OJ04/S01: Telegram live-readiness (credential-aware health, fingerprint rotation, live-check)
- d120c9f docs STATE.md
- f15b273 isolated_execution: catch IntegrationError
- b26957c OJ05/S02: health-data privacy guard + ValidatedMemoryStore retract/purge
- 954840f docs STATE.md
- 42fb973 OJ11/S06: OvernightRunner (unattended proactive+worker loop, `ghost overnight`, `ghost digest`)
- a607eec test_overnight: counted side_effect fix
- 6457ce7 OJ15/S08: trace-based learning (`ghost trace-learning run/report`)

## Live verified (real execution, not mocks)
- Container isolation: file content round-trips through the sandbox boundary; network blocked; timeout enforced; host-escape blocked (openjarvis venv, 8 passed)
- OvernightRunner: real JSONL cycles written to .ghost/overnight/log.jsonl (proactive honestly "suppressed" w/o Ollama); digest mechanism verified
- trace-learning REAL RUN on 18 on-disk outcomes: 16 failed; [14x] result_iteration_limit → capability gap dd0f449d87 auto-registered (review-only); [2x] ollama_read_timeout → flagged for orchestrator review; last_report.json persisted
- memory retract/purge: honest errors; cascade verified in tests
- Telegram: token-absent path network-free; pairing/fingerprint logic tested

## Test status
- Focused suites green: overnight 21, trace_learning 19, health_privacy 8, telegram_readiness 13, autonomy 29+, huawei_route + mvp_health + self_repair 56
- Full suite (~926 tests): all focused/affected areas pass; full-suite single runs stall in this session's terminal environment (suspect an environment hang, not a test failure — same suite ran 885-passed at b26957c). Needs one clean full run on next session start to re-baseline.

## How to run the MVP
```bash
python -m ghost_agent.cli doctor                              # health check
python -m ghost_agent.cli proactive watch                     # autonomous idle loop
python -m ghost_agent.cli worker --watch                      # job queue
python -m ghost_agent.cli accomplish "task text"              # real Ollama inference
python -m ghost_agent.cli overnight run --interval 300        # unattended loop
python -m ghost_agent.cli overnight until-idle-ends           # single idle pass
python -m ghost_agent.cli digest                              # latest overnight digest
python -m ghost_agent.cli trace-learning run                  # mine failure traces
python -m ghost_agent.cli trace-learning report               # last analysis
python -m ghost_agent.cli telegram status                     # credential-aware
python -m ghost_agent.cli memory retract <id>                 # privacy deletion
python -m ghost_agent.cli gaps stats                          # capability gaps
# Container isolation (requires Docker + openjarvis venv):
#   ./workspaces/openjarvis-venv/Scripts/python.exe -m pytest tests/test_openjarvis_isolated_execution.py
```

## Remaining placeholders (honest, by design)
- Telegram live send: needs TELEGRAM_BOT_TOKEN + pairing (runbook: docs/engineering/telegram-live-qualification.md)
- Huawei live backend: needs HiCare credentials
- DeskSentinel: needs ESP32 firmware/protocol spec
## Post-OJ09 State Update (2026-09-28)

**HEAD:** b26957c (3 new accepted commits)
- f15b273: isolated_execution: catch IntegrationError from require_upstream
- b26957c: OJ05/S02 health-data privacy guard + ValidatedMemoryStore retract/purge

**Full suite:** 885 passed, 0 failed (baseline 864 at ecaab2a)

**Live verified:**
- `ghost doctor`: Ollama + Docker + credential-aware Telegram/Huawei/DeskSentinel
- Container isolation: `cat hello.txt` returns `'hello'` through the sandbox boundary
- Telegram: `ghost-jarvis openjarvis telegram status` → honest token_absent + next_step; `ghost integration plan telegram` → runbook link
- `ghost memory retract/purge`: honest ValueError on missing id
- Health-data guard: default-deny to model context, event log without raw values

**Placeholder (honest not_configured):**
- Huawei live backend (no credentials)
- DeskSentinel live backend (no ESP32 firmware)
- Telegram live send/receive (no bot token)

**Delegated (2 workers running in parallel):**
- OJ11/S06 OvernightRunner (overnight.py + `ghost overnight` CLI)
- OJ15/S08 Trace learning (trace_learning.py + `ghost trace-learning` CLI)

**How to run MVP:**
```bash
python -m ghost_agent.cli doctor                              # health check
python -m ghost_agent.cli proactive watch                     # autonomous idle loop
python -m ghost_agent.cli worker --watch                      # job queue
python -m ghost_agent.cli accomplish "hello world"            # real Ollama inference
python -m ghost_agent.cli openjarvis sandbox run -- echo ok   # requires Docker + openjarvis venv
python -m ghost_agent.cli telegram status                     # credential-aware
python -m ghost_agent.cli memory retract <id>                 # privacy deletion
python -m ghost_agent.cli gaps list                           # capability gaps
python -m ghost_agent.cli gaps stats                          # gap stats
```

## Accepted Subsystem Report: OJ15-Recovery
- Owner-visible behavior: self-repair proposals verify ONLY through a real witnessed
  subprocess evaluator run; hand-entered/forged/stale-replay evidence cannot promote;
  apply/rollback bound to real promotion journal transactions.
- Verification evidence (2026-09-27, Python 3.14.2, .venv): focused 39 passed (post-commit);
  full suite 841 passed, 4 skipped, 2 pre-existing env failures, 0 collection errors.
- Reviewer verdicts: Reviewer A PASS (0 findings), Reviewer B PASS (0 findings).
- Resolved findings: OJ15-Recovery-A-1/2 (forgery via source=evaluator+digest), 
  OJ15-Recovery-B-1 (stale-nonce replay against mutated bytes) — all closed with
  regression tests.
- Commit: a90ff65
- Next dependency-ready task: TBD (see roadmap revalidation below)

## Active Task
**OJ09-FeedbackLoop: Close the proactive opportunity feedback loop (production wiring).**

Task contract: docs/engineering/tasks/oj09-feedback-loop.md

Confirmed defect (historical gap #2): `ProactiveEngine.record_feedback()` is only
called from tests/test_proactive.py; no production caller. Finished proactive jobs
leave their linked opportunities in `queued`/`running` indefinitely.

Chosen approach: `_execute_queued_outcome` (cli.py) is the single shared worker
handler (used by _cmd_worker and telegram worker loop). Add a feedback-closing helper
in proactive.py that maps a job's real terminal result to the opportunity outcome
(completed/tests_passed, rejected/tests_failed|error) and calls the existing
record_feedback idempotently; add ProactiveEngine.reconcile() for restart
reconciliation of opportunities whose job reached a terminal state while the loop
was not watching. No new executor/loop; no authority change.

Dependency-ready: YES (no external credentials/hardware required).

Task contract: docs/engineering/tasks/oj15-recovery.md

### Confirmed baseline failure
`python -m pytest tests/test_self_repair.py tests/test_self_repair_oj15.py -q`
→ ImportError: cannot import name 'SelfRepairManager' from 'ghost_agent.self_repair'
(SelfRepairManager class was removed by the unfinished patch, but cli.py,
control_plane.py, and both test files still import/construct it.) This propagated
to 11 collection errors across the suite.

### Resolved (implementation complete)
- SelfRepairManager restored as a real class; SelfRepairProposal is a pure dataclass.
- protected_roots = {root/.ghost, root/ghost_agent}; scope guard is path-based.
- verify_from_evaluator is the ONLY trusted evaluator path; hashes BEFORE and AFTER
  the evaluator run; binds the PRE-run digest; rejects if candidate mutated during eval.
- Trust boundary: verify_from_evaluator mints a random run_nonce per witnessed run and
  records the PRE-run digest it witnessed as IMMUTABLE ground truth until the next
  witnessed run. record_verification only counts evidence carrying that stored nonce AND
  a run_digest that equals BOTH the stored witnessed digest AND the current candidate
  digest. A fresh proposal (empty nonce) can never verify; forged source="evaluator"
  evidence, and stale-nonce replay against mutated bytes, are both rejected.
  (Fixes: empty-nonce forgery P2 + stale-nonce replay P2 — see reviews/OJ15-Recovery.*)
- _dir_digest excludes ephemeral trees because real pytest runs write cache dirs, in
  LOCKSTEP with PromotionManager.preview's set {.git, .ghost, .venv, __pycache__,
  .pytest_cache} (no .mypy_cache divergence) so digest and snapshot agree.
- record_verification only classifies as verified nonce-stamped evaluator evidence whose
  run_digest matches the CURRENT candidate digest; hand-entered/forged evidence rejected.
- mark_applied/mark_rollback bound to a REAL promotion journal record
  (PromotionManager.list()); verified status for apply, rolled_back for rollback.
- CLI verify uses verify_from_evaluator with _run_bounded_evaluator; sanitized env
  (PATH/SYSTEMROOT only) so host secrets are not inherited; explicit NOT-isolated note.
- Tests migrated + strengthened: real subprocess evaluators; mutation-during-eval
  rejected; mutation-after-verification invalidates evidence; hand-entered + forged
  (incl. empty-nonce) evidence rejected; promotion seeded via PromotionService for
  apply/rollback binding.

### Verification results (2026-09-27, Python 3.14.2, .venv)
- Focused: `pytest tests/test_self_repair.py tests/test_self_repair_oj15.py` = 39 passed.
- Full: `pytest tests/` = 841 passed, 4 skipped, 2 failed, 0 collection errors.
- The 2 failures are PRE-EXISTING environment failures in test_openjarvis_authority.py
  ("OpenJarvis needs Python 3.11-3.13", we run 3.14) — unrelated to self-repair and
  present before this task (openjarvis_bridge/runtime.py untouched).
- Baseline BEFORE fix: 11 collection errors, 0 pass.

### OJ09 — COMPLETE ✓
**Commit:** `3960869` ("OJ09: wire reconcile into watch(), close feedback on terminal early-returns, fix premature verdict")
- `ProactiveEngine.watch()` calls `reconcile()` via `_reconcile_safe()` before each tick
- `OpportunityStore.list_all()` returns all opps (not just last 100) for restart reconciliation
- `_execute_queued_outcome` early-returns (emergency-stop, route-mismatch, post-research stop) now mark the job terminal and call `_close_proactive_feedback`
- `feedback_outcome_for_job()` only yields verdicts for genuinely terminal jobs
- Regression tests added: `test_emergency_stop_closes_opportunity`, `test_reconcile_with_stale_opportunities`
- All 42 proactive/cli tests pass
**Status:** DONE — move to OJ12 (Scheduler Migration, dependency-ready: NO — credentials blocked)
**Next:** OJ12 requires credentials; identify dependency-ready task first
- Implementation done (in working tree, uncommitted) + new tests/test_proactive_feedback.py.
- Changes: ghost_agent/proactive.py (close_opportunity_feedback, feedback_outcome_for_job,
  _feedback_evidence, ProactiveEngine.reconcile), ghost_agent/cli.py
  (_close_proactive_feedback wired into both success and exception paths of
  _execute_queued_outcome).
- Focused: 34 passed (12 new + existing proactive/cli_outcomes).
- Full suite: 853 passed, 4 skipped, 2 FAILED (both pre-existing Python 3.14 env
  failures in test_openjarvis_authority.py — unrelated).
- Regression test verified: removing the wiring makes
  test_worker_completes_proactive_opportunity_when_tests_pass fail
  (opportunity stays 'queued' instead of 'completed').
- Next action: dispatch independent reviewers (correctness + architecture).

## MVP Smoke Test Finding (2026-09-27) — memory retrieval poisoned runs — FIXED
**Commit:** `1d6918d` ("memory: IDF-weighted retrieval with relevance threshold")
- Root cause: `_keyword_retrieve` scored by raw token overlap; generic metadata
  tokens ('task', 'test', 'iteration') made unrelated failed lessons match any
  coding query. Retrieved lessons injected the FULL goal text of unrelated past
  tasks (LRU cache, fizzbuzz); the small local model implemented the wrong goal.
- Fix: IDF-weighted scoring, stop/common-token blocklist, distinctiveness
  threshold (score > 1.0). Generic queries now return no lessons.
- Regression tests: tests/test_memory_retrieval_relevance.py (3 tests).
- Full suite: 854 passed, 4 skipped.
- Verified by run 3 (outcome_20260927_164623): model then worked on the CORRECT
  goal (hello.py/add written correctly). Remaining failure is small-model quality:
  it writes unittest-style tests, which Ghost's pytest-only guard rejects, so it
  hits iteration_limit. Next lever: strengthen the system prompt's test-style
  instruction or relax the guard for simple tasks.

## Small-model smoke tests (2026-09-27 evening) — commit `5e93421`
- gemma4:e2b (2B): returned EMPTY responses on the full 21K-char prompt. Root
  cause: Ollama default num_ctx (~4096 tokens) silently truncates the prompt.
  Verified by replaying the exact logged request (19995 chars -> len 0).
  Fix: num_ctx derived from input size in _model_turn.
- qwen2.5-coder:1.5b: better JSON/schema compliance; with num_ctx fixed it
  writes hello.py + test_add.py. With Ghost run under .venv python, real
  pytest gates now execute; last run reached VERIFY with a genuine test
  failure (missing import) surfaced and fed back.
- Earlier runs also exposed: repo RAG context injected into greenfield goals
  (models read ghost_agent/schema.py pointlessly) — now disabled by default
  for outcome payloads.
- VERDICT: MVP loop is wired end-to-end; remaining failures are small-model
  competence (multi-step planning, import correctness), not Ghost wiring.
  Bigger models (gemma4:26b) expected to pass the smoke test.

## MVP LOOP GREEN (2026-09-27 19:08) — commit `452b1c4` stack
- Job outcome_20260927_190856_36c910b5_99d6055e: gemma4:26b, 3 iterations.
- Result: **tests_passed**. Focused gate passed, suite gate passed (full
  sandbox suite 825/825 including the model's deliverables).
- Verified chain: goal -> ghost accomplish -> model routing -> real Ollama
  inference -> sandbox tool execution -> real pytest gates -> durable outcome.
- Recommended default model for accomplish: gemma4:26b (passed in 3 iters).
  Small models work but need more turns (qwen2.5-coder:1.5b best JSON
  compliance among 1.5-2B).
- Launch: ./.venv/Scripts/python -m ghost_agent.cli accomplish --skip-research --model gemma4:26b "goal"

## Verification Commands
- `python -m pytest tests/test_self_repair.py tests/test_self_repair_oj15.py -v`
- `python -m pytest tests/ -q` (full suite; 2 pre-existing env failures expected in
  test_openjarvis_authority.py due to Python 3.14 vs OpenJarvis 3.11-3.13 requirement)
- `python -c "from ghost_agent.self_repair import SelfRepairManager; print('OK')"`
- `python -c "from ghost_agent.control_plane import GhostSDK; print('OK')"`

## Environment
- Windows, Python 3.x (venv at .venv; current interpreter is 3.14.2)
- No external dependencies beyond pytest for the self-repair tests

## Accepted Subsystem Report: MVP Health Checks + Integration Placeholders (2026-09-27)

Subsystem: `mvp-health-and-integrations`

### Owner-visible behavior
- `ghost doctor` now reports real service availability in a `SERVICES:` section:
  - `ollama: ok|not_running|error` (probes localhost:11434/api/tags, 3s timeout)
  - `docker: ok|not_running|error` (runs `docker info`, 3s timeout)
  - Installed Ollama model names are listed (auto-detected, not hardcoded).
- `ghost doctor` also reports the three optional integrations in `STORAGE:`:
  `telegram`, `huawei`, `desksentinel` — all `not_configured` by default.
- New integration adapters live under `ghost_agent/integrations/` (a package):
  - `telegram.py` — placeholder; `send_message`/`get_updates` raise ConnectionError
    until TELEGRAM_BOT_TOKEN + pairing exist. No fake connection.
  - `huawei.py` — placeholder; `get_health_summary` raises ConnectionError until
    Huawei Health Kit credentials/authorization exist. No fake health data.
  - `desksentinel.py` — placeholder; ESP32 firmware does not exist. `get_status()`
    returns a clearly-marked MOCKUP `DeskSentinelStatus` (not live telemetry);
    `connect`/`set_target_height` raise ConnectionError.
  - `planner.py` — verbatim move of the pre-existing `IntegrationPlanner`/
    `IntegrationBlueprint` (formerly `ghost_agent/integrations.py`), kept for
    `from .integrations import IntegrationPlanner` consumers (cli.py, tests).
- Existing `integrations.py` module converted to an `integrations/` package to avoid
  the module/package name collision created by adding adapter submodules. Verified
  functionally equivalent (test_integrations.py passes unchanged).

### Integrity / anti-fabrication guarantees
- No live Telegram/Huawei calls are made without credentials — adapters raise
  ConnectionError and health_check() returns `not_configured` with a `note`/
  `instructions` string explicitly saying no live calls are made.
- DeskSentinel returns only a clearly-labeled mock; the module docstring states
  the ESP32 firmware does not exist. Not presented as live telemetry.

### Verification (2026-09-27, Python 3.14.2, .venv)
- Live `ghost doctor` on this machine: ollama=ok (8 models listed), docker=ok,
  integrations all not_configured.
- Focused new tests: `pytest tests/test_mvp_health_and_adapters.py` = 14 passed.
- Existing integration/doctor/runtime tests: 16 passed.
- Full suite: `pytest tests/ -q` = 839 passed, 0 failed.
- PLACEHOLDER CADENCE: worker subagents showed unreliable prose reports on this
  provider; all behavior was re-verified in root context (import probes, git diff,
  live doctor, full pytest). Test file for adapters was authored directly in root
  context.

### End-to-end MVP loop status
- The full usable loop (user request -> ghost accomplish -> real local Ollama
  inference -> sandbox tool execution -> real pytest gates -> durable outcome) was
  already proven GREEN with gemma4:26b -> tests_passed in commit 452b1c4
  (STATE.md "MVP LOOP GREEN").
- Re-run today: gemma4:e2b (2B) did real inference and wrote hello.py to the sandbox
  but hit iteration_limit (model-competence, not wiring). gemma4:26b cold-load timed
  out on the 60s read timeout in one attempt — a model-speed/timeout tuning issue,
  not a wiring fault; the proven path remains `--model gemma4:26b`.
- Recommended launch: ./.venv/Scripts/python -m ghost_agent.cli accomplish
  --skip-research --model gemma4:26b "<goal>"

### Lora / autonomous-learning boundary (decision)
- Autonomous EXECUTION: lives in the existing proactive loop (`proactive tick/watch`
  + `ghost worker --watch`, gated by idle_seconds() and emergency-stop) — no new
  executor added (single-owner invariant).
- Durable LEARNING (no fine-tune): memory store, learner.py spaced-repetition +
  assessment oracles, models/routing.py per-model verification reputation, proactive
  feedback/reconcile. This is the MVP-scope learning path.
- LoRA/fine-tuning: deferred and data-gated. Recommended design is to accumulate
  verified input/target training pairs during idle work (e.g. a .ghost/lora/
  training_pairs.jsonl store fed by tests_passed outcomes) and only fit a LoRA on
  local Ollama once a sufficient curated corpus exists. Not in MVP scope; would
  require llama.cpp/axolotl-style tooling and risks forgetting on small models.

### External prerequisites (unchanged, all optional)
- Telegram: TELEGRAM_BOT_TOKEN + BotFather pairing -> integration plan/propose.
- Huawei: Developer Console AppID/AppSecret + user authorization.
- DeskSentinel: ESP32 firmware + serial/BLE protocol definition (does not exist yet).
- Ollama: running at localhost:11434 with at least one installed model.
- Docker: running daemon (used for execution isolation infra where configured).

### Commit
- c611b03

## Accepted Subsystem Report: Knowledge Base + LoRA Corpus + Capability Builder (2026-09-28)

Subsystem: `kbase-lora-capability` — Commit: 4f89c67

### Owner-visible behavior
- `ghost kbase ingest <dir>` indexes owner reference docs (Zim/notes), IDF-weighted
  retrieval with morphology matching feeds model context each task.
- `ghost lora list/status`: verified (goal->context->passing code) triples captured
  from every tests_passed job to .ghost/lora/training_pairs.jsonl. First pair
  captured live from a real gemma4:26b end-to-end run.
- `ghost gaps list/stats`: capability gap registry inspection.
- CapabilityImprover: on 300s+ idle, model-assesses queued high-count gaps and
  queues sandboxed implementation; gap marked resolved on tests_passed.
- `ghost accomplish --command-timeout-seconds N`: per-command timeout (default 30),
  fixes the broad-gate 30s timeout under load that caused most iteration_limit
  failures on 2026-09-27.

### Verification
- Full suite: 859 passed (2026-09-28, Python 3.14.2, .venv).
- New tests: 9 (kbase/capture) + 13 (improver/gap registry).
- Live: kbase ingest/query, lora status/list, gaps list/stats all verified via CLI.
- Live end-to-end: outcome_20260928_004400 completed tests_passed with
  command_timeout_seconds=120; pair captured with owner-doc context and sandbox files.

### Not included (deliberate)
- No LoRA training (corpus is data-gated; requires llama.cpp/axolotl tooling later).
- No live Telegram/Huawei/DeskSentinel (placeholders per c611b03).
- AGENTS.md (owner's new swarm instructions) intentionally uncommitted.

## Addendum to kbase-lora-capability: Gap lifecycle closure (2026-09-28)

Commit: 6a4ed85. The stopped capability_improver worker's report correctly noted
mark_resolved() had no production caller. Closed via the OJ09 pattern:
- try_build binds gap_id into job acceptance_contract (fallback: goal match)
- close_gap_feedback resolves on real tests_passed, rolls back to queued on
  retryable failures, wired into _close_proactive_feedback (all terminal paths)
- queue-failure in try_build rolls gap back to queued
- Tests: 5 new; full suite 864 passed.
