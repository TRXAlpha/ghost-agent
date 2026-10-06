# T14: Delete `notifications.py` (competing notification system)

Size: XS · Wave 2 (after T13; same test file) · Files: delete
`ghost_agent/notifications.py`; edit `tests/test_ai_os_subsystems.py`

## Why this is a deletion (decided)

- The real notification store is `runtime.AttentionManager` / `runtime.Notification`
  (`.ghost/notifications.json`). It is used by check-ins, the control plane and
  the CLI. Preferences live in `control_plane.NotificationPreferences`.
- `notifications.py` adds a second store (`.ghost/notifications/`), its own
  quiet hours, and a `send_telegram` path that bypasses the Telegram pairing
  and authority bridge (`openjarvis_bridge/telegram.py`). That is a safety
  concern.
- It has no production caller (only `tests/test_ai_os_subsystems.py`).

## Steps

Follow the "Shared deletion procedure" in
`tasks/T13-delete-capability-manager.md`, with these substitutions:

- grep pattern: `"ghost_agent.notifications\|from .notifications\|from \.notifications\|NotificationStore\|QuietHours\|ChannelPreferences"`.
  Note that `runtime.Notification` and `control_plane.NotificationPreferences`
  are **different, real** classes. They will appear in the grep and must stay.
  Report every match, and explain why each one is or is not a caller of
  `notifications.py`.
- module to delete: `ghost_agent/notifications.py`.
- in the test file: remove the
  `from ghost_agent.notifications import (...)` import (it spans several lines)
  and the whole `class TestNotifications:` block.

## Must NOT

- Touch `runtime.py`, `checkins.py` or `control_plane.py`.
- Delete `.ghost/notifications/` or `.ghost/notifications.json` on disk.

## Verification

```bash
"$PY" -c "import ghost_agent.cli"
"$PY" -m pytest -q -p no:cacheprovider tests/test_ai_os_subsystems.py tests/test_checkins.py tests/test_checkin_visibility.py tests/test_attention_integrity.py
"$PY" -m pytest -q -p no:cacheprovider --deselect tests/test_overnight.py::TestRun::test_max_cycles_stops
```

## Acceptance criteria

- AC1: `notifications.py` is gone, and nothing imports it.
- AC2: Only `TestNotifications` was removed.
- AC3: The full suite passes.
