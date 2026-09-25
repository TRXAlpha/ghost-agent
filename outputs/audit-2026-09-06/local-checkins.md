# Opt-in local check-ins

Verification: full suite **521 passed**; compileall passed; CLI help smoke check
passed. These checks do not establish broad autonomous reliability.

Implemented a single explicit per-project reminder schedule in checkins.py,
with CLI enable/status/disable/tick and polling between worker watch jobs.
No schedule was enabled in the user's project during development.

Schedule writes are atomic and process-locked. Corrupt records are preserved
and refused. Notifications use stable occurrence IDs, so replay after a failed
schedule update also recognizes acknowledged notifications. Quiet hours use
host-local time; emergency stop defers issuance. One pending check-in suppresses
further reminders. Unconfigured status/tick do not create state.

Tests cover due boundaries, quiet-hour boundaries, pending suppression,
acknowledgement, disable, emergency stop, invalid configuration, corrupt state,
busy locks, and injected schedule-write failure after notification commit.

Limits: this is a local reminder, not a proactive reasoning supervisor or an
external delivery adapter. It requires explicit configuration and an active
watch worker or manual tick. Running jobs can delay polling. Host-clock changes
affect due times. No OS service, external message, model call, or user-activity
monitor was started. Local files are not protected from arbitrary host writers.
