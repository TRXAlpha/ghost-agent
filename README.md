# Ghost

Ghost is Christian's personal AI. It runs on the Windows PC, knows his context (projects, devices,
deadlines, routine, where he is, what the PC and phone are doing), talks to him on Telegram and
through its own Android app, takes initiative, researches and builds things in a sandbox while he
is away, and keeps an auditable "why" for everything it does. He stays in control through standing
policy modes, scoped grants, an emergency stop that works from Telegram, and explicit promotion of
any code change.

> Status: 2026-11-23, branch `master`. 1,524 tests pass in the main venv and 1,635 in the
> OpenJarvis venv. Platform: Windows 10/11 for Ghost Core (idle detection, job objects and the
> autostart task are Windows features). Model: `zai-org/GLM-5.3-Flash` through gonkarouter.

Evidence words used in this file: **live** means it ran for real on this setup and the result was
checked; **emulator** means it was proven on the Android emulator, not on the phone yet;
**built** means production code plus tests but no real run recorded; **placeholder** means the
interface exists and honestly reports `not_configured`.

---

## Contents

1. [How it fits together](#how-it-fits-together)
2. [Setup from scratch](#setup-from-scratch)
3. [Talking to Ghost](#talking-to-ghost)
4. [The life layer](#the-life-layer)
5. [Ghost Companion (Android)](#ghost-companion-android)
6. [Coding jobs and initiative](#coding-jobs-and-initiative)
7. [Models and routing](#models-and-routing)
8. [Control, grants and safety](#control-grants-and-safety)
9. [Services, autostart and the Pi](#services-autostart-and-the-pi)
10. [Memory, knowledge, learning and devices](#memory-knowledge-learning-and-devices)
11. [Command reference](#command-reference)
12. [Environment variables](#environment-variables)
13. [Where state lives](#where-state-lives)
14. [Troubleshooting](#troubleshooting)
15. [Honest limits](#honest-limits)
16. [Development and docs](#development-and-docs)

---

## How it fits together

```text
                Telegram (ghostbot)                 Ghost Companion app (phone)
                      |                              GPS, app/screen/unlock events,
          ghostpi: ghost-bot (only poller)           DND/silent, notifications, "Send to Ghost"
          non-command messages -> ghost-inbox.jsonl        |  token endpoint :8799 (Tailscale only)
          location + /sethome -> ghost-place timer         |
          SearXNG :8888 (tailnet only)                     |
                      | SSH tail -F                        | HTTP pull / notify
                      v                                    v
   +---------------------------- Ghost Core (Windows PC) ---------------------------+
   |  relay  ->  TelegramBridge (pairing, receipts, stop/resume, facts, build N,   |
   |             chat agent, read-write code session)                              |
   |  daemon ->  life lane (every 60 s): situation, reminders, briefings, build    |
   |             reports, alerts, digest, phone inbox, quiet-window DND, watchdog  |
   |          -> heartbeat (30 min or on salience): one allowlisted action         |
   |          -> overnight cycle (when idle): proactive tick + sandbox worker       |
   |  owner files, validated memory, situation.json, vault graph (read-only)       |
   |  supervisor (Task Scheduler "GhostLife") keeps relay + daemon alive           |
   +---------------------------------------------------------------------------------+
                      |
              GLM-5.3-Flash via OpenJarvis openai-compat (remote, owner choice)
```

Ghost Core is the only writer of goals, memory, grants and the situation record. ghostpi and the
phone provide observations and small authenticated actions; neither runs a model. OpenJarvis
provides the model engine and Telegram plumbing only; it owns no scheduler and no memory.

---

## Setup from scratch

Most of this is already done on this PC. It is listed so the system can be rebuilt.

**1. Ghost Core**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
.\scripts\bootstrap-openjarvis.ps1      # isolated Python 3.13 venv for the model route and Telegram
ghost doctor
```

**2. Secrets and settings (user environment variables, set once, never in files):**

```powershell
[Environment]::SetEnvironmentVariable('GHOST_TELEGRAM_BOT_TOKEN', '<ghostbot token>', 'User')
[Environment]::SetEnvironmentVariable('GHOST_OPENJARVIS_API_KEY', '<gonkarouter key>', 'User')
[Environment]::SetEnvironmentVariable('GHOST_SEARCH_URL', 'http://100.71.145.20:8888', 'User')
[Environment]::SetEnvironmentVariable('GHOST_PHONE_URL', 'http://100.95.143.88:8799', 'User')
[Environment]::SetEnvironmentVariable('GHOST_PHONE_TOKEN', '<token from the companion app>', 'User')
```

**3. Pair Telegram and turn things on:**

```powershell
ghost openjarvis telegram pair --user-id 5309400589 --chat-id 5309400589
ghost openjarvis telegram live-check --chat-id 5309400589
ghost policy set idle_work on              # unattended work while you are away
ghost grant care.message_owner on          # Ghost may message you first
ghost grant care.phone_actions on          # DND/silent in quiet windows
ghost grant session.code on                # code execution in the chat
ghost channel phone                        # app first, Telegram as fallback
```

**4. Autostart (current user, no admin):**

```powershell
.\scripts\ghost-life.ps1 -Check            # probes the model route
.\scripts\ghost-life.ps1 -Install          # Task Scheduler "GhostLife" at logon
Start-ScheduledTask GhostLife
.\scripts\ghost-life.ps1 -ScheduleMobileTest   # nightly companion app test, 03:30
```

**5. Tell Ghost about you:** `ghost owner show USER.md`, edit it, then
`ghost owner edit USER.md --file <path>`. Put quiet windows under `## Quiet windows`
(e.g. `- Monday 09:05-09:49`).

**First run check:** `ghost situation` should list place, PC activity, project and phone activity;
`ghost session` should show the code session; a Telegram message should get a reply.

---

## Talking to Ghost

### Telegram

Send anything to ghostbot that is not a hub command (`/status`, `/wake`, `/dns`, `/brief`, ...;
those stay with ghost-bot on the Pi). What happens depends on the message:

| You send | Ghost does |
|---|---|
| A statement about you: "got a new Samsung watch", "I'm joining HackFoo on 2027-03-20", "timing FTC autos by hand is tedious" | Stores a validated owner fact (device, goal, deadline, complaint), replies `Noted (...)` with a `ghost why` id. Goals start a briefing. |
| A question or request | The chat agent answers with your owner files, facts, situation and relevant vault pages in context. With `session.code` on it can also write files, run code and read results (see below). |
| `build N` | Queues idea N from the last briefing as a sandbox job; the tested result is reported once. |
| `stop` | Emergency stop: holds queued jobs, ends a running code-session command. Handled at once even while a long message is running. |
| `resume` | Clears the emergency stop and requeues held jobs (paired owner only). |
| Live location, then `/sethome` | ghost-bot saves where home is; place becomes home/away. |

Only the paired private chat (user 5309400589) is answered; everything else is ignored. Every
message is deduplicated by a receipt ledger, so a replay is never answered twice.

**How messages get to the PC.** ghost-bot on ghostpi is the only Telegram poller for the token. It
appends non-command owner messages to `~/ghost-hub/state/ghost-inbox.jsonl`, and Ghost's relay
follows that file over one SSH `tail -F`. Replies go out through the Bot API, in 3,900-character
parts. **If ghostpi is down for 3 minutes, the relay polls Telegram itself from the PC** and hands
polling back as soon as the Pi answers (or Telegram reports another poller with HTTP 409). Hub
commands then get "ghostpi is offline".

### Read-write code session (chat)

With `ghost grant session.code on`, the chat agent gets four tools: `run` (python, pytest, git, ruff,
node; no shell, no pipes, no pip), `read_file`, `write_file`, `list_dir`. They are rooted in one
workspace: `.ghost/sessions/telegram/work` by default, or a directory you choose with
`ghost session root <dir>` (CLI only, never from chat). Up to 20 model turns per message.

Controls: off until granted, 500 calls per hour, every call in `.ghost/sessions/telegram/audit.jsonl`
(`ghost session audit`), secret-named files (`.env*`, `*.pem`, `*.key`, `id_rsa*`, ...) refused,
output secret-redacted, `stop` ends a running command. **This is not OS isolation:** a script it
writes and runs has your Windows user's rights. Your Telegram account is therefore a remote control
for this PC: keep Telegram two-step verification on.

Live: GLM wrote `primes.py`, ran it, wrote a pytest and ran it, from one request (14 s).

### The companion app and other channels

- **Ghost Companion app**: messages arrive as notifications; the "Send to Ghost" box is handled
  exactly like a Telegram message (facts, questions, stop/resume, build N, code session), replies
  come back as notifications. Needs neither the Pi nor Telegram.
- **`ghost channel telegram|phone|both`** picks where Ghost writes first; the other is the fallback.
  Current setting: `phone`. Replies go back on the channel the message came from.
- **CLI**: `ghost tell "<statement>"`, and the chat agent through `ghost openjarvis ask`.

---

## The life layer

The part that makes Ghost a personal assistant instead of only a coding agent. Plan and evidence:
[docs/engineering/life-layer/](docs/engineering/life-layer/) (`PLAN.md`, `STATUS.md`).

### Who you are

- `.ghost/owner/SOUL.md`: who Ghost is and its purpose toward you.
- `.ghost/owner/USER.md`: your routine, quiet windows, tools you already run, projects,
  preferences. Every edit keeps the previous version (`ghost owner history`).
- **Owner facts**: statements become validated memory records (`ghost owner facts`). Health wording
  is tagged `health` and kept out of model prompts. Ghost never diagnoses.
- **The vault**: Ghost reads `Ghost/vault/wiki` (the Obsidian vault) as a graph: best-matching pages
  plus the pages they link to, secrets redacted, read-only. Used in proactive ticks, heartbeats and
  chat answers. `ghost vault query "<text>"`.

### The situation record

`.ghost/situation.json`, rebuilt every minute. Every field carries value, confidence, source and
freshness; a field older than its time-to-live reads `unknown`. Nothing is a fact unless a sensor
measured it.

| Field | Sources (best first) |
|---|---|
| `place` | phone GPS vs home point (companion app), ghostpi place timer (Telegram live location, Tailscale endpoint on home Wi-Fi, AdGuard activity) |
| `pc_activity` | ActivityWatch (`localhost:5600`), Windows idle |
| `current_project` | editor window title (ActivityWatch), latest git commit |
| `task_guess` | derived: idle, gaming, ftc, assignment, coding:<project> |
| `phone_activity` | companion app: foreground app, screen, last unlock |
| `owner_contact` | last Telegram/app/CLI message |
| `health` | unknown until a health source is approved (HealthHub is unconfirmed) |

Coordinates and health values never reach a model: only home/away and "measured". History is kept
in `.ghost/situation-history.jsonl` for patterns and reports.

### What runs on its own

The daemon has an **assistant lane on its own thread, every 60 s**, independent of builds:

- rebuilds the situation;
- fires due reminders (held during quiet hours and quiet windows);
- reports finished `build N` results once;
- researches pending owner goals (briefings);
- delivers the morning digest once a day (overnight work, situation, services health);
- forwards important worker alerts (e.g. a paused job) once;
- handles messages typed in the companion app;
- switches phone DND + silent on at the start of a quiet window and off at the end (only a DND it
  turned on itself);
- tells you once if the phone app has stopped answering for 15 minutes.

The **heartbeat** runs every 30 minutes, or early on a new fact, briefing or situation change (5-min
minimum gap). It is not idle-gated. It may take one action: `none`, `note`, `message_owner`,
`post_room` (not wired yet), `propose_opportunity`. Deterministic code checks the action: allowlist,
grant, cap, quiet hours, a no-diagnosis filter, no secrets. Each action is a receipt
(`ghost heartbeat receipts`, `ghost why hb_...`).

### Goals, briefings, reminders, ideas

"I'm joining X" leads to:
1. **Research** through SearXNG: up to 5 credible pages; a fact is kept only if its quote appears
   word for word in the fetched page. Sites that refuse Ghost's honest user agent are skipped, never
   spoofed.
2. **Reminders** 7 days and 1 day before each verified date and your own deadline, at 09:00.
3. **Exactly 3 ideas** fitted to your gear and the rules, sent with the facts.
4. **`build N`** queues one as a sandbox prototype; the tested result comes back once.

Live: NASA Moon Base Challenge, 15 verified facts (registration until 2027-01-25), 3 ideas; a
reminder fired on Telegram; `build 2` (DTN Lunar Mesh) passed its tests in 4 iterations.

`ghost remind "<text>" --in-minutes N` adds a reminder by hand. `ghost life-report --hours 24`
summarizes heartbeats, messages, model calls, situation freshness and service health.

---

## Ghost Companion (Android)

ghost-mobile flavor `v8companion` ("Ghost Companion", installs beside v1-v7). Code:
`ghost-mobile/android/app/src/v8companion/`.

- Foreground service: GPS every 2 min or 50 m (`LocationManager`), app/screen/unlock events
  (`UsageStatsManager`), append-only event log with sequence numbers.
- Endpoint on port 8799: answers only Tailscale (`tun*` interface) or adb loopback, bearer token
  (48 hex characters, created on first start, "Copy Ghost token" in the app). `GET /health`,
  `GET /events?since=N`, `POST /dnd`, `POST /ringer`, `POST /notify`.
- Screen: grant buttons (location, background location, usage access, DND access, background
  running), Start companion, Message Ghost, Copy token, and a status line.
- The screen-reading accessibility services of the other flavors are removed from this APK.

Setup: install the APK, grant everything, tap "Allow background running", start it, copy the token
into `GHOST_PHONE_TOKEN`. On Honor/Huawei phones also: Settings > Apps > App launch > Ghost Companion
> Manage manually > Auto-launch, Secondary launch, Run in background all on, and lock the app in
Recents. Without that the phone freezes the service about 3 minutes after you leave it (measured).

Testing: `ghost mobile-test` builds the APK, boots the `Medium_Phone_API_36.0` emulator headless,
installs, grants over adb and checks 10 things (boot, install, token, health, wrong token refused,
GPS, notify, message to Ghost, DND on/off). It runs nightly at 03:30 and messages you PASS/FAIL. It
never touches your phone. Status: **emulator** 10/10; on the phone, pulls, GPS, notifications and
DND access work, background survival depends on the Honor settings above.

---

## Coding jobs and initiative

### Sandbox jobs

```powershell
ghost accomplish "Build wordfreq.py with top_words() and tests" --model <m> --workspace empty
ghost accomplish "Fix the parser bug" --model <m>           # sandbox = copy of this project
ghost accomplish "..." --queue                              # run later by the worker
```

Each job: research, plan, implement, verify, repair, in a copied sandbox under
`.ghost/outcomes/<job>/run/sandbox`. Nothing reaches your real project without `ghost promote`.

- **Budgets.** Queued jobs (Telegram builds, proactive, capability work) get **99 iterations**, a
  command budget of 3,600 s / 250 calls; `ghost accomplish` and `ghost jobs add` default to 99.
  `ghost bench` stays at 10 so its scores stay comparable.
- **Budget awareness.** Every model turn says "Budget: iteration k of N". The same feedback three
  times in a row adds "do not repeat, change approach". In the last 20% the model is told to finish
  the smallest change that passes, or leave a working state and a `NOTES.md` of what remains.
- **Verification.** Focused tests for touched test files first, then the suite (`pytest`, `node
  --test`), plus declared compile/smoke checks. Verification commands may take 600 s; the model's
  own commands 30 s. A missing declared check fails the job instead of being skipped.
- **Robustness.** A failed or empty model turn costs one turn (with "take one small step"); three in
  a row end the job as `model_unavailable`. Transient 5xx/429 are retried.
- **Yield on return.** Unattended jobs pause at the next turn when you come back to the PC
  (`yield_to_owner`) and resume when you are idle again.
- **Integrity.** A job's contract is sealed when queued; changing it later is refused ("create a
  new outcome instead"). Retry with `ghost jobs retry <id>` or queue a new job.

### Initiative

- **Proactive tick** (when you are idle): reads owner files, facts, vault pages, goals, outcomes and
  events, proposes one bounded experiment. A new owner fact must be addressed by the proposal.
  Ranked by an outcome-tuned score, similar rejected ideas suppressed, one clarifying question when
  input is needed. `ghost proactive status/ranking/less/dismiss`.
- **Capability improver and trace learning**: recurring failure signatures become improvement jobs.
- **Self-improvement and self-repair**: proposals evaluated in isolation (Docker when available),
  promotion only with owner approval; authority files are never changed by self-repair.

### Benchmark

`ghost bench` runs 8 fixed coding goals through the real job path and scores Ghost's own gate and
independent hidden tests. Last full measurement (2026-10-10): GLM-5.3-Flash 0.50 own gate, 0.875
hidden acceptance; local gemma4:e4b 0.00. Older than the November loop fixes; re-run with
`ghost bench run`.

---

## Models and routing

- **Default route**: OpenJarvis `openai-compat` engine to `https://api.gonkarouter.io/v1`, model
  `zai-org/GLM-5.3-Flash`, set per process by `scripts/ghost-life.ps1` (your global `GHOST_MODEL`
  for other tools is untouched). Remote inference sends owner files, facts, situation (no
  coordinates, no health values) and job context to that endpoint; there is no silent cloud
  fallback.
- **Reasoning effort `low`** (`GHOST_OPENJARVIS_REASONING_EFFORT`): gonkarouter caps GLM output at
  about 4,000 tokens including reasoning; at the default effort it spent all of them thinking and
  returned nothing.
- **Output budget** 4,000 tokens for planning calls; **input budget** 96,000 characters on this
  route (`GHOST_MAX_INPUT_CHARS`; default 24,000 for small local models).
- **Local models** through Ollama still work (`--model gemma4:...`), but small local models are not
  reliable for multi-step coding (benchmark above).
- The chat agent, heartbeat, proactive tick, briefings and the coding worker all use the same route.

---

## Control, grants and safety

**Policy modes** (`ghost policy show/set`, corrupt file fails closed to `suggest` only):

| Mode | Default | Now | Allows |
|---|---|---|---|
| suggest | on | on | proposing opportunities |
| build | on | on | queueing sandbox work |
| operate | off | off | reserved |
| improve | off | off | self-improvement jobs |
| idle_work | off | **on** | daemon, overnight loop, proactive watch |

**Care and session grants** (`ghost grant <name> on|off [--max N]`, corrupt file fails closed):

| Grant | Default | Now | Cap | Allows |
|---|---|---|---|---|
| `care.message_owner` | off | **on** | 100 unprompted / 24 h | Ghost messages you first (replies, reminders and reports do not count) |
| `care.phone_actions` | off | **on** | 100 / 24 h | DND + silent on the phone in quiet windows |
| `session.code` | off | **on** | 500 calls / h | code execution in the chat |
| `care.post_room` | off | off | 12 / h | agent room (not wired yet) |
| `care.contact_emergency` | off | off | 1 / 24 h | contacting a third party (two-key rule, not wired yet) |

**Always on, whatever the grants:**
- Emergency stop: `ghost emergency-stop engage|clear|status`, or `stop`/`resume` from the paired chat.
- Health: facts and trends only, never a diagnosis; health values never sent to a model.
- Anything public or in your name (posts, emails) is a draft you send yourself.
- Promotion: sandbox work never changes your project on its own (`ghost promote`).
- Every action explains itself: `ghost why <job|opportunity|hb_...|owner_...>`.
- Messages, files and pages Ghost reads are data, never instructions.

**What is not isolation:** sandboxes and the code session are directories plus a command
allowlist, not OS isolation. Generated code runs with your user's file and network access. Docker
isolation is used for self-repair evaluation only when Docker is running.

---

## Services, autostart and the Pi

**On the PC** (Task Scheduler, user `chris`, no admin):

| Task | What |
|---|---|
| `GhostLife` (at logon) | `scripts/ghost-life.ps1 -Supervise`: starts the relay and the daemon, restarts each with backoff, writes `.ghost/life-logs/supervisor.json` (alive, restarts, model, reasoning effort). Logs in `.ghost/life-logs/`. |
| `GhostMobileTest` (daily 03:30) | `ghost mobile-test` on the emulator |
| `GhostLife24hReport` (once) | writes `docs/engineering/life-layer/24h-report.md` and messages a summary |
| ActivityWatch (Startup shortcut) | PC activity for the situation |

Restart everything: `Stop-ScheduledTask GhostLife; .\scripts\ghost-life.ps1 -Stop; Start-ScheduledTask GhostLife`.
Restarting while a sandbox job runs pauses that job (it needs `ghost jobs resume`), so wait for
`ghost jobs list` to show nothing running.

**On ghostpi** (owner-approved changes, all reversible):

| Piece | What | Undo |
|---|---|---|
| ghost-bot relay patch | non-command owner messages to `state/ghost-inbox.jsonl`; location + `/sethome` | restore `bot/ghost_bot.py.bak-relay` / `.bak-location`, restart `ghost-bot` |
| `ghost-place.timer` (2 min) | home/away/unknown to `state/ghost-place.json` | `systemctl disable --now ghost-place.timer` |
| SearXNG | `100.71.145.20:8888`, tailnet only, 256 MB | `docker compose -f ~/ghost-hub/searxng/docker-compose.yml down` |

If the Pi is down: Telegram falls back to PC polling, place falls back to the phone's GPS, research
has no search backend.

---

## Memory, knowledge, learning and devices

| Area | State | Commands |
|---|---|---|
| Validated memory: provenance, edits with revisions, retention, sensitivity, retract/purge | live | `ghost memory ...` |
| Owner knowledge base: versions, delete with invalidation, PDF (`pdftotext`) and OCR (`tesseract`) | live | `ghost kbase ...` |
| Learning: concepts, spaced repetition, deadlines, generated exercises with protected answers, practice challenges, rustc oracle | live | `ghost learn ...` |
| Research: source-grounded deep research with exact quotes, private-network fetch block | live | `ghost research` |
| Devices: records, pairing, telemetry validation, command safety gate, pan/tilt simulator | simulation only | `ghost device ...` |
| Huawei health route | placeholder (synthetic/manual data) | `ghost huawei ...` |
| DeskSentinel (ESP32 pan/tilt) | placeholder until firmware exists | |
| Local dashboard and API, loopback only, token for policy/stop changes | live | `ghost api` -> http://127.0.0.1:8765/ |
| Durable state: event cursors, failed-event queue, job leases, schema migrations, secret-free export | live | `ghost events`, `ghost migrate`, `ghost export` |

---

## Command reference

**Life layer and channels**

| Command | Does |
|---|---|
| `ghost tell "<statement>"` | store an owner fact (and start a briefing for goals) |
| `ghost owner show\|edit\|history\|facts` | owner files and facts |
| `ghost situation [show\|rebuild\|push]` | situation record; push an observation |
| `ghost heartbeat [beat\|receipts]` | one heartbeat now; recent receipts |
| `ghost grant [<name> on\|off] [--max N]` | care and session grants |
| `ghost channel [telegram\|phone\|both]` | preferred channel |
| `ghost brief` / `ghost ideas` / `ghost build N` | briefings, current ideas, queue an idea |
| `ghost remind "<text>" --in-minutes N` / `ghost reminders` | reminders |
| `ghost session [show\|root <dir>\|audit]` | code session |
| `ghost phone pull\|sethome` | pull phone events now; save home from the phone's GPS |
| `ghost mobile-test` | emulator test of the companion app |
| `ghost vault stats\|query "<text>"` | the vault graph |
| `ghost life-report [--hours 24] [--out f] [--telegram]` | run report |
| `ghost openjarvis telegram pair\|status\|live-check\|relay\|receipts\|revoke` | Telegram |
| `ghost openjarvis ask "<prompt>"` | chat agent from the CLI |

**Jobs, initiative, control:** `accomplish`, `worker`, `jobs list|add|pause|resume|retry|cancel`,
`why`, `proactive tick|watch|status|ranking|less|dismiss`, `daemon run|status|stop`,
`overnight run`, `trace-learning run|report`, `self-improve`, `self-repair`, `promote`,
`bench list|run|report|history|compare`, `policy show|set`, `emergency-stop engage|clear|status`,
`notifications list|ack`, `focus start|status|stop`, `approval`, `check-in`.

**Knowledge and other:** `memory`, `kbase`, `learn`, `research`, `search`, `index`, `knowledge`,
`device`, `huawei`, `capability`, `gaps`, `integration`, `extension`, `mcp`, `model-profile`,
`perception`, `context`, `events`, `migrate`, `export`, `doctor`, `api`, `control-plane`, `lora`,
`parallel`, `protocol-bench`, `run`, `resume`, `audit`, `status`, `init`.

`ghost <command> --help` shows the options of each.

---

## Environment variables

| Variable | Purpose |
|---|---|
| `GHOST_TELEGRAM_BOT_TOKEN` | ghostbot token (user env) |
| `GHOST_OPENJARVIS_API_KEY` | gonkarouter key (user env) |
| `GHOST_INFERENCE_BACKEND`, `GHOST_OPENJARVIS_ENGINE`, `GHOST_OPENJARVIS_URL`, `GHOST_ALLOW_REMOTE_INFERENCE` | model route (set per process by the launcher) |
| `GHOST_OPENJARVIS_REASONING_EFFORT` | `low` on the GLM route |
| `GHOST_MAX_INPUT_CHARS` | worker prompt cap (96000 on the GLM route, default 24000) |
| `GHOST_MODEL`, `GHOST_RESEARCH_MODEL` | default models |
| `GHOST_OLLAMA_BASE_URL`, `GHOST_OLLAMA_TIMEOUT` | local Ollama and model read timeout |
| `GHOST_SEARCH_URL` | SearXNG for briefings |
| `GHOST_PHONE_URL`, `GHOST_PHONE_TOKEN` | companion app endpoint and token |
| `GHOST_SESSION_ROOT` | code session workspace (overrides `ghost session root`) |
| `GHOST_VAULT` | Obsidian vault (default `~/Documents/Ghost/vault`) |
| `GHOST_MOBILE_REPO` | ghost-mobile checkout for `mobile-test` (default `../ghost-mobile-companion`) |
| `GHOST_API_TOKEN` | token for policy/stop changes over the local API |
| `GHOST_ALLOW_PRIVATE_FETCH` | let research fetch private addresses (off) |
| `GHOST_OWNER_RETURN_IDLE_S` | idle seconds below which unattended jobs yield (default 120) |

---

## Where state lives

```text
.ghost/
  owner/            SOUL.md, USER.md (+history/), grants.json, channel.json, ideas.json,
                    reminders.json, briefings/, home.json, session_root.json
  situation.json    current situation      situation-history.jsonl   samples
  heartbeat/        receipts.jsonl (every message/action), state.json, forwarded-alerts.json
  phone/            events.jsonl (raw phone log), cursor, inbox cursor, dnd.json, reach.json
  sessions/telegram/  work/ (code session workspace), audit.jsonl
  openjarvis/       telegram pairing, receipts, relay/fallback cursors, chat receipts, sessions.db
  outcomes/<job>/   outcome.json, research.md, run/{sandbox, actions.log, llm.log, state.json}
  scheduler.json    jobs              events.jsonl, events/   event log and cursors
  validated-memory.json   memory      opportunities.json   proactive ideas
  policy.json, notifications.json, goals.json, digest/, overnight/, mobile-tests/,
  life-logs/ (supervisor.json, relay/daemon logs), life_status.json, vault-graph.json
```

`ghost export --out ghost-export.json` makes a versioned, secret-free copy.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Ghost answers on Telegram, not in the app | the app is frozen by the phone; set the Honor "Manage manually" switches, lock it in Recents, open it. Ghost tells you once after 15 min of silence. |
| No Telegram replies | `.ghost/life-logs/supervisor.json` (relay alive?); if ghostpi is down the PC takes over after 3 min. |
| "Model returned no text content" | reasoning effort not applied: check `reasoning_effort` in `supervisor.json`, restart GhostLife. |
| A job shows `paused`, `yield_to_owner` | normal: you came back; it resumes when you are idle. |
| A job is paused after "Worker exited during ..." | a restart killed it mid-run: `ghost jobs resume <id>`. |
| "Outcome contract changed" | a queued job was edited; queue a new one (`ghost build N` again after clearing, or `ghost accomplish`). |
| Place is unknown | no fresh phone GPS and no home point: `ghost phone sethome` while at home, or `/sethome` on Telegram. |
| Changes not active | services run the code they started with: restart GhostLife when no job is running. |

---

## Honest limits

- Isolation: sandboxes and the code session run with your user's rights (see above).
- The phone app depends on the phone letting it run in the background.
- `health` has no source until HealthHub is located and approved; Huawei and DeskSentinel are
  placeholders.
- Not built yet: the agent room and two-key escalation (Phase 2 slice 1), learned patterns
  ("you open the FTC repo when you get home"), automatic continuation of a job that ran out of
  iterations, voice, model fine-tuning (the LoRA corpus is collected only), CTF environments.
- The benchmark numbers are from October and predate the November loop fixes.
- Ghost's model is remote: when gonkarouter is down, Ghost answers nothing until it is back.

---

## Development and docs

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider                       # main suite
.\workspaces\openjarvis-venv\Scripts\python.exe -m pytest -q -p no:cacheprovider  # incl. Telegram/OpenJarvis tests
```

- Product specification: [implementation.md](implementation.md)
- Life layer plan, status and evidence: [docs/engineering/life-layer/](docs/engineering/life-layer/)
- Engineering state and history: [docs/engineering/STATE.md](docs/engineering/STATE.md)
- OpenJarvis integration: [docs/openjarvis.md](docs/openjarvis.md)
- Telegram qualification runbook: [docs/engineering/telegram-live-qualification.md](docs/engineering/telegram-live-qualification.md)
- Companion app: `ghost-mobile/android/app/src/v8companion/`, test script
  `ghost-mobile/scripts/companion_emulator_test.ps1`
