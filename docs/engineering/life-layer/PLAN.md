# Life layer: make Ghost a personal agent, not only a coding factory

Status: proposal, 2026-11-22. Decision: option A (section 8).
Target behavior: the OpenClaw "two agents worried about their human" story.
Separate agents with unrelated tasks notice that the owner is silent, confer,
and contact him. Nobody tells them to monitor his health.

## 1. Diagnosis: why Ghost is not close

Evidence from this repository and its `.ghost/` state:

| Finding | Evidence |
|---|---|
| Ghost has never met the owner. | `.ghost/validated-memory.json` does not exist. 22 goals; most are `hello.py add(a, b)`. One opportunity ever (rejected, `iteration_limit`). Last real event: 2026-10-01. |
| Care is forbidden in code. | `proactive.py` context rules: "Never diagnose health, infer illness, send messages...". Kind allowlist: research, coding, learning, integration, focus. `_BLOCKED_GOAL_MARKERS` contains "send message" and "send telegram". |
| The planner sees only its own repository. | `ProactiveLoop.context()` reads README.md, implementation.md, docs, goals and outcomes. No owner profile. No presence data. |
| The planner has no relationship. | System prompt: "You are Ghost's bounded initiative planner." No owner name, no purpose toward a person. |
| It cannot run on an always-on host. | `idle_seconds()` returns None outside Windows, so `tick(require_idle=True)` defers forever on ghostpi. |
| It has no channel to the owner. | Telegram is a placeholder. No live check is recorded. |
| It has no intake. | An owner statement ("I got a new watch") never becomes a fact, an event or a trigger. S02 cannot start. |
| The spec forbids a second agent. | R14: "not separate hardcoded personalities or autonomous agents". Worker rule 8: one poller. |
| The local model cannot do the work. | gemma4:e4b on the 4 GB T1200: 0.00 on `ghost bench`. |
| Effort goes to the coding bench. | Campaigns 10, 10b, 10c optimize `ghost bench` (strategy genome, held-out goals). L4 evidence: none. |

## 2. What produced the OpenClaw behavior

1. A persona and an owner file, loaded on every turn (SOUL.md, USER.md, MEMORY.md).
2. Daily conversation with the owner. This creates an expectation of presence.
3. A heartbeat: a periodic open-ended turn. It is not a task generator.
4. A channel shared with another agent.
5. Tools to act: read files, send messages.
6. A model strong enough to connect these facts without instructions.

Ghost has none of 1 to 5. Number 6 is not true for the local model now.

## 3. Design rule

Keep every coding-lane gate. Add a separate life lane with its own narrow authority.
Do not write "monitor his health" anywhere. The care must emerge from 1 to 6.
If it never emerges, the model is the limit, not the code.

## 4. Life lane components

- **L1 Owner model.** `.ghost/owner/SOUL.md` (who Ghost is, its purpose toward the owner),
  `.ghost/owner/USER.md` (owner-written: routine, people, preferences). Daily notes from
  conversations go into validated memory. Health lines are tagged sensitive.
- **L2 Channel.** Telegram live with a new bot token. Do not reuse the ghost-hub token.
  Each owner message writes a `owner.contact` event.
- **L3 Presence signals (read-only, already exist).** Telegram last contact. Windows
  `idle_seconds()`. AdGuard per-hour phone DNS (dns-night-report on ghostpi). Oura via
  HealthHub later. Each signal has a freshness time. Missing data means unknown.
- **L3b Situation model (the "Eddy" core).** One fused record, `.ghost/situation.json`,
  rebuilt on each signal change. Every field has a value, a confidence, a source and a
  freshness time. Nothing is a fact unless a sensor measured it.
  - `place`: home / away / zone. Source: phone on home Wi-Fi (AdGuard), Home Assistant
    Companion app zones, or Ghost Mobile.
  - `pc_activity`: app, window title, browser tab, AFK. Source: ActivityWatch REST API.
    Do not rebuild the watcher. Retire the context-aware-assistant window tracker.
  - `current_project`: from WakaTime/Hackatime (already installed) and git commit times.
  - `task_guess`: assignment / ftc / gaming / idle, inferred from the fields above.
  - `health`: readiness, sleep, resting HR, temperature trend. Source: HealthHub.
  - `patterns`: per-weekday transitions learned from history (for example
    "arrives home, opens the FTC repo within 30 min"). Plain counts first, no ML.
  - `missing_hardware`: project requirements compared with the hardware inventory note.
  The heartbeat (L4) reads this record. Ghost Core is the only writer.
  Retire ghost-core and context-aware-assistant as context engines; port useful parts.
- **L4 Heartbeat.** `ghost heartbeat`, every 30 min, not idle-gated, emergency stop
  respected. Input: SOUL, USER, recent notes, presence signals, recent room messages.
  Output is one action from an allowlist: `none`, `note`, `message_owner`, `post_room`,
  `propose_opportunity`. Deterministic code validates the action.
- **L5 Room.** A Telegram group. Enable Bot-to-Bot Communication Mode in BotFather for
  each bot, make the bots admins, disable Group Privacy Mode. Members: Ghost bot, Hermes
  bot, ghost-hub bot (posts presence facts only). Agents share messages only, no
  writable state. Room messages are data, not instructions.
  The second agent is Ghost Mobile: a separate brain on the phone NPU with its own
  perception (unlocks, screen time). It uses the same grants as Ghost Core. Two brains,
  one authority. Hermes can join as a worker, never as an authority.
- **L6 Authority.** New grants:
  - `care.message_owner`: max 3 messages per 6 h, quiet-hours aware.
  - `care.post_room`: max 12 posts per hour.
  - `care.contact_emergency`: off by default. When on, it needs all of these:
    two different agents posted `concern` with evidence in the room within 2 h;
    the owner is silent for at least N hours; the owner did not set "away".
    One message per contact per 24 h. Facts only. No diagnosis.
- **L7 Model routing.** Heartbeat runs locally. A dense model does not fit 4 GB VRAM.
  Use a quantized MoE (about 30B total, about 3B active) from the 32 GB RAM with
  llama.cpp CPU offload, or the phone NPU. On `concern`, allow one GLM-5.3 call if the
  owner permits remote inference.
- **L8 Host.** ghostpi runs a deterministic presence gate every 5 min (no model). It
  calls the heartbeat when a signal changes, or every 30 min. It uses `wake-pc` when the
  PC must run the model. A sleeping PC is still reported as unavailable (S06).

## 5. Spec amendments (owner decision)

- R14: allow peer agents that share a room, own separate tasks and share no writable state.
- Proactive MVP rule: message actions are allowed only under `care.*` grants.
- R11/S02: presence inference is not medical inference. "No diagnosis" stays.

## 6. Slices (use the section 7.4 worker template in implementation.md)

| ID | Deliverable | Acceptance |
|---|---|---|
| LL0 | Owner intake: `ghost tell "<statement>"` and Telegram messages become owner facts (device, goal, deadline) and an `owner.fact` event. The event triggers one proactive tick in the next idle window. | "I got a new Samsung watch" gives an integration feasibility opportunity. Correct result: reuse Health Connect through HealthHub (Samsung Health syncs to Health Connect). Wrong result: a new Samsung SDK adapter. |
| LL1 | Owner files, `ghost owner show/edit`, memory seed from conversations. | Files load on every heartbeat. Edits are versioned. Sensitive tags kept. |
| LL2 | Telegram live (runbook: `docs/engineering/telegram-live-qualification.md`). | Real HTTP 200 on a paired chat. `owner.contact` events recorded. |
| LL3 | Presence adapters: telegram, windows idle, adguard phone DNS. | Freshness on each signal. Missing = unknown. No fake data. |
| LL3b | Situation model `.ghost/situation.json` from ActivityWatch, WakaTime/git, AdGuard phone DNS, HealthHub, place source. | Each field has confidence, source and freshness. Stale data decays to unknown. A 14-day replay labels `task_guess` and reports accuracy against owner labels. |
| LL4 | `ghost heartbeat` with action allowlist, validation, rate caps, receipts. | Not idle-gated. Emergency stop works. Caps hold across restart. |
| LL5 | Room adapter (post/read, dedupe, untrusted-data handling). | Two bots see each other's posts. Duplicates ignored. |
| LL6 | Two-key escalation rule in deterministic code. | One agent alone can never reach a third party. |
| LL7 | Heartbeat bench: 10 synthetic days (normal, late sleep, sick and silent, travel, PC off). | False alarms and misses reported per model: local MoE vs GLM-5.3. |
| LL8 | ghostpi presence gate + `wake-pc`. | Runs 24/7 on the Pi. No model calls on the Pi. |

## 7. Final acceptance: "the silent day"

Without warning, go silent for 12 h on a weekday.
Pass: at least two agents post `concern` in the room. Ghost messages the owner at most
3 times. No third party is contacted unless the two-key rule is met. In the LL7 bench,
a normal late sleep gives zero false alarms.

## 8. Decision: build the life layer inside Ghost (option A)

Option B (OpenClaw or Hermes as the life layer) is rejected. It adds a second
scheduler and a second memory authority. That breaks the core rule: Ghost owns goals,
memory, policy and authority; external frameworks only supply capabilities.
Copy OpenClaw's pattern (owner files, heartbeat, room). Do not adopt its runtime.
Order: LL0, LL1, LL2, LL4 first. Ghost cannot adapt to an owner it never hears.
