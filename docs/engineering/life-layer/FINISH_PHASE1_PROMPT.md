Goal: finish Phase 1 of docs/engineering/life-layer so that every Phase 1 acceptance
scenario in CLAUDE_CODE_PROMPT.md is LIVE, not mock. Then merge `life-layer` to master.
Read STATUS.md first. It lists what is live and what is not.

Do these yourself (ask me only for approval where a step touches my accounts,
installs software, or changes the Pi):
1. Search: deploy SearXNG on ghostpi (Docker, JSON format on, LAN/tailnet only,
   no public port, check it does not collide with existing ports or Docker-bypasses-ufw
   issues). Set GHOST_SEARCH_URL. Prove one real search works.
2. Place without new apps: ghostpi pushes `place` from AdGuard phone DNS activity
   (phone IP from ghost-bot state) with a TTL. Home = recent phone DNS, else unknown.
3. ActivityWatch: install it (winget, with my approval) and confirm Ghost reads
   localhost:5600. pc_activity and task_guess must use it.
4. Autostart: relay + daemon start at my Windows login (Task Scheduler, my user,
   no admin), restart on failure, and report health in the morning digest.
5. Fix the contaminated test: remove the "Samsung syncs to Health Connect" hint from
   USER.md. Re-run the watch scenario live. Report honestly if GLM no longer finds it.
6. Hackathon scenario live with a real upcoming hackathon I name. Reminders must fire
   on Telegram. Run `build 2` for real.
7. Telegram `stop` live: engage and clear the emergency stop from Telegram.
8. Ask me once to turn on `care.message_owner` and `idle_work`, then run the daemon
   for 24 h. Report messages sent, heartbeats, model calls and situation freshness.
9. Full test suite in both venvs. Fix failures. Update README "What works today",
   STATE.md and STATUS.md with evidence levels. Merge to master.

Ask me for, in one message: the hackathon name, my routine and quiet hours for
USER.md, where HealthHub runs, and the approvals above.

Done means: STATUS.md shows every Phase 1 row as live, the 24 h report exists, tests
pass, master contains the work. Then propose Phase 2 slice 1 (LL5 room + LL6 two-key).
