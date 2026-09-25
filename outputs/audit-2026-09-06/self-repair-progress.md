# Self-repair gate correction — 6 September 2026

`record_verification` now requires nonempty regression and benchmark result lists. Each result must contain an integer zero return code; booleans, floats, strings, missing codes, malformed entries, and failures are rejected. Rejected proposals cannot request promotion through the self-repair manager.

Nine added regression cases exercise these rejection paths and persisted status. Full suite: **275 passed in 7.80 seconds**; compileall completed successfully.

## Subsequent patch binding and persistence

Verification records now include the promotion snapshot: candidate hashes and the corresponding live destination hashes. Promotion request creation compares its own snapshot against that record before creating a transaction or approval. Edited, added, or removed patch files and changed destinations require re-verification. Empty patches cannot become verified. Legacy verified records without hashes also require re-verification.

State writes use atomic replacement and a process lock; proposal creation, verification, and promotion requests share that lock. Unreadable, malformed, or duplicate records fail closed instead of being discarded and overwritten. Added regressions cover changed patches, live edits, legacy records, empty patches, and corrupt state preservation.

Latest full suite: **285 passed in 8.23 seconds**; compileall completed successfully.

These result records are still caller-supplied, not trusted independently executed evidence; the CLI's manual pass assertion still needs replacement. The snapshot binds the promotable change set, not the entire execution environment or unchanged dependencies. Consequently self-repair is not yet suitable for autonomous enablement, and this check must not be represented as proof that a candidate is correct.
