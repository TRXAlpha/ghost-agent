# Ghost

Ghost is a local-first personal agent for Windows. It turns goals into
sandboxed, tested work. It can notice opportunities while you are away, learn
from its own failures, and keep an auditable trail of everything it did. You
stay in control: standing policy modes, an emergency stop, explicit
promotion, and "why did you do this?" on every job.

It is **progressing toward** the personal AI OS described in
[implementation.md](implementation.md), but it isn't that yet. This README
says what works today, with how much evidence, and what is still a
placeholder.

> Status: 2026-10-10 · branch `master` · 1,403 automated tests passing (`pytest`, ~2–3 min; plus 106 OpenJarvis-route tests).
> Platform: **Windows 10/11** (idle detection, process cleanup and job
> objects are Windows APIs; on other OSes, idle state is treated as unknown,
> so background work never starts).

---

## Measured reliability

`ghost bench` runs 8 fixed, small but real coding goals through the
production job path. It scores each result twice:

- **ghost_pass_rate**: Ghost's own gate (its tests passed);
- **accepted_rate**: independent **hidden** acceptance tests that the model
  never sees.

Live results on this machine, 2026-10-10:

| Model (route) | ghost_pass_rate | accepted_rate | Notes |
|---|---|---|---|
| `zai-org/GLM-5.3-Flash` (OpenJarvis `openai-compat` route) | **0.50** | **0.875** | After four loop fixes found by the benchmark. One later run lost goals to a provider outage, reported honestly as `model_unavailable`. |
| `gemma4:e4b` 7.5B (local Ollama, 4 GB GPU) | 0.00 | – | The model degenerates into invalid output on these goals; slow on this GPU. |

Read these as: **with a capable model, the code Ghost produces is usually
correct (7 of 8), and Ghost's own procedure now completes about half of the
goals end to end.** The gap between the two numbers is Ghost's own process,
not the model. Small local models are not reliable for multi-step coding.
Compare runs with `ghost bench history` and `ghost bench compare previous latest`.

---

## Quick start (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
ghost doctor                                  # storage, schema, Ollama, Docker, RAM/GPU, integrations
ghost accomplish "Create calc.py with add/sub and pytest tests" --model gemma4:26b --workspace empty
```

Ghost only probes services; it never starts, installs or configures them for
you. Use a model that is installed in Ollama (`ollama list`).

**OpenAI-compatible endpoints** (LM Studio, vLLM, or a remote API) go through
the pinned OpenJarvis route, which has its own isolated Python 3.13
environment. Install it with `.\scripts\bootstrap-openjarvis.ps1`, then:

```powershell
$env:GHOST_INFERENCE_BACKEND = 'openjarvis'
$env:GHOST_OPENJARVIS_ENGINE = 'openai-compat'
$env:GHOST_OPENJARVIS_URL    = 'https://your-endpoint/v1'
$env:GHOST_ALLOW_REMOTE_INFERENCE = '1'        # required for non-local URLs
$env:GHOST_OPENJARVIS_API_KEY = '<key>'        # never put it in a file, task or URL
.\workspaces\openjarvis-venv\Scripts\python.exe -m ghost_agent.cli accomplish "..." --model "<served-model>"
```

Remote inference sends the task and its context to that endpoint. There is no
automatic cloud fallback. See [docs/openjarvis.md](docs/openjarvis.md).

---

## What works today

"Live" means: production code, reachable from a real command, with tests.
**External** integrations are only called live after a real authorized
connection test.

| Area | State | Commands |
|---|---|---|
| Sandboxed coding jobs: plan → implement → verify → repair, durable and resumable | live | `ghost accomplish`, `ghost worker --watch`, `ghost jobs …` |
| Independent benchmark with hidden tests | live, measured above | `ghost bench run/report/history/compare` |
| Standing autonomy policy (suggest / build / operate / improve / idle_work), fail-closed | live | `ghost policy show/set` |
| Emergency stop (holds queued work; latency recorded per component) | live | `ghost emergency-stop engage/clear/status` |
| Proactive opportunities: grounded evidence, transparent outcome-tuned ranking, similarity suppression, one clarifying question | live, idle-gated | `ghost proactive tick/watch/status/ranking/less/dismiss` |
| Unattended loop: overnight cycles, quiet hours, digest with coverage gaps, daily inference cap, yields to you when you return | live, needs `idle_work` | `ghost daemon run`, `ghost overnight run` |
| Notifications with quiet hours, batching and focus holds | live | `ghost notifications list/ack`, `ghost focus start/status/stop` |
| "Why did you do this?" for any job, opportunity, heartbeat action or owner fact | live | `ghost why <id>` |
| Self-improvement: recurring failures → sandbox fix → self-repair evaluation → owner-gated promotion with update policy | live pipeline; no live end-to-end run recorded yet | `ghost trace-learning`, `ghost self-improve`, `ghost self-repair`, `ghost promote` |
| Isolated evaluation in Docker (`--isolation auto/container/host`) | live when Docker and the OpenJarvis venv are present; otherwise records `runner: host` honestly | `ghost self-repair verify` |
| Personal memory with provenance, edit/revisions, retention, sensitivity, retract/purge | live | `ghost memory …` |
| Owner knowledge base: versions, replace/delete with derived invalidation, sections, PDF and OCR | live; PDF and OCR need `pdftotext`/`tesseract` on PATH | `ghost kbase …` |
| Learning: concepts, spaced repetition, deadline-aware scheduling, generated exercises with protected answers, practice challenges with reset, rustc oracle with toolchain evidence, format effectiveness | live; Rust checks need `rustc` | `ghost learn …` |
| Devices: records with pairing and freshness, telemetry validation, command safety gate with pan/tilt simulator | live in simulation; no real hardware | `ghost device …` |
| Huawei health route: privacy guard, corrections, trends, retention/purge | **synthetic/manual data only** | `ghost huawei …` |
| Capability lifecycle: feasibility records, reuse of installed integrations, degraded/verified states | live | `ghost capability …`, `ghost gaps …` |
| Model profiles (unknown limits and costs stay unknown); private-network fetch block in research | live | `ghost model-profile …`, `ghost research` |
| Durable state: event IDs, cursors, failed-event queue, job leases/generations, schema registry and migrations, secret-free export | live | `ghost events …`, `ghost migrate`, `ghost export` |
| Local dashboard and API with owner controls and token entry | live, loopback only | `ghost api` → http://127.0.0.1:8765/ |
| **Life layer** owner intake: `ghost tell` / Telegram statements become validated owner facts + `owner.fact`; the next proactive tick must address them (trigger `owner_request`) | **live** (GLM-5.3-Flash): "got a new Samsung watch" -> fact -> feasibility opportunity; without a seeded hint it reuses existing tools but does not name Health Connect (partial) | `ghost tell`, `ghost owner facts` |
| **Life layer** owner files `.ghost/owner/SOUL.md` / `USER.md` (routine, quiet windows), versioned edits | **live** | `ghost owner show/edit/history` |
| **Life layer** situation `.ghost/situation.json`: value, confidence, source, freshness per field; stale = unknown; history in `situation-history.jsonl` | **live**: place from ghostpi (Telegram live location, Tailscale endpoint, AdGuard activity), PC activity from ActivityWatch, project from git, owner contact; health **unknown** (no approved source) | `ghost situation [rebuild\|push]` |
| **Life layer** heartbeat + life lane: own daemon thread every 60 s (reminders, briefings, build reports, alerts, digest), heartbeat every 30 min or on salience, not idle-gated; `care.message_owner` cap from a durable ledger (owner set 100/24 h); night quiet hours + weekly quiet windows; no-diagnosis filter; health values withheld | **live** under the GhostLife task | `ghost heartbeat`, `ghost grant`, `ghost why hb_…` |
| **Life layer** owner goals ("I'm joining X"): SearXNG research where each fact must quote a fetched page, T-7d/T-1d reminders at 09:00, 3 ideas, `build N` -> sandbox, one result report | **live**: NASA Moon Base Challenge briefing (15 verified facts, 3 ideas) on Telegram; reminder fired on Telegram; `build 2` queued from Telegram | `ghost brief`, `ghost ideas`, `ghost build N`, `ghost remind`, `ghost reminders` |
| **Telegram** outbound (ghostbot token) and inbound via the ghost-bot relay (`state/ghost-inbox.jsonl`, one SSH `tail -F`); `stop` engages the emergency stop and holds jobs, `resume` clears it (paired owner only) | **live**: facts, chat answers, build 2, stop, resume all recorded from real messages | `ghost openjarvis telegram relay` |
| Autostart: Task Scheduler `GhostLife` (user, no admin) runs a supervisor that keeps relay + daemon alive with backoff and writes health for the digest | **live** | `scripts/ghost-life.ps1 -Install/-Supervise/-Stop/-Report` |
| **Ghost Companion** (Android, ghost-mobile flavor `v8companion`): GPS + app/screen/unlock logger, DND/silent actuator, token endpoint reachable over Tailscale/adb only; Ghost pulls it (`phone_activity`, GPS place) and sets DND in quiet windows | **live on the emulator** (nightly `ghost mobile-test`, PASS 2026-11-23); on the phone after install (STATUS owner step 0) | `ghost mobile-test`, `scripts/ghost-life.ps1 -ScheduleMobileTest` |
| **Read-write code session** in the Telegram/phone chat: with `session.code` on, the chat agent gets run (python/pytest/git/ruff/node, no shell, no pip), read_file, write_file, list_dir in one workspace; calls capped per hour and audited; secret-looking files refused, output redacted; emergency stop ends a run and is handled even while a long message runs. **Not OS containment**: a script it runs has your user rights | **live** (GLM wrote and ran a script and a pytest from a chat call, 2026-11-23); on for the owner | `ghost grant session.code on/off`, `ghost session [root <dir>\|audit]` |
| Run report (heartbeats, messages, model calls, situation freshness, services) | **live**; first 24 h report scheduled | `ghost life-report --hours 24` |
| **Telegram direct polling from Ghost** | **not used** (ghost-bot holds the token's only poller) | |
| **Huawei live data** | **placeholder** until Huawei developer credentials and user authorization exist | |
| **DeskSentinel (ESP32 pan/tilt)** | **placeholder** until firmware and protocol exist; the safety gate and simulator are ready | |

---

## You stay in control

**Policy modes** are set once and enforced where work is handed off and again
when the worker claims it:

```powershell
ghost policy show
ghost policy set idle_work on     # allow unattended background loops
ghost policy set improve on       # allow self-improvement jobs
```

| Mode | Default | Allows |
|---|---|---|
| suggest | on | proposing opportunities |
| build | on | queueing sandbox build/test work (proactive, capability improver, benchmark) |
| operate | off | reserved for operating integrations; nothing uses it yet |
| improve | off | self-improvement jobs |
| idle_work | off | `ghost daemon` / `ghost overnight` / `proactive watch` |

A corrupt `policy.json` fails closed: only `suggest` stays on.

**Other controls:**
- **Emergency stop:** `ghost emergency-stop engage` stops new work and holds
  queued jobs; `clear` releases only the jobs it held.
- **Promotion:** sandbox work never changes your project on its own.
  `ghost promote …` previews, applies with hash-bound approval, and can be
  rolled back. Self-repair changes to authority files (policy, approvals,
  promotion, evaluation, security, kernel) are refused outright and need the
  owner-controlled update path.
- **Yield on return:** unattended jobs stop at the next model turn when you
  are back at the PC (idle under `GHOST_OWNER_RETURN_IDLE_S`, default 120 s).
  They are paused, not failed; resume them with `ghost jobs resume <id>`.

**Dashboard and API:** `ghost api` serves http://127.0.0.1:8765/. It covers
emergency stop, policy toggles, notifications, focus, jobs
(pause/resume/cancel) and "why". If you set `GHOST_API_TOKEN`, enter it in the
dashboard; it is kept in sessionStorage only. Changing policy or clearing the
emergency stop over HTTP **requires** a configured token. The API is
loopback-only, with Host/Origin checks, 64 KiB bodies and bounded handlers. It
does not isolate other programs running as your Windows user.

---

## Coding jobs

```powershell
ghost accomplish "Build wordfreq.py with top_words() and tests" --model <m> --workspace empty --skip-research
ghost accomplish "Fix the parser bug" --model <m>                    # sandbox = copy of this project
ghost accomplish "..." --require-file src/app.py --check compile --check smoke --smoke-cmd "python app.py --selftest"
ghost accomplish "..." --queue                                       # run later via: ghost worker --watch
```

- **`--workspace empty`** starts from an empty sandbox; this is right for new
  things. The default `project` copies this project (excluding `.git`,
  `.ghost`, `.venv`, `workspaces`, secrets).
- **Check classes:** unit tests are discovered (pytest, `node --test`).
  `--check compile` and `--check smoke` add gates, and a declared check that
  cannot run fails the job rather than being skipped.
- **Budgets:**
  - `--iterations` (default 12; queued unattended jobs get 10) counts only
    model implement/repair turns;
  - the command budget defaults to 32 calls / 300 s (queued jobs: 32 / 600 s);
  - `--command-timeout-seconds` sets the per-command timeout (default 30 s);
  - `GHOST_OLLAMA_TIMEOUT` sets the model read timeout (default 180 s).
- **Model robustness:**
  - a timed-out or failed model call costs one turn, and three in a row end
    the job as `model_unavailable`;
  - transient provider errors (5xx, 429) are retried;
  - truncated responses get specific feedback;
  - file content can be sent as raw blocks, so small models don't have to
    escape code inside JSON;
  - results of read-only turns are shown back to the model.
- **Diagnostics:** every run writes `.ghost/outcomes/<job>/run/state.json`,
  with turn counts, parse errors, truncations and, when it stops,
  `limit_reason`. `ghost trace-learning run` turns recurring failures into
  specific signatures (for example `model_output_truncated`,
  `limit_tests_missing`).

---

## Learning, knowledge and memory

```powershell
ghost kbase ingest .\lessons         # .md/.txt/.py, PDFs (pdftotext), images (tesseract OCR)
ghost kbase sources ; ghost kbase delete lessons\old.md
ghost learn add-concept rust-ownership "Rust ownership"
ghost learn exercises rust-ownership --model <m>   # questions + progressive hints; answers stored separately
ghost learn deadline midterm --date 2026-11-02 --concepts rust-ownership
ghost learn challenge create --spec challenge.json  # refused unless the solution passes and the starter fails
ghost learn formats                                 # which teaching format actually works for you
ghost memory list ; ghost memory edit <id> --content "..." ; ghost memory export --out mem.json
```

Missing toolchains produce a written dependency plan
(`.ghost/learner/dependency_plan.md`). Ghost never installs toolchains
itself. Cybersecurity/CTF environments (network services, containers) are not
built yet; practice challenges cover code exercises only.

---

## Unattended operation

```powershell
ghost policy set idle_work on
ghost daemon run --model <m> --quiet-hours 22 7 --max-inference-calls 48
ghost daemon status ; ghost emergency-stop engage
```

The daemon composes the existing loops. Each cycle runs one proactive tick
and one worker job, both only when you are away. It runs trace-learning
periodically, plus capability improvement (outside quiet hours) and handoff of
finished self-improvement jobs. The morning digest lists outcomes and
**coverage gaps** ("no cycles from 01:10 to 06:40: PC asleep or Ghost not
running"), so a quiet night is never reported as "nothing happened". Nothing
installs itself as a startup service.

---

## External integrations (optional)

| Integration | What exists | What you must provide |
|---|---|---|
| Telegram | Pairing, receipts, uncertain-delivery reconciliation, reconnect backoff, credential-aware health | A bot token in `GHOST_TELEGRAM_BOT_TOKEN`, then `ghost openjarvis telegram pair --user-id … --chat-id …` and a live check. Don't reuse a token another bot process polls. |
| Huawei Health | Route, privacy guard, corrections, trends, retention | Huawei Developer Console app (AppID/AppSecret), Health Kit access, user authorization |
| DeskSentinel | Device record, pairing, limits gate, pan/tilt simulator, telemetry validation | ESP32 firmware and command protocol |

Until these exist, the adapters report `not_configured` and never fake data or
connections.

---

## Security model and honest limits

- **Isolation:** by default, sandboxes are copied directories, **not
  OS-isolated**. Commands run without a shell from an allowlist, `pip` is
  blocked, and child processes are cleaned up through Windows job objects, but
  generated code still runs with your user's file and network access. Docker
  isolation is used for self-repair evaluation when available. Don't run
  untrusted tasks unattended.
- **Secrets:** snapshots exclude `.env*` and key files; known token patterns
  are redacted in logs and exports. This is best-effort, not a guarantee.
- **Research fetches** refuse localhost, private, link-local and metadata
  addresses, except with an explicit `GHOST_ALLOW_PRIVATE_FETCH=1`.
- **What tests prove:** a passing test is not proof that your whole outcome
  was achieved. That's why the benchmark's hidden acceptance exists.
- **Not built:** voice or mobile clients, distributed or multi-device execution,
  CTF environments, live Telegram/Huawei/DeskSentinel, and model fine-tuning.
  The LoRA corpus (`ghost lora`) is captured, but nothing is trained.

---

## Where state lives

```text
.ghost/
  outcomes/<job>/        outcome.json, research.md, run/{sandbox,actions.log,llm.log,state.json}
  scheduler.json         jobs (leases, generations, pause reasons)
  events.jsonl, events/  event log, cursors, failed-event queue
  policy.json            standing modes          focus.json   focus session
  notifications.json     attention records       devices.json, telemetry/
  validated-memory.json  personal memory         knowledge-base/, learner/
  opportunities.json     proactive ideas         bench/<run>/report.json
  schema.json            store versions (ghost migrate)   backups/ (pre-migration copies)
```

Back up or move with `ghost export --out ghost-export.json` (versioned and
secret-free). `ghost migrate --check` refuses data newer than the code.

---

## Development

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

- OpenJarvis-specific tests run under `workspaces\openjarvis-venv`.
- Engineering history and evidence:
  - [docs/engineering/STATE.md](docs/engineering/STATE.md)
  - the campaign records in [docs/engineering/campaign-2026-10](docs/engineering/campaign-2026-10/00-README.md)
    and [campaign-2026-10b](docs/engineering/campaign-2026-10b/00-AUDIT-BRIEF.md)
    (requirement audits with file:line evidence).
- Product specification: [implementation.md](implementation.md).
