# Life layer status

Branch: `life-layer` (merged to master after Phase 1). Plan: [PLAN.md](PLAN.md).
Campaign 2026-10c is paused (dispatcher already dead on 2026-11-22; c3/E4-E8 branches unmerged).

## What runs, and where (2026-11-22)

| Piece | Where | Notes |
|---|---|---|
| Relay + daemon (heartbeat, life lane, proactive, worker) | PC, Task Scheduler `GhostLife` at logon, user `chris`, no admin | `scripts/ghost-life.ps1 -Supervise` restarts each part with backoff; health in `.ghost/life-logs/supervisor.json`, in the digest |
| Model | gonkarouter `zai-org/GLM-5.3-Flash`, OpenJarvis openai-compat (remote, owner choice) | key in user env `GHOST_OPENJARVIS_API_KEY` (copied from the Hermes .env with owner approval) |
| Telegram | ghostbot token (user env `GHOST_TELEGRAM_BOT_TOKEN`); ghost-bot on ghostpi is the only poller | non-command owner messages -> `~/ghost-hub/state/ghost-inbox.jsonl` -> relay; owner location -> `state/ghost-location.json`; `/sethome` |
| Search | SearXNG on ghostpi `100.71.145.20:8888` (tailnet only, mem 256 MB, JSON on) | `GHOST_SEARCH_URL`; compose `~/ghost-hub/searxng/` |
| Place | `ghost-place.timer` on ghostpi every 2 min -> `state/ghost-place.json` (home/away/unknown only) | Telegram live location (<=150 m of home) > Tailscale LAN endpoint > public endpoint with recent AdGuard activity |
| PC activity | ActivityWatch (user install, Startup shortcut), `localhost:5600` | `pc_activity`, `task_guess` |
| Fallbacks | PC | ghostpi down >= 3 min: relay polls Telegram from the PC and hands back when the Pi answers or on 409 (live 2026-11-23 07:46). Telegram delivery failing: messages go to the companion app as notifications. Companion app "Send to Ghost": inbound channel with no Pi or Telegram. |
| Code session | PC | Owner decision 2026-11-23: Telegram/phone chat can run code. `session.code` grant (on), scratch workspace `.ghost/sessions/telegram/work` (`ghost session root <dir>` to point it at a repo, CLI only), 500 calls/h, audit `.ghost/sessions/telegram/audit.jsonl`. Amends the old rule that the bridge never grants shell: the allowlist in authority.py is unchanged, session tools are a separate grant. |
| Worker prompt cap | PC | `GHOST_MAX_INPUT_CHARS` (GLM launcher 96000, default 24000) |
| Grants | `care.message_owner` on, cap 100/24 h (owner: no restriction); `idle_work` on | hard rules stay: no diagnosis, public = draft, emergency stop |

Pi changes (all owner-approved 2026-11-22): `ghost_bot.py` relay patch (backup `ghost_bot.py.bak-relay`),
location + `/sethome` patch (backup `ghost_bot.py.bak-location`), `ghost-place.service/.timer`,
SearXNG compose. Undo: restore the backups, `systemctl disable --now ghost-place.timer`,
`docker compose -f ~/ghost-hub/searxng/docker-compose.yml down`.

## Owner steps still open

0. **Install the Ghost Companion on the phone** (built 2026-11-23, ghost-mobile branch `companion`, flavor
   `v8companion`, installs beside v1-v7 as "Ghost Companion"):
   APK `ghost-mobile-companion/android/app/build/outputs/apk/v8companion/debug/app-v8companion-debug.apk`.
   Open it, tap Grant location, Allow all the time, Grant usage access, Grant DND access, Start, then
   Copy Ghost token and set it on the PC: `[Environment]::SetEnvironmentVariable('GHOST_PHONE_TOKEN','<token>','User')`
   (`GHOST_PHONE_URL` is already `http://100.95.143.88:8799`). Then restart: `Start-ScheduledTask GhostLife`.
   Ghost then pulls phone_activity + GPS place and switches DND/silent in the quiet windows
   (`care.phone_actions` is on). Nightly at 03:30 Ghost rebuilds and tests the app on the emulator
   (`GhostMobileTest` task), never on the phone; first run PASS (Telegram).

1. **HealthHub:** owner is checking where it runs. Until an endpoint is approved, `health` stays unknown.
2. **Keep the Telegram live location shared** (Attach -> Location -> Share live location, until turned off);
   otherwise place falls back to Tailscale (home on Wi-Fi only; mobile data at home reads as away 0.6).
3. **Phone DND/silent during quiet windows:** Ghost holds messages in those windows; switching the phone
   itself needs a phone-side component (the Ghost Android app, next).

## Evidence per Phase 1 scenario

| Scenario | Evidence | Detail |
|---|---|---|
| 1. "New Samsung watch" -> fact -> idle tick concludes reuse Health Connect via HealthHub | **live, partial** | Fact `owner_dab60c695a28` from a real Telegram message. With the seeded hint removed from USER.md, GLM proposed `opp_4b5c6b4017fd6a1a2531` "Samsung watch data path: feasibility research": reuse-first, no new adapter, "HealthHub once confirmed", Health Connect only as "Health Connect-style paths". It does not conclude the full Health Connect -> HealthHub path because HealthHub is honestly unconfirmed. The earlier full match (`opp_4b39acab…`) was contaminated by the hint. |
| 2. Hackathon: research, reminders, 3 ideas, "build 2" | **live** | `ghost tell "I'm joining the NASA Moon Base Challenge"` -> SearXNG -> 5 pages (3 nasa.gov) -> 15 quote-verified facts (registration until 2027-01-25 via NASA STEM Gateway; six design categories; U.S. high-school and post-secondary teams) -> Telegram message 190 with 3 ideas. Reminders 2027-01-18 and 2027-01-24 09:00; a live-check reminder fired on Telegram (message 197, `hb_78d4a8574902`). Owner sent `build 2` (msg 191) -> job `outcome_20261122_223559_c98a44bb_e80f064e` "DTN Lunar Mesh". Caveat: moonbasechallenge.org refuses Ghost's honest user agent, and the nasa.gov pages yielded one quotable fact (the $1 million prize), so dates, deadline and rules are quoted from news pages. |
| 3. 24 h run: fresh situation, <= 3 unprompted messages, digest with situation | **live, running** | GhostLife started 2026-11-22 22:30; task `GhostLife24hReport` at 2026-11-23 22:32 writes [24h-report.md](24h-report.md) and a Telegram summary. Owner raised the unprompted cap to 100/24 h, so "<= 3" is now the owner's choice, not a code limit; the report counts unprompted messages either way. |
| 4. `ghost why` explains every action; Telegram emergency stop | **live** | Real messages: `stop` (msg 193) engaged the stop and held jobs; `resume` (msg 195) cleared it (both receipts `delivered`). Heartbeat receipts and owner facts resolve with `ghost why`. |
| 5. All existing tests pass; new code has tests | **local** | Main venv and OpenJarvis venv full suites; counts in STATE.md. |

Build 2 result: reported to the owner on Telegram once the worker finishes (idle-gated); recorded in the 24 h report.

## Found and fixed during the live run

- 700/2048-token budgets returned no text on GLM (reasoning tokens count): `REASONING_BUDGET = 4000`.
- GLM ignored new owner facts for older coding noise; tagging needed a relevance check.
- A long sandbox build blocked reminders and replies: the life lane now has its own daemon thread.
- Restarting services mid-build pauses the job and blocks the worker until review (by design); such
  alerts are now forwarded to Telegram once. 18 stale worker notices (17 from September) were acknowledged first.
- `installed_integrations` treated an unpaired Telegram (`{"enabled": False}`) as paired.
- DuckDuckGo bot challenge -> SearXNG; site blocks on Ghost's user agent are skipped, not spoofed.
- Phone DNS goes over Tailscale everywhere, so "recent phone DNS" alone cannot mean home.

## Next

1. Ghost uses the vault knowledge graph (Ghost/vault + graphify) as memory/context.
2. Done 2026-11-23: Ghost Companion (see owner step 0). Reviewed by opencode/gemini-4-hydrogen;
   6 of 10 findings fixed, 4 documented as shortcuts. Live on the phone pending install.
2b. Iteration limits raised on owner request (queued jobs 30, accomplish 30). Build 2 rerun with them.
3. Phase 2 slice 1: LL5 room + LL6 two-key escalation (proposal in STATE.md).
