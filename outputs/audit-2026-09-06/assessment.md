# Ghost implementation audit — 6 September 2026

Historical audit snapshot. See [subsequent hardening progress](hardening-progress.md) for repairs and the latest verification results; the findings below describe the code at the time of this audit.

Ghost has a substantial, working local-agent foundation. Its intended separation between model proposals and kernel authority is sound, but the implementation does not yet enforce that separation comprehensively. It is a development prototype with reproducible safety and workflow defects, not a finished AI OS or a demonstrated autonomous researcher.

This assessment compares `implementation.md`, the actual modules, CLI wiring, and tests. Passing the current tests establishes a useful baseline; it does not certify the roadmap's safety or reliability claims.

## Verification and the interruption fix

The original suite contains 162 collected cases. Before changing production code, excluding `test_process_perception_records_current_process` yielded **161 passed, 1 deselected** in 5.06 seconds. The earlier report of 164 collected cases was inaccurate.

The process adapter used `os.kill(pid, 0)` on every platform. In Windows CPython, zero is `CTRL_C_EVENT`; `os.kill` calls `GenerateConsoleCtrlEvent` for it. This is not the POSIX existence check. The test probes its own PID, making this a concrete interruption trigger that matches the earlier repeated KeyboardInterrupts. Earlier statements attributing these interruptions definitively to Codex's command bridge were not justified. This finding does not establish the cause of every unrelated desktop-app crash.

The adapter now uses `OpenProcess(SYNCHRONIZE)` and a zero-timeout `WaitForSingleObject`, closing the handle in `finally`. It does not request termination rights or send console events. Access denial/query failure produces `alive: null` with zero confidence rather than incorrectly declaring a process dead. It also rejects invalid/truncating PID inputs.

The revised current-process test intercepts any Windows `os.kill` call before it can deliver a signal. It failed against the old implementation and passed after the fix. Nine additional cases cover a live child remaining alive, an exited child with exit code 259, invalid PIDs, access denial, absent processes, and handle cleanup on query failure.

Observed final results on Windows / Python 3.14.2:

- `python -m pytest -q`: **171 passed in 3.03 seconds**, no deselections or interruptions.
- `python -m compileall -q ghost_agent tests`: exit code 0.
- `python -m pip check`: no broken requirements.

The platform behavior is documented in [Python's os.kill reference](https://docs.python.org/3/library/os.html#os.kill) and [CPython 3.14.2's implementation](https://github.com/python/cpython/blob/v3.14.2/Modules/posixmodule.c#L8993). The replacement follows Microsoft's [OpenProcess](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-openprocess) and [WaitForSingleObject](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject) contracts.

## Completion matrix

Status refers to the full requirement in `implementation.md`, not simply whether a class with the corresponding name exists.

| Roadmap layer | Working implementation | Remaining work / correctness limits | Assessment |
| --- | --- | --- | --- |
| 1. Identity, goals, intent | Profiles, policies/prohibitions, hierarchical goals, priorities, dependency eligibility, deadlines/stale state, intent and plan records. | Goal-specific acceptance criteria are largely a generic template. Clarification and authorized-action fields are not a complete interactive authorization workflow. Prohibitions use keyword matching. | Partial; useful persistence and basic policy behavior. |
| 2. Capability kernel | Registered manifests, required-input checking, write target containment, approval-needed decisions; typed schema for the six model tools. | Manifest scopes, resource limits, secrets and health metadata are not comprehensively enforced. Arbitrary Python execution has host-user permissions; Windows command chaining bypasses the executable check. Automatic verification and research have additional paths outside the model-tool gate. | Partial; not an OS security boundary. |
| 3. Transactions, promotion, undo | Persisted approval records, transaction states, changed-file preview, matching approval ID/file-list checks, per-file backups, manual rollback. | Normal outcome sandboxes cannot be previewed. Approved bytes and destination preconditions are not pinned; verification is only a state transition. No crash-safe per-file journal/automatic rollback, delete propagation, or general undo graph. | Implemented skeleton with critical defects. |
| 4. World model and perception | Sourced, timestamped observations; freshness queries; filesystem/Git observations; Windows process observation fixed in this audit. | No continuous event subscriptions, service/browser/message/scheduled-event adapters, or broad environment sensing. EventBus is append/read storage rather than a complete subscriber/dispatch system. | Basic local observations work. |
| 5. Model orchestration | Classifier, reputation storage/scoring, candidate selection and planner/primary/critic/synthesizer/verifier role labels. Separate coding/research configuration. | Recording role selections does not run a complete multi-role workflow. Most execution uses the selected primary. Research success uses report-text heuristics; no validated benchmark corpus/routing calibration was found. | Routing foundation, unproven quality. |
| 6. Context and memory | Typed, expiring, retractable memory; validated-memory filtering; context containing goals, policies, observations, events and prior failures; code index and legacy memory/knowledge stores. | Hard context-size guarantees, relevance ranking, evidence-bound validation and automatic propagation of retractions remain incomplete. The new store coexists with older learning paths. | Partial. |
| 7. Research and verification | OpenAlex abstract retrieval, DuckDuckGo discovery fallback, HTML extraction, exact-quote checks, topic gates, entailment/contradiction handling, deterministic reporting, evidence gaps and provenance graph. | No PDF extraction/citation traversal or domain evaluation set. Source-host classification is spoofable. All OpenAlex records share a domain tag that collides with a two-per-domain cap. Independent factuality and coverage benchmarks remain absent. | Working conservative pipeline with quality defects; no evidence of expert-level reliability. |
| 8. Autonomous delivery | Copied project workspaces, plan/implement/verify/repair loop, iteration limits, focused pytest gate, outcome artifacts and failure follow-ups. | Separate artifact/sandbox paths produce false `plan_missing`. Verifiers are Python-oriented and can be too weak (compile-only or trivial model-written tests). Dependencies/processes are not OS-isolated. Self-repair stores supplied verification rather than proving it. | Real workflow, incomplete acceptance and execution guarantees. |
| 9. Extensions, MCP, integrations | Lifecycle records, package scaffold, approval requests, secret environment references, Telegram setup blueprint, non-executable MCP server scaffold. | `extension test` only changes status; scaffold test is `assert True`. Permission changes after approval are accepted. Package content/signatures, real health/revoke logic, adapter loading and Telegram transport remain unfinished. | Scaffolding and bookkeeping; no working external integration. |
| 10. Runtime, resources, security | Persistent queue/statuses, one-job worker/poll loop, dependency checks, notification priorities/acknowledgements/quiet-hour filtering, emergency-stop marker, simple doctor/hash/secret-pattern checks. | Non-atomic queue claiming and JSON updates; no leases, robust crash recovery or enforced token/time/GPU budgets. Foreground accomplish can strand another job. Unknown resources and some corrupt policy state fail open. Stop is not immediate cancellation and is bypassed by automatic verification. Doctor mainly checks files/directories. | Partial; not ready for unattended reliability claims. |
| 11. Devices and physical interfaces | Declared devices/capabilities/constraints and matching selection. | No remote execution, secure device enrollment, hardware commands, camera/microphone/desktop/browser control, or enforcement of device freshness in selection. | Registry only. |
| 12. API, SDK, control plane | SDK, loopback binding check, optional bearer authentication, snapshot plus goal/observation writes, small dashboard using textContent. | UI lacks job/artifact/research/memory/model/extension/transaction controls; token-enabled API cannot be read by the dashboard because fetch supplies no token. Input/body limits, local-browser request protections and broader API coverage need work. | Small local API/view, not a full control application. |

## Reproduced defects requiring priority fixes

The companion [reproduce_findings.py](reproduce_findings.py) exercises these conditions in automatically cleaned temporary fixtures. It uses scripted model responses, synthetic approvals, mocked command dispatch for the emergency-stop check, and one harmless Windows `echo` command. It does not run a real research/coding benchmark, change the project's existing `.ghost` state, enable a real extension, or promote user files. A probe reporting a bypass is evidence of a defect, not a passing safety test.

Run it from the project with:

```powershell
.\.venv\Scripts\python.exe outputs/audit-2026-09-06/reproduce_findings.py
```

1. **Windows command allowlist bypass — critical.** `tools/cmd_tools.py:40` validates only the initial executable, then uses `shell=True`. `python -V & echo GHOST_AUDIT_CHAIN` executes both commands. Also, accepting arbitrary Python, pip and Git arguments while constraining only `cwd` does not restrict filesystem/network/process side effects. Eliminating shell chaining is necessary but insufficient: generated code and tests need a real containment strategy with controlled credentials/environment and network access.

2. **Automatic verification bypasses stop/policy/kernel checks — high.** `orchestrator.py:643` calls `cmd_tools.run_cmd` directly. With emergency stop engaged in a fixture, the mocked command is still reached. This path also does not attach the generic tool provenance recorded by `_execute_actions`. Route verification through the same enforced boundary and add cancellation for already-running work; a dispatch flag alone cannot halt active processes.

3. **Documented promotion path is rejected — high.** `promotion.py:25` excludes a file whenever `.ghost` appears anywhere in its absolute path. Every file under `.ghost/outcomes/<job>/run/sandbox` therefore disappears from the preview. A fixture containing changed `app.py` returned `[]` and `Sandbox has no promotable changes`. Apply exclusions to paths relative to the candidate sandbox and test the actual artifact layout.

4. **Promotion accepts changed content and claims verification without a check — high.** `PromotionService.request` binds approval to file names, not file bytes or the live project's original state. The fixture approves `value = 1`, changes the candidate to `value = 2`, then successfully promotes the changed version. `commit` immediately sets transaction state `verified`. Pin approved content and destination hashes, require recorded verification tied to those bytes, recheck before writing, and journal each operation before it occurs so partial writes can recover. Rollback must validate paths and avoid silently overwriting later user edits.

5. **Extension testing and approval are insufficient — high before real loading is added.** `extensions.py:125` marks an extension tested without executing tests. `enable` checks approval capability/name but not approved permissions, side effects or package content. The fixture requests/approves network-disabled permissions, changes network to true, and reaches enabled. Bind approval and test evidence to a package digest and immutable scopes; invalidate approval on any change. Re-running `scaffold` also overwrites scaffold files, including adapter/test files, and should preserve user implementations.

6. **Self-repair can be verified with zero evidence — high.** `self_repair.py:66` uses `all(...)` over concatenated result lists. `record_verification(id, [], [])` yields `verified`. The CLI `verify --passed` repeats the same user assertion as both regression and benchmark evidence. Require nonempty independent checks, capture commands/results through a trusted runner, bind them to the candidate digest and invalidate on edits. User attestation should be labeled separately from executed verification.

7. **Valid plans are rejected in the outcome execution path — high.** `run_task_with_roots` separates artifacts from sandbox, while `_run_loop` expects `artifacts_root/plan.md` and the model writes `sandbox/plan.md`. A correct scripted plan is recorded as `plan_missing`. The older `run_task` test uses one directory and misses this. Use one explicit plan location and test the real accomplish/worker path without replacing its orchestrator.

8. **Foreground accomplish can strand an older queued job — high for worker reliability.** `cli.py:2365` claims the next global job, then returns if it is not the newly created job. A fixture with a higher-priority existing job leaves that older job `running` without executing it. Claim the intended job atomically or dispatch through the worker, then add leases/recovery and concurrent-claim tests. The current load/select/save pattern also permits two workers to claim the same job.

9. **A look-alike source host receives scholarly credibility — medium.** `tools/web_tools.py:66` uses substring membership. `https://nature.com.untrusted.invalid/article` receives 0.85. Validate exact hostnames or proper subdomain suffixes and reassess redirected sources. Add adversarial host and source-independence cases.

## Other specific gaps found by source inspection

- **Research diversity:** `academic_search` labels every result `domain: openalex.org`; `_research_directly` permits at most two findings per domain and chooses academic results instead of the general fallback whenever academic results are nonempty. Different papers/publishers can therefore be collapsed into the same two-source allowance. Track index, publisher, paper identity and independent evidence separately.
- **Research scoring:** `cli.py:2384` records success when a report lacks `## Evidence Status`. The no-sources return string also lacks that heading. Report structure is not independent evidence of research success; use structured outcomes and held-out evaluation.
- **Durability:** persistent stores rewrite JSON without locking/atomic replacement. Several readers silently substitute empty data after errors. Corruption of an emergency-stop file is treated as stop inactive. Fail closed for safety state, preserve corrupt files for diagnostics, and add crash/concurrency tests.
- **Acceptance quality:** a written `test_*` file and exit code 0 do not prove that the requested behavior was implemented. Model-authored tests may be trivial or altered to fit broken behavior. Add trusted acceptance fixtures, negative controls and task-specific assertions that the implementation cannot rewrite.
- **Secret handling:** `SecretStore` persists references, which is appropriate. The broader statement that Ghost never stores secrets in `.ghost` is not guaranteed: raw tool inputs/results and model text are logged, search event fields are only truncated, and the command path can read environment variables. Redaction and credential isolation must cover every persistence and execution boundary.
- **Authenticated dashboard:** `dashboard.py:20` sends no bearer header, so enabling `GHOST_API_TOKEN` causes its snapshot request to fail with 401. Authentication needs a usable local UI flow.
- **Dry run:** the preview proposal supplies `path` but omits required `content`; it is denied for missing input, rather than representing a valid authorized execution plan. Current preview data is mostly a generic template, not actual changed files, commands, network targets or costs.

## What to build next, in order

1. Harden command execution, test execution, emergency stop and policy/secret boundaries. Verify denied paths and adverse behavior, not just successful calls.
2. Repair the actual accomplish/worker path: plan location, exact job claiming, deterministic verification and durable terminal states.
3. Make promotion and extension approval refer to immutable, verified content with safe rollback; remove empty/manual-only verification shortcuts from machine-verified states.
4. Add atomic persistence, worker leases/recovery, concurrency limits and enforceable budgets. Exercise restart, partial-write, corruption and cancellation cases.
5. Repair research diversity and source identity, then run repeatable research/coding evaluations with installed models and independent acceptance fixtures. Keep evidence-gap behavior.
6. Build one Telegram adapter after the boundary fixes: scoped recipients, token reference, mocked transport tests, rate limiting, health checks, revoke/disable and explicit enable approval. Device/UI expansion follows verified adapter capability.

Both `gemma4:e4b-Claude` and `qwen2.5-coder:1.5b` are present in the local Ollama inventory. No `evaluations.jsonl` or `models.json` was present at the project's expected new evaluation/reputation paths during this audit; `.ghost/evals` contained an LRU task definition. Installed model names and a task definition are not measured success rates. No live model benchmark was run in this audit. Querying the inventory caused Ollama to start its local server automatically; no model download or update was requested.

## Changes made during this continuation

- Repaired Windows process observation in `ghost_agent/perception.py`.
- Strengthened the existing current-process test and added nine regression cases in `tests/test_perception.py`.
- Added this assessment and its disposable reproduction script under `outputs/audit-2026-09-06/`.

All other findings above remain unresolved. Existing tests pass after the bounded process-observation repair, but several important safety/workflow requirements are not covered by those tests. The project has no Git metadata in this checkout; no commit or Git diff could be supplied.
