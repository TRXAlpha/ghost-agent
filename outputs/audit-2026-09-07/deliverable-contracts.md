# Required deliverable contracts

Post-test follow-up: required deliverables are rechecked after all command gates
pass, before tests_passed can be recorded. If tests removed a required file, a
failed deliverables gate records the missing paths and the outcome cannot pass.
A real pytest regression deliberately deletes app.py while both focused and
broad tests exit zero; the final contract check correctly rejects completion.

Verification: **640 tests passed** in 22.29 seconds; compileall passed.
Post-test follow-up: **641 passed** in 23.24 seconds; compileall passed.

Task construction rejects non-object constraints/context and malformed
required_files lists. Required paths must be nonempty relative paths without
traversal, absolute/drive-relative forms, alternate streams, control characters,
wildcards, or duplicates. Limits are 256 paths and 1024 characters per path.
Duplicate comparison is case-insensitive for portability to the Windows host.

Verification revalidates the contract in case a caller mutated it. Directories
do not satisfy file requirements. Resolved paths must equal their lexical
workspace paths; links cannot satisfy required deliverables. This remains a
preflight check, not protection against concurrent host filesystem replacement.

The earlier behavior silently treated a non-list required_files value as no
requirements and skipped non-string/empty entries. That bypass is removed.
Tests cover malformed data, path escapes, duplicate paths, nested valid files,
directory-vs-file requirements, and mutation before verification. Content
acceptance, artifacts other than files, and independent outcome checks remain
unfinished; existence is not proof of correct behavior.
