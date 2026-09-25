# Extension approval integrity — 6 September 2026

Enable requests now include SHA-256 hashes for the package's files as well as its permissions and side effects. Enablement recomputes and compares the entire scope against the persisted approval. Added, removed, or edited files and changed permissions require a new approval. Legacy name-only approvals no longer authorize enablement. Package/storage paths following links are refused, and emergency stop prevents enablement.

The public transition API no longer allows direct transitions to approved/enabled; callers must use approval-gated enablement. Manifest writes are atomic. Malformed manifests fail closed instead of being treated as missing and overwritten.

Scaffold retries return the existing scaffold rather than overwriting user implementations. For a proposed package with partial/user-created files, scaffolding refuses to overwrite them and asks for review. A new scaffold is marked scaffolded only after its templates have been written.

Regressions cover code edits, added/deleted files, permission changes, direct-transition bypass, repeated scaffolding, partial scaffold preservation, corrupt manifests, and emergency stop.

Full suite: **437 passed in 12.53 seconds**; compileall completed successfully.

Remaining boundaries: manually asserted test status, placeholder scaffold tests, package signing, isolated runtime loading, ongoing enabled-package integrity checks, lifecycle recovery after interruption, and coordinated locking against concurrent writers. Hash comparison before enablement is not an atomic filesystem snapshot or protection against a hostile process sharing the user's filesystem. No real external adapter was installed or enabled by this work.

## Lifecycle commit follow-up

Public extension mutations now share a nonblocking process lock. Internal helpers avoid nested lock acquisition. A caller holding a stale manifest cannot overwrite a newer lifecycle state through the transition API. This coordinates Ghost's own writers, not arbitrary editors or hostile host processes.

Enablement persists the final enabled state and its approval ID in one atomic manifest write. Persisting an intermediate approved state is unnecessary because approval already exists in the approval store. An injected pre-commit write failure leaves the package tested and eligible for retry with its unchanged approval. Disabled packages can request a fresh content-bound approval and re-enable; the old approval no longer matches the changed manifest. Secret references cannot be added while enabled.

Added tests verify one final manifest write, failure/retry, stale-state refusal, busy-lock refusal/retry, fresh approval after disabling, and refusal of active secret-scope changes. This does not implement runtime loading, lifecycle migration for previously stranded approved records, or protection against external package edits between checks.

Latest full verification: **443 passed in 12.94 seconds**; compileall succeeded.
