# Track TH progress (campaign-2026-10b)

| ID | status | candidate branch | review verdicts | merge commit | full-suite line | notes |
|----|--------|------------------|-----------------|--------------|-----------------|-------|
| TH1 | merged | hermes-subagent/subagent-sa-0-f4f362e4 (3e37e07) | A: PASS (P3 parse-error counting, disposed: intentional, coherent with REPAIR iteration cost); B: PASS (P3 dead except block left, P3 parse_error keying — both disposed) | 3b3ff67 | 1041 passed in 193.51s | timeout default 60→180 at 6 cli.py sites; proactive.py min(60) cap untouched |
| TH2 | merged | hermes-subagent/subagent-sa-0-e0539f3b (1834aca) + repair ff888e4 | A: PASS→P2 probe clobber → repair re-review PASS (P3 owner-context erasure on unknown probe, disposition accepted: sheet permits overwriting context_tokens) | bdbf867 | 1050 passed in 126.17s | probe merges into owner profile, preserving owner-set values |
| TH4 | merged | hermes-subagent/subagent-sa-1-3cf26d74 (0471725) | PASS (P3 arg duplication in cli.py — sheet-allowed, protocol_benchmark.py must stay untouched) | merged via 1834aca chain (in bdbf867) | 1050 passed in 126.17s | argv rebuild + forward to protocol_benchmark.main() |
| TH3 | merged | hermes-subagent/subagent-sa-0-f0f5a280 (8b5a571 + bb179e3) | A: PASS (P3 refusal→{} conversion, P3 DNS-rebinding TOCTOU residual, P3 loose public-IP assert); B: PASS (same P3s, all disposed: fail-closed behavior retained, TOCTOU out of scope) | cd994df | 1059 passed in 135.86s | GHOST_ALLOW_PRIVATE_FETCH=1 override; two pre-existing tests repaired by fixture-only changes (root disposition) |

| TH5 | merged | hermes-subagent/subagent-sa-0-cbc4a31b (5c6f454) + repair 1c4c0b8 | A: FAIL on 2 P2 test-coverage gaps → repaired → re-review PASS | 693dd3e + 0131eb0 | see final line | truncation counting at call sites (disposed: matches TH1 pattern); unrelated pre-existing Huawei failure fixed on master (3505342), resolved by re-sync |
| TH6 | merged | hermes-subagent/subagent-sa-1-8797f0cf (f79b290) | B: PASS (P3 looser schema default accepted: still rejected downstream by _validate_action_inputs) | 0131eb0 | see final line | content_ref blocks resolved before placeholder guard; plain form dump byte-identical |

## Final suite (after last merge, c2/TH @ 461f1a5, includes TH5/TH6 and master sync)

`1225 passed in 119.79s (0:01:59)`

- TH5: truncations counted at the PLAN/IMPLEMENT/REPAIR call sites (state not visible inside _model_turn), matching TH1's model_failures pattern.

## Final verification

- Files touched on c2/TH since 4ceb41d (excluding docs/): ghost_agent/cli.py, ghost_agent/models/profiles.py, ghost_agent/orchestrator.py, ghost_agent/schema.py, ghost_agent/tools/web_tools.py, tests/* — all within the track ownership list.
- No commit touches master, nothing pushed, no denied commands.

## Dispositions / root decisions

- TH1: model_failures counts any failed model turn (parse errors included), reset on success — coherent with "a broken model channel" and the sheet's reset-on-success rule.
- TH2 repair: probe merges into an existing owner-set profile, overwriting only context_tokens/source/updated_at; the P3 edge (owner context erased when probe learns nothing) accepted per sheet's overwrite rule.
- TH3: refusal error type is the module's ValueError; the surrounding except converting refusal to {} is the module's pre-existing convention and remains fail-closed. Two legacy tests (loopback server, synthetic publisher hosts) fixed by fixture patches only, never by weakening the block. DNS-rebinding TOCTOU noted as residual P3, out of scope.

No denied commands. Nothing pushed; master untouched.
