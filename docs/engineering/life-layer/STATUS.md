# Life layer status

Branch: `life-layer`. Plan: [PLAN.md](PLAN.md). Campaign 2026-10c is paused (its kanban
dispatcher was already dead on 2026-11-22; its c3/E4-E8 branches stay unmerged).

## Owner-only steps (Ghost cannot do these)

1. **Telegram bot token (new bot, not the ghost-hub token).** BotFather `/newbot`, then in the
   shell that runs Ghost: `$env:GHOST_TELEGRAM_BOT_TOKEN = '<token>'`. Never paste it in chat or a file.
2. **Pair your private chat.** Send the bot any message, read your user id, then
   `ghost openjarvis telegram pair --user-id <id> --chat-id <id>` and
   `ghost openjarvis telegram live-check --chat-id <id>` (runbook: `../telegram-live-qualification.md`).
3. **Turn on the assistant-lane grant:** `ghost grant care.message_owner on` (cap 3 per 24 h).
4. **Model for the heartbeat and proactive loop.** Either start Ollama with a model that fits, or set
   the GLM-5.3-Flash route in the Ghost shell: `GHOST_INFERENCE_BACKEND=openjarvis`,
   `GHOST_OPENJARVIS_ENGINE=openai-compat`, `GHOST_OPENJARVIS_URL`, `GHOST_OPENJARVIS_API_KEY`,
   `GHOST_ALLOW_REMOTE_INFERENCE=1`. Remote inference sends owner files and situation (health values
   are withheld) to that endpoint; that is your call.
5. **ActivityWatch** on the PC (https://activitywatch.net, free). Ghost reads `localhost:5600`.
6. **Home Assistant Companion** on the phone (place zones), or let ghostpi push place from AdGuard
   phone DNS: `ghost situation push --field place --value home --source ghostpi.adguard --ttl 900`.
7. **HealthHub endpoint approval.** HealthHub was not found on this PC or in the vault; tell Ghost where
   it runs (`ghost tell` or edit USER.md) and which endpoint it may read.
8. **Edit `USER.md`** with your routine and quiet hours: `ghost owner show USER.md`, then
   `ghost owner edit USER.md --file <path>`.
9. **Enable unattended work** for the daemon: `ghost policy set idle_work on`, then `ghost daemon run`.

## Evidence per Phase 1 scenario (2026-11-22)

| Scenario | State | Evidence |
|---|---|---|
| 1. "New Samsung watch" -> fact -> idle tick concludes reuse Health Connect via HealthHub | Intake, fact, event, one-shot owner context in the tick, `why` link: built | **mock** (scripted planner). Whether the model concludes "reuse" is unverified: no model was reachable |
| 2. Hackathon: research, reminders, 3 ideas, "build 2" | Goal + deadline fact from "I'm joining X on YYYY-MM-DD" only | not started |
| 3. 24 h run: fresh situation, <= 3 unprompted messages, digest with situation | Situation record, decay, heartbeat cap 3/24 h, digest line: built | **local** (situation rebuild on this PC: Windows idle + CLI contact; ActivityWatch absent). No 24 h run |
| 4. `ghost why` on every action; Telegram emergency stop | Heartbeat receipts and owner facts explainable; `stop` over Telegram engages the stop | **mock** (OpenJarvis channel test doubles) |
| 5. All existing tests pass; new code has tests | see STATE.md | **local** |

## Next slices

1. LL2 live: owner steps 1-3, then a real `owner.contact` and a real heartbeat message.
2. Scenario 2: `research` lane for owner goals (dates, deadline, rules, sources), reminders from
   deadlines, 3 ideas message, "build N" over Telegram -> `queue_sandbox_outcome`.
3. Salience gate (tier 2) on `situation.changed` / `owner.fact` so the heartbeat runs early on change.
4. ActivityWatch installed -> 14-day `task_guess` replay against owner labels (LL3b acceptance).
