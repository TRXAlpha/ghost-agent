# Final prompt for Claude Code: turn Ghost into my daily personal AI

## End goal
Ghost becomes my personal AI, like Jarvis or Eddy: it knows my context, pays
attention all the time, takes useful initiative, learns my patterns, builds things for
me while I am away, and stays out of my way. It must become something I use every
day. Ship usable value first, then grow it. Work in small slices, commit each one on
the branch `life-layer`, and keep going across sessions until the phase acceptance
passes. Then continue with the next phase.

## Read first
- implementation.md: the product contract (sections 1, 4, 7.4, 8).
- docs/engineering/life-layer/PLAN.md: the diagnosis and the slices.
- README.md, docs/engineering/STATE.md, docs/openjarvis.md,
  docs/engineering/telegram-live-qualification.md.
- docs/engineering/campaign-2026-10c/HANDOFF.md: pause that campaign. Do not continue it.

## The truth about the current state
The plumbing exists (sandbox worker, gates, memory store, notifications, research,
Telegram bridge, EventBus). The product does not. Ghost has no personal memory, has
never talked to me, reads only its own repository, and its proactive prompt forbids
messaging me. Fix that first.

## Architecture: continuous attention, tiered (design it this way)
1. Always-on stream processors, no model: every event updates `.ghost/situation.json`.
2. Salience gate, deterministic: novelty, anomaly, deadline, owner mention, pattern break.
3. Small local model: reacts to what passes the gate, within minutes.
4. GLM-5.3-Flash (existing openai-compat route): planning, research, building. Rare.
5. Idle time: deep work, memory consolidation, practice builds, surprise tools.
6. Heartbeat every 30 min as a backstop. Not idle-gated. Heavy builds stay idle-gated.
Budgets cap every tier. It must feel continuous and cost little.

## Hard rules
- Ghost is the only authority for goals, memory, grants and scheduling. External tools
  supply capabilities only: OpenJarvis (engines, Telegram transport), ActivityWatch
  REST API, WakaTime data and git history, Home Assistant Companion (place, phone
  state), AdGuard/Ghostwall data on ghostpi (read-only), HealthHub (Health Connect).
  No external framework owns a scheduler or a memory store.
- Reuse what exists. Do not rewrite working subsystems. Before building any new
  capability, check for an existing tool and prefer free.
- Keep every coding-lane gate. Add an assistant lane with scoped grants and rate caps:
  message me, reminders, calendar writes (with my OAuth), phone actions (DND etc.),
  agent-room posts. A learned automation is proposed once; it runs automatically only
  after I accept it.
- Anything public or in my name (Reddit, HN, email, social posts) is draft-only. Ghost
  prepares a launch kit; I press send.
- Never diagnose. State health facts and trends only.
- No silent cloud fallback. Health and location data go only to endpoints I approve.
- Ghost Core is the only writer of situation and memory. ghostpi provides observations
  and small authenticated actions; it runs no model. Ghost Mobile joins later as a
  second brain under the same grants.
- At the start, list the exact owner-only steps (bot token, pairing, ActivityWatch and
  HA Companion install, OAuth). Do all other work while you wait.

## Phase 1: usable every day (PLAN.md LL0, LL1, LL2, LL3, LL3b, LL4)
Acceptance, live:
1. Telegram: "I got a new Samsung watch." Ghost stores the fact. In the next idle
   window it concludes: reuse Health Connect through HealthHub. No new adapter.
2. "I'm joining <real hackathon>." Ghost researches it (dates, registration deadline,
   rules, sources), sets reminders, proposes 3 ideas, and on "build 2" queues a
   sandbox prototype and reports the result.
3. 24 h run: situation.json stays fresh (place, PC activity, current project, health
   trend; each with confidence and freshness). At most 3 unprompted messages. The
   morning digest includes a situation summary.
4. `ghost why <id>` explains every action. Emergency stop works from Telegram.
5. All existing tests pass. New code has tests.

## Phase 2: it learns me (LL5 to LL8, patterns)
- Learned patterns (for example "DND at location Y at time Z", "FTC repo after
  arriving home"), proposed once, then automated.
- Complaints and friction I mention become idle-time opportunities (example: an FTC
  auto-path timing analyzer). I wake up to tested tools with a "why" note.
- Agent room and two-key escalation. The "silent day" test from PLAN.md passes.
- Desk camera: revive sentinel-ai as a snapshot sensor; Moondream captions locally.

## Phase 3: body and second brain
- Ghost Mobile as a second brain: signed grants, offline operation, event sync.
- TARS as a thin client body (voice, motion); the brain stays on the PC.
- Research lane: affect / activation steering from my research repo, only on a local
  open-weights model, never in the loop that messages people or escalates.

## Each session ends with
Commits, tests run and results, evidence level per scenario (mock, local, live),
updated README "What works today", remaining owner steps, and the next slice.
