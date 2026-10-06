# T19: Make README and STATE describe only what exists

Size: S · Wave 8 (last) · Files: `README.md`, `docs/engineering/STATE.md`

## Objective

After the campaign, update the docs so every capability claim matches the
merged code. Use the root's notes and the merged commits as the source.
**Do not describe anything you have not seen in the code.**

## Steps

1. Collect the facts from master:
   ```bash
   git log --oneline <campaign-start-commit>..HEAD
   ls ghost_agent
   "$PY" -m ghost_agent.cli --help
   ```
   Ask the root for `<campaign-start-commit>` (it is `a602677` unless the root
   says otherwise).
2. For each command documented in `README.md` ("Core commands", the `ghost
   accomplish` examples, and "What works today"), check that it exists in
   `--help` output. List any documented command that does not exist, and remove
   it or correct it.
3. Add a section to `README.md`, directly after "What works today", with this
   exact heading and table structure. Fill in the rows from the merged code and
   any owner-supplied live results:

   ```markdown
   ## Live vs placeholder (updated YYYY-MM-DD)

   | Capability | State | Evidence |
   |---|---|---|
   | Sandboxed coding jobs (`ghost accomplish`, worker) | live | <tests + owner bench result if provided> |
   | Greenfield empty-workspace jobs | live | T06 tests |
   | Benchmark (`ghost bench`) | live command; results: <owner's numbers or "not yet run"> | T11 |
   | Proactive tick / overnight / daemon | live, idle-gated | T12 tests; <owner run if provided> |
   | Self-improvement (`ghost self-improve` → `ghost self-repair`) | live pipeline, owner-gated promotion | T18 tests |
   | Learning (`ghost learn`) | local tracking + rustc oracle; no content generation | learner tests |
   | Telegram | placeholder until token + pairing | |
   | Huawei | placeholder until credentials | |
   | DeskSentinel | placeholder until firmware/protocol | |
   ```

   For any row whose task was **not** merged, write "not implemented". Never
   write "live" from tests alone for an external integration.
4. Add new commands (`bench`, `self-improve sync`, `accomplish --workspace`,
   `daemon run --model`) to "Core commands", each with a one-line description.
5. Remove from README every mention of the removed modules or commands:
   `nexus`, `capability_manager`, `notifications.py` and the "unified
   notification routing" wording, `memory/long_term`, `resource_manager`.
6. In `STATE.md`, add a section `## Campaign 2026-10 result` at the top, after
   the audit corrections. Include: the merged commit for each task (ask the
   root), the full-suite result (run it, and paste the final line), the owner's
   bench numbers if provided (otherwise "bench not yet run live"), and the
   remaining placeholders.
7. Do not delete historical STATE sections.

## Must NOT

- Claim live results the owner has not supplied.
- Edit code.

## Verification

```bash
"$PY" -m pytest -q -p no:cacheprovider
"$PY" -m ghost_agent.cli --help
git diff --stat
```

## Acceptance criteria

- AC1: Every command in README exists in `--help`.
- AC2: The live-vs-placeholder table matches the merged code, and no fake
  "live" claims are made.
- AC3: Removed modules are no longer mentioned as features.
- AC4: STATE.md has the campaign result, with the real full-suite line.
