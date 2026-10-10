# Track TN: transparent, outcome-tuned opportunity ranking (R03)

Wave 2 track, created 2026-10-10 when TD finished. Audit: `audit/A3.md`
(R03 gap 3: "Initiative ranking/selection is heuristic … no composite score,
no ranking reasons, no tuning from outcomes, benefit enum never consulted").
Branch `c2/TN`, worktree `C:/Users/chris/Documents/Ghost/ghost_c2_TN`.
Files this track owns: `ghost_agent/proactive.py`, new
`ghost_agent/initiative.py`, `cli.py` (the proactive/opportunity block and
new blocks only), and tests.

Context: after TD, `Opportunity` has `kind`, `confidence`, `trigger`,
`goal_ids`, `evidence_items`, `cost_effort` (small|medium|large), `benefit`
(low|medium|high), `result`, and `status`. Today `tick` auto-queues when
`status == "proposed" and confidence >= 0.65 and kind != "focus"`
(proactive.py, the `if auto_queue and ...` line).

---

## TN1: composite score with recorded reasons (review: 2)

**Decided design:** new module `ghost_agent/initiative.py`:

```python
DEFAULT_WEIGHTS = {"goal_relevance": 0.25, "urgency": 0.2, "confidence": 0.2,
                   "benefit": 0.2, "effort": 0.1, "interruption": 0.05}
def load_weights(root: Path) -> dict[str, float]  # .ghost/initiative_weights.json overrides; invalid file -> DEFAULT_WEIGHTS + a warning entry
def score(opp: Opportunity, *, kind_success: float | None = None, weights=None) -> tuple[float, list[str]]
```

Factor values, each in 0..1:
- `goal_relevance`: 1.0 if `goal_ids` is non-empty, else 0.3.
- `urgency`: 1.0 if `trigger == "deadline"`, 0.7 if `"stale_goal"`, else 0.3.
- `confidence`: `opp.confidence` clamped to 0..1.
- `benefit`: low 0.2 / medium 0.6 / high 1.0.
- `effort`: small 1.0 / medium 0.6 / large 0.3 (cheaper is better).
- `interruption`: 0.2 if `requires_user_input`, else 1.0.

`base = sum(weight * value) / sum(weights)`. Outcome tuning: when
`kind_success` is not None, `final = base * (0.5 + 0.5 * kind_success)`.
`reasons` is a list of short strings, one per factor (for example
`"benefit high (+0.20)"`), plus the tuning line when applied. Round the score
to 3 decimals.

- Add `score: float = 0.0` and `score_reasons: list[str] = field(default_factory=list)`
  to `Opportunity` (defaults so old records load).
- In `tick`, compute the score after building the proposal, then store it and
  its reasons.
- **Auto-queue rule becomes:**
  `status == "proposed" and kind != "focus" and confidence >= 0.65 and score >= QUEUE_THRESHOLD`,
  where `QUEUE_THRESHOLD = 0.55`, overridable as `"threshold"` in the weights
  file. When the score blocks queueing, the opportunity stays `proposed`, with
  the extra reason `"below queue threshold <t>"`.
- Do not present the score as validated utility: the reasons list ends with
  `"heuristic ranking; tuned only by recorded outcomes"`.

**Tests:** factor values for each enum; the weighted sum; a weights-file
override plus an invalid file falling back; the threshold blocking a
high-confidence/low-score proposal; legacy records load; the reasons contain
every factor.

---

## TN2: tuning from recorded outcomes

**Decided design:** `kind_success_rate(store: OpportunityStore, kind: str) -> float | None`
in initiative.py, over the opportunities of that kind with `result` in
{`tests_passed`, `tests_failed`, `error`} **or** status
`rejected`/`dismissed` with `less_like_this` true: success =
`tests_passed` / all of those. It returns None when there are fewer than 3
decided items (no evidence → no tuning). `tick` passes it to `score`.

**Tests:** fewer than 3 → None; a mix of results gives the right rate;
less_like_this counts as a failure; the tuning lowers a historically failing
kind's score.

---

## TN3: bound active initiatives (parallel-ok with TN2)

**Decided design:** `MAX_ACTIVE_INITIATIVES = 2` (overridable as
`"max_active"` in the weights file). `tick` counts opportunities with status
`queued`/`running`. If that count is at least the maximum, a new proposal is
stored as `proposed` with the reason `"active initiative limit reached (n/max)"`
and is not queued. `ghost proactive ranking [--limit N]` prints the proposed
and queued opportunities sorted by score with their reasons, and `--json`.

**Tests:** the limit holds; the ranking output is ordered with its reasons.
