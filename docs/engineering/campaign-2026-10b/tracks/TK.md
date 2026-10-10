# Track TK: practice challenges, oracle evidence, teaching-format learning (S04, R10)

Wave 2 track, created 2026-10-10 when TG finished. Audit: `audit/A2.md`.
Branch `c2/TK`, worktree `C:/Users/chris/Documents/Ghost/ghost_c2_TK`.
Files this track owns: `ghost_agent/learner.py`, `ghost_agent/study.py`,
new `ghost_agent/challenges.py`, `cli.py` (the `learn` block and new blocks
only), and tests. Never run challenge code on the host outside the existing
bounded subprocess oracle; never install toolchains.

---

## TK1: oracle evidence and dependency plan

**Goal:** Rust checks record the exact toolchain, and a missing toolchain
produces a concrete plan instead of a silent failure (S04: "Rust examples
compile under a recorded toolchain"; "missing toolchains trigger a concrete
dependency plan; no silent unrestricted host installation").

**Decided design:**

- `RustCompileOracle.toolchain() -> Optional[str]` runs `rustc --version`
  (5 s timeout) and returns its stripped output, or None. Cache it per
  instance.
- `check()` result feedback is prefixed with `"[rustc <version>] "` when known.
  `LearnerStore.record_attempt` already takes `feedback`, so the attempt keeps
  the version. Make sure the study.py `rustc` path passes it through.
- When `available()` is False, `check` returns
  `(False, "rustc not available; dependency plan written to .ghost/learner/dependency_plan.md")`
  and writes that file (overwrite allowed). It contains: what is missing
  (`rustc`), why (which exercise/concept), the official install source
  `https://rustup.rs` (link only), the exact owner command to run themselves
  (`rustup-init` from rustup.rs), and the statement
  `Ghost will not install toolchains itself.` The oracle needs a `root` for
  this; add an optional `plan_root: Path | None = None` init arg. With None,
  no file is written.

**Tests:** patch `shutil.which` and subprocess. Version prefix in the
feedback; unavailable → plan file with the required lines; no plan_root → no
file.

---

## TK2: practice challenges with protected solutions and reset (review: 2)

**Goal:** reproducible, resettable practice challenges (Rust or Python),
with solutions/oracles kept apart from learner-facing material (S04
"solvable, reproducible, isolated, and resettable"; "a solution and test
oracle exist separately"). Cybersecurity/CTF *environments* (network
services, containers) are **out of scope** here; this covers code
challenges only.

**Decided design:** new module `ghost_agent/challenges.py`:

- Learner-facing: `.ghost/learner/challenges/<challenge_id>/` holding
  `README.md` (the prompt) and the `starter/` files.
- Protected: `.ghost/learner/protected/<challenge_id>/` holding `oracle.json`
  (`{"kind": "pytest"|"rustc", "tests": "<test file content>"}`) and
  `solution/` (reference).
- `ChallengeStore(root)` methods:
  - `create(concept_ids, prompt, starter: dict[str,str], oracle: dict, solution: dict[str,str]) -> str`.
    It **verifies solvability before saving**: copy the solution into a temp
    dir, run the oracle against it (pytest via `sys.executable -m pytest -q`
    with the oracle test file written into the temp dir; rustc via
    `RustCompileOracle`), and refuse to create it if the oracle fails on the
    solution. The oracle must also **fail on the starter files** (negative
    control); otherwise refuse ("starter already passes").
  - `reset(challenge_id)` restores `starter/` from the stored originals: keep
    a copy in protected `starter_original/`.
  - `check(challenge_id, concept_id) -> (bool, str)` runs the oracle against
    the learner's current `starter/` files in a temp copy and records
    `LearnerStore.record_attempt`.
  - `list()`.
  The learner directory never contains oracle or solution files.
- Subprocess runs use a 60 s timeout and run in a temp copy; nothing runs in
  the learner directory itself.
- CLI under `ghost learn`: `challenge create --spec PATH.json`,
  `challenge list`, `challenge check <id> --concept <cid>` and
  `challenge reset <id>`.

**Tests:** create refuses an unsolvable spec and refuses a starter that
already passes; check fails on the starter and passes after copying in the
solution; reset restores; the learner dir has no protected files (walk it).

---

## TK3: which teaching format works

**Goal:** Ghost learns which instructional format helps the owner and can
switch or stop producing unused material (R10, S04: "evaluates whether the
format helped and can switch approaches").

**Decided design:** in learner.py add a `FormatStats` store at
`.ghost/learner/formats.json`. For each format (`exercise`, `challenge`,
`explanation`), keep `generated`, `attempted`, `correct`, and
`last_used_at`. Hook in: study.py `generate` increments `generated` for
`exercise`; `check` increments `attempted`/`correct`; `ChallengeStore.create`
and `check` do the same for `challenge`.
`recommend_format() -> {"format": str, "reason": str}` works like this:
- among formats with `attempted >= 3`, pick the highest `correct/attempted`;
- any format with `generated >= 5 and attempted == 0` is listed in `unused`
  with the reason "generated but never attempted: stop generating";
- with no data, the default is `exercise` with reason "no evidence yet".

CLI: `ghost learn formats`.

**Tests:** recommendation by success rate; unused detection; the no-data
default; counters increment through the real study/challenge paths.
