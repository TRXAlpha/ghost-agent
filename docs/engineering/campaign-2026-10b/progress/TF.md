# Track TF progress: devices, device safety, Huawei route wiring (R11, S02, S03)

Base: 0e8bbc0 on c2/TF. Note: implementation was done by the track orchestrator
in root context (GLM-5.3-Flash implementation workers reliably die on output
exhaustion); review was delegated to independent read-only reviewer subagents
per the brief's review requirement.

| ID | status | candidate branch | review verdicts | merge commit | full-suite line | notes |
|----|--------|------------------|-----------------|--------------|-----------------|-------|
| TF2 | merged | c2/TF (9ea9349) | A: PASS; B: PASS (after fix) | 9ea9349 | 1060 passed in 109.00s | R11 fields, update/freshness, CLI pair/revoke/status, legacy load |
| TF1 | merged | c2/TF (132ab5f) | A: PASS; B: PASS (after fix) | 132ab5f | 1060 passed in 109.00s | `ghost huawei` CLI, synthetic-only, privacy-guarded, no network |
| TF4 | merged | c2/TF (132ab5f, 28f9af5) | A: PASS; B: FAIL→PASS after P2 fix | 28f9af5 | 1060 passed in 109.00s | retention 1-3650, purge on ingest, purge-all; P2 flaky privacy test fixed by parsing event JSON (28f9af5) |
| TF3 | merged | c2/TF (c1f2574) | A: PASS; B: PASS (after fix) | c1f2574 | 1060 passed in 109.00s | DeviceCommandGate + SimulatedPanTilt + CLI simulate; all refusal reasons tested |

## Reviews

- Reviewer A (correctness/security/tests): VERDICT PASS on c1f2574. Findings all P3:
  retention stored in retention.json not route state (disposition: acceptable;
  route record is a frozen dataclass; retention.json uses atomic_json);
  last_seen kept non-Optional for legacy compatibility; weak purge-event test
  assertion (fixed as P2 by reviewer B); update() allows status/constraints
  without shape validation (downstream-gated). Verified tests fail on base via
  scratch revert (9 failed + 9 collection errors).
- Reviewer B (architecture/wiring): VERDICT FAIL on c1f2574 with one P2 —
  tests/test_tf1_tf4_huawei.py:95 privacy assertion could pass vacuously and
  could flake on epoch-timestamp digits containing "42" (observed 2/7 full-suite
  runs). Fix: parse huawei.purge event JSON, assert payload keys ⊆
  {removed, reason} and value "42" absent (commit 28f9af5). Re-review: VERDICT
  PASS on 28f9af5; 3 consecutive green runs of the fixed test; remaining P3:
  _cmd_device register branch restructured (behavior-preserving, required to
  wire new sub-commands).

## Acceptance

1. All four tasks merged: TF2 9ea9349, TF1+TF4 132ab5f, TF3 c1f2574, review fix
   28f9af5 — each with reviewer PASS verdicts recorded above.
2. Full suite on c2/TF after last merge: `1060 passed in 109.00s (0:01:48)`.
3. Scope: diff 0e8bbc0...28f9af5 touches only the track ownership list
   (devices.py, huawei_route.py, desksentinel.py, device_control.py, cli.py)
   plus new tests. Nothing pushed; master untouched.
4. Denied commands: none encountered; no git push/reset/clean/rebase used.

External limitations: all device/health verification is local and simulated;
no real Huawei credentials or ESP32 hardware exist (synthetic-only by design).
