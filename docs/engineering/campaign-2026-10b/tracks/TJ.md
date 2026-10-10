# Track TJ: feasibility records, reuse inventory, real resource snapshot (R06, R08, R12)

Wave 2 track, created 2026-10-10 when TE finished. Audits: `audit/A5.md`
(R06 feasibility), `audit/A7.md` (R08 reuse), `audit/A1.md` (R12 resources).
Branch `c2/TJ`, worktree `C:/Users/chris/Documents/Ghost/ghost_c2_TJ`.
Files this track owns: `ghost_agent/capability_acquisition.py`, the
`ResourceMonitor` class in `ghost_agent/runtime.py` (that class only),
`ghost_agent/runtime.py`'s `Doctor` (TJ3: one `resources` key only),
`cli.py` (the `capability` block and new blocks only), and tests.

---

## TJ1: feasibility record (review: 2)

**Goal:** a capability investigation produces R06's feasibility record (need,
approaches, sources with dates, constraints, prerequisites, cost,
maintenance, unknowns, next step), instead of a stub.

**Decided design:**

- Add to `AcquisitionRecord` (defaults so old records load):
  `feasibility: dict = field(default_factory=dict)`.
  Validated keys (all optional, unknown keys refused):
  - `need: str`
  - `approaches: list[str]` (≤ 5)
  - `sources: list[{"url"|"path": str, "checked_at": float, "kind": "official_docs"|"repo"|"article"|"local"}]` (≤ 10)
  - `constraints: list[str]`
  - `prerequisites: list[str]`
  - `scopes: list[str]`
  - `expected_data: str`
  - `cost: str` (free text; `""` means unknown, never "free" by default)
  - `maintenance: str`
  - `unknowns: list[str]`
  - `next_step: str`

  Strings are capped at 500 chars and lists at 10 items. Anything else is a
  `ValueError`.
- `CapabilityAcquisition.record_feasibility(record_id, data: dict) -> AcquisitionRecord`
  merges validated data and sets `updated_at`. Allowed statuses:
  `discovered|researched|feasible|waiting_external`. Recording it while
  `discovered` moves the record to `researched`. Find the transition table and
  use the existing transition mechanism; don't bypass it.
- CLI under the existing `ghost capability` group:
  `feasibility <id> --set-json PATH` (reads a JSON file and validates it) and
  `feasibility <id>` (prints the record's feasibility as a readable report,
  showing "unknown" for empty `cost`/`maintenance`).

**Tests:** validation (unknown key, oversize, bad source kind); recording moves
discovered → researched via the transition table; the print shows unknown
cost explicitly; old records load.

---

## TJ2: reuse inventory from installed integrations

**Goal:** "reuse before build" checks what is actually installed or
configured, not only a static map (R08, S01).

**Decided design:** keep the static `known` map as the base. Add
`installed_integrations(root) -> dict[str, str]` in capability_acquisition.py,
which returns capability → integration for:
(a) ExtensionManager extensions whose status is `enabled`/`approved` (read
extensions.py's list API; the capability is the extension's declared
capability/tool name);
(b) MCP add-ons registered via `mcp_addons.py` (read its list API);
(c) OpenJarvis channels, **only** if `openjarvis_bridge.runtime.require_upstream()`
succeeds (catch everything; if it fails, add nothing and set
`installed_integrations.last_error`).
Then `reuse_inventory(capability, root=None)`: if `root` is given, check the
installed integrations first, then the static map. Existing callers without
`root` behave as before; update the production caller(s) in the acquisition
flow to pass `root` (`git grep -n "reuse_inventory("`).

**Tests:** an enabled extension is found for its capability; a disabled one
isn't; upstream unavailable → static map only, with the error recorded; the
legacy call without root is unchanged.

---

## TJ3: real RAM/GPU snapshot and `ghost doctor resources` (parallel-ok with TJ2)

**Goal:** resource admission and the doctor see actual RAM and GPU on this
Windows host (R12). Today RAM is absent on Windows (`os.sysconf` is
unavailable) and there is no GPU at all.

**Decided design** (`ResourceMonitor.available`):

- RAM on Windows: `ctypes` `GlobalMemoryStatusEx` → `ram_bytes` (available
  physical) and `ram_total_bytes`. Keep the POSIX `sysconf` path for other
  OSes.
- GPU: if `shutil.which("nvidia-smi")`, run
  `nvidia-smi --query-gpu=memory.total,memory.used --format=csv,noheader,nounits`
  with a 3-second timeout. Sum the GPUs: `gpu_vram_total_bytes` and
  `gpu_vram_free_bytes` (MiB × 1048576). Any failure or timeout leaves the GPU
  keys **absent**, never zero or guessed.
- `_fits_budget` (Scheduler) already compares keys present in both, so a job
  budget like `{"gpu_vram_free_bytes": 6e9}` is refused on this 4 GB GPU.
  Confirm by reading it; **do not edit Scheduler** (track TA owns it). If
  `_fits_budget` doesn't behave that way, report it instead.
- `Doctor`: add a `resources` key, a short string such as
  `"ram 5.1/16.0 GB free, gpu 3.6/4.0 GB free"`, or the parts that are
  unknown.

**Tests:** patch ctypes/subprocess. Windows path fields; an nvidia-smi parse
with 2 GPUs; nvidia-smi missing or timing out → keys absent; the doctor
string with unknown parts.
