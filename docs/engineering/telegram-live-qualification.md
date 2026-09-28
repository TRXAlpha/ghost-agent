# Task OJ04/S01: Telegram live qualification runbook

## Purpose

This runbook records what counts as real success for the OpenJarvis Telegram
bridge. A live test is only qualified by a real HTTP 200 from the Telegram Bot
API on an authorized, owner-paired connection. Nothing else (mock output,
assumed connectivity, green unit tests) counts.

The bridge itself (`ghost_agent/openjarvis_bridge/telegram.py`) is owner-paired,
read-only, and receipt-deduped. The commands below exercise credentials and one
authorized message; they never start a second Telegram client or polling daemon.

## Safety invariants (hold for every step)

- Token value is NEVER stored in a file, echoed, or printed. Only a SHA-256
  prefix (16 hex chars) fingerprint is ever recorded (`.ghost/openjarvis/telegram-token-fingerprint.json`).
- No network call is made when the token is absent.
- Message delivery happens ONLY via explicit `live-check` invocation. No other
  command sends to Telegram.
- Any test refusal prints `next_step` and exits non-zero; it never fakes success.

## Owner steps

### 1. Create the bot

In Telegram, open @BotFather → `/newbot` → follow the prompts. Save the token
it gives you (format `<numeric-id>:<alphanumeric>`). Never paste it into chat,
files, or this document.

### 2. Export the token (per-session, not committed)

```bash
# bash / git-bash on Windows
export GHOST_TELEGRAM_BOT_TOKEN="<paste the BotFather token here>"
# or, equivalently for this adapter:
export TELEGRAM_BOT_TOKEN="<paste the BotFather token here>"
```

Check presence without exposing the value:

```bash
./workspaces/openjarvis-venv/Scripts/ghost-jarvis.exe openjarvis telegram status
# Expect "token_present": true and a next_step pointing at pairing.
```

### 3. Pair the owner

Send your bot any message from your own account first (this wakes the bot and
gives you your numeric user ID via any Telegram user-lookup bot or BotFather's
`/mybots` → bot → "Bot Settings" → no; use @userinfobot or similar). Then:

```bash
./workspaces/openjarvis-venv/Scripts/ghost-jarvis.exe openjarvis telegram pair --user-id <your-numeric-user-id> --chat-id <same-number>
# Private chats only; chat_id must equal user_id for this qualification.
```

Verify:

```bash
./workspaces/openjarvis-venv/Scripts/ghost-jarvis.exe openjarvis telegram status
# Expect "pairing_present": true and next_step: "ready for authorized live test".
```

### 4. Authorized live test

Send exactly one harmless ping to the paired chat:

```bash
./workspaces/openjarvis-venv/Scripts/ghost-jarvis.exe openjarvis telegram live-check --chat-id <your-numeric-user-id>
```

On success this prints a JSON record with `"outcome": "success"`, a
`"checked_at"` timestamp, and the token fingerprint; the same record is written
to `.ghost/openjarvis/telegram-live-check.json`. The message "Ghost live
qualification ping" appears in your paired Telegram chat.

If the token was just rotated, first run:

```bash
./workspaces/openjarvis-venv/Scripts/ghost-jarvis.exe openjarvis telegram rotate-token-check
# Compares against the stored SHA-256 fingerprint; records the new one if changed.
```

### 5. Revoke (when needed)

```bash
./workspaces/openjarvis-venv/Scripts/ghost-jarvis.exe openjarvis telegram revoke
# Then revoke the token itself via BotFather (/revoke or /mybots → API token).
```

## Acceptance list (S01)

A Telegram live-qualification slice is accepted only when ALL of the following
are true on a real, authorized connection:

1. `ghost openjarvis telegram status` reports `token_present` (bool), `pairing_present` (bool), and a single `next_step` string.
2. With the token absent from the environment, no Bot API network call is made by any status/doctor path.
3. `rotate-token-check` rejects a malformed token (non-`<numeric>:<alphanumeric>`), accepts a well-formed one, and stores only a SHA-256 prefix fingerprint (never the token) in `.ghost/openjarvis/telegram-token-fingerprint.json`.
4. `live-check --chat-id <id>` refuses (non-zero exit, no network) unless: token present, pairing enabled, chat_id equals the paired chat, and emergency stop is clear.
5. `live-check` success requires a real Bot API 200 response; the result (success/failure + timestamp + fingerprint) is recorded to `.ghost/openjarvis/telegram-live-check.json`.
6. Emergency stop engaged ⇒ live-check refuses before any network call.
7. `ghost doctor` surfaces the Telegram integration health (ready / not_ready + credentials present/absent) without any network call when the token is absent.
8. `ghost integration plan telegram` output cross-links this runbook.
9. No credential value ever appears in any file, log, or command output.

## Known limitations

- The read-only bridge (`serve`) is a separate qualification path; this runbook
  only qualifies the live-check ping.
- The live-check result is only meaningful on the paired private chat; group
  chats are refused by design.
- Windows: export via git-bash or PowerShell `$env:GHOST_TELEGRAM_BOT_TOKEN="..."`.
