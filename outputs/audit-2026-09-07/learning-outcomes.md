# Outcome learning classification

Repair-pattern follow-up: new fixes no longer inherit another fix's success
rate. Updates match the error type, stored 200-character message, and exact fix
text. Explicit boolean verdicts and valid loaded counters/rates/timestamps are
required. Tests verify that a failed new fix cannot displace a repeatedly
successful different fix, and shared 50-character prefixes no longer merge
different stored messages. The JSON-error learning note no longer invents a
successful fence-stripping attempt. Existing legacy aggregated scores cannot be
unmixed without original attempt evidence; caller-provided verdicts and message
truncation remain limitations, not independent proof that a repair works.

Task-record follow-up: task identity/text, counters, timestamps, and list fields
are validated on load/admission. Identical task records (ignoring timestamp and
the derived success flag) receive credit once; repeated callbacks do not append
new records. Existing duplicate history is retained but excluded from duplicate
strategy totals and similarity counts. Changed outcomes/attempt details still
form distinct records. File-graph/error-pattern validation remains incomplete.

Storage follow-up: saves use atomic replacement under an OS-backed lock and
compare the loaded file hash before writing. Stale copies and externally deleted
stores are refused instead of overwriting/recreating newer user state. Invalid
JSON and malformed top-level containers fail closed instead of loading as empty
history. Tests cover corrupt input, stale instances, write failure, and deletion.
Record-field validation and task deduplication remain incomplete. A refused
in-memory instance must be reconstructed before retry; no automatic merge occurs.

Verification: **601 tests passed** in 25.13 seconds; compileall passed.
Storage follow-up: **608 passed** in 24.52 seconds; compileall passed.
Task-record follow-up: **614 passed** in 23.04 seconds; compileall passed.
Repair-pattern follow-up: **623 passed** in 21.90 seconds; compileall passed.

Legacy knowledge-graph success credit now requires tests_passed. Intermediate
plan_ok/implement_ok and no_test_cmd records are retained but not scored as
strategy successes or failures. Emergency stop, command-budget stops, and unknown
results are likewise excluded from strategy scoring and success-rate suggestions.
Structured memory no longer describes those unassessed stops as strategy failures.

On load, strategy aggregates are rebuilt in memory from retained task records,
rather than trusting old cached totals that could include false success credit.
Reading does not rewrite the user's legacy file. Subsequent normal saves include
the rebuilt aggregates. Regression tests cover excluded outcomes and deliberately
inflated legacy success fields/aggregates.

This is outcome/strategy history, not the separate verified model-reputation
store. Iteration-limit and explicit implementation/verification failures still
count as unsuccessful outcomes; this does not establish that model reasoning
alone caused the failure. Tests_passed still reflects harness gates, not proof of
independent acceptance coverage. Historical records remain caller-provided, and
this store still needs stronger record validation and deduplication.
