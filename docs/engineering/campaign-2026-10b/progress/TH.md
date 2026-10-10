# Track TH progress (campaign-2026-10b)

| ID | status | candidate branch | review verdicts | merge commit | full-suite line | notes |
|----|--------|------------------|-----------------|--------------|-----------------|-------|
| TH1 | merged | hermes-subagent/subagent-sa-0-f4f362e4 (3e37e07) | A: PASS (P3 parse-error counting, disposed: intentional, coherent with REPAIR iteration cost); B: PASS (P3 dead except block left, P3 parse_error keying — both disposed) | 3b3ff67 | 1041 passed in 193.51s | timeout default 60→180 at 6 cli.py sites; proactive.py min(60) cap untouched |
| TH2 | merged | hermes-subagent/subagent-sa-0-e0539f3b (1834aca) + repair ff888e4 | A: PASS→P2 probe clobber → repair re-review PASS (P3 owner-context erasure on unknown probe, disposition accepted: sheet permits overwriting context_tokens) | bdbf867 | 1050 passed in 126.17s | probe merges into owner profile, preserving owner-set values |
| TH4 | merged | hermes-subagent/subagent-sa-1-3cf26d74 (0471725) | PASS (P3 arg duplication in cli.py — sheet-allowed, protocol_benchmark.py must stay untouched) | merged via 1834aca chain (in bdbf867) | 1050 passed in 126.17s | argv rebuild + forward to protocol_benchmark.main() |
| TH3 | merged | hermes-subagent/subagent-sa-0-f0f5a280 (8b5a571 + bb179e3) | A: PASS (P3 refusal→{} conversion, P3 DNS-rebinding TOCTOU residual, P3 loose public-IP assert); B: PASS (same P3s, all disposed: fail-closed behavior retained, TOCTOU out of scope) | cd994df | 1059 passed in 135.86s | GHOST_ALLOW_PRIVATE_FETCH=1 override; two pre-existing tests repaired by fixture-only changes (root disposition) |

## Final suite (after last merge, c2/TH @ cd994df)

`1059 passed in 135.86s (0:02:15)`

## Dispositions / root decisions

- TH1: model_failures counts any failed model turn (parse errors included), reset on success — coherent with "a broken model channel" and the sheet's reset-on-success rule.
- TH2 repair: probe merges into an existing owner-set profile, overwriting only context_tokens/source/updated_at; the P3 edge (owner context erased when probe learns nothing) accepted per sheet's overwrite rule.
- TH3: refusal error type is the module's ValueError; the surrounding except converting refusal to {} is the module's pre-existing convention and remains fail-closed. Two legacy tests (loopback server, synthetic publisher hosts) fixed by fixture patches only, never by weakening the block. DNS-rebinding TOCTOU noted as residual P3, out of scope.

No denied commands. Nothing pushed; master untouched.
