# Ghost: Personal AI OS and Proactive Self-Evolving Agent

Status: target product specification and OpenJarvis-first integration roadmap.
Updated: 16 September 2026.
Audience: the owner, implementing agents, and reviewers, including inexpensive coding workers.

This document preserves the owner's intended product. OpenJarvis is the selected upstream foundation; Ghost supplies personal behavior, domain extensions, and verified controls around it. This revision supersedes the earlier build-from-scratch work assignments. Requirements below define the composed product, not a mandate to reimplement each subsystem inside Ghost. A first working integration now exists; qualification is limited to the evidence recorded below, not the entire target product.

## Implementation checkpoint — 10 September 2026

Read [the integration ledger](outputs/openjarvis-integration/2026-09-10.md) and [operating instructions](docs/openjarvis.md) before assigning more work. The pin, isolated Python 3.13 environment, native Rust build and installer already exist. Ghost coding/research uses upstream engines through `ghost-jarvis`; upstream read-only conversations, sessions, explicit document retrieval, owner-paired Telegram transport, Telegram sandbox build queue, and the first proactive loop are wired and locally tested. **785 tests pass with upstream installed; 749 pass and 2 modules skip in the preserved legacy environment.**

These are partial OJ01–OJ05/OJ08 slices, not completed milestones M1–M4. The model service was unavailable for live inference, and Telegram has no configured owner credentials. Full authority migration, hostile-code containment, upstream scheduler/operator integration, durable spending limits and proactive capability acquisition remain required. Do not enable upstream default tools or autonomous self-modification based on these narrower tests. Index deletion also does not yet erase prior conversation excerpts.

Subsequent workers must reuse this implementation and its tests. Do not commission another from-scratch foundation or duplicate the existing engine/Telegram/retrieval/session implementations.

## MVP checkpoint — 16 September 2026

The first thin proactive vertical slice now exists. `ghost-jarvis proactive tick` reads durable Ghost state plus explicitly supplied project-context text, asks one selected model for one bounded opportunity, stores a deduplicated proposal, and queues a high-confidence sandbox outcome. `proactive watch --run-worker` adds verified Windows idle admission and hands queued work to the existing bounded research/build/test/repair worker. The opt-in Telegram `--allow-sandbox-builds --run-worker` path uses that same queue and worker for explicit build-shaped requests. Unknown activity state defers. The existing Ghost worker remains the only coding executor. Unsafe/medical/account/device/message/money/deployment proposals become `needs_input` instead of running. This is enough to demonstrate the observe → propose → queue → research/build/test/repair loop, not enough to claim continuous life integration or subjective self-motivation.

The practical MVP workflow is now: register a device or keep a project description/lesson file, run the proactive watcher during a chosen idle period, let one queued outcome run, inspect its research and sandbox evidence, and explicitly promote anything useful. Telegram, Huawei, health interpretation, DeskSentinel control, educational games, and self-updates remain adapters or products to be built from these queued, testable outcomes—not implicit permissions.

## 1. Product promise

Ghost is a persistent personal agent that understands the owner's goals, learns about their life from available context, discovers useful opportunities, builds missing capabilities, operates those capabilities, and improves from observed results.

The owner should not have to design every automation or name every feature. Saying "I want to learn Rust," supplying school lessons, or mentioning an ESP32 project should give Ghost material for independent investigation and useful initiative. Ghost should connect those facts with known goals, generate possible actions, choose a worthwhile experiment, execute within standing permissions, and learn whether it helped.

Ghost should feel continuous across the PC, Telegram, future voice interfaces, and connected devices. It should remain useful when the owner is away, work on selected projects during idle periods, maintain what it has built, and recover from interruptions.

Its objective is to help the owner learn, create, focus, manage projects, and maintain useful systems. Its objective is not maximum activity, maximum engagement, maximum code generation, or constant self-rewriting.

### 1.1 Meaning of "self-evolving"

Self-evolution is a measurable loop:

1. Observe an unmet need, recurring failure, or improvement opportunity.
2. Form a specific hypothesis about how to improve.
3. Research, reuse, configure, or build a candidate.
4. Test the candidate against independent acceptance checks.
5. Activate it within existing authority, or request the missing specific input.
6. Measure technical behavior and, where applicable, usefulness to the owner.
7. Retain, revise, roll back, or retire it; record the evidence.

Candidates can change tools, adapters, prompts, workflows, retrieval, knowledge, planning strategies, learning content, and Ghost's application code. Training model weights is a separate optional research track, not a prerequisite or an implied capability.

"Wanting to evolve" means persistent goals and a functioning initiative mechanism. Do not claim subjective desire or consciousness. Ghost should seek improvements even when no current chat asks for them.

### 1.2 Model independence

Support many model providers, architectures, sizes, and endpoint formats through adapters. Local inference must remain a supported first-class route; a paid proprietary model must not be mandatory.

Compatibility does not imply equal intelligence. A small model receives shorter context, narrower assignments, constrained outputs, proven procedures, and concrete feedback. Complex work is decomposed, attempted with bounded retries, or explicitly left unresolved if no capable model is available.

Escalation to a stronger or paid model is optional and subject to configured availability, data-routing preferences, and spending limits. Never silently substitute a paid model, fabricate a successful result, or keep retrying indefinitely.

### 1.3 What would miss the product

These do not satisfy this specification:

- A chatbot that acts only after an explicit command for each feature.
- A timer that periodically asks a model to "be proactive" without a durable opportunity-selection loop.
- A collection of hardcoded Telegram, Huawei, Rust, or homework branches.
- Extension scaffolds, role labels, or status flags presented as functioning integrations.
- A model editing its tests or success criteria until its own changes pass.
- An agent that builds speculative projects endlessly without learning what the owner uses.
- Constant permission questions for steps already covered by standing authorization.
- An assistant that infers new permissions from a preference or from untrusted content.

### 1.4 OpenJarvis-first engineering decision

Adopt [OpenJarvis](https://github.com/open-jarvis/OpenJarvis) as Ghost's primary reusable agent infrastructure. Start by composing its SDK, supported agents, tools, channels, and services through a small integration boundary. Keep upstream as a pinned dependency or separately managed checkout, not copied wholesale into Ghost.

For every requirement, apply this order:

1. Reuse a working OpenJarvis capability.
2. Configure it for Ghost and the owner.
3. Adapt its interface with the smallest bridge.
4. Extend the upstream component or contribute a focused patch when a verified gap exists.
5. Build a Ghost-owned component only for a remaining product-specific need.
6. Defer unavailable provider/hardware features with an exact prerequisite.

A successful work package may consist only of configuration, wiring, and acceptance tests. Lines of new code are not progress metrics.

Before creating a duplicate model client, scheduler, Telegram transport, retrieval engine, optimizer, UI, or generic tool framework, record the exact upstream path/version inspected, the unmet acceptance check, and why configuration or a small extension cannot satisfy it. This evidence rule is not an extra owner-approval step: workers may resolve routine implementation choices within their assignment.

Ghost remains responsible for its personal goals, open-ended opportunity selection, learning progress, DeskSentinel/Huawei behavior, and outcome evidence. These should use upstream services wherever suitable. Existing Ghost protections and artifacts remain until a replacement demonstrates equivalent required behavior and a reversible migration.

## 2. Behavioral scenarios: preserve these through implementation

These scenarios are product acceptance contracts. Concrete providers and devices are examples of a general mechanism. They must not become the entire scope of Ghost's initiative.

### S01. "I want to talk to you on Telegram"

Ghost detects a communication capability gap and first configures OpenJarvis's existing Telegram channel through the Ghost bridge. Research and code are needed only for an uncovered requirement. Do not write another Telegram transport to demonstrate autonomy: discovering and activating an existing capability is the intended first outcome.

While a necessary account or credential step is pending, it completes independent preparation. It explains the exact remaining step and resumes when the required input arrives. Once connected, conversations and tasks continue through Telegram and the PC.

Acceptance:

- Authenticated incoming messages reach the same task service as the CLI/UI.
- An unpaired sender cannot operate the owner's agent.
- Message/update retries do not repeat task creation or other consequential actions.
- Connection interruptions and process restarts preserve conversation/task continuity.
- Sending, reconnecting, credential rotation, rate limits, disabling, and health reporting work.
- The owner performs only the provider/account steps Ghost cannot legitimately complete within available authority.
- Real integration success is recorded only after a real authorized connection test; mocked transport is labeled separately.

### S02. "My watch is a Huawei"

Ghost remembers the device fact and investigates possible uses in light of the owner's goals. It discovers relevant APIs and checks actual eligibility, phone platform, region, available scopes, data freshness, and any required developer application.

Ghost explains necessary developer/user-authorization steps, then builds the adapter and downstream features against synthetic data while waiting. It does not promise that every watch exposes every metric or assume direct watch access.

Acceptance:

- A discovered provider route includes sources, checked date, prerequisites, and unresolved limitations.
- Revoked or unavailable access produces an honest integration state.
- Activity, sleep, and other available observations preserve units, timestamps, time zone, freshness, and provenance.
- Configured inactivity reminders use sufficiently recent data; no data means unknown.
- The owner can correct context such as illness, travel, an unworn watch, or an intentional rest day.
- Personal trend summaries distinguish measured changes from hypotheses.
- Burnout or illness prediction is a separately validated future feature; connecting the API is not evidence of predictive validity.
- Health data is excluded from unauthorized model endpoints, logs, and unrelated integrations.

### S03. DeskSentinel: ESP32 with two pan/tilt servos

Given the DeskSentinel project as context, Ghost inventories the firmware, command protocol, actual sensors, actuator limits, and development environment. It connects available capabilities with useful personal goals and independently investigates a small experiment.

For example, it may discover a focus-status interaction if the hardware supports an appropriate signal. It must not invent a camera, presence sensor, or distraction detector merely because two servos exist.

Acceptance:

- The adapter is based on inspected firmware/protocol evidence, not guessed endpoints.
- A simulator validates command encoding, angle/rate limits, disconnect behavior, and cancellation before hardware activation.
- Device identity and pairing prevent a different device from receiving commands.
- Physical commands stay within configured limits and stop or enter a known safe state on communication loss.
- Flashing firmware is a distinct action from routine control.
- Focus interventions follow the owner's configured mode and have an immediate owner override.
- Another programmable device can enter the same discovery/build workflow without a new hardcoded product branch.

### S04. "I want to learn Rust" and "help me practise cyber"

Ghost creates a learning goal, estimates current knowledge from available evidence, and considers exercises, games, small projects, explanations, or practice labs. It may prepare a tailored Rust learning game during idle hours if that is a justified experiment.

Cybersecurity practice uses owned or isolated training environments. Ghost generates challenges based on mastered topics and recurring mistakes, provides graded hints, and tracks learning rather than merely counting completions.

Acceptance:

- An exercise references learning objectives and prerequisite concepts.
- Rust examples compile under a recorded toolchain; incorrect solutions fail meaningful checks.
- CTF challenges are solvable, reproducible, isolated, and resettable.
- A solution and test oracle exist separately from the learner-facing material.
- Progress survives restarts; future work responds to actual answers and misconceptions.
- Ghost evaluates whether the format helped and can switch approaches or stop generating unused material.
- Missing toolchains trigger a concrete dependency plan; no silent unrestricted host installation.

### S05. School lessons and an upcoming test

The owner supplies lessons and test context without requesting a particular feature. Ghost notices the opportunity and assembles or builds a study workspace with source-grounded answers, revision questions, and progress tracking.

A NotebookLM-like experience is a product reference, not a requirement to copy a proprietary service or rebuild functionality already available in Ghost.

Acceptance:

- Documents retain source identity, page/section references, version, and extraction quality.
- OCR is available through an optional adapter when required.
- Answers and generated revision material cite the supplied lesson passages.
- Ghost identifies unanswered questions and conflicts instead of filling gaps with invented lesson content.
- Retrieval, correction, document replacement, deletion, and derived-content invalidation work.
- Quiz answers and misconceptions inform the next revision session.
- Test dates, quiet periods, and available study time influence scheduling.
- Uploading arbitrary text cannot grant permissions or alter the supervisor's operating instructions.

### S06. Idle time and overnight initiative

The owner sets a standing preference for useful background work. Ghost detects permitted idle periods, chooses work from its goals and opportunity backlog, and performs research, builds, tests, or maintenance.

Acceptance:

- Work is selected for a stated benefit, not because the PC happens to be idle.
- Job checkpoints survive interrupted sessions.
- Returning to the PC reduces or pauses resource-heavy work within a configured, measured deadline.
- Quiet hours suppress ordinary notifications while a morning digest summarizes meaningful results.
- Paid inference, network, compute, disk, and concurrency limits remain enforced.
- A sleeping/offline PC is reported as unavailable; Ghost does not claim work occurred while it could not run.
- Another enrolled device may execute eligible work later; distributed execution is not required for the first release.

### S07. Open-ended discovery from unfamiliar context

The evaluation supplies a new device, project, subject, or workflow not named in this document. Ghost must connect available context to an owner goal, investigate feasibility, and produce a useful bounded experiment or a justified decision to defer.

Acceptance:

- The general opportunity pipeline processes the new context without adding product-name conditionals.
- Recorded reasoning links the idea to actual observations and goals.
- Contradictory preferences suppress inappropriate ideas; rejected ideas do not repeatedly reappear unchanged.
- A suitable existing tool is reused when building a new one offers no clear benefit.
- Missing context can produce one targeted question while independent work continues.
- The worker does not receive the evaluator's desired feature as a hidden implementation instruction.

### S08. Maintenance and self-improvement

Ghost notices a broken adapter or a recurring coding failure, diagnoses it, builds a candidate fix, runs independent checks, and stages an update. Once eligible under the configured update policy, it deploys incrementally and observes the result.

Acceptance:

- A real failure and proposed benefit are linked to the change.
- Evidence identifies the exact candidate, environment, tests, and results.
- A candidate cannot modify the evaluator, permission decisions, or evidence records used to approve itself.
- A failed update can return to a known working version.
- Rejected or unchanged candidates do not enter endless repair loops.
- Updating Ghost preserves active jobs, personal memory, and integration configuration.
- Core authority changes use a separately controlled update path.

## 3. Existing foundations, reuse map, and qualification

### 3.1 Existing Ghost: assets to retain or migrate

This is a source-informed baseline, not a declaration that the target is complete. The 720-test pass reported earlier in this conversation is a historical observation, not a fresh certification attached to every subsequent change.

| Existing area | Files to inspect first | Preserve and extend |
| --- | --- | --- |
| Outcome delivery | `autonomy.py`, `orchestrator.py`, `cli.py` | Research dossiers, copied workspaces, plan/implement/verify/repair, contracts, focused Python/Node tests, resume validation. |
| Runtime | `runtime.py`, `worker.py`, `checkins.py` | Atomic job claims, one worker per project, interrupted-job pause/review, dependency checks, local attention, emergency stop. |
| Authority and application | `kernel.py`, `approvals.py`, `promotion.py`, `self_repair.py` | Capability checks, persisted decisions, content-bound promotion, recovery journals, rollback conflicts. |
| Integration groundwork | `extensions.py`, `integrations.py`, `mcp_addons.py`, `secrets.py` | Package/scope hashes, reviewed scaffolds, secret references, Telegram blueprint. These are not a running general adapter service. |
| Personal context | `identity.py`, `intent.py`, `context_manager.py`, `context.py`, `knowledge.py` | Intent separation, validated/retractable memory, code context, lessons, provenance. Consolidate overlapping stores carefully. |
| Models and evaluation | `models/ollama.py`, `models/routing.py`, `evaluation.py`, `protocol_benchmark.py` | Local backend, measured reputation, evaluation records, input/output request limits. Recorded specialist roles are not proof those roles execute. |
| Research | `research.py`, `tools/web_tools.py`, research functions in `cli.py`, `provenance.py` | Source/quote gating, evidence gaps, identity/diversity checks, bounded source bodies, provenance graphs. |
| Execution | `tools/cmd_tools.py`, `tools/process_runner.py`, `command_budget.py`, `security.py` | Shell-free dispatch, bounded output, Windows descendant cleanup, stop polling, durable command budgets. A copied directory is not OS isolation. |
| Observation and control | `devices.py`, `perception.py`, `control_plane.py`, `api_server.py`, `dashboard.py`, `tui.py` | Device declarations, opt-in observations, loopback API, current UI controls. A registered device is not an implemented control adapter. |

All paths in this table are relative to `ghost_agent/`.

Known limits to preserve in planning:

- No complete provider-neutral model execution interface or persistent personal initiative engine.
- No demonstrated production Telegram/Huawei/DeskSentinel runtime integration.
- No host filesystem/network containment for generated commands.
- Some persistent stores lack consistent atomicity, locking, corruption handling, or shared transaction boundaries.
- Current worker recovery already exists but is conservative; one interrupted paused job can hold further dispatch.
- Extension tests and self-repair evidence need trusted execution and runtime integrity, beyond caller-supplied status/results.
- Functional acceptance and live model reliability are not established by unit-test totals.
- Body limits and command timeouts are not whole-job inference, network, memory, or compute quotas.
- Current state is mostly project-scoped; a persistent personal supervisor needs a deliberate instance/project separation.

Historical evidence, to read only when relevant:

- [Original assessment](outputs/audit-2026-09-06/assessment.md) — historical defects; many were subsequently repaired.
- [Dispatch hardening](outputs/audit-2026-09-06/hardening-progress.md).
- [Worker recovery](outputs/audit-2026-09-06/worker-progress.md).
- [Promotion integrity](outputs/audit-2026-09-06/promotion-progress.md).
- [Extension integrity](outputs/audit-2026-09-06/extension-integrity.md).
- [Process lifetime](outputs/audit-2026-09-07/process-lifetime.md).
- [Research verification](outputs/audit-2026-09-06/research-progress.md).
- [Research download limits](outputs/audit-2026-09-06/research-download-limits.md).

Do not repeat a historical finding as current without checking the implementation and a relevant test. Do not replace a working subsystem merely because this specification uses a new conceptual name.

### 3.2 OpenJarvis reuse map

Evidence below was checked against public documentation and selected published source on 9 September 2026. These are reuse candidates with documented implementations, not claims that every behavior has passed Ghost's acceptance suite. OJ01 records the actual release/commit used; workers check that pin rather than relying on moving documentation.

Upstream source paths below are relative to `src/openjarvis/` in the OpenJarvis checkout. They are lookup starting points, not a promise that all are public extension APIs.

| Ghost need | OpenJarvis candidate and evidence | Default worker action | Ghost-specific remainder to verify or add |
| --- | --- | --- | --- |
| Model backends and generation | `engine/`, SDK; [engine interface](https://open-jarvis.github.io/OpenJarvis/architecture/engine/) | Reuse engines and normalize only missing result fields at the bridge. | Small-model protocol handling, allowed endpoints, cancellation, budgets. |
| Application composition | `sdk.py`, `system/`; [Python SDK](https://open-jarvis.github.io/OpenJarvis/user-guide/python-sdk/) | Compose through supported APIs, beginning with `Jarvis`. | Ghost intent/contracts, explicit instance/project scope, compatibility layer. |
| Persistent/scheduled execution | `operators/`, `scheduler/`, `daemon/`, `agents/operative.py`; [scheduled monitor](https://open-jarvis.github.io/OpenJarvis/user-guide/scheduled-monitor/) | Adopt operator/scheduler execution and configure its lifecycle. | Recovery/receipt guarantees, interactive responsiveness, idle policy. |
| Proactive proposals and decisions | `agents/proactive_agent.py`, `tools/proactive_tools.py`; [proactive source](https://open-jarvis.github.io/OpenJarvis/api-reference/openjarvis/agents/proactive_agent/) | Extend proposal/decision tools; reuse scheduling and existing feedback mechanisms. | General opportunity selection beyond routine items, S07 evaluation, capability acquisition. |
| Telegram and other channels | `channels/telegram.py`, channel/server integration; [Telegram source](https://open-jarvis.github.io/OpenJarvis/api-reference/openjarvis/channels/telegram/) | Configure and bridge existing transport. | Pairing, owner/task identity, duplicate receipts, restart and revocation checks. |
| Persistent memory | `memory/`, `sessions/`; [memory service](https://open-jarvis.github.io/OpenJarvis/api-reference/openjarvis/memory/service/) | Reuse memory/session service; import or project Ghost records deliberately. | Corrections, provenance, sensitivity, invalidation and learner/resource relationships. |
| Research and documents | Research agents, connectors, retrieval/storage tools; [upstream agent overview](https://github.com/open-jarvis/OpenJarvis#built-in-agents) | Configure existing research/retrieval tools. | Preserve Ghost evidence gates where stronger; lesson citations, feasibility records, source lifecycle. |
| Tools, skills, MCP, workflows | `tools/`, `skills/`, `mcp/`, `workflow/`; [upstream overview](https://github.com/open-jarvis/OpenJarvis) | Register Ghost tools/skills and reuse upstream workflows/importers. | Tested acquisition/activation of missing capabilities, immutable verification and maintenance. |
| Container execution | `sandbox/runner.py`; [ContainerRunner](https://open-jarvis.github.io/OpenJarvis/api-reference/openjarvis/sandbox/runner/) | Qualify/configure the supplied runner first. | Actual host mount/network/credential limits, resource controls, stop/cleanup; patch only failed requirements. |
| Security and audit | `security/`, approval tools; [security architecture](https://open-jarvis.github.io/OpenJarvis/architecture/security/) | Compose existing policy/scanning/audit with retained Ghost controls. | One enforced authority across tool paths; streaming/data-routing behavior and revocation. |
| Traces, routing, optimization | `traces/`, `learning/`, `telemetry/`; [learning architecture](https://open-jarvis.github.io/OpenJarvis/architecture/learning/) | Reuse trace collection, routing, benchmark/configuration improvement. | Owner utility, independent checks, teacher budget/privacy, update eligibility. |
| API, UI and future interfaces | `server/`, upstream frontend, `speech/`; [installation/interfaces](https://open-jarvis.github.io/OpenJarvis/getting-started/installation/) | Extend existing views/routes and clients. | Ghost opportunity, learning, device, grant, evidence, and override controls. |

Missing upstream coverage is a verification question, not a reason to immediately rebuild an entire category.

### 3.3 Compatibility, evidence limits, and upstream maintenance

Concrete compatibility finding: Ghost's current local virtual environment reports Python 3.14.2. The inspected OpenJarvis package requires Python `>=3.10,<3.14` and classifies the project as alpha. OJ01 must use a separate supported environment, defaulting to Python 3.13 if compatible with the selected pin, rather than installing into or downgrading the existing Ghost environment. Check required extras and any native/Rust components for the chosen features. [Inspected package metadata](https://github.com/open-jarvis/OpenJarvis/blob/main/pyproject.toml)

The preferred composition runs both packages in that new compatible environment after tests. If dependencies cannot coexist, use a separately versioned local service boundary through verified upstream APIs. Record the reason; do not write a second runtime because a Python-version mismatch is inconvenient. A separate process/environment is dependency separation, not execution containment.

The repository publishes Apache-2.0 licensing. Preserve applicable license/notice/attribution files and record dependency/model/skill licenses separately. Do not assume all downloaded models or community skills share the repository license. [Upstream license](https://github.com/open-jarvis/OpenJarvis/blob/main/LICENSE)

OpenJarvis's roadmap identifies further work on operator liveness, capability wiring, event triggers, rate limits, and rollback. Documentation can lag code; use these as targeted checks at the pinned revision, not automatic defect claims. [Upstream roadmap](https://open-jarvis.github.io/OpenJarvis/development/roadmap/)

The documented security wrapper is composable and optional, and its output streaming scan is post-delivery. Ghost must explicitly configure its required boundaries and test the relevant path; a wrapper name does not establish enforcement. [Security behavior](https://open-jarvis.github.io/OpenJarvis/architecture/security/)

The learning documentation describes a stronger teacher diagnosing traces and proposing harness/configuration edits that are evaluated and retained or reverted. This is a valuable starting point for R13. Qualification must distinguish configuration improvement from independently developing a new device integration or safely updating application code. Paid-teacher use is optional and budgeted; local-only operation stays available. [Learning and spec search](https://open-jarvis.github.io/OpenJarvis/architecture/learning/)

Dependency discipline:

- Record upstream tag/commit, package lock, selected extras, interpreter/platform, image digests, configuration, and qualification artifacts.
- Keep upstream code separate from owner data and Ghost source; no untracked vendor dump.
- Prefer public APIs/registries. Isolate necessary private-interface use in one bridge with a compatibility test.
- Keep focused upstream patches in a documented patch queue with their reason, tests, base commit, and upgrade disposition.
- Qualify upgrades in a candidate environment, then use controlled rollout and known-good rollback.
- Reuse upstream tests; add tests for Ghost's contracts and actual uncovered gaps.
- Do not build a custom replacement merely because a feature is named differently upstream.
- Disable unrelated connectors, background services, model downloads, external analytics, and paid inference unless selected in the deployment configuration.
- For source lookup, use official docs and the pinned repository. Affiliations, contributor counts, and feature lists are not substitutes for execution evidence.
- Neither upstream installation nor service activation occurs merely by editing this roadmap.

## 4. Architecture and trust boundaries

Ghost supplies the personal product layer. OpenJarvis supplies the default agent runtime, model engines, channels, memory/retrieval services, and reusable learning infrastructure. A narrow integration boundary carries Ghost's contracts and authority into actual upstream execution.

```text
Owner: OpenJarvis UI / Telegram / Ghost CLI / future clients
                              |
               Ghost personal goals and context
         Opportunities / learner progress / device intent
                              |
               Ghost-OpenJarvis integration boundary
       Identity / scoped grants / budgets / receipts / contracts
                              |
                    Pinned OpenJarvis runtime
      Agents / operators / scheduler / engines / memory / tools
                              |
            Existing connectors + Ghost-specific extensions
        Research / school / Rust and CTF / Huawei / DeskSentinel
                              |
              Qualified runners and independent evaluation
                              |
          Activation / feedback / maintenance / controlled updates
```

Default ownership:

| Concern | Default owner | Integration rule |
| --- | --- | --- |
| Owner goals, opportunities, learner/device meaning | Ghost product extensions | Use upstream services; persist only semantics they do not already represent. |
| Agent inference, channels, operational schedule/session state | OpenJarvis | One active owner per job/channel. Ghost holds references, not a competing scheduler or polling loop. |
| Personal memory and retrieval | Selected upstream stores/services | Preserve Ghost validation/sensitivity metadata through extensions or references; avoid writable duplicate memory stores. |
| Scoped authority and verification eligibility | Ghost policy integration with upstream enforcement | One decision source; every enabled tool path must reach it. Upstream and Ghost controls cannot disagree silently. |
| Candidate creation and evaluation | Qualified upstream agents/runners/evals plus retained Ghost acceptance | One executor per step; evidence is tied to the tested bytes and environment. |
| Live-file promotion and protected updates | Existing Ghost mechanisms until a qualified replacement exists | Expose as a scoped tool, never bypass because upstream has a generic file-write tool. |

The legacy Ghost orchestrator may remain as a selected compatibility executor during migration. Do not run two planners, workers, or retry loops over the same task. Replace components one at a time after acceptance and retain a recovery route.

Documents, messages from unpaired identities, fetched pages, tool output, generated code, and model responses are data. None can grant authority merely by containing instructions.

### 4.1 Logical services; incremental implementation

Compose OpenJarvis's existing interfaces before creating new services. A single-host deployment with a small Ghost bridge and qualified isolated workers is the initial target. Avoid a wholesale rewrite, a deep fork, or a new distributed platform. Add a local service boundary only for demonstrated compatibility/lifecycle needs; add distributed execution after local correctness.

Separate:

- Personal instance state: owner identity, channel bindings, global goals, opportunities, devices, standing policies, and model configuration.
- Project state: source workspace, project goals, build artifacts, task checkpoints, and project-specific provenance.
- Execution state: leased jobs, isolated worker identity, resource reservations, checkpoints, and side-effect receipts.

The instance root must be explicit and independent of the process's current working directory. Switching projects must not create a new owner or lose the conversation. Project-specific context must not leak into unrelated projects or model endpoints.

## 5. Core requirements and logical contracts

Sxx and Rxx identifiers remain stable. The former WP01-WP24 implementation queue is superseded by the OJ assignments in section 7. Requirements below describe externally observable contracts of the combined Ghost/OpenJarvis system. Map them onto upstream types before adding local ones; shared names do not require duplicate stores.

### R01. Durable state, events, and recovery

Implementation route: Reuse upstream scheduler/operator/session stores and events; retain Ghost contracts/receipts through references or small extensions. Close specific transaction/recovery gaps instead of replacing all persistence.

- Introduce versioned records and migrations with backups, validation, and rollback.
- Map upstream state ownership first. For missing cross-record guarantees, use transactions, an outbox, or explicit reconciliation; do not introduce another generic database merely because both projects already have stores.
- Preserve existing JSON/artifact exports and importer compatibility where needed.
- Corrupt safety/authority state must not silently become permissive empty defaults.
- Events need stable IDs, source, occurrence and ingestion times, schema version, correlation/causation IDs, sensitivity, and payload.
- Use durable subscriptions/cursors, retries, deduplication, and a bounded failed-event queue.
- A job needs stable identity, owner/lease generation, attempt count, checkpoint reference, budget reservations, next eligible time, and lifecycle timestamps.
- Recover safe internal work automatically; reconcile uncertain external actions before repeating them.
- Do not promise universal exactly-once delivery. Use provider idempotency keys or receipt reconciliation where supported; otherwise preserve an explicit uncertain result.
- A slow research/model/build job must not block incoming messages, stop controls, or the entire scheduler.
- Reconcile outcome, scheduler, goal, and event state after partial completion; one failed record write must not manufacture success.

Suggested durable states:

```text
queued -> running -> completed
             |----> failed
             |----> paused -> queued
             |----> waiting_input -> queued
             |----> waiting_external -> queued
queued/paused/waiting_* -> cancelled
running -> cancelling -> cancelled
```

Migration must map existing statuses explicitly. An expired lease is a recovery trigger, not permission to repeat an unknown external side effect.

### R02. Personal context, memory, and correction

Implementation route: Reuse OpenJarvis memory, session and retrieval services. Add only missing personal semantics and preserve corrections, provenance and deletion behavior during migration.

Store facts, preferences, goals, observations, hypotheses, procedures, and feedback as distinguishable record types.

Minimum context fields: ID, kind, subject, value/content, source reference, confidence, observed/created time, last verified time, expiry, sensitivity, supporting evidence IDs, and retraction state.

- User statements may establish preferences; inferred preferences remain labeled hypotheses.
- Historical memory must not substitute for current device state.
- Preserve contradictions and corrections; invalidate dependent plans/material where needed.
- Offer inspect, edit, forget, export, and retention controls.
- Deleting a source removes or invalidates its derived embeddings, summaries, quizzes, and inferred records according to the configured retention policy.
- Clearly disclose backup retention and what a deletion can or cannot remove from old backups.
- Build a capability/resource graph: owner goals, projects, devices, services, documents, accounts, skills, and their actual relationships.
- Maintain a learner model with concepts, prerequisites, attempts, mistakes, evidence of mastery, and review timing.
- Personalization remains useful without camera, microphone, browser history, or health access.

### R03. Initiative and opportunity selection

Implementation route: Extend OpenJarvis's proactive/operative agent mechanisms with Ghost's opportunity records, goal-linked selection, unfamiliar-context evaluation and feedback. Do not implement another generic agent loop.

Triggers include new relevant context, approaching deadlines, recurring failures, owner feedback, integration health changes, goal staleness, and permitted idle windows.

The loop is: assemble relevant context -> generate candidate opportunities -> check grounding/novelty -> estimate benefit/cost -> choose the smallest informative next step -> schedule or defer -> evaluate -> update.

An opportunity needs:

- ID, trigger/event IDs, owner goal IDs, and evidence references.
- Problem hypothesis and expected owner benefit.
- Alternatives considered, including reuse or doing nothing.
- Required capabilities, unknowns, risks, and any missing external input.
- Cost/effort estimate with uncertainty, deadline, and interruption cost.
- Experiment/deliverable contract, evaluation method, deduplication key, status, and review time.
- Feedback, outcome evidence, and reasons for rejection/retirement.

Start with a transparent configurable ranking of goal relevance, urgency, confidence, expected benefit, effort, and interruption cost. Record reasons; do not present an arbitrary score as validated utility. Tune ranking using observed outcomes.

Bound active initiatives, idea generation, retries, and idle compute. Coalesce duplicate triggers. Reconsider rejected ideas only when relevant evidence changes or their review interval arrives.

The owner can set "research and prototype useful improvements when idle" once. Candidate work within that policy should proceed without repeated prompts. Initiative generation may happen before all build capabilities exist; its state must accurately identify what is actionable.

### R04. Standing autonomy, permissions, and owner control

Implementation route: Wire existing Ghost grants/approvals and upstream capability/approval controls into one enforced decision path. Qualify actual dispatch, including built-in tools and background execution.

Use structured permissions with inspectable scopes, not natural-language keyword matching as the final authorization mechanism.

A grant records principal, capability, resource scope, permitted side effects, conditions, limits, expiry, provenance, and revocation state. Evaluate it at dispatch and during long-lived sessions where applicable.

Recommended configurable modes:

| Mode | Expected behavior |
| --- | --- |
| Suggest | Research within available read access and propose work; no automatic feature activation. |
| Build | Research, create code/content, install pinned dependencies inside eligible isolated workers, and test within a configured budget. |
| Operate | Run specified messaging, reminders, service, or device capabilities under standing scopes. |
| Improve | Build and evaluate candidate updates; automatically activate eligible changes only under an explicit update policy. |

Modes compose by capability; they are not a global unrestricted-access switch.

Do not ask again for a covered action. Ask only for missing facts, necessary account participation, materially expanded authority, unavailable credentials, or an unresolved consequential decision. Continue independent preparation while waiting.

Examples:

- A paired Telegram chat may receive replies and task notifications within its grant.
- Routine servo motion within established limits need not request approval per movement.
- A scoped focus mode may block configured distractions during a session and must remain easy to override.
- Merely mentioning homework or owning a device does not authorize surveillance, application blocking, firmware flashing, or data sharing.
- A research page cannot authorize installing its recommended software on the host.
- New permissions or changes to the authority/evaluation infrastructure cannot be self-approved.

Revocation cancels relevant queued work, closes affected sessions, and stops future actions. Emergency stop must bypass model availability and normal job queues; measure its effect and latency for each backend.

### R05. Model interface and inexpensive execution

Implementation route: Use OpenJarvis engines and routing as the implementation. The Ghost bridge adapts only missing protocol, privacy, cancellation or usage contracts.

Map OpenJarvis's generation/streaming, discovery, health and usage interfaces to Ghost's contracts. Supply missing cancellation, limits, finish-reason, truncation or structured-error behavior at the smallest suitable boundary.

A model profile includes provider/endpoint identity, context/output limits, format/tool support, modalities, measured task-class reliability, latency, resource needs, and cost information. Unknown limits or costs remain unknown, not zero.

- Configure existing upstream Ollama and compatible-endpoint engines; run the same Ghost contract checks through each. Write a new provider adapter only if the selected provider is absent or a specific interface gap is demonstrated.
- Native tool calling is optional. A constrained JSON/text action adapter is a supported fallback.
- Validate all actions before execution; repair malformed output within a bounded allowance.
- Record explicit inability, timeout, refusal, truncation, or invalid output as such.
- Use task-specific context with retrieved evidence and compact checkpoint summaries.
- Treat exact token accounting, character estimates, response-size limits, and backend-enforced output limits as different controls.
- Bound transport bytes, total request time, retries, and whole-job usage in addition to model output requests.
- Reserve shared budgets atomically across parallel calls; persist consumption over resume.
- Use deterministic software for timers, state changes, parsing, arithmetic, eligibility checks, and permission decisions.
- Assign one narrow action or implementation step at a time when a model struggles.
- Reuse validated procedures and cached versioned research; invalidate stale cache entries.
- Invoke additional reviewers/specialists when the task warrants it; do not mandate multiple models on every trivial step.
- Two prompts to the same model are not independent functional verification.
- Route personal/health/source data according to endpoint-specific policies.

Demonstrate text-only protocol conformance without proprietary credentials. Report live task success by model and task class; do not claim that all model sizes achieve the same outcomes.

### R06. Research, discovery, and practical feasibility

Implementation route: Compose upstream research agents, connectors and retrieval tools with useful existing Ghost evidence checks. Add feasibility/domain-specific outputs, not another general search stack.

Research should resolve specific unknowns for a goal or opportunity. Avoid repeating a broad web search before every coding step when local code and current verified documentation suffice.

Produce a feasibility record: need, candidate approaches, sources and checked dates, platform/version constraints, prerequisites, scopes, expected data, cost, maintenance burden, unanswered questions, and proposed next step.

- Prefer official API/SDK documentation for implementation.
- Distinguish observed API behavior, documentation claims, inference, and speculative use.
- Preserve exact source identity, relevant quotations, contradictions, and evidence gaps.
- Support permitted HTML, document/PDF, repository, and scholarly sources through bounded adapters.
- Enforce scheme/destination policy, private-network restrictions unless explicitly scoped, redirect checks, decompression/body limits, and total deadlines.
- Prevent secret-bearing requests and fetched instructions from crossing authority boundaries.
- Account creation, developer review, unsupported regions, and unavailable API scopes are durable blockers with a next action; they must not turn into infinite retries.
- Provider-specific behavior belongs in adapters and feasibility records, not in the generic initiative engine.

Provider starting references, to verify again when implementing:

- [Telegram tutorial](https://core.telegram.org/bots/tutorial).
- [Telegram Bot API](https://core.telegram.org/bots/api).
- [Huawei Health Kit overview](https://developer.huawei.com/consumer/en/hms/huaweihealth/).
- [Huawei verification documentation](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/verification-0000001211587947).

These references are starting points, not promises of current eligibility, exact endpoints, or available watch metrics.

### R07. Isolated execution and independent acceptance

Implementation route: Qualify OpenJarvis ContainerRunner and evaluation facilities first. Configure or patch demonstrable gaps; retain existing Ghost acceptance/promotion controls where needed.

The current Ghost command runner is a foundation for process control, not hostile-code containment. Qualify and configure an existing OpenJarvis OS-enforced runner on the owner's actual host before granting unattended generated-code execution. A replacement backend is allowed only after an evidenced incompatibility or unsatisfied requirement.

Record and demonstrate filesystem mounts, writable paths, network policy, credential delivery, child-process lifetime, resource limits, dependency environment, cancellation, and cleanup. Explicitly report unavailable isolation; do not silently fall back to host execution.

- The supervisor, credentials store, approval records, and evaluator live outside candidate-writable space.
- Build dependencies are installed within an eligible isolated environment from declared sources, with recorded versions.
- Project ecosystems choose appropriate compile, type, unit, integration, UI, property, and smoke checks.
- Browser-facing games/study tools require actual interaction checks in addition to build success.
- Acceptance is task-specific and captured before implementation. Required filenames alone are insufficient.
- A trusted evaluator runs acceptance checks against the candidate without executing candidate-controlled evaluator code on the trusted host.
- Bind evidence to candidate bytes, dependencies/environment identity, check versions, runner identity, timestamps, exit status, and artifacts.
- Tests added by the implementation help, but cannot replace protected acceptance.
- Changing candidate bytes or relevant dependencies invalidates associated verification.
- Include negative controls: broken implementations must fail.
- Cancellation and budget exhaustion preserve useful checkpoints and honest terminal states.

### R08. Integration factory and live capability service

Implementation route: Use upstream tool/skill/MCP/workflow registration and existing connectors as the capability substrate. Add Ghost's acquisition and verified activation lifecycle only where absent.

Replace blueprint-only completion with an executable lifecycle:

```text
discovered -> researched -> feasible -> building -> testing -> ready_to_activate -> active
active -> degraded -> active
ready_to_activate / active / degraded -> disabled -> ready_to_activate
disabled -> retired
```

Research/build/test phases may enter failed or waiting_external with a saved resume phase and an exact missing prerequisite. Resume rechecks eligibility and evidence. A disabled package must satisfy activation checks again. Define exact transitions and retries in code, including rollback and a durable reason for every failure or wait.

A package requires a versioned manifest, typed inputs/outputs, dependency locks, implementation, meaningful tests, health checks, documentation, rollback/migration behavior, and immutable artifact identity.

Map packages to upstream tool/skill/connector manifests and attach missing Ghost lifecycle metadata. Keep one canonical manifest authority; do not introduce an incompatible duplicate package ecosystem. If JSON/YAML exports coexist, generate them from that authority.

The service owns configuration, credential references, pairing, startup/shutdown, retry/backoff, rate limits, operational logs, and health. Generated adapters run in scoped workers and request brokered capabilities; they do not execute unrestricted inside the supervisor.

Check package integrity at activation and restart. Post-verification edits invalidate eligibility. Distinguish tests passed, account access pending, configured, activated, and operationally verified.

Maintain an inventory of dependencies, licenses, versions, external endpoints, data scopes, and affected services for updates and retirement. MCP is one optional adapter protocol, not an automatic authority grant.

### R09. Messaging, presence, attention, and focus

Implementation route: Use upstream Telegram/channels, sessions, proactive decision tools and notifications. Add owner-specific identity, attention/focus policy and missing controls.

- Bind channel identities to one owner identity through an explicit pairing mechanism.
- Preserve conversation IDs, attachments, task IDs, and message delivery receipts across interfaces.
- Keep conversational responsiveness while background work proceeds.
- Support notification importance, quiet periods, batching, cooldowns, deduplication, and acknowledgement.
- Make "why did you do this?", "stop", "undo", "forget this", and "less of this" functional operations.
- Infer activity from enabled signals with confidence and expiry; ask or defer when ambiguity matters.
- Focus mode is a user-controlled session with a purpose, duration, configured interventions, and immediate override.
- Do not treat browser changes, lack of sensor data, or inactivity alone as proof of distraction.
- Evaluate interruption usefulness, not message count or engagement time.
- Future voice/mobile clients share the same authority and task service; they do not create separate agents with divergent memory.

### R10. Learning, study, and personal knowledge

Implementation route: Build learning behavior on upstream ingestion/retrieval/research/UI services; Ghost adds concept mastery, revision, exercises and independent checks.

- Ingest supplied documents into a versioned source library with provenance and access policy.
- Use citations, extraction-quality flags, and explicit unknowns in answers.
- Generate revision schedules, questions, hints, examples, projects, and optional games.
- Track conceptual mastery from evidence; task completion alone does not imply understanding.
- Compare instructional approaches through small owner-relevant experiments.
- Keep answer keys and protected tests separate from learner-editable artifacts.
- Manage local practice environments and challenge resets.
- Personal notes, decisions, and project history can share retrieval infrastructure with distinct scopes.
- Defer costly custom UI development when existing components already meet the goal.
- Evaluate educational usefulness separately from software correctness.

### R11. Devices, health, and physical-world behavior

Implementation route: Implement device-specific protocols and personal observations as upstream-compatible tools/connectors. Reuse telemetry, scheduling, grants and evidence infrastructure.

A device record needs identity, ownership, transport, firmware/API version, observed capabilities, configured limits, pairing state, freshness, and availability.

- Separate declared capabilities from verified commands/data.
- Prefer read-only observation before physical experiments.
- Support simulated devices, loss of connection, rate/angle limits, conflicting command ownership, and a stop mechanism.
- Validate inbound telemetry and prevent device data from becoming instructions.
- Support time zones, clock skew, units, missing samples, and stale data explicitly.
- Health observations need selective retention, access restrictions, and endpoint routing.
- Trend summaries present evidence and uncertainty; clinical/predictive features require their own validation and scope before activation.
- Future device routing may place continuous monitoring on an enrolled low-power device and heavy builds on the desktop.
- Remote enrollment, authentication, revocation, offline behavior, and version compatibility precede remote execution.

### R12. Background operation and resource management

Implementation route: Extend upstream operators, scheduler, daemon and telemetry with PC-idle admission, owner priorities, persisted budgets and measured pause behavior.

Define configured modes such as interactive, idle, quiet-hours, and maintenance. Idle and quiet hours are separate: the owner may want background work but no messages.

- Use opt-in presence/idle signals; missing presence means uncertain, not permission to consume all resources.
- Assign configurable ceilings for CPU, RAM, GPU/VRAM, disk, network, inference usage, cost, runtime, and concurrent jobs.
- Reserve resources before work and reclaim them after completion or reconciled failure.
- Interactive tasks outrank speculative builds.
- Avoid launching work whose minimum requirements exceed available resources.
- Detect return/activity and pause, checkpoint, or throttle according to backend support and a documented deadline.
- Resume work after restart only when authority, data freshness, dependencies, and budgets remain valid.
- Coalesce missed timers and bound recovery backlog.
- Provide installation/status controls for the background service; do not silently install startup behavior.
- Report what ran, what was verified, what remains pending, and the actual resources measured.
- Periodic self-review must target a bounded failure/opportunity backlog, not unconstrained architectural reinvention.

### R13. Self-improvement, deployment, and maintenance

Implementation route: Reuse upstream traces, learned routing, optimization/spec-search and benchmark facilities. Retain Ghost application-promotion controls and add missing independent evidence or update boundaries.

Keep stable and candidate versions separate. The updater and evaluation boundary cannot be rewritten by the candidate under evaluation.

An improvement proposal needs a trigger, hypothesized benefit, affected components, candidate digest, baseline, evaluation plan, budget, rollback plan, and deployment eligibility.

- Evaluate the candidate against a protected regression set and relevant held-out scenarios.
- Track both improvements and regressions in reliability, latency, resource use, and owner usefulness.
- Candidate code cannot mark itself verified or edit the evidence store.
- Self-authored benchmark cases may expand coverage but cannot replace the protected baseline.
- Repeated failed attempts stop or defer with preserved evidence.
- Runtime integrity, health checks, shadow operation where practical, and limited initial activation precede broad rollout.
- Keep a known working version and a tested recovery path, including state-schema compatibility.
- Routine updates may activate under an existing scoped update policy.
- Changes to permission enforcement, evaluator authority, updater trust, data routing, or owner goals require the separate owner-controlled update process.
- Ghost must never optimize by weakening controls, changing success criteria, hiding failures, or expanding its own authority.
- Routine maintenance includes dependency failures, API changes, expired credentials, stale integrations, storage growth, and retirement of unused services.

### R14. Control plane and additional personal capabilities

Implementation route: Extend upstream UI/API/client surfaces and keep useful Ghost CLI compatibility. Add product-specific views rather than a second complete control plane.

The UI/CLI/API should expose current work, its reason, expected benefit, evidence, permissions, costs, and recovery controls. They are operational interfaces, not only log viewers.

Include:

- Goals, projects, opportunities, deadlines, and unresolved decisions.
- Personal memory inspection/correction and a visible source for each important inference.
- Integration setup progress, health, credentials-needed states, and disable controls.
- Device status, simulation results, operating limits, and stop controls.
- Source library, learning progress, revision plans, and generated artifacts.
- Model capabilities, actual route/usage, and failure reasons.
- Job pause/resume/cancel, update/rollback, notifications, and data export.
- Authentication usable by the UI itself, with secure local/remote pairing as relevant.

Additional aspects that follow the same product idea:

| Capability | Expected personal behavior | Prerequisite |
| --- | --- | --- |
| Deadline preparation | Prepare relevant lessons, files, or project context before a test or planned work session. | Authorized calendar/deadline context, source access. |
| Project continuity | Remember where work stopped, dependencies, and the next useful step across channels/devices. | Durable tasks and scoped context. |
| Personal experiments | Compare reminder timing or teaching formats, keeping what helps. | Feedback and measurable outcomes. |
| Maintenance ownership | Notice and repair the services/artifacts Ghost created. | Health checks and scoped update policy. |
| Cross-project learning | Reuse a validated ESP32/debugging procedure in later work. | Provenance, relevance checks, privacy boundaries. |
| Workload support | Surface conflicting commitments and suggest a manageable plan. | Correctable goals and time estimates; no medical inference required. |
| Resource stewardship | Schedule demanding work and avoid unapproved spend. | Resource accounting and admission controls. |
| Forgetting and retirement | Remove obsolete memories, unused experiments, and redundant services. | Retention policy, dependency inventory, recoverability where needed. |
| Degraded/offline operation | Continue local tasks and queue eligible remote work without pretending connectivity. | Capability health, durable queues, clear UI state. |
| Backup and portability | Restore the assistant on another owned machine without losing goals or exposing credentials. | Versioned export, encrypted sensitive storage where needed, migration tests. |

These are extensions of the core loop, not separate hardcoded personalities or autonomous agents competing for control.

## 6. Shared record and interface rules

These are minimum logical contracts of the composed product, not instructions to create every listed dataclass/table. Reuse upstream records and store Ghost-specific extensions or external IDs only where fields/semantics are missing. Record one write authority for each entity.

| Record | Required content beyond ID/version/timestamps |
| --- | --- |
| Intent | Original request/source, inferred goal, ambiguity, plan reference, owner/project, authorization references. |
| Goal | Owner benefit, parent/dependencies, priority, deadline, constraints, deliverable contract, status. |
| Observation | Subject, value/units, source, occurrence/receipt time, confidence, freshness, sensitivity. |
| Opportunity | Trigger, goal/evidence links, hypothesis, alternatives, expected benefit, required capabilities, budget, experiment, feedback. |
| Grant | Principal, capability, scoped resources, permitted side effects, conditions, expiry, limits, revocation. |
| Job | Goal/opportunity, project, state, lease generation, checkpoint, attempts, reservations, cancellation state, terminal reason. |
| Action receipt | Job/action IDs, idempotency key, request digest, grant, provider/worker, result or uncertainty, reconciliation state. |
| Integration | Package identity/digest, manifest, configuration references, prerequisites, granted scopes, lifecycle, health. |
| Verification | Candidate/environment/check identities, trusted runner, result, evidence artifacts, expiration/invalidation. |
| Improvement | Failure/benefit hypothesis, baseline, candidate, evaluation references, deployment policy, rollout/rollback state. |
| Feedback | Target action/opportunity, source, explicit or inferred signal, observed outcome, confidence, owner correction. |

Common rules:

- Stable IDs, explicit schema versions, validated types, and UTC timestamps with original time zone where scheduling requires it.
- Sensitivity and access policy follow derived data.
- Optimistic version checks or transactions prevent lost updates.
- Model-produced records are proposals until validated and persisted by trusted code.
- Commands return structured errors with retryability and required next action.
- Current-state transitions and their durable events commit together or use an outbox/reconciliation protocol. Do not directly mutate private upstream SQLite schemas to simulate a shared transaction.
- Credentials are referenced, never embedded in ordinary records or prompts.
- A model's narrative summary is not the source of truth for execution status.
- Proposed service interfaces should cover context retrieval, opportunity generation/selection, job submission/control, capability invocation, verification, and activation without exposing private storage mutation methods.
- Keep public contracts small; domain adapters own provider details.

## 7. OpenJarvis-first delivery plan for inexpensive workers

This queue supersedes the former WP01-WP24 build assignments. Execute OJ01-OJ16. The Sxx scenarios and Rxx requirements remain the product contract; passing them through upstream software counts fully.

The default implementation sequence is a thin working composition first, then missing personal behavior. Do not build a complete alternative runtime before trying a real upstream path.

### 7.1 Reuse decision and evidence ledger

Keep one concise ledger under `outputs/openjarvis-integration/`. Each component entry records:

- Requirement/scenario and upstream pin.
- Source path/public API, relevant configuration, and existing upstream tests.
- Evidence level: documented, inspected, executed locally, or live-provider verified.
- Decision: reuse, configure, adapt, focused patch, Ghost-specific implementation, or defer.
- Exact failed/missing acceptance behavior, if any.
- Change location and single state/authority owner.
- Minimal verification command/artifact and actual result.
- Remaining input/platform limitation and the next bounded action.

Use the ledger to stop repeated discovery work. Once a component passes, reuse its evidence until code, configuration, environment, or requirements materially change.

A missing owner-specific field does not justify a new database. A missing UI control does not justify a new frontend. A failed API assumption does not justify a new SDK. Apply the smallest change that closes the recorded gap.

### 7.2 Ordered work packages

Each row is an integration outcome. Split large rows into short independently testable slices. Components already meeting acceptance can close with configuration and evidence only.

| ID | Deliverable and reuse target | Depends on | New work allowed | Acceptance |
| --- | --- | --- | --- | --- |
| OJ01 | Pin OpenJarvis and establish an isolated compatibility environment; inspect its SDK, packaging and needed feature tests. | None | Lock/configuration, small smoke fixture, concise reuse ledger. | Exact tag/commit, supported interpreter, minimal dependencies, import/health and harmless configured model call when available. Existing Ghost environment/state unaffected; unavailable service recorded honestly. |
| OJ02 | Compose Ghost with OpenJarvis engines/agents through supported SDK interfaces; preserve a useful Ghost CLI entry point and reuse upstream UI/API. | OJ01 | Thin bridge for task/context/result IDs, error translation, config and lifecycle. | A request reaches a real upstream engine/agent and returns traceable results; explicit endpoint/config/state root; timeout and shutdown checks. No duplicate model client or generic agent loop. |
| OJ03 | Connect owner identity, structured grants, receipts and project boundaries to upstream dispatch. Map state ownership before migrations. | OJ02 | Missing policy hooks, typed metadata/ID mapping, reversible import, reconciliation. | Built-in and Ghost-added tools obey the same owner/scopes; revocation, cross-project refusal, duplicate action handling, corrupt-state preservation, import/rollback fixtures. No competing approval databases. |
| OJ04 | Configure upstream Telegram and connect it to the same Ghost task/conversation path. | OJ03 | Pairing/session/task bridge and verified transport fixes only. | S01 sender isolation, responses, duplicate updates, reconnect, stop and configuration recovery. Credential-free transport fixtures and a separate authorized live test. No second Telegram implementation. |
| OJ05 | Reuse upstream memory, sessions, connectors, document retrieval and research with Ghost's useful evidence checks. | OJ03 | Context schema projections, migration, source/feasibility metadata and missing deletion/privacy checks. | Fact correction, stale/contradictory data, source-backed answer, deletion invalidation, project/endpoint boundaries. No parallel generic retrieval stack. |
| OJ06 | Qualify upstream container runner and evaluation tools on this host; connect retained Ghost contracts/promotion. | OJ03 | Runner configuration, protected acceptance fixtures, evidence binding and focused hardening. | Real positive/negative filesystem/network/credential tests, timeout/child cleanup, meaningful good/broken candidate results; no evaluator mutation or unsafe host fallback. |
| OJ07 | Adopt upstream operators, scheduler and daemon as the execution owner for composed jobs. | OJ03 | Missing durable receipt/recovery hooks and legacy job migration. | Scheduled run retains state across restart; input/stop remain responsive; duplicate triggers do not duplicate effects; old/new schedulers cannot claim the same task. |
| OJ08 | Configure upstream routing, traces and telemetry for inexpensive local-model work and optional teacher calls. | OJ03 | Missing bounded retries, usage reservations, data-routing and stop hooks. | Repeated/resumed/parallel work retains limits; no unconfigured paid fallback; protocol failures are explicit; compare at least two available routes or report a missing route. Existing optimizer/engine code reused. |
| OJ09 | Extend upstream proactive/operative agents with Ghost opportunity selection and feedback. | OJ05, OJ07, OJ08 | Goal/resource relationships, bounded opportunity backlog, ranking, rejected-idea memory, novelty tests. | S07 unfamiliar context yields a grounded experiment or justified defer; no per-product branches; existing grants suppress repeat questions; ideas have evaluation/stop conditions. |
| OJ10 | Implement general capability acquisition through upstream registries, skills/MCP, research and qualified coding workflows. | OJ05, OJ06, OJ09 | Discover/configure/adapt/build decisions, prerequisite tracking, candidate-bound activation, health/retirement metadata. | Reuse an existing integration before coding; a genuinely absent capability is built/verified in a held-out fixture; account wait preserves other progress; one tool execution owner. |
| OJ11 | Add PC-idle admission, personal attention/focus policy and useful overnight selection around upstream scheduling/channels/telemetry. | OJ04, OJ07, OJ08, OJ09 | Idle adapter, owner priorities, missing resource enforcement, quiet-hours/override behavior. | S06 interrupted work, owner return deadline, budgets and morning digest; controllable focus mode; no inference from missing data; no speculative work when none is worthwhile. |
| OJ12 | Add school revision, learner progress and optional Rust games/CTF activities on reused research/retrieval/coding/UI. | OJ05, OJ06, OJ10, OJ11 | Educational domain logic, assessment content, progress views and task-specific oracles. | S04/S05 cited lessons, compiled Rust, wrong-solution rejection, solvable/resettable CTF, source replacement/deletion and feedback-driven next activity. |
| OJ13 | Add DeskSentinel as an upstream-compatible device tool/connector from actual supplied firmware. | OJ03, OJ06, OJ10, OJ11 | Device-specific protocol, simulator, pairing, physical limits and useful owner interaction. | S03 simulated limits/disconnect/stop; grounded sensor inventory; owner-scoped physical operation separately tested. Reuse scheduler, tool runtime and notifications. |
| OJ14 | Implement the eligible Huawei route as a connector feeding shared observations and attention. | OJ05, OJ10, OJ11 | Provider-specific access/data mapping and conservative health/activity behavior. | S02 developer/access prerequisites, synthetic vs real data, units/time zones, revocation, stale data and privacy. No unsupported diagnosis claims; no duplicate generic connector framework. |
| OJ15 | Connect upstream trace-based learning, optimization/spec search and benchmarks to controlled Ghost improvements. | OJ06, OJ08, OJ10, OJ11 | Personal evaluation tasks, update-policy bridge and application-update evidence beyond upstream configuration edits. | S08 one measured change accepted/rejected with artifacts; failed candidate rolls back; teacher spend/data scoped; candidate cannot alter grants/evaluator; no second general optimizer. |
| OJ16 | Qualify integrated scenarios, expose remaining product controls in upstream UI, and maintain the pinned integration. | OJ04-OJ15 for full qualification; completed slices earlier | Scenario tests, focused UI additions, upgrade/migration checks, later interface/device extensions where demonstrated missing. | S01-S08 evidence by level, actual intervention/cost results, single-task/channel ownership, known-good restore. Native/remote/voice features evaluated incrementally, never declared complete from a module listing. |

Early parallel work: after OJ03, Telegram (OJ04), memory/research (OJ05), isolated execution (OJ06), scheduling (OJ07), and budget/routing configuration (OJ08) can advance independently with explicit file ownership. Merge shared bridge changes sequentially.

Do not wait for all OJ16 work to expose controls. Pairing, cancel, stop, evidence and other controls required to use a feature ship with its package. Full-suite qualification is the final stage, not a reason to delay the first useful Telegram path.

Do not spend an entire package examining every upstream feature. Start with the path needed by the next scenario, execute it, and expand investigation only for a failed check or the next feature.

### 7.3 Milestones and saved effort

| Milestone | Evidence the owner can see | Main assignments | Reuse expectation |
| --- | --- | --- | --- |
| M1: Ghost talks through OpenJarvis | Local model request and Telegram conversation share identity/tasks; controls and errors work. | OJ01-OJ04 | Existing engines, agent interface, transport and UI. |
| M2: Ghost selects useful work | Context produces an unrequested, grounded opportunity; permitted research/build work starts and survives interruption. | OJ05-OJ11 | Memory, research, operators, scheduling, coding and container tools. |
| M3: Ghost helps with learning | Lessons become cited revision; an activity adapts to actual understanding and passes meaningful checks. | OJ12 | Retrieval/research, build/evaluation, UI infrastructure. |
| M4: Ghost uses personal devices | DeskSentinel behavior and eligible Huawei observations work with configured limits and freshness. | OJ13, OJ14 | Tool/connector lifecycle, messaging, schedules and identity. |
| M5: Ghost improves measurably | A recurring failure leads to an evaluated change, controlled activation and demonstrable rollback. | OJ15 | Upstream trace analysis, optimization and benchmark machinery. |
| M6: Ghost remains maintainable | Repeated scenarios, version upgrades and state recovery work; new interfaces are added as useful. | OJ16 | Upstream UI, clients, runtime fixes and ongoing development. |

Configuring the existing Telegram connector satisfies communication delivery. It does not alone demonstrate creating a missing capability. OJ10 therefore separately tests an unfamiliar capability absent from the pinned installation. Both reuse and new capability construction are required behaviors; needless reimplementation is not.

### 7.4 Worker assignment template

Every worker reads section 1, the relevant reuse-map rows in section 3.2, section 4, this execution policy, and its assigned Sxx/Rxx requirements. Supply selected code/tests and ledger entries instead of the entire repository/transcript.

```text
Task: <OJ ID and one bounded integration slice>
Product behavior: <observable change; Sxx/Rxx>
OpenJarvis pin/environment: <verified commit, interpreter, lock/config>
Reuse target: <upstream symbol/path, supported API, existing tests>
Existing evidence: <ledger references; do not repeat completed discovery>
Ghost-specific gap: <exact missing acceptance behavior, or "none: configure">
State/execution authority: <one owner and any ID mapping>
Owned files/components: <bridge/config/domain extension; shared-file rules>
Acceptance: <positive, negative, recovery checks appropriate to the slice>
Budget: <spend/time/attempt limits; permitted model routes>
Out of scope: <adjacent features and forbidden duplicate components>
Finish with: config/code changes, upstream components reused, exact checks,
evidence level, remaining prerequisites and next bounded assignment.
```

Worker rules:

1. Start with the reused component's real runtime path. A package may need no new production code.
2. Check the selected pin's source and tests when documentation is ambiguous. Do not infer APIs from names or clone whole frameworks from an example.
3. Record a failed or missing requirement before writing a replacement. Prefer configuration, adapters or a focused upstream patch.
4. Preserve the owner-facing product promise and S01-S08. Default upstream behavior may need configuration or an extension to match them.
5. Preserve current Ghost artifacts and useful protections until an equivalent path and reversible migration pass.
6. Do not directly edit installed upstream dependencies or silently float to a different version. Record patches and the tested pin.
7. Do not let upstream defaults bypass owner grants, privacy, budgets, stop controls or independent acceptance.
8. Ensure one scheduler, message poller, retry owner and canonical record per operation. Read-only projections are allowed; competing writable state is not.
9. Add tests for the integration and actual gaps; reuse upstream test coverage rather than copying its suite into Ghost.
10. Distinguish documentation, inspected code, mocks, real local runs, live integrations and owner usefulness.
11. Account, service and hardware blockers do not authorize fabricated completion. Finish independent work and record the exact missing input.
12. Run targeted tests first; broaden checks when integration risk warrants it. Avoid spending model calls interpreting deterministic failures software can report.
13. Keep concise handoffs and bounded attempts. Split oversized work without dropping its acceptance criteria.
14. Submitting upstream PRs/messages is separate from preparing a local patch; do not publish anything without existing authority to do so.

### 7.5 Ready-to-use next assignment

```text
Use implementation.md as the product specification. Read
outputs/openjarvis-integration/2026-09-10.md and docs/openjarvis.md first.
The pinned source, isolated environment, native extension, shared inference
bridge, read-only upstream agent, document memory and Telegram adapter already
exist. Do not restart OJ01 or duplicate those implementations.
OpenJarvis is the selected foundation. Do not implement a replacement model
client, agent loop, scheduler, Telegram transport, memory stack or optimizer.

Implement the next bounded OJ03 authority/task-service slice against the actual
upstream dispatch path. Preserve Ghost's existing grants, owner state, jobs,
test/promotion gates and the single coding executor. The current upstream
assistant is deliberately read-only: do not implicitly enable its built-in
shell, browser, MCP, channel-send, scheduler or direct-operation agents.

Choose one missing acceptance slice with explicit ownership, scope and
revocation tests. Exercise real upstream classes, not only bridge mocks.
Use synthetic local fixtures. Preserve the Telegram patch and reproducible
bootstrap. Do not automatically download models, enable services or change
provider/data-sharing policy. Missing live services are not reasons to build
replacement infrastructure.

Write a concise handoff under outputs/openjarvis-integration/ with:
- Actual files/contracts changed and remaining acceptance criteria.
- Actual commands/results and the evidence level for each integrated path.
- Reuse decisions and only demonstrated compatibility gaps.
- The next bounded slice, preserving this roadmap's dependency order.

Do not run a broad benchmark, enable channels/startup services or consequential
agent tools, or expand beyond the selected slice. Conclude with the evidence
and the next bounded task; do not mark an entire OJ assignment complete from a
partial slice or a model's success claim.
```

The first task includes a working compatibility experiment; do not keep recommissioning a purely conceptual audit. Missing runtime prerequisites should narrow the next task, not trigger a build-from-scratch fallback.

### 7.6 Previous work-package mapping

Old assignments are superseded. Their product coverage is preserved as follows; this table is for migration of existing plans, not an executable second backlog.

| Former assignment | New assignment | Change in approach |
| --- | --- | --- |
| WP01 | OJ01 | Pin and exercise upstream rather than design all primitives independently. |
| WP02, WP03 | OJ03, OJ05, OJ07 | Map/migrate to existing state, memory and scheduling; extend verified gaps. |
| WP04 | OJ03 | Connect authority to actual upstream execution. |
| WP05, WP06 | OJ02, OJ08 | Reuse engines, routing and telemetry. |
| WP07, WP08 | OJ06 | Qualify existing runners/evaluation and keep protected acceptance. |
| WP09, WP14 | OJ10 | Extend capability acquisition over existing tools/skills/connectors. |
| WP10 | OJ04 | Configure the existing Telegram implementation. |
| WP11, WP12, WP13 | OJ05, OJ09, OJ10 | Reuse context/research; add personal opportunity logic. |
| WP15, WP16 | OJ11 | Add idle/focus/attention policy around upstream operations. |
| WP17, WP18 | OJ12 | Build educational behavior on existing research/coding/UI. |
| WP19, WP20 | OJ13, OJ14 | Build only provider/device-specific gaps. |
| WP21 | OJ15 | Integrate upstream learning with Ghost's required update controls. |
| WP22, WP23, WP24 | OJ02, OJ16 | Extend existing UI/interfaces, qualify scenarios and upgrades incrementally. |

### 7.7 Coordinator and upgrade policy

- Keep one compatibility/reuse ledger and one small bridge; discourage per-worker wrappers around the same upstream component.
- Track saved duplication by reused capability and tests, not speculative time estimates.
- Assign cheap workers narrow integration slices; reserve stronger reasoning for unresolved cross-component design or measured failures.
- Lock the upstream version across concurrent workers. Upgrade only through a separate tested change.
- Keep owner-facing controls functional as features arrive.
- Treat upstream test failures as evidence to diagnose, not permission to discard upstream wholesale.
- Preserve applicable upstream notices and maintain a focused local patch queue.
- Compare upstream-only and Ghost-composed behavior on the same relevant scenarios, models and budgets.
- Reconcile requirements to code and evidence before marking milestones complete.
- Do not infer operational quality from institutional affiliation; qualify the code you actually deploy.
- Configuration/runtime upgrades and Ghost application self-updates use the same required evidence, authority and recovery standards.
- Keep local-only defaults and clear teacher/paid-model spending limits; model availability is not spending authorization.

## 8. Evaluation and definition of done

Qualification applies to the composed Ghost/OpenJarvis runtime at its recorded upstream pin. Upstream tests provide valuable evidence but do not automatically satisfy Ghost-specific behavior. Use documented upstream test suites plus focused integration tests; compare with upstream-only behavior where this clarifies the value of an extension.

Evaluation has four evidence levels:

1. Contract/unit tests: deterministic logic and adapters with fixtures.
2. Integration/scenario tests: real Ghost execution with controlled services, isolated workers, and fault injection.
3. Live qualification: configured models, real authorized providers/devices, recorded artifacts.
4. Personal usefulness: observed learning, reduced friction, maintained services, and explicit owner feedback over time.

A pass at one level must not be described as a pass at all levels. Record model/version, configuration, attempts, scenario inputs, acceptance checks, resources, results, and remaining uncertainty. Keep held-out scenario answers outside candidate/worker context.

Report at least:

| Measure | What it establishes |
| --- | --- |
| Task completion rate | Independent acceptance passed / attempted tasks, by task class and model. |
| False completion rate | Claimed successes rejected by independent checks. |
| Initiative acceptance/usefulness | Proposed/built ideas the owner accepts, uses, corrects, or rejects; report each separately. |
| Intervention burden | Unnecessary questions and manual recovery steps, alongside legitimate external prerequisites. |
| Recovery reliability | Restart, timeout, duplicate-delivery, interrupted-update, and disconnected-device results. |
| Cost and latency | Inference, wall time, resources, retries, and time to useful result; distinguish estimates from measured usage. |
| Learning outcome | Evidence of concept understanding/review retention, distinct from exercise completion. |
| Maintenance burden | Breakages, successful repairs, time degraded, unused service growth, and retirements. |

Set scenario pass/fail criteria before implementing its candidate. Deterministic invariants such as unauthorized sender refusal, candidate/evaluator separation, scope enforcement, and stale-data handling must have explicit passing checks. Model-dependent performance targets need a measured baseline and owner-facing qualification report; do not invent universal percentages.

Initial acceptance requires:

- S01-S08 are traced to implementation and evidence, with live provider/hardware limitations shown explicitly.
- At least one useful opportunity arises from context without the owner specifying the feature.
- An existing upstream integration is configured successfully, and a separately tested missing capability becomes operational through the general acquisition workflow.
- A generated artifact passes meaningful independent checks.
- A permitted overnight job survives interruption and yields a useful result.
- A demonstrated candidate improvement can be evaluated, activated, observed, and rolled back.
- The owner can inspect, stop, correct, revoke, forget, and override through working interfaces.
- Local model operation remains possible, and weak-model limits are reported honestly.
- Applicable existing Ghost tests, selected upstream tests, and integration acceptance checks pass, with no fabricated completion.
- Every adopted component has an exact upstream version, reuse decision, single state/execution owner and upgrade/recovery evidence.
- New generic infrastructure has a documented unmet upstream requirement; unnecessary replacements are not accepted deliverables.

Until these behaviors are demonstrated, describe Ghost as progressing toward this personal AI OS specification. Preserve ambition in the product goal and precision in every claim of what currently works.
