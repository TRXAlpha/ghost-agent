# Attention queue integrity — 6 September 2026

Notification creation and acknowledgement now use a process lock and atomic file replacement. Retrying the same notification ID with identical priority/message/source returns the existing record, including its acknowledged status. Conflicting ID reuse is refused. Repeated acknowledgement is idempotent.

Corrupt or duplicate persisted records fail closed instead of being treated as an empty queue and overwritten. New notifications require valid pending state and record fields. Quiet-hour values are checked explicitly; urgent notifications retain their documented bypass, while silent notifications remain undeliverable.

Tests cover creation/acknowledgement retries, conflicting content, corrupt-file preservation, lock contention, invalid quiet hours, overnight boundaries, and urgent/silent behavior.

Full verification: **502 passed in 11.33 seconds**; compileall succeeded.

This is queue correctness, not scheduled check-in delivery. Proactive opt-in configuration, subscriptions, durable delivery attempts, provider adapters, rate limits, and acknowledgement across external services remain incomplete. No background monitor was started and no external message was sent. Retries deduplicate only when callers reuse a stable ID.
