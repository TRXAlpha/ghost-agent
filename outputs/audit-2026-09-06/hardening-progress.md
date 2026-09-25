# Ghost hardening progress — 6 September 2026

This records the first hardening increment. See [promotion progress](promotion-progress.md) for the subsequent transaction work and newer test results.

The complete AI-OS roadmap remains active and unfinished. Qwen 2.5 Coder 1.5B remains the coding-model target. This increment repairs demonstrated workflow and dispatch defects; it does not establish general autonomous reliability or complete the roadmap.

The [original assessment](assessment.md) is a historical snapshot. Its reproduction script intentionally exercised defects and is not a current passing acceptance suite. The production tests now cover the repaired cases directly.

## Implemented and checked in this increment

- Commands use argv lists and `shell=False` on Windows and POSIX. Windows parsing follows `CommandLineToArgvW`; argument round-trip tests exercise spaces, quotes, and backslashes. Python/pytest/ruff use the harness interpreter. An arbitrary executable path cannot gain authorization from an allowlisted basename, batch launchers are refused, and an empty allowlist denies all commands.
- Standalone shell operators and direct pip entry points are rejected before dispatch. Command environment inheritance is limited; arbitrary credentials and Python/Git environment hooks are not inherited. This is dispatch hardening, not containment of arbitrary code.
- Automatic verification goes through the same tool execution function as model proposals, including policy, capability, emergency-stop checks, provenance, and action audit. Missing kernels and unreadable/malformed identity policies fail closed. Invalid emergency-stop state is treated as engaged. The outcome loop stops before further model turns when stop is engaged; already-running subprocess cancellation remains unfinished.
- The outcome workflow checks `sandbox/plan.md`, where the model actually writes it. A passing verification on the last allowed iteration is preserved instead of overwritten with `iteration_limit`. Focused test filenames containing spaces are quoted.
- Artifact/workspace initialization no longer recursively deletes existing directories. Invalid/outside destinations are refused. Task IDs cannot be paths, and resume rejects redirected workspace paths. Snapshot creation rejects links/junctions and excludes conventional environment/private-key files. Existing user artifacts are preserved, not migrated or scrubbed.
- Pattern-based redaction applies at JSON/text logging, provenance, and security-event persistence boundaries, before event truncation. Complete recognized private-key blocks are redacted. Rejected secret writes no longer leak their original input into action/provenance logs. This does not detect every credential or cover every other store.
- Documentation now explicitly distinguishes directory copies from actual OS isolation and no longer describes the current worker as proven reliable overnight execution.

The first added regression batch failed in 22 cases against the previous implementation. Two further negative controls demonstrated inherited environment data and persisted secret leakage before those fixes. Tests use disposable fixtures and scripted model responses; they do not alter the project's live `.ghost` artifacts or run arbitrary model-generated code.

## Verification

Baseline: 171 tests passed; compileall passed. Final results: **214 tests passed in 4.20 seconds**, `python -m compileall -q ghost_agent tests` exited 0, and `python -m pip check` reported no broken requirements. This adds 43 collected regression cases. The repair-loop acceptance test now exercises both legacy `run_task` and the real `run_task_with_roots` directory layout with actual pytest execution and a scripted bad-then-repaired calculator implementation.

These are Windows/Python 3.14.2 results, not a cross-platform certification or a live Qwen benchmark. No dependencies, accounts, integrations, or services were installed or enabled by this increment.

## Remaining roadmap work

| Roadmap layer | Work still required before calling the layer complete |
| --- | --- |
| Identity, goals, intent | Task-specific acceptance, structured policy enforcement, arbitration and interactive clarification. |
| Capability kernel | Real OS execution containment; enforced network/secrets/resource scopes at every adapter; bounded output and cancellation of process trees. |
| Transactions and undo | Fix canonical `.ghost` promotion previews; bind approval to source bytes and live preconditions; verified application, crash-safe journal/recovery, conflict-aware undo. |
| World model and perception | Opt-in continuous subscriptions, services, browser/incoming-message/scheduled-event adapters, fresh-state enforcement. |
| Model orchestration | Execute critic/synthesis/verifier roles where appropriate; benchmark and calibrate routing with installed models. |
| Memory and context | Hard context limits, evidence-bound validation, relevance and retraction propagation across old/new stores. |
| Research | Spoof-resistant source identity, publisher diversity, citation/PDF retrieval, adversarial factuality datasets and independent evaluation. |
| Autonomous delivery | Trusted acceptance checks, broader ecosystems, isolated dependencies, trustworthy nonempty self-repair regression/benchmark evidence. |
| Extensions/integrations | Real tests, immutable scope/content approval, preserved scaffolds, health/revoke/rate limits and an opt-in Telegram transport. |
| Runtime and security | Atomic durable stores/claims, exact foreground job claiming, leases/recovery, pause/resume/cancel semantics, budgets, stronger doctor and anomaly detection. |
| Devices | Secure opt-in enrollment and real, scoped browser/desktop/sensor/device adapters; registration alone grants no control. |
| API and UI | Authenticated dashboard flow, validation and request limits, browser-origin protection, complete resource and approval views. |

Refusing to overwrite existing runs intentionally prevents destructive retries; proper resumable attempts are still needed. Redaction leaves previous logs unchanged. Shell removal and environment filtering cannot stop generated Python from reading host files, making network requests, or changing the live project. Real isolation remains the highest-priority unresolved boundary before live autonomous benchmarks or external adapter execution.

Implementation references: [Python subprocess security considerations](https://docs.python.org/3/library/subprocess.html#security-considerations), [Microsoft CommandLineToArgvW](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-commandlinetoargvw).
