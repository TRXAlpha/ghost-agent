# Life layer status

Branch: `life-layer`. Plan: [PLAN.md](PLAN.md). Campaign 2026-10c is paused (its kanban
dispatcher was already dead on 2026-11-22; its c3/E4-E8 branches stay unmerged).

## Owner-only steps (Ghost cannot do these)

1. **Done 2026-11-22 (outbound):** ghostbot token copied from ghostpi `~/ghost-hub/.env` into the Windows
   user env var `GHOST_TELEGRAM_BOT_TOKEN` (never printed, not in the repo); chat 5309400589 paired from
   `state/owner.json`; live check `success` (fingerprint 4d6c579e43942932).
2. **Inbound: done via ghost-bot relay (owner choice 2026-11-22).** `~/ghost-hub/bot/ghost_bot.py` patched
   (backup `ghost_bot.py.bak-relay`, service restarted): non-command owner messages are appended to
   `~/ghost-hub/state/ghost-inbox.jsonl` instead of "Unknown command". Ghost runs
   `ghost openjarvis telegram relay --model <m> --allow-sandbox-builds` (OpenJarvis venv, token in env);
   it needs to be started after each PC boot (no autostart yet). Hub commands (status, wake, dns, brief, ...)
   still go to ghost-bot. To undo on the Pi: restore the backup and restart `ghost-bot.service`.
2b. **Search backend.** DuckDuckGo returns a bot challenge (HTTP 202) to Ghost. Run SearXNG on ghostpi
   (`docker run -d -p 8888:8080 searxng/searxng`, enable `json` in `formats`) and set
   `GHOST_SEARCH_URL=http://<pi-tailnet-ip>:8888`.
3. **Turn on the assistant-lane grant:** `ghost grant care.message_owner on` (cap 3 per 24 h).
4. **Model: GLM-5.3-Flash via gonkarouter (owner choice 2026-11-22, remote).** Copy the Hermes key once into a
   Ghost-only user env var (run it yourself; Ghost was not allowed to read the Hermes key):
   `$k = (Select-String '^HERMES_CUSTOM_API_GONKAROUTER_IO_API_KEY=' "$env:LOCALAPPDATA\hermes\.env").Line.Split('=',2)[1]; [Environment]::SetEnvironmentVariable('GHOST_OPENJARVIS_API_KEY', $k, 'User')`
   Then `.\scripts\ghost-life.ps1 -Check` and `.\scripts\ghost-life.ps1` (relay + daemon; routing env is
   per process). Remote inference sends owner files, facts and situation (health values withheld) to gonkarouter.
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
| 2. Hackathon: research, reminders, 3 ideas, "build 2" | Briefing with quote-verified facts, reminders, 3 ideas, `build N` (CLI + Telegram), one result report: built | **mock** (scripted search/fetch/model). Live blocked on search backend and a model |
| 3. 24 h run: fresh situation, <= 3 unprompted messages, digest with situation | Situation record, decay, heartbeat cap 3/24 h, digest line: built | **local** (situation rebuild on this PC: Windows idle + CLI contact; ActivityWatch absent). No 24 h run |
| 4. `ghost why` on every action; Telegram emergency stop | Heartbeat receipts and owner facts explainable; `stop` over Telegram engages the stop | **live** inbound fact path: 2026-11-22 owner sent "got a new Samsung watch" -> ghost-bot inbox -> relay -> fact owner_dab60c695a28 -> reply delivered. `stop` over Telegram: mock only |
| 5. All existing tests pass; new code has tests | see STATE.md | **local** |

## Next slices

1. Phase 1 live run once a model is reachable: `ghost tell` the watch and a real hackathon, `ghost brief`,
   24 h daemon run with `care.message_owner` on.
2. LL5 room adapter + LL6 two-key escalation (Phase 2 start), ghost-bot relay for inbound if no second bot.
3. Phase 2 patterns: per-weekday transition counts from situation history, proposed once, automated after accept.
4. ActivityWatch installed -> 14-day `task_guess` replay against owner labels (LL3b acceptance).
