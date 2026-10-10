# Track TE progress

Base for all tasks: c2/TE. Every task: worker subagent → scope check (exact
file set, single commit) → independent read-only reviewer(s) via
delegate_task → repair + re-review when P0–P2 findings → merge --no-ff into
c2/TE → full suite. Final full-suite line after the last merge (TE3):

  1067 passed in 162.70s (0:02:42)
  (PY=ghost_agent/.venv, `pytest -q -p no:cacheprovider`, worktree ghost_c2_TE)

| ID | status | candidate branch | review verdicts | merge commit | full-suite line | notes |
|----|--------|------------------|-----------------|--------------|-----------------|-------|
| TE1 | merged | hermes-subagent/subagent-sa-0-6bd44a4c (26f9b9f) | A: PASS, B: PASS (P3 only) | d8543a2 | 1042 passed | snapshot adds held_notifications; held markers quiet-hours only at TE1 stage |
| TE2 | merged | hermes-subagent/subagent-sa-0-2b3f3246 (9cece88) | PASS (P3 only) | 60e8495 | 1056 passed (after TE2+TE4+TE6 batch) | BATCH_THRESHOLD=3; synthetic copy not persisted; originals ackable via --all |
| TE3 | merged | hermes-subagent/subagent-sa-0-72de8db6 (f757ca7) | A: PASS, B: PASS (P3 only) | 726077e | 1067 passed in 162.70s | ghost_agent/focus.py new; deliverable(focus_active=); held: focus vs held: quiet hours |
| TE4 | merged | hermes-subagent/subagent-sa-1-53f58cfb (25ef83c) | PASS (P3 only) | f1becee | 1056 passed (after TE2+TE4+TE6 batch) | GhostSDK.explain + ghost why; unknown id message tested |
| TE5 | merged | hermes-subagent/subagent-sa-1-4abcbf9f (47584f3) + repair hermes-subagent/subagent-sa-0-8e8df0ba (5101a0c) | initial: FAIL (P1 weakened _request_enable guard, P2 permissions unverified, 2xP3); repair re-review: PASS | 4c69a28 | 1046 passed | repair fixed all findings; manifest.json excluded from hash comparison (changes on enable) but permissions/side_effects compared; tamper guards on mark_tested/enable/request_enable |
| TE6 | merged | hermes-subagent/subagent-sa-2-64af888f (3c5ba7b) | PASS (P3 only) | ffb9d55 | 1056 passed (after TE2+TE4+TE6 batch) | STATES transitions already existed (tested); operationally_verified + verification_evidence added; CLI degrade/recover/verify-live |

## P3 dispositions (accepted, no repair)
- TE1/TE3: deliverable() computed twice in the `--all` path — harmless pure recomputation.
- TE1: invalid quiet-hours prefs degrade to deliver-all (consistent with _quiet_hours).
- TE2: batched-away items surface as `held: quiet hours` in --all (per sheet, TE1 marker stage); output ordering of batched groups nondeterministic (cosmetic).
- TE3: silent-priority held items under non-focus conditions keep the quiet-hours label.
- TE4: uses IntentStore._load (private); needle picks job_id when both ids known.
- TE5: tampered recovery requires manual state reset (by design: re-test + re-approve flow).
- TE6: no CLI-level automated test for the thin parser pass-throughs (store logic covered); recover emits no event.

## Compliance
- No commit on c2/TE touches files outside the track ownership list, AGENTS.md or .ghost/.
- Nothing pushed; master never modified. Denied commands: none.
