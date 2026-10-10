# Track TD: initiative, opportunities, overnight value (R03, S06, S07)

Audit: `audit/A3.md`. Branch `c2/TD`, worktree `C:/Users/chris/Documents/Ghost/ghost_c2_TD`.
Files this track owns: `ghost_agent/proactive.py` (`Opportunity`,
`OpportunityStore`, `ProactiveEngine`, `parse_proposal`; **not**
`queue_sandbox_outcome`, which TB edits), `ghost_agent/overnight.py` (digest
methods only), `ghost_agent/digest.py`, `ghost_agent/kernel.py` (`GoalStore`
read use only), `cli.py` (new blocks only), and tests.

---

## TD1: structured opportunity records and evidence (review: 2)

**Goal:** each opportunity records why it exists, what it links to, and when
to look at it again (R03, S07).

**Decided design:**

- Add these fields to `@dataclass Opportunity`, all with defaults so old
  records load:
  - `trigger: str = "tick"` (one of `tick`, `context_change`, `stale_goal`, `deadline`, `owner_request`, `failure`);
  - `goal_ids: list[str] = field(default_factory=list)`;
  - `evidence: list[dict] = field(default_factory=list)` (each `{"source": str, "excerpt": str}`, excerpt ≤ 300 chars, max 5 items);
  - `alternatives: list[str] = field(default_factory=list)` (max 3, each ≤ 200 chars, model-supplied);
  - `risks: list[str] = field(default_factory=list)` (max 3, model-supplied);
  - `review_at: Optional[float] = None`;
  - `attempts: int = 0`.
- `OpportunityStore._load` must ignore unknown keys and accept missing ones.
- `parse_proposal`: accept optional `alternatives` and `risks` lists from the
  model JSON, validated by length and type (drop invalid items, never raise
  for them). Update the prompt's JSON schema text in
  `ProactiveEngine._prompt` to mention both as optional.
- `ProactiveEngine.tick` fills, **deterministically** (never from the model):
  - `goal_ids`: ids of active goals whose title shares at least 2 lowercase
    word tokens (len ≥ 4) with the proposal goal;
  - `evidence`: one item per provided context source that contributed to the
    prompt (source name + first 300 chars used), plus one per matched goal;
  - `trigger`: `"stale_goal"` if TD2 supplied stale goals this tick, else `"tick"`;
  - `review_at = now + 7*86400` for `proposed`/`needs_input`.

**Tests:** old opportunities.json loads; a tick with a scripted chat produces a
record with evidence and goal_ids; invalid alternatives are dropped; the model
cannot set `goal_ids` or `evidence` (extra keys are ignored).

---

## TD2: stale-goal and deadline triggers

**Goal:** goals going stale or nearing a deadline feed initiative (R03
triggers).

**Decided design:** in `ProactiveEngine.context()`, call
`GoalStore(root).mark_stale(...)`. Read its signature in kernel.py:75; use its
default threshold. Add `"stale_goals": [{"goal_id", "title"}]` (max 5) to the
context dict, and `"upcoming_deadlines"` for active goals with a deadline
within 7 days (if Goal has a deadline field; if not, omit this key and say so
in the report). The prompt includes both lists. `mark_stale` must not run
more than once per 6 hours: store the last-run time in
`.ghost/proactive_state.json`.

**Tests:** a stale goal appears in context and the trigger is `stale_goal`;
the 6 h throttle holds; with no goals, both keys are empty.

---

## TD3: rejection memory and "less of this" (review: 2)

**Goal:** rejected or dismissed ideas, and owner "less of this" feedback,
suppress similar ideas, not just exact duplicates (S07, R09).

**Decided design:**

- New function `similar(a: str, b: str) -> float`: Jaccard similarity of
  lowercase word-token sets (tokens of length ≥ 3, stopwords removed with a
  small fixed list in the module).
- `OpportunityStore.suppressed_by_similarity(goal: str, days: int = 30) -> Optional[Opportunity]`
  returns the most similar opportunity with status `rejected`/`dismissed`, or
  `less_like_this` true (below), created within `days`, when similarity is at
  least `SIMILARITY_THRESHOLD = 0.6`.
- `tick`: if a new proposal is suppressed, record it with status `rejected`
  and `rejection_reason = f"similar to {id} ({score:.2f})"`, and do not queue.
- Add `less_like_this: bool = False` to Opportunity. The CLI is
  `ghost opportunity less <id>`, which sets it, and dismisses the opportunity
  if it is still `proposed`. Find the existing opportunity/proactive CLI
  group and add the sub-action there; if there is none, add a new
  `opportunity` parser after the `proactive` block.

**Tests:** a reworded rejected idea is suppressed; an unrelated idea isn't;
old rejections (> 30 days) don't suppress; `less` marks and dismisses; the
similarity function cases.

---

## TD4: one clarifying question for needs_input

**Goal:** when an opportunity needs owner input, Ghost asks one targeted
question instead of silently parking it (S07).

**Decided design:** add `question: str = ""` to Opportunity. `parse_proposal`
accepts an optional `question` (≤ 240 chars). When the resulting status is
`needs_input` and the question is non-empty, `tick` sends exactly one
`AttentionManager.notify(Notification("normal", f"Ghost needs input: {question} (opportunity {id})", source="proactive"))`,
deduplicated by `notification_id = f"opp-question-{id}"`. Check that
`notify` dedups by id; it does by content per audit A1, so reuse it.

**Tests:** needs_input with a question notifies once, even across two ticks;
no question → no notification.

---

## TD5: daily inference budget for unattended loops

**Goal:** idle-time inference is bounded by a durable daily call cap (S06
"paid inference ... limits remain enforced").

**Decided design:**

- `ProactiveEngine.__init__` gets
  `inference_budget: Optional[DurableInferenceBudget] = None`. When set, `tick`
  reserves before the model call: `reservation = budget.reserve(1)`. If that
  fails because the budget is exhausted (catch the exception `reserve` raises;
  read spend.py), return `{"status": "deferred", "reason": "inference_budget_exhausted"}`
  without calling the model. Pass `budget=` and `reservation=` to `chat()`,
  which already commits or rolls back.
- Daily window: create the budget with
  `path = root/.ghost/inference/<YYYY-MM-DD>.json` (local date) and
  `max_calls = N`.
- CLI: `--max-inference-calls N` (default 48, range 1–10000) on
  `overnight run` and `daemon run`. These handlers build the budget and pass
  it through to the ProactiveEngine that `OvernightRunner` constructs. Add an
  `inference_budget` keyword to `OvernightRunner.__init__` and use it in
  `_proactive()`; TA edits other parts of these files, so keep the change
  local.

**Tests:** after N reservations the tick defers without calling chat; a new
day means a new file; the CLI default of 48 reaches the engine.

---

## TD6: one digest with outcomes and coverage (review: 2)

**Goal:** the morning digest reports what actually ran, what was verified,
and when the PC wasn't running (S06).

**Decided design:**

- `OvernightRunner._render_digest` gets two new sections:
  1. **"Outcomes"**: call `MorningDigest(self.root).collect(since)`, reusing its
     outcome-store collection (read digest.py first), and render its items
     with `MorningDigest.render`. This makes MorningDigest's logic reachable
     from production.
  2. **"Coverage"**: the first and last cycle timestamps in the window. Any
     gap between consecutive cycles longer than `3 × interval` is listed as
     `"no cycles from HH:MM to HH:MM (PC asleep/offline or Ghost not running)"`.
     With zero cycles, the digest says `"Ghost did not run during this window."`.
- Do not add a second digest file or notification. `MorningDigest.emit` stays
  as is (it may remain CLI/test-only); only its collect/render are reused.

**Tests:** the digest contains Outcomes with a completed job fixture; a gap is
reported; zero cycles gives the explicit sentence.
