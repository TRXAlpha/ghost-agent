# Track TH: model resilience, model profiles, safe research fetch (R05, R06)

Audit: `audit/A5.md`, plus the meta finding from the live benchmark on
2026-10-10. Branch `c2/TH`, worktree `C:/Users/chris/Documents/Ghost/ghost_c2_TH`.
Files this track owns: `ghost_agent/orchestrator.py` (`_model_turn` and the
`_run_loop` exception handling only), `ghost_agent/models/*.py`,
`ghost_agent/tools/web_tools.py`, `ghost_agent/protocol_benchmark.py`,
`cli.py` (the `GHOST_OLLAMA_TIMEOUT` default and new blocks), and tests.

---

## TH1: a model timeout fails the turn, not the whole job (review: 2)

**Goal:** the live benchmark (gemma4:e4b on a 4 GB GPU) failed 3 of 3 jobs
outright with `HTTPConnectionPool ... Read timed out (read timeout=60)`. `chat()`
retries transport errors, then raises; `_model_turn` re-raises; the worker
marks the whole job `failed`. A slow model must cost one bounded turn, not the
outcome.

**Decided design:**

- In `Orchestrator._model_turn`: catch `requests.exceptions.Timeout`,
  `requests.exceptions.ConnectionError` and `OSError` raised by `ollama_chat`
  **after** chat's own retries. Return
  `(ActionResponse(thought="model_unavailable", actions=[]), f"model call failed: {type(exc).__name__}: {str(exc)[:200]}")`,
  so the existing parse-error path treats it as a failed turn. The next phase
  is REPAIR, and it consumes an iteration per T08's rules.
- Add `state["model_failures"]` (an int, consecutive count; reset to 0 on any
  successful model response). In `_run_loop`, if it reaches
  `MAX_CONSECUTIVE_MODEL_FAILURES = 3`, end with `phase=DONE` and
  `last_result="model_unavailable"`, an honest terminal result. Write the
  state and memory like the iteration-limit path.
- Do not catch other exceptions (budget/integration/programming errors keep
  propagating).
- Raise the default of `GHOST_OLLAMA_TIMEOUT` from 60 to 180 at every
  `os.getenv("GHOST_OLLAMA_TIMEOUT", "60")` site in cli.py (find them with
  `git grep -n GHOST_OLLAMA_TIMEOUT`). `proactive.py`'s `min(60, ...)` cap
  stays as is; it isn't this track's file.

**Tests:** a scripted chat raising `requests.exceptions.ReadTimeout` once, then
succeeding → the job still reaches `tests_passed`; three consecutive timeouts
→ `last_result == "model_unavailable"`; a `ValueError` from chat still
propagates.

---

## TH2: model profiles (unknown is unknown)

**Goal:** a model profile records its limits, modalities and cost, with
unknown values kept explicitly unknown (R05).

**Decided design:** new `ghost_agent/models/profiles.py`:

```python
@dataclass
class ModelProfile:
    model: str
    provider: str = "ollama"
    context_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    supports_tools: Optional[bool] = None
    modalities: list[str] = field(default_factory=lambda: ["text"])
    cost_per_1k_input: Optional[float] = None   # None = unknown, NOT free
    cost_per_1k_output: Optional[float] = None
    source: str = "owner"                         # owner | probe
    updated_at: float = ...
class ModelProfileStore:  # .ghost/model_profiles.json via atomic_json
    def get(self, model) -> ModelProfile   # unknown model -> profile with all None
    def set(self, profile) -> None
    def probe_ollama(self, model, base_url) -> ModelProfile  # reads /api/show for context length; source="probe"; never guesses missing values
```

CLI: `ghost model-profile show <model>`, `set <model> --context N --max-output N --cost-in X --cost-out Y`,
and `probe <model>`. `ghost doctor` doesn't change. The rule "unknown cost is
not zero" is enforced by `ModelProfile.estimated_cost(...)`, which returns
None if any needed price is None.

**Tests:** an unknown model gives all-None values; estimated_cost is None with
an unknown price; probe parses a fixture `/api/show` response (patch
requests); set/show round trip.

---

## TH3: private-network fetch block (review: 2)

**Goal:** research fetches can't reach localhost, private, link-local or
metadata addresses unless explicitly scoped (R06).

**Decided design:** in `tools/web_tools.py`, add
`_is_forbidden_destination(host: str) -> bool`. It resolves the host with
`socket.getaddrinfo` (all results) and returns True if **any** address is
loopback, private (RFC1918, fc00::/7), link-local (169.254/16, fe80::/10),
multicast, unspecified, or reserved (use `ipaddress` properties). Unresolvable
hosts are forbidden (fail closed). Call it before **every** request in
`fetch_source_text`, including each redirect hop and the first request, and
in any other fetch function in that module (find them). It raises the
module's existing refusal error type. Environment variable
`GHOST_ALLOW_PRIVATE_FETCH=1` disables the check (explicit owner scope);
document it in the function docstring.

**Tests** (patch `socket.getaddrinfo`; no network): localhost, 10.x,
169.254.169.254 and ::1 are refused; a public IP is allowed; a redirect to a
private IP is refused at the hop; resolution failure is refused; the env
override allows it.

---

## TH4: `ghost protocol-bench` CLI (parallel-ok with TH3)

**Goal:** the existing non-executing protocol benchmark is reachable from the
main CLI (R05 reporting).

**Decided design:** add a `protocol-bench` parser block that forwards to
`protocol_benchmark`'s existing main/argparse entry. Read the module; reuse
its argument definitions, either by calling its parser-building function or
by passing argv through. No behavior change.

**Tests:** `ghost protocol-bench --help` works; a dry/fixture run path if the
module has one; otherwise only argument forwarding with the run function
patched.

---

# Added 2026-10-10 by the meta-orchestrator (live benchmark findings)

The live benchmark shows `IMPLEMENT: 0` and 0 completed goals. Root causes
from `llm.log`:

- The model puts whole files in `thought`, then repeats them in `actions`,
  and is cut off at `num_predict = 2048` partway through the JSON, which then
  fails to parse.
- Code escaped inside JSON strings is mangled by a 7B model.
- `SYSTEM_PROMPT` Example 2 literally teaches the placeholder `"# updated content"`.
- The rules say to "ALWAYS use web_search", which drives pointless searches.
- After a failed PLAN turn the loop never reaches IMPLEMENT (REPAIR ↔ VERIFY only).

Extra files this track owns for these tasks: `ghost_agent/schema.py` and the
`SYSTEM_PROMPT` constant and `_run_loop` PLAN/REPAIR branches in
`orchestrator.py`.

## TH5: prompt and output-budget fixes plus truncation feedback (review: 2)

**Decided design:**

1. `Orchestrator.__init__` default `max_output_tokens` goes from 2048 to
   4096 (the cap stays 8192).
2. `SYSTEM_PROMPT` edits (exactly these):
   - Add to RULES: `- "thought" is at most two short sentences. Never put code in "thought".`
     and `- Write at most ONE file per response; you will get more turns.`
   - Replace Example 2's `"content": "# updated content"` with real code
     content (for example a two-line function), so the prompt never shows a
     placeholder.
   - Replace the three web_search STRATEGY bullets ("Use web_search when you
     need…", "If you do not know something, ALWAYS use web_search…", "If asked
     to come up with a new idea, FIRST use web_search…") with one bullet:
     `- Use web_search only when the task needs information that is not in the workspace or the task description.`
3. Truncation detection in `_model_turn`: when parsing fails **and** the
   stripped response does not end with `}` (nor with a closing ``` fence after
   a `}`), replace the parse error text with:
   `"Your response was cut off because it was too long. Keep thought to one sentence and write only ONE file per response."`
   and count it in `state["truncations"]`. TH1/T10 already add counters;
   follow their pattern.
4. Plan failure no longer blocks implementation: in the PLAN branch, if the
   plan turn fails **for any reason**, go to `IMPLEMENT` (not REPAIR) with
   `last_result="plan_skipped"`, and set the feedback to the parse/plan error.
   The plan is an aid, not a gate. The plan turn still happens once.

**Tests:** default max tokens; prompt contains the new rules and no
`# updated content` (assert on the SYSTEM_PROMPT text); a truncated scripted
response produces the cut-off feedback and increments truncations; a failed
plan goes to IMPLEMENT (assert `state["turns"]["IMPLEMENT"] >= 1` with a
scripted model that fails the plan and then writes a correct file and test).

## TH6: raw file blocks for write_file content (review: 2)

**Goal:** small models write file content without escaping it inside JSON.

**Decided design:**

- New response convention, documented in `SYSTEM_PROMPT` with one example. A
  `write_file` action may use `"content_ref": "F1"` instead of `"content"`,
  and the file body follows the JSON object as:
  ```text
  <<<F1
  ...raw file content, any characters...
  F1>>>
  ```
- `schema.parse_action_response`: before JSON parsing, extract all blocks
  matching `^<<<([A-Z][A-Z0-9_]{0,15})\n(.*?)\n\1>>>$` (multiline, dotall),
  remove them from the text, parse the JSON as today, then for every
  `write_file` action with `content_ref`, set `content` to the block body. A
  missing ref → `ValueError("content_ref F1 has no matching block")`. Having
  both `content` and `content_ref` is a `ValueError`. Unused blocks are ignored.
- The existing plain `content` form keeps working unchanged.
- Update the write_file schema/validator so `content_ref` is allowed and
  resolved before `_validate_action_inputs` runs (placeholder checks apply to
  the resolved content).
- In SYSTEM_PROMPT, make the file-block form the **recommended** form for
  files over 5 lines.

**Tests:** a block with quotes, backslashes and braces round-trips exactly;
multiple blocks; a missing ref raises; both content and content_ref raises;
the plain content form is unchanged; the resolved content passes through the
T09 placeholder guard.
