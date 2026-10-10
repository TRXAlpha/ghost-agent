# Track TM: operational controls in the loopback API and dashboard (R14, R04, R09)

Wave 2 track, created 2026-10-10 when TB finished. Audit: `audit/A4.md`
(R14). Branch `c2/TM`, worktree `C:/Users/chris/Documents/Ghost/ghost_c2_TM`.
Files this track owns: `ghost_agent/api_server.py`, `ghost_agent/dashboard.py`,
`ghost_agent/control_plane.py`, and tests. No new dependencies, no external
JS/CSS (the dashboard stays a single self-contained HTML page), and no
non-loopback binding.

**Shared rule for TM1/TM2:** every API action calls the **same** store or
GhostSDK method the CLI uses. Read the CLI handlers for policy,
notifications, focus, why, emergency-stop and jobs first, and never duplicate
logic. If the CLI holds logic inline, move it into a GhostSDK method, and
make the CLI call that method too.

---

## TM1: API endpoints for owner controls (review: 2)

**Decided design** (in `api_server.py`'s dispatch, after the existing `/v1/jobs/` handling):

| Method | Path | Body | Behavior |
|---|---|---|---|
| GET | `/v1/policy` | – | PolicyStore.load() |
| POST | `/v1/policy` | `{"mode": str, "enabled": bool}` | PolicyStore.set; **requires a configured token** (see below) |
| GET | `/v1/notifications` | – | `{"deliverable": [...], "held": n}` (TE1's logic) |
| POST | `/v1/notifications/<id>/ack` | `{}` | AttentionManager.acknowledge |
| GET | `/v1/focus` | – | active session or null |
| POST | `/v1/focus/start` | `{"purpose": str, "minutes": int, "hold_notifications": bool}` | FocusStore.start |
| POST | `/v1/focus/stop` | `{}` | FocusStore.stop (always allowed: override) |
| GET | `/v1/why/<id>` | – | GhostSDK.explain(id); 404 if unknown |
| POST | `/v1/emergency-stop/engage` | `{"reason": str}` | allowed **without** a token: stopping is always safe |
| POST | `/v1/emergency-stop/disengage` | `{}` | **requires a configured token** |

- "Requires a configured token" means: if `GHOST_API_TOKEN` is not set,
  respond 403 with
  `{"error": "set GHOST_API_TOKEN to change policy or disengage emergency stop over the API"}`.
  If it is set, the existing bearer check applies. This is stricter than the
  other endpoints on purpose: authority changes over HTTP need an explicit
  secret.
- Validation errors → 400 with a message; store errors (PolicyError,
  focus refusal) → 409; unknown ids → 404. Keep the existing limits
  (64 KiB, content type, Host and Origin checks) for every new route.

**Tests** (follow `tests/test_api_server.py`): each route's happy path; 403
for a policy change and for disengage without a token; 401 with a wrong token
when one is configured; engage without a token works; 404/409/400 cases; the
API and CLI produce the same state change (for example, set build off via the
API, then `PolicyStore.load()` shows off).

---

## TM2: dashboard controls and token entry

**Goal:** the dashboard becomes an operational interface, not a log viewer
(R14). This also closes the README's "the dashboard does not yet provide
token entry".

**Decided design** (`dashboard.py`; one self-contained page, vanilla JS):

- **Token:** a password input saved to `sessionStorage` (never
  `localStorage`) and sent as `Authorization: Bearer <token>` on every request
  when present. Show a clear message on 401/403 responses.
- **Sections, each refreshed every 10 s and after any action:**
  1. **Emergency stop:** a status line, an **Engage** button (always
     enabled), and a **Disengage** button.
  2. **Policy:** each mode with an on/off toggle, sent via `POST /v1/policy`.
  3. **Notifications:** the deliverable list, each with an **Ack** button,
     plus "N held".
  4. **Focus:** current session or start form (purpose, minutes, hold
     checkbox), and a **Stop** button.
  5. **Jobs:** a table (id, status, goal) with Pause/Resume/Cancel buttons
     using the existing `/v1/jobs/<id>/<action>`, where the job list comes from
     the snapshot (add `scheduler_jobs` to the snapshot if absent, through a
     GhostSDK method).
  6. **Why:** an id input that shows the `/v1/why/<id>` result as formatted
     text.
- Every API value is inserted with `textContent`, never `innerHTML`, so there
  is no XSS from stored data. This is a hard requirement.
- Keep the existing snapshot view.

**Tests:** the rendered HTML contains each section id and button; there is no
`innerHTML` assignment with dynamic data (grep the rendered string for
`innerHTML`; only the static template is allowed); the token is stored in
sessionStorage (string present) and never in localStorage (string absent);
the snapshot includes `scheduler_jobs`.
