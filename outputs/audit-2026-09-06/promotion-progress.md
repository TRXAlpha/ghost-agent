# Promotion and recovery progress — 6 September 2026

The AI-OS objective remains active and incomplete. This increment follows dispatch hardening and repairs promotion/rollback behavior with actual failure and process-exit tests. Qwen 2.5 Coder 1.5B remains the coding target; no live model benchmark was run here.

## Changes

- Canonical `.ghost/outcomes/<job>/run/sandbox` paths now produce valid previews. Metadata exclusions apply to relative paths, not every ancestor of the sandbox.
- Approval and transaction scope include exact candidate SHA-256 hashes and live pre-action hashes. Candidate edits, destination edits, changed file sets, mismatched approvals, and invented low-level approval IDs are refused before live application.
- Emergency stop is checked before promotion and before each file write. Failure after an earlier write triggers recovery from staged backups.
- Backups are checked and the complete file journal is persisted before the first live write. Each target is replaced atomically and its resulting hash is checked. A process exiting immediately after a write leaves a recoverable journal.
- Rollback preflights all target/backup paths and hashes. Changed user content or damaged backups cause refusal; unchanged before-states make recovery idempotent. Recovery state is persisted before undoing files, and another check runs immediately before each undo.
- Promotion IDs are single-use per approval. A process-level lock serializes Ghost promotion/application/rollback operations and is released by the OS on process death. Incomplete or unrecognized journals block new application until recovery/review.
- `ghost promote list` and the SDK snapshot expose promotion status. CLI rollback updates transaction state; request, successful byte verification, and explicit rollback emit provenance records.
- Approval and transaction writes use atomic replacement. Approval writes/decisions use a process lock, corrupt approvals are preserved rather than replaced with empty state, and stale transaction objects cannot overwrite newer transaction state.
- CLI output explicitly labels promotion verification as file-hash verification, not functional testing.

## Evidence

Eight newly added cases failed against the previous implementation before the promotion fixes. Subsequent tests cover a real child-process exit after a live-file write, successful recovery after restarting the manager, injected second-file write failure, damaged backups, conflicting user edits, single-use approval replay prevention, busy process locks, stale transaction updates, corrupt approval preservation, and failed atomic replacement retaining the original file.

Final full suite: **235 passed in 5.53 seconds** on Windows/Python 3.14.2. `python -m compileall -q ghost_agent tests` exited 0. The real CLI help exposes `request`, `commit`, `rollback`, and `list`.

All promotion/rollback tests operated on disposable fixtures. No user project files were promoted, rolled back, or removed. No packages, accounts, integrations, or services were installed/enabled.

## Still incomplete

- Real OS containment of generated code is still missing. The current host process can access host files; local hashes/locks are not a security boundary against arbitrary code running as the same OS user.
- The process lock coordinates Ghost writers only. Concurrent editors or other programs can race path/hash checks; this is not a filesystem-wide compare-and-swap or multi-file atomic transaction.
- Recovery proves restoration of recorded bytes. Permission/ownership/timestamp metadata, deleted-file propagation, directory cleanup, power-loss guarantees, environment/package snapshots, and a general cross-service undo graph need further work.
- Independently trusted functional acceptance tied to the promoted revision remains unfinished. Hash equality does not prove the user outcome, and self-repair verification still needs stronger evidence.
- Legacy journals are retained and refused for automatic recovery; no unsafe migration or automatic deletion was attempted. Failed staging may retain reviewable backup directories; a fresh request is needed to use a new approval.
- Atomic storage has been adopted for approvals, transactions, and promotion journals, not all persistent stores. Scheduler/worker concurrency and leases, extension integrity, research correctness, resource budgets, real opt-in adapters, and the broader UI remain in the roadmap.

Implementation references: [Python atomic replacement interface](https://docs.python.org/3/library/os.html#os.replace), [Windows byte-range locking](https://docs.python.org/3/library/msvcrt.html#msvcrt.locking), and [POSIX flock](https://docs.python.org/3/library/fcntl.html#fcntl.flock). The tests establish process-interruption behavior, not hardware/power-failure certification.
