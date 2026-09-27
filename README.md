# Ghost Agent

Ghost is a local-first outcome agent in active development. It can research a goal, build in a copied sandbox, run focused tests, attempt repairs, and preserve an auditable artifact trail. It is not a general-purpose autonomous AI operating system yet.

## What works today

- **End-to-end usable loop**: `ghost accomplish --model gemma4:26b --command-timeout-seconds 120 "<goal>"` → real Ollama inference → sandbox tool execution → real pytest gates → durable outcome with `tests_passed`. Run with a model large enough for multi-step reasoning (gemma4:26b recommended; `--command-timeout-seconds` is the per-command timeout, default 30s, increase for heavyweight test suites).
- **`ghost doctor`**: real Ollama (localhost:11434) + Docker daemon health probes; all integrations report as `not_configured` until credentials/hardware exist.
- **Owner knowledge base**: `ghost kbase ingest <dir>` indexes your Zim/notes/docs; `ghost kbase query "<text>"` retrieves relevant chunks injected into the model's context — stop re-inventing API facts.
- **LoRA corpus capture**: every passing job auto-captures a verified (goal → reference context → passing code) triple in `.ghost/lora/training_pairs.jsonl`; `ghost lora list` shows what's accumulated. Nothing trained yet — the corpus is ready for a future fine-tune.
- **Autonomous improvement**: `ghost proactive tick/watch` + `ghost worker --watch` — Ghost observes goals, devices, and context files, proposes one bounded experiment per tick, queues it for the worker, and closes the loop on results. Idle-time gating (`idle_seconds()` + emergency stop) ensures owner work is never interrupted.
- **Autonomous adapter building**: capability gaps from repeated failures are tracked (`ghost gaps list`); Ghost uses the model to assess whether a gap is autonomously addressable, queues sandbox implementation work, and marks the gap resolved on tests_passed. All changes are sandbox-only; promotion requires owner approval.
- **Capability gap registry**: `ghost gaps list` / `ghost gaps stats` — see queued high-count gaps and their followup goals.
- **Proactive feedback loop**: queued/running opportunities are reconciled on restart and closed on terminal outcomes (completed/tests_failed/error/cancelled).
- Source-grounded research with credibility tiers, exact-quote claim gating, and per-run provenance graphs.
- Sandboxed coding runs, plan → implement → verify → repair state machine, and durable outcome jobs via `ghost accomplish`.
- Outcome acceptance contracts, persistent intent records, and model routing based on measured reputation.
- Reviewable MCP extension scaffold, explicit approval gates, and loopback-only API (`ghost api`).
- Promoted artifacts use atomic per-file replacement with a recovery journal.

## Honest limits

The orchestrator requests a 2,048-token output limit (`max_output_tokens`
constructor setting, maximum 8,192) through Ollama's `num_predict` option.
This relies on backend enforcement and is not a transport-memory or whole-job
token quota. Truncated invalid JSON does not count as successful implementation.

Use `ghost accomplish "Create app" --require-file src/app.py` to require specific
deliverables. Repeat `--require-file` for multiple paths. Requirements appear in
dry-run previews, persist for queued jobs, and are checked before and after tests.
They check file presence, not content correctness.

Outcome command execution shares a durable default allowance of 32 command
attempts and 300 seconds. Task constraints `command_budget_calls` and
`command_budget_seconds` configure this allowance; resume refuses changed limits.
The CLI exposes these as `--command-budget-calls` and `--command-budget-seconds`
on `ghost accomplish`, including queued jobs and dry-run previews.
Each command reserves up to its 30-second timeout before execution and refunds
unused time afterward. Crashed attempts retain their reservation. These are not
whole-job CPU/RAM/GPU, model-inference, or network budgets.

Dependency-free JavaScript tests named `*.test.js`, `*.test.cjs`, or `*.test.mjs`
can now use installed Node's `--test` runner, including focused changed-test
checks. Node dispatch requires explicit workspace files; npm, installation,
and additional Node flags remain refused. Mixed Python/Node projects run separate
focused and broad gates for each detected ecosystem, stopping at the first failure
and recording gate results. An explicit acceptance command still overrides default
discovery. This does not yet support general Jest/Vitest,
TypeScript compilation, browser tests, or isolated dependencies.

Command execution now uses bounded output capture and Windows job-based child
cleanup on completion/timeout. Running orchestrator commands poll emergency stop
every 100 ms. These controls do not isolate host files or network access and do
not provide hostile-code containment. A Windows supervisor-handle watcher now
also stops command descendants if Ghost exits unexpectedly; equivalent POSIX
crash cleanup remains unfinished. See [execution checks](outputs/audit-2026-09-07/process-lifetime.md).

Small local models can still fail multi-step research and coding tasks. Ghost has evidence and test gates, but independent acceptance checks and measured reliability remain incomplete; a passing test command is not proof that a user's whole outcome was achieved. It does not automatically install extensions, deploy services, create accounts, obtain secrets, or apply sandbox changes to the live project.

A live Qwen 2.5 Coder 1.5B [protocol baseline](outputs/audit-2026-09-06/model-protocol-baseline.md) matched only 4 of 8 expected action lists. Run `python -m ghost_agent.protocol_benchmark` for repeatable, non-executing measurements. These results do not establish coding or research reliability.

Coding model requests default to a 24,000-character serialized-message limit. Optional retrieval/history can be omitted; oversized required task instructions or feedback cause an explicit refusal. This is not an exact token budget. See [context budget behavior](outputs/audit-2026-09-06/context-budget.md).

## Install

For the new OpenJarvis-backed route on Windows, use the isolated installer:

```powershell
.\scripts\bootstrap-openjarvis.ps1
.\workspaces\openjarvis-venv\Scripts\ghost-jarvis.exe openjarvis doctor --probe
.\workspaces\openjarvis-venv\Scripts\ghost-jarvis.exe accomplish "Build a calculator with tests" --model "<installed-model>" --research-model "<installed-model>"
```

Start your inference server first and replace the model placeholders. The installer pins upstream source/dependencies and builds its native extension; it does not start services or download models. Existing `.venv` and owner state remain intact. See [OpenJarvis commands](docs/openjarvis.md) for document import, Telegram pairing, other endpoints, and qualification limits.

The original Ollama-only compatibility installation remains available:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

Run Ollama locally, then select models installed on your machine.

Check runtime health with `ghost doctor`. It reports Ghost storage state plus a
`SERVICES:` section for real service availability (Ollama at localhost:11434 and
Docker daemon) with detected Ollama model names, and reports the optional
integrations (`telegram`, `huawei`, `desksentinel`) as `not_configured` until you
provide credentials/hardware. Health checks are read-only probes; Ghost never
starts or configures services for you.

## External integrations (all optional — Ghost runs without any of them)

Telegram, Huawei, and DeskSentinel are gated behind clearly-marked placeholder
adapters under `ghost_agent/integrations/`. Without credentials/hardware they
raise `ConnectionError` and report `not_configured`; they never fake a live
connection or live data.

- **Telegram**: set `TELEGRAM_BOT_TOKEN` and pair via `ghost integration plan telegram`,
  review the extension proposal, and approve enablement. Until then, messaging is
  unavailable.
- **Huawei**: requires Huawei Developer Console credentials (AppID/AppSecret) and
  user authorization. Until then, no live health data is fetched.
- **DeskSentinel**: the ESP32 firmware and serial/BLE protocol do not exist yet.
  `connect`/`set_target_height` raise `ConnectionError`; `get_status()` returns a
  clearly-labeled mock — not live telemetry.

## Core commands

```powershell
ghost                              # Interactive mode
ghost research "topic"             # Source-grounded research
ghost accomplish "desired outcome" # Research -> sandbox build -> test/repair
ghost kbase ingest <dir>           # Index owner reference docs (Zim/notes/docs)
ghost kbase query "text"           # Retrieve relevant reference chunks
ghost kbase status                 # Show indexed document/chunk counts
ghost lora list                    # Show captured verified training pairs
ghost lora status                  # Corpus summary and next step hint
ghost gaps list                    # Show queued capability gaps
ghost gaps stats                   # Gap counts by status
ghost run task.json                 # Sandboxed task-file run
ghost audit                         # Inspect saved artifacts
ghost knowledge                     # Inspect learned task history
ghost doctor                        # Check local runtime health (ollama, docker, integrations)
ghost proactive tick --model <model> --context-file <file>  # Propose one experiment
ghost proactive watch --model <model> --run-worker          # Continuous proactive loop
ghost worker --watch                # Claim and execute queued outcomes
ghost accomplish "build a small game" --model qwen2.5-coder:1.5b
ghost accomplish "investigate X and prototype Y" --research-model gemma4:e4b-Claude
ghost accomplish "fix the parser" --iterations 20 --command-timeout-seconds 120
ghost accomplish "local-only code task" --skip-research
ghost accomplish "research then build this overnight" --queue
```

When `ghost api` is running, open `http://127.0.0.1:8765/` for the local dashboard. It reads the same loopback-only control-plane snapshot; it does not expose a public web service.

API writes require `Content-Type: application/json` and a body no larger than 64 KiB. The transport rejects cross-origin browser requests and unexpected Host headers. Set `GHOST_API_TOKEN` to require bearer authentication for API data/actions; the current dashboard does not yet provide token entry. These checks do not isolate other programs running as your local user. See [API hardening](outputs/audit-2026-09-06/api-progress.md).

The API admits at most eight client handlers at once; excess connections close immediately. Embedded callers can set `create_server(..., max_clients=...)` from 1 to 64. This bounds handler concurrency, not request rate or total processing time.

Headers and body share a 15-second read deadline, so partial arrivals cannot keep a connection open indefinitely. Embedded callers can set `read_timeout` from 0.05 to 300 seconds. This deadline does not cancel SDK work after a request has been read.

`ghost accomplish` accepts:

```powershell
ghost accomplish "build a small game" --model qwen2.5-coder:1.5b
ghost accomplish "investigate X and prototype Y" --research-model gemma4:e4b-Claude
ghost accomplish "fix the parser" --iterations 20
ghost accomplish "local-only code task" --skip-research
ghost accomplish "research then build this overnight" --queue
ghost worker --watch                 # Claim and execute queued outcomes
ghost-jarvis proactive tick --model <installed-model> --context-file desksentinel.md
ghost-jarvis proactive watch --model <installed-model> --context-file lessons/rust.md --run-worker
ghost-jarvis openjarvis telegram serve --model <installed-model> --allow-sandbox-builds --run-worker
ghost self-repair propose "diagnosis" --sandbox <sandbox-path>
ghost device register desktop --name Precision --capability inference.run --capability filesystem.read
ghost mcp logs --goal "Read approved service logs" --tool logs.read
```

In interactive mode, code/integration requests use the same outcome workflow. Use `/accomplish <goal>` to force it explicitly.

## Artifact layout

```text
.ghost/
  research/                 # Query -> source -> quote -> verdict graphs
  outcomes/<job>/
    outcome.json            # Job status, models, result, errors
    research.md             # Research dossier, when used
    run/sandbox/            # Copied project snapshot and generated work
    run/actions.log         # Tool calls and outputs
    run/llm.log             # Model turns
    run/state.json          # Phase/result checkpoint
  capabilities.json         # Recurring capability gaps and follow-up queue
  events.jsonl              # Runtime event stream
  world.json                # Timestamped world observations
  scheduler.json            # Queued budget-aware jobs
  models.json               # Verified model/task-class reputation metrics
  transactions/             # Preview/commit/verify/rollback journals
  approvals.json            # Explicit user decisions for consequential actions
  secrets.json              # Environment-variable references only; never secret values
  transactions/             # Preview/commit/verify/rollback transaction history
  promotions/               # Per-file backups for explicit live promotion undo
  extensions/               # Proposed/scaffolded/tested/approved extension packages
  devices.json              # User-owned devices and their declared capabilities
  world.json                # Fresh, sourced current-state observations
```

## Security model

Ghost works in a copied project directory, **not an OS-isolated execution environment**. File tools constrain their targets, and automatic verification now uses the same capability, user-policy, emergency-stop, and audit checks as model-requested tools. Commands run without a shell, use allowlisted tool entry points, and inherit only a small set of environment variables. Direct `pip`/`python -m pip` commands are blocked. Generated Python, tests, and allowed programs still have host-user filesystem/process/network access; these checks do not make hostile code safe. Do not run untrusted tasks unattended until real execution isolation and process cancellation are implemented.

New runs refuse to replace existing artifact/workspace directories. Snapshots exclude `.env*`, common private-key files, dependency caches, and Ghost metadata, and reject symlinks/junctions. Known token/private-key patterns are redacted from action/model logs, provenance, and security events. This is best-effort protection, not a guarantee that every secret format or persistence path is covered; existing artifacts are not retroactively scrubbed.

Live promotion binds approval to candidate and destination SHA-256 hashes, stages backups before writes, and uses atomic per-file replacement with a recovery journal. `ghost promote list` shows recovery status; `ghost promote rollback <id>` restores recorded bytes only when the live state still matches the approved before/after state. Promotion verification means approved bytes were applied, not that the program passed independent functional acceptance. Legacy/corrupt journals require manual review and are not blindly migrated. File metadata, deletion propagation, and coordination with non-Ghost writers still need stronger guarantees.

Extension enablement/testing also needs stronger evidence and immutable scope checks. Account setup, external deployment, and real integration enabling remain user-controlled. See the [audit](outputs/audit-2026-09-06/assessment.md), [dispatch hardening](outputs/audit-2026-09-06/hardening-progress.md), and [promotion progress](outputs/audit-2026-09-06/promotion-progress.md) for verified behavior and unresolved defects.

## Roadmap

See [implementation.md](implementation.md) for the full Jarvis/AI-OS roadmap, including durable scheduling, richer scholarly retrieval, extension/MCP lifecycle management, integrations, secret handling, promotion, benchmarks, and a desktop control layer.

## Job recovery

Foreground `accomplish` and queued jobs use one worker path, with atomic claims and one active worker per project. Inspect state with `ghost jobs list`. After an interrupted worker, its job is paused for review; inspect preserved artifacts and any surviving child processes before `ghost jobs resume <id>`. Resume keeps the existing sandbox/checkpoint, grants a fresh bounded coding attempt, and may repeat the unfinished step. Corrupt checkpoints or changed outcome contracts are refused rather than overwritten. `ghost jobs retry <id>` retries failed jobs and reactivates a blocked goal; deadlines and dependencies still apply.

`ghost jobs pause <id>` controls queued jobs; `ghost jobs cancel <id>` accepts queued, paused, or failed jobs. These commands deliberately refuse running-process cancellation. This is a single-host recovery foundation, not distributed leases or exactly-once external execution. See [worker progress](outputs/audit-2026-09-06/worker-progress.md).

## Development checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The suite currently covers parsing, sandbox paths, command restrictions, research gates, provenance graphs, autonomous repair behavior, outcome persistence, and capability-gap queues.
# Opt-in local check-ins

`ghost check-in enable --interval-seconds 86400 --quiet-hours 22 8` configures
a local attention reminder (first due after the interval). `ghost check-in status`
inspects it; `ghost check-in disable` stops future reminders without deleting
existing notifications. Equal quiet-hour endpoints disable quiet hours; hours
use the host's local time zone.

An explicitly running `ghost worker --watch` checks this schedule between jobs;
`ghost check-in tick` performs one check manually. Neither command installs a
background service. Long-running jobs delay check-ins. Emergency stop and quiet
hours defer reminders; an unanswered check-in suppresses additional ones. Missed
intervals coalesce into one reminder rather than a backlog. Stable occurrence IDs
allow recovery after a notification commit but before the schedule update.

This is disabled until configured, creates local attention records only, and
does not call a model, send external messages, observe user activity, or execute
goals. Existing notification acknowledgement controls allow the next reminder.
