# Validated-memory integrity — 6 September 2026

Memory creation rejects duplicate IDs, including reuse of an invalidated ID. Invalidation changes validation status to retracted and preserves the first invalidation reason/time on retries. Active-memory queries exclude retracted records even if an older record has no invalidation timestamp. This closes the duplicate-ID case where invalidating one record could leave a conflicting duplicate active.

Writes use atomic replacement under a process lock. Loads validate identities, content/source strings, memory kinds, validation labels, confidence, timestamps, artifact-reference types, and duplicate IDs. Malformed storage fails closed and is preserved rather than treated as an empty store. Legacy duplicate records require explicit review; they are not automatically deleted or merged.

Regression coverage includes duplicate creation/reuse, idempotent invalidation, retracted labels without timestamps, invalid values, corrupt storage, legacy duplicates, and busy-lock refusal/retry.

Full verification: **477 passed in 14.80 seconds**; compileall succeeded.

Remaining boundaries: memory validation is still a caller-supplied label, not proof of evidence quality. Supporting artifact changes do not automatically retract dependent memories or derived claims. Stores are read fully, and legacy memory/index systems remain separate. These changes do not establish a complete memory dependency graph, bounded storage, or protection against a hostile process with filesystem access.

## Explicit dependency follow-up

Memory records now accept `depends_on`, a list of existing active memory IDs. Dependencies are append-only and must precede their dependents, preventing cycles and forward/missing references. Duplicate dependency IDs are rejected. A validated dependent requires validated active sources; a caller cannot use an unvalidated parent to establish a validated descendant through this API.

Invalidating a source retracts its transitive descendants in the same atomic store replacement. Existing invalidation reasons are preserved. Source expiry also excludes descendants from active views without rewriting history. Independent memories remain unaffected. Legacy records without dependencies remain compatible.

Tests cover transitive retraction, expiry, missing/self/duplicate dependencies, retracted and unvalidated parents, and corrupt-cycle preservation. This addresses declared memory-to-memory links only. It does not infer dependencies, validate factual truth, watch artifact hashes, or propagate retraction into separate research/legacy indexes.

Latest verification: **485 tests passed in 14.11 seconds**; compileall succeeded.
