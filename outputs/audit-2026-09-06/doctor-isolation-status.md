# Execution-environment and diagnostic check — 6 September 2026

Read-only environment inspection found Docker and WSL executables, but Docker's Linux-engine named pipe was absent and `docker info` could not connect. No service was started, image downloaded, or generated code executed. Installed executables alone do not establish an available isolation backend.

Ghost's command runner still executes tools with the host user's privileges. `ghost doctor` now exposes `execution_isolation: not_implemented_host_user_access` rather than leaving this critical limitation implicit. Scheduler and event-bus files are checked for usable record structure rather than merely existence. Extension manifest checks reject structurally invalid records such as an empty JSON object.

A new diagnostic regression revealed that constructing the emergency-stop store created `.ghost` in an uninitialized project. Doctor now avoids that initialization and remains read-only in that case. Invalid tested storage is preserved, not repaired or discarded.

The full run also reproduced the Windows slow-header closure race. The timeout test now accepts connection reset as well as EOF for header expiry while retaining elapsed-time, continued-send, completion, and subsequent-request checks. Body-expiry response assertions remain unchanged.

These are diagnostic improvements, not process isolation. Real containment, process-tree cancellation, resource quotas, and isolated coding benchmarks remain required. Doctor still does not provide comprehensive live service health or validate every store and dependency.

Full verification: **491 passed in 11.49 seconds**; compileall succeeded.
