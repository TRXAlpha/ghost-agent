# Evaluation and routing-record integrity — 6 September 2026

Evaluation previously converted verdicts with `bool(...)`, so the string `"false"` could increase a model's verified-success count. It now requires an explicit boolean success verdict, validates optional boolean flags, and treats malformed or contradictory verdicts as verification failures. Exceptions also remain failed evaluations.

Evaluation records retain the original case ID, task class, goal, and acceptance contract. The contract is copied before evaluator invocation, so evaluator mutation cannot relabel a protocol measurement as coding evidence. Known secret patterns are redacted recursively from the persisted contract and from detail text. This is best-effort redaction, not universal secret detection.

Reputation writes validate boolean outcomes, finite nonnegative numeric metrics, and nonnegative integer counters. Reads reject malformed identities, missing required aggregates, impossible outcome counts, and invalid metrics. Corrupt files are preserved and surfaced for review rather than silently discarded. Optional legacy metrics receive defaults without discarding prior counts. Writes use atomic replacement under a nonblocking process lock; a concurrent caller must retry if the lock is busy.

Evaluation log appends are also serialized. However, the evaluation log and reputation file are still separate writes, not one atomic transaction; interruption between them requires reconciliation. JSONL tail recovery, exactly-once recording, evaluator registration, and immutable artifact-backed verdicts remain work to do. Strict boolean validation does not establish that an evaluator really ran meaningful tests, nor does it isolate a malicious process able to edit Ghost's files.

Regressions cover truthy strings/numbers, contradictory results, malformed counters, invalid metrics, corrupt-file preservation, busy-lock refusal and retry, legacy metrics, and mutation of the original contract. Existing routing tests remain in the full suite.

Full verification: **400 passed in 11.02 seconds**; compileall completed successfully.

## Recoverable reputation delivery

New evaluation records carry a unique evaluation ID. The complete JSONL log is atomically replaced before updating reputation, avoiding new partial append tails. A receipt digest is committed in the same atomic file replacement as the reputation counters. Replaying the same ID and metrics does not increase counters; changed metrics or model identity under an existing ID are refused.

`EvaluationSuite.reconcile()` replays identified records without invoking evaluators and returns the number newly applied. Each new suite run reconciles prior records first. Legacy results without IDs are preserved but not automatically replayed because their prior delivery cannot be established. Corrupt/truncated logs fail closed for manual review rather than being discarded.

Tests cover failure before reputation commit, failure after commit, conflicting receipt reuse, identical duplicate records, legacy preservation, and corrupt-tail refusal. A real child process exits with code 73 after committing its evaluation log but before reputation delivery; a fresh suite recovers it once and a second reconciliation applies nothing.

This is recoverable delivery for identified evaluation records, not a single transaction spanning both files or exactly-once external execution. Results may temporarily be absent from reputation until reconciliation. Legacy records still need manual reconciliation, receipts/logs grow without compaction, and trusted evaluator registration plus immutable artifact-backed verdicts remain incomplete. Untrusted host processes are not isolated by these files or locks.

Latest full suite: **407 passed in 11.73 seconds**; compileall completed successfully.
