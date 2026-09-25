# Node verification

Verification: full suite **571 passed** in 19.11 seconds; compileall passed.
Discovery follow-up: **578 passed** in 19.26 seconds; compileall passed.
Mixed-gate follow-up: **581 passed** in 21.55 seconds; compileall passed.

Added installed-Node native test execution through the same capability-gated,
bounded-output, emergency-stop-aware process runner used for Python commands.
Only `node --test` with explicit existing workspace JavaScript files is accepted.
No npm/package installation, shell, or additional Node switches are enabled.
Executable-path validation rejects workspace-provided Node impersonation using
the existing trusted-tool checks. Arbitrary generated tests still have host access.

Automatic discovery and focused tests recognize `.test.js`, `.test.cjs`, and
`.test.mjs`. Discovery prunes common dependency/state directories, skips symlinks,
and stops after 10,000 enumerated entries. Follow-up discovery uses incremental
scandir rather than collecting entire directories, skips Windows reparse points
(including junctions), and discards incomplete scans after filesystem errors or
budget exhaustion. This is not protection against concurrent hostile filesystem
replacement. Mixed Python/Node discovery now produces multiple gates: focused
Python, focused Node, broad Python, broad Node (where applicable). The first
failure stops later gates and all attempted results are retained in outcome
state. Tests execute successful mixed projects and verify that a failing Node
focused gate prevents suite execution even after Python passes. An explicit
acceptance command still overrides broad discovery. Rust/Go/Java,
TypeScript, third-party JS runners, linting and isolated dependencies remain open.

Real tests ran on installed Node v24.12.0: passing/failing assertions, paths with
spaces, scoped-command refusals, and a scripted orchestrator outcome executing
focused and broad Node gates in the copied workspace. No live model inference
was used; this proves harness integration, not Qwen's JavaScript competence.

Node's [native test runner](https://nodejs.org/download/release/v24.0.2/docs/api/test.html)
can treat a script as a test file without requiring substantive assertions. Thus
green exit codes still do not prove adequate acceptance coverage; independent
test generation and meaningfulness checks remain unfinished.
